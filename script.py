#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import getpass

# ----------------------------
# Git Helpers
# ----------------------------
def run_git_command(cmd):
    try:
        result = subprocess.check_output(["git"] + cmd, stderr=subprocess.STDOUT)
        return result.decode().strip()
    except subprocess.CalledProcessError as e:
        print("Git command failed:", e.output.decode())
        sys.exit(1)

def get_current_commit():
    return run_git_command(["rev-parse", "HEAD"])

def get_commit_message(commit_hash):
    return run_git_command(["log", "-1", "--pretty=%B", commit_hash])

def extract_change_id(commit_message):
    match = re.search(r'Change-Id:\s*(I[a-f0-9]+)', commit_message)
    if match:
        return match.group(1)
    else:
        return None

# ----------------------------
# SSH Query Functions
# ----------------------------
def query_change_ssh(gerrit_config, query_param):
    """
    Uses SSH to run:
      ssh -p <port> <user>@<host> gerrit query --patch-sets --comments --format=JSON <query_param>
    Returns a list of change objects (skipping the final stats object).
    """
    ssh_host = gerrit_config.get("ssh_host")
    ssh_port = str(gerrit_config.get("ssh_port", "29418"))
    ssh_user = gerrit_config.get("ssh_user")
    if not ssh_host or not ssh_user:
        print("SSH configuration (ssh_host and ssh_user) missing in config.")
        sys.exit(1)
    cmd = [
        "ssh", "-p", ssh_port,
        f"{ssh_user}@{ssh_host}",
        "gerrit", "query", "--patch-sets", "--comments", "--format=JSON", query_param
    ]
    try:
        result = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        output_lines = result.decode().strip().splitlines()
        changes = []
        for line in output_lines:
            if line.strip():
                try:
                    obj = json.loads(line)
                    if "rowCount" in obj:  # skip the stats line
                        continue
                    changes.append(obj)
                except json.JSONDecodeError as e:
                    print("Error decoding JSON:", e)
        return changes
    except subprocess.CalledProcessError as e:
        print("SSH command failed:", e.output.decode())
        sys.exit(1)

