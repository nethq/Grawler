#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import getpass
import requests

# ----------------------------
# Helper functions for Git
# ----------------------------
def run_git_command(cmd):
    """Run a git command and return its output."""
    try:
        result = subprocess.check_output(["git"] + cmd, stderr=subprocess.STDOUT)
        return result.decode().strip()
    except subprocess.CalledProcessError as e:
        print("Git command failed:", e.output.decode())
        sys.exit(1)

def get_current_commit():
    """Return the current commit hash (HEAD)."""
    return run_git_command(["rev-parse", "HEAD"])

def get_commit_message(commit_hash):
    """Return the commit message for a given commit hash."""
    return run_git_command(["log", "-1", "--pretty=%B", commit_hash])

def extract_change_id(commit_message):
    """Extract Gerrit Change-Id from the commit message."""
    match = re.search(r'Change-Id:\s*(I[a-f0-9]+)', commit_message)
    if match:
        return match.group(1)
    else:
        return None

# ----------------------------
# Gerrit REST API functions
# ----------------------------
def query_change_rest(gerrit_config, query_param):
    """
    Query Gerrit for a change using the given query parameter.
    Returns a list of changes (could be empty).
    """
    base_url = gerrit_config.get("base_url").rstrip("/")
    url = f"{base_url}/changes/?q={query_param}&o=ALL_REVISIONS"
    auth = None
    if "username" in gerrit_config and "password" in gerrit_config:
        auth = (gerrit_config["username"], gerrit_config["password"])
    try:
        resp = requests.get(url, auth=auth)
        if resp.status_code != 200:
            print(f"Error: Received status code {resp.status_code} from Gerrit")
            sys.exit(1)
        # Gerrit REST responses start with ")]}'" – remove it.
        text = re.sub(r"^\)\]\}'\n", "", resp.text)
        data = json.loads(text)
        return data
    except Exception as e:
        print("Error querying Gerrit:", e)
        sys.exit(1)

def fetch_comments_rest(gerrit_config, change_number, revision):
    """
    For a given change (by its Gerrit numeric ID) and revision (commit hash),
    fetch inline comments and change messages.
    """
    base_url = gerrit_config.get("base_url").rstrip("/")
    auth = None
    if "username" in gerrit_config and "password" in gerrit_config:
        auth = (gerrit_config["username"], gerrit_config["password"])
    # Inline comments endpoint:
    url_inline = f"{base_url}/changes/{change_number}/revisions/{revision}/comments"
    # Change messages endpoint:
    url_messages = f"{base_url}/changes/{change_number}/messages"
    try:
        resp_inline = requests.get(url_inline, auth=auth)
        if resp_inline.status_code != 200:
            print("Error fetching inline comments. Status:", resp_inline.status_code)
            inline_comments = {}
        else:
            text_inline = re.sub(r"^\)\]\}'\n", "", resp_inline.text)
            inline_comments = json.loads(text_inline)

        resp_messages = requests.get(url_messages, auth=auth)
        if resp_messages.status_code != 200:
            print("Error fetching change messages. Status:", resp_messages.status_code)
            messages = []
        else:
            text_messages = re.sub(r"^\)\]\}'\n", "", resp_messages.text)
            messages = json.loads(text_messages)
        return inline_comments, messages
    except Exception as e:
        print("Error fetching comments:", e)
        sys.exit(1)

# ----------------------------
# Selection and formatting helpers
# ----------------------------
def select_change(changes, change_id):
    """If multiple changes are found, prompt the user to select one."""
    if not changes:
        print("No matching change found on Gerrit.")
        sys.exit(1)
    if len(changes) > 1:
        print("Multiple changes found:")
        for idx, ch in enumerate(changes):
            print(f"{idx+1}: Change {ch.get('_number')} - {ch.get('subject')}")
        choice = input("Select the change number: ")
        try:
            index = int(choice) - 1
            if index < 0 or index >= len(changes):
                raise ValueError
        except ValueError:
            print("Invalid selection.")
            sys.exit(1)
        return changes[index]
    else:
        return changes[0]