def select_change(changes, query_value):
    if not changes:
        print("No matching change found on Gerrit for query:", query_value)
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
    For SSH output, patchsets are provided under the "patchSets" key.
    Returns a tuple: (patchset number, revision id, created timestamp)
    """
    patchsets = change_info.get("patchSets", [])
    if not patchsets:
        print("No patchsets found in change info.")
        sys.exit(1)
    ps_list = []
    for ps in patchsets:
        # Use "number" (or _number) and "revision"
        number = ps.get("number") or ps.get("_number")
        revision = ps.get("revision")
        created = ps.get("createdOn", "Unknown")
        ps_list.append((number, revision, created))
    # Sort by patchset number (as integer)
    ps_list.sort(key=lambda x: int(x[0]))
    if chosen_patchset:
        for ps in ps_list:
            if str(ps[0]) == str(chosen_patchset):
                return ps
        print(f"Patchset {chosen_patchset} not found.")
        sys.exit(1)
    if len(ps_list) == 1:
        return ps_list[0]
    print("Available patchsets:")
    for ps in ps_list:
        print(f"Patchset {ps[0]}: revision {ps[1][:7]} (Created: {ps[2]})")
    choice = input("Select patchset number (or press Enter for latest): ")
    if choice.strip() == "":
        return ps_list[-1]
    else:
        for ps in ps_list:
            if str(ps[0]) == choice.strip():
                return ps
        print("Invalid patchset selection.")
        sys.exit(1)

# ----------------------------
# Output Formatting Functions
# ----------------------------
def format_json(inline_comments, messages, patchset_number):
    filtered_messages = [m for m in messages if str(m.get("_revision_number")) == str(patchset_number)]
    combined = {
        "inline_comments": inline_comments,
        "change_messages": filtered_messages
    }
    return json.dumps(combined, indent=2)

def format_markdown(inline_comments, messages, patchset_number, change_number):
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
            # Only include comments for the chosen patchset.
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
# VS Code Integration
# ----------------------------
def vscode_open_comments(inline_comments, patchset_number):
    """
    For each inline comment in the chosen patchset, list its file and line.
    Then prompt the user to select one, and open that file in VS Code at that exact line.
    """
    comment_list = []
    for file, comments in inline_comments.items():
        for c in comments:
            if str(c.get("patch_set")) == str(patchset_number):
                line = c.get("line")
                author = c.get("author", {}).get("name", "Unknown")
                message = c.get("message", "")
                comment_list.append((file, line, author, message))
    if not comment_list:
        print("No inline comments found for VS Code integration.")
        return
    print("\nInline Comments Found:")
    for idx, (file, line, author, message) in enumerate(comment_list, start=1):
        summary = message if len(message) < 50 else message[:50] + "..."
        print(f"{idx}: {file} line {line} by {author} - {summary}")
    choice = input("Enter comment number to open in VS Code (or press Enter to skip): ")
    if not choice.strip():
        return
    try:
        sel = int(choice)
        if sel < 1 or sel > len(comment_list):
            raise ValueError
    except ValueError:
        print("Invalid selection.")
        return
    file, line, author, message = comment_list[sel-1]
    print(f"Opening {file} at line {line} in VS Code...")
    try:
        subprocess.run(["code", "--goto", f"{file}:{line}"])
    except Exception as e:
        print("Failed to open in VS Code:", e)

# ----------------------------
# Main Function
# ----------------------------
def main():
    parser = argparse.ArgumentParser(description="Gerrit Comments Fetcher via SSH")
    parser.add_argument("--config", help="Path to config file (JSON)")
    parser.add_argument("--method", choices=["ssh", "rest"], default="ssh",
                        help="Method to use (default: ssh)")
    parser.add_argument("--commit", help="Commit hash to use (default: HEAD)")
    parser.add_argument("--query-mode", choices=["change-id", "commit"], default="change-id",
                        help="Query by change-id (default) or by commit")
    parser.add_argument("--patchset", help="Patchset number to fetch", type=int)
    parser.add_argument("--output-format", choices=["json", "markdown", "text"], nargs="+",
                        default=["json"], help="Output format(s)")
    parser.add_argument("--vscode", action="store_true",
                        help="Open inline comments in VS Code at their exact file and line")
    args = parser.parse_args()

    # Load configuration if provided.
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
    # Get Gerrit config (for SSH, expect ssh_host, ssh_user, ssh_port)
    gerrit_config = config.get("gerrit", {})
    gerrit_config.setdefault("method", args.method)
    if gerrit_config["method"] == "ssh":
        if not gerrit_config.get("ssh_host"):
            gerrit_config["ssh_host"] = input("Enter Gerrit SSH host (e.g., gerrit.example.com): ").strip()
        if not gerrit_config.get("ssh_user"):
            gerrit_config["ssh_user"] = input("Enter Gerrit SSH username: ").strip()
        if not gerrit_config.get("ssh_port"):
            gerrit_config["ssh_port"] = input("Enter Gerrit SSH port (default 29418): ").strip() or "29418"

    # Get commit hash (default HEAD) and commit message
    commit_hash = args.commit if args.commit else get_current_commit()
    print("Using commit:", commit_hash)
    commit_message = get_commit_message(commit_hash)
    change_id = extract_change_id(commit_message)
    if not change_id:
        print("No Change-Id found in commit message. Cannot map to a Gerrit change.")
        sys.exit(1)
    print("Found Change-Id:", change_id)

    # Build query parameter based on query mode.
    if args.query_mode == "change-id":
        query_param = f"Change-Id:{change_id}"
    else:
        query_param = f"commit:{commit_hash}"

    # Use SSH method (default) to query Gerrit.
    if gerrit_config["method"] == "ssh":
        changes = query_change_ssh(gerrit_config, query_param)
    else:
        print("REST method not implemented; defaulting to SSH.")
        changes = query_change_ssh(gerrit_config, query_param)

    change_info = select_change(changes, query_param)
    change_number = change_info.get("_number")
    print(f"Found Gerrit change: {change_number} - {change_info.get('subject')}")

    # Select patchset (by CLI flag or prompt)
    ps = select_patchset(change_info, chosen_patchset=args.patchset)
    patchset_number, revision_id, created = ps
    print(f"Selected Patchset {patchset_number}: revision {revision_id[:7]}, created on: {created}")

    # In SSH query, inline comments and messages are returned within the change info.
    inline_comments = change_info.get("comments", {})
    messages = change_info.get("messages", [])

    # Generate output files in the chosen format(s).
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

    # VS Code Integration: Offer to open inline comments at their file and line.
    if args.vscode:
        vscode_open_comments(inline_comments, patchset_number)

if __name__ == "__main__":
    main()