def select_patchset(change_info, chosen_patchset=None):
    """
    Given change info (with a revisions dict), let the user select a patchset.
    Returns a tuple: (patchset number, revision id, subject, created date)
    """
    revisions = change_info.get("revisions", {})
    patchsets = []
    for rev_id, rev_info in revisions.items():
        number = rev_info.get("_number")
        created = rev_info.get("created")
        subject = rev_info.get("commit", {}).get("subject", "")
        patchsets.append((number, rev_id, subject, created))
    # Sort by patchset number
    patchsets.sort(key=lambda x: x[0])
    if chosen_patchset:
        for ps in patchsets:
            if str(ps[0]) == str(chosen_patchset):
                return ps
        print(f"Patchset {chosen_patchset} not found.")
        sys.exit(1)
    if len(patchsets) == 1:
        return patchsets[0]
    print("Available patchsets:")
    for ps in patchsets:
        print(f"Patchset {ps[0]}: revision {ps[1][:7]} - {ps[2]} (Created: {ps[3]})")
    choice = input("Select patchset number (or press Enter for latest): ")
    if choice.strip() == "":
        return patchsets[-1]
    else:
        for ps in patchsets:
            if str(ps[0]) == choice.strip():
                return ps
        print("Invalid patchset selection.")
        sys.exit(1)

def format_json(inline_comments, messages, patchset_number):
    """Return a JSON-formatted string with comments."""
    filtered_messages = [m for m in messages if str(m.get("_revision_number")) == str(patchset_number)]
    combined = {
        "inline_comments": inline_comments,
        "change_messages": filtered_messages
    }
    return json.dumps(combined, indent=2)

def format_markdown(inline_comments, messages, patchset_number, change_number):
    """Return a Markdown-formatted string with comments."""
    md = f"# Comments for Change {change_number}, Patchset {patchset_number}\n\n"
    filtered_messages = [m for m in messages if str(m.get("_revision_number")) == str(patchset_number)]
    if filtered_messages:
        md += "## General Comments\n"
        for m in filtered_messages:
            author = m.get("author", {}).get("name", "Unknown")
            date = m.get("date", "")
            text = m.get("message", "")
            md += f"- **{author}** ({date}): {text}\n"
        md += "\n"
    if inline_comments:
        md += "## Inline Comments\n"
        for file, comments in inline_comments.items():
            relevant = [c for c in comments if str(c.get("patch_set")) == str(patchset_number)]
            if relevant:
                md += f"### File: {file}\n"
                for c in relevant:
                    line = c.get("line", "N/A")
                    author = c.get("author", {}).get("name", "Unknown")
                    message = c.get("message", "")
                    md += f"- Line {line} by **{author}**: {message}\n"
                md += "\n"
    return md

def format_text(inline_comments, messages, patchset_number, change_number):
    """Return a plain-text formatted string with comments."""
    txt = f"Comments for Change {change_number}, Patchset {patchset_number}\n\n"
    filtered_messages = [m for m in messages if str(m.get("_revision_number")) == str(patchset_number)]
    if filtered_messages:
        txt += "General Comments:\n"
        for m in filtered_messages:
            author = m.get("author", {}).get("name", "Unknown")
            date = m.get("date", "")
            text_msg = m.get("message", "")
            txt += f"- {author} ({date}): {text_msg}\n"
        txt += "\n"
    if inline_comments:
        txt += "Inline Comments:\n"
        for file, comments in inline_comments.items():
            relevant = [c for c in comments if str(c.get("patch_set")) == str(patchset_number)]
            if relevant:
                txt += f"File: {file}\n"
                for c in relevant:
                    line = c.get("line", "N/A")
                    author = c.get("author", {}).get("name", "Unknown")
                    message = c.get("message", "")
                    txt += f"  - Line {line} by {author}: {message}\n"
                txt += "\n"
    return txt

# ----------------------------
# Main function
# ----------------------------
def main():
    parser = argparse.ArgumentParser(description="Gerrit Comments Fetcher")
    parser.add_argument("--config", help="Path to config file (JSON)")
    parser.add_argument("--method", choices=["rest", "ssh"], help="Method to use: rest or ssh")
    parser.add_argument("--commit", help="Commit hash to use (default: HEAD)")
    parser.add_argument("--patchset", help="Patchset number to fetch", type=int)
    parser.add_argument("--output-format", choices=["json", "markdown", "text"], nargs="+", default=["json"],
                        help="Output formats (one or more)")
    parser.add_argument("--vscode", action="store_true", help="Open output file in VS Code after generation")
    args = parser.parse_args()

    # Load configuration if provided
    config = {}
    if args.config:
        if not os.path.exists(args.config):
            print("Config file not found:", args.config)
            sys.exit(1)
        with open(args.config, "r") as f:
            try:
                config = json.load(f)
            except Exception as e:
                print("Error loading config:", e)
                sys.exit(1)

    # Get Gerrit config (or prompt if missing)
    gerrit_config = config.get("gerrit", {})
    if not gerrit_config.get("base_url"):
        base_url = input("Enter Gerrit base URL (e.g., https://gerrit.example.com): ").strip()
        gerrit_config["base_url"] = base_url

    # Determine method: CLI flag, config, or default to REST.
    if args.method:
        method = args.method
    else:
        method = gerrit_config.get("method", "rest")
    
    # For REST, ensure username and password are provided.
    if method == "rest":
        if "username" not in gerrit_config:
            gerrit_config["username"] = input("Enter Gerrit username: ").strip()
        if "password" not in gerrit_config:
            gerrit_config["password"] = getpass.getpass("Enter Gerrit HTTP password or API token: ")

    # Get commit hash from Git (or use provided one)
    commit_hash = args.commit if args.commit else get_current_commit()
    print("Using commit:", commit_hash)

    commit_message = get_commit_message(commit_hash)
    change_id = extract_change_id(commit_message)
    if not change_id:
        print("No Change-Id found in commit message. Cannot map to a Gerrit change.")
        sys.exit(1)
    print("Found Change-Id:", change_id)

    # Query Gerrit for the change using commit hash first
    query_param = f"commit:{commit_hash}"
    changes = query_change_rest(gerrit_config, query_param)
    if not changes:
        # Fallback: query using Change-Id if commit search fails
        query_param = f"Change-Id:{change_id}"
        changes = query_change_rest(gerrit_config, query_param)
    change_info = select_change(changes, change_id)
    change_number = change_info.get("_number")
    print(f"Found Gerrit change: {change_number} - {change_info.get('subject')}")

    # Select patchset (either via CLI flag or prompt)
    patchset_tuple = select_patchset(change_info, chosen_patchset=args.patchset)
    patchset_number, revision_id, subject, created = patchset_tuple
    print(f"Selected Patchset {patchset_number}: revision {revision_id[:7]}, subject: {subject}")

    # Fetch comments using the chosen method (currently REST)
    if method == "rest":
        inline_comments, messages = fetch_comments_rest(gerrit_config, change_number, revision_id)
    else:
        print("SSH method not implemented in this version.")
        sys.exit(1)

    # Format and save output in requested formats.
    for fmt in args.output_format:
        if fmt == "json":
            output = format_json(inline_comments, messages, patchset_number)
            ext = "json"
        elif fmt == "markdown":
            output = format_markdown(inline_comments, messages, patchset_number, change_number)
            ext = "md"
        elif fmt == "text":
            output = format_text(inline_comments, messages, patchset_number, change_number)
            ext = "txt"
        else:
            continue
        filename = f"comments_{change_number}_ps{patchset_number}.{ext}"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output saved to {filename}")
        if args.vscode:
            try:
                subprocess.run(["code", filename])
            except Exception as e:
                print("Failed to open file in VS Code:", e)

if __name__ == "__main__":
    main()
