#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import getpass

# ----------------------------
# Git Helper Functions
# ----------------------------
def run_git_command(cmd):
    try:
        out = subprocess.check_output(["git"] + cmd, stderr=subprocess.STDOUT)
        return out.decode("utf-8").strip()
    except subprocess.CalledProcessError as e:
        print("Git command failed:", e.output.decode())
        sys.exit(1)

def get_current_commit():
    return run_git_command(["rev-parse", "HEAD"])

def get_commit_message(commit_hash):
    return run_git_command(["log", "-1", "--pretty=%B", commit_hash])

def extract_change_id(commit_message):
    # Look for a Change-Id line and return the value (raw, without prefix text)
    m = re.search(r'Change-Id:\s*(I[a-f0-9]+)', commit_message)
    if m:
        return m.group(1)
    return None

# ----------------------------
# SSH Query & JSON Parsing
# ----------------------------
def run_ssh_query(gerrit_config, identifier_value):
    """
    Runs:
      ssh -p <port> <user>@<host> gerrit query --patch-sets --comments <identifier_value> --format=JSON
    Note: identifier_value is used raw (no prefix).
    Returns the first JSON object (the change info) or exits on failure.
    """
    ssh_user = gerrit_config.get("ssh_user")
    ssh_host = gerrit_config.get("ssh_host")
    ssh_port = str(gerrit_config.get("ssh_port", "29418"))
    # Build the command (using shell=False is tricky with arguments, so here we join)
    cmd = [
        "ssh", "-p", ssh_port,
        f"{ssh_user}@{ssh_host}",
        "gerrit", "query", "--patch-sets", "--comments", identifier_value, "--format=JSON"
    ]
    try:
        result = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        print("SSH query failed:", e.output.decode())
        sys.exit(1)

    # Gerrit outputs one JSON object per line; the final line is stats.
    change_objs = []
    for line in result.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            # Skip the stats object (if present)
            if "rowCount" in obj:
                continue
            change_objs.append(obj)
        except json.JSONDecodeError as e:
            print("Warning: JSON decode error on line:", line)
    if not change_objs:
        print("No change information found from Gerrit query.")
        sys.exit(1)
    return change_objs[0]

# ----------------------------
# Output Formatting Functions
# ----------------------------
def format_output_json(change_info):
    # Save the entire change_info as JSON (it contains patchSets, comments, messages, etc.)
    return json.dumps(change_info, indent=2)

def format_output_markdown(change_info):
    md = []
    change_number = change_info.get("_number", "Unknown")
    subject = change_info.get("subject", "No subject")
    md.append(f"# Gerrit Change {change_number}: {subject}\n")
    
    md.append("## Patch Sets\n")
    patchsets = change_info.get("patchSets", [])
    if patchsets:
        # Sort patch sets by number
        patchsets.sort(key=lambda ps: int(ps.get("number", 0)))
        for ps in patchsets:
            rev = ps.get("revision", "N/A")[:7]
            uploader = ps.get("uploader", {}).get("name", "Unknown")
            created = ps.get("created", "")
            md.append(f"- **Patchset {ps.get('number')}**: revision `{rev}`, uploader: {uploader}, created: {created}")
    else:
        md.append("No patch set information found.")

    md.append("\n## Change Messages\n")
    messages = change_info.get("messages", [])
    if messages:
        for m in messages:
            ps_num = m.get("_revision_number", "N/A")
            author = m.get("author", {}).get("name", "Unknown")
            date = m.get("date", "")
            msg = m.get("message", "")
            md.append(f"- **Patchset {ps_num}** by {author} on {date}: {msg}")
    else:
        md.append("No change messages found.")
        
    md.append("\n## Inline Comments by File\n")
    comments = change_info.get("comments", {})
    if comments:
        for file, comms in comments.items():
            md.append(f"### File: {file}")
            # Group comments by patch set
            grouped = {}
            for c in comms:
                ps = c.get("patchSet", c.get("patch_set", "Unknown"))
                grouped.setdefault(ps, []).append(c)
            for ps, clist in grouped.items():
                md.append(f"  - **Patchset {ps}**:")
                for c in clist:
                    line = c.get("line", "N/A")
                    reviewer = c.get("reviewer", {}).get("name", "Unknown")
                    msg = c.get("message", "")
                    md.append(f"      - Line {line}: {reviewer}: {msg}")
    else:
        md.append("No inline comments found.")
    return "\n".join(md)

def format_output_text(change_info):
    txt = []
    change_number = change_info.get("_number", "Unknown")
    subject = change_info.get("subject", "No subject")
    txt.append(f"Gerrit Change {change_number}: {subject}\n")
    
    txt.append("Patch Sets:")
    patchsets = change_info.get("patchSets", [])
    if patchsets:
        patchsets.sort(key=lambda ps: int(ps.get("number", 0)))
        for ps in patchsets:
            rev = ps.get("revision", "N/A")[:7]
            uploader = ps.get("uploader", {}).get("name", "Unknown")
            created = ps.get("created", "")
            txt.append(f"  Patchset {ps.get('number')}: revision {rev}, uploader: {uploader}, created: {created}")
    else:
        txt.append("  None")
        
    txt.append("\nChange Messages:")
    messages = change_info.get("messages", [])
    if messages:
        for m in messages:
            ps_num = m.get("_revision_number", "N/A")
            author = m.get("author", {}).get("name", "Unknown")
            date = m.get("date", "")
            msg = m.get("message", "")
            txt.append(f"  Patchset {ps_num} by {author} on {date}: {msg}")
    else:
        txt.append("  None")
        
    txt.append("\nInline Comments by File:")
    comments = change_info.get("comments", {})
    if comments:
        for file, comms in comments.items():
            txt.append(f"File: {file}")
            grouped = {}
            for c in comms:
                ps = c.get("patchSet", c.get("patch_set", "Unknown"))
                grouped.setdefault(ps, []).append(c)
            for ps, clist in grouped.items():
                txt.append(f"  Patchset {ps}:")
                for c in clist:
                    line = c.get("line", "N/A")
                    reviewer = c.get("reviewer", {}).get("name", "Unknown")
                    msg = c.get("message", "")
                    txt.append(f"    Line {line}: {reviewer}: {msg}")
    else:
        txt.append("  None")
    return "\n".join(txt)

# ----------------------------
# VS Code Diff Functions
# ----------------------------
def annotate_file_with_all_comments(filepath, comments):
    """
    Read the original file and create an annotated version that includes inline comment markers.
    Comments are grouped by line number and show the patch set number and reviewer.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            original_lines = f.readlines()
    except Exception as e:
        print(f"Error reading file {filepath}: {e}")
        return None

    # Build a mapping: line number -> list of annotation strings.
    annotations = {}
    for comment in comments:
        line = comment.get("line")
        if line is None:
            continue
        ps = comment.get("patchSet", comment.get("patch_set", "Unknown"))
        reviewer = comment.get("reviewer", {}).get("name", "Unknown")
        msg = comment.get("message", "")
        annotation = f"[Patchset {ps}] {reviewer}: {msg}"
        annotations.setdefault(line, []).append(annotation)

    annotated_lines = []
    for i, line in enumerate(original_lines, start=1):
        annotated_lines.append(line.rstrip("\n"))
        if i in annotations:
            for ann in annotations[i]:
                # Prepend an annotation marker; these lines are only for display.
                annotated_lines.append("  >>> " + ann)
    return "\n".join(annotated_lines) + "\n"

def show_diff_in_vscode(filepath, annotated_content):
    """
    Write the annotated content to a temporary file and open a VS Code diff view
    between the original file and the temporary annotated version.
    """
    temp_dir = os.path.join(tempfile.gettempdir(), "gerrit_comments")
    os.makedirs(temp_dir, exist_ok=True)
    temp_file = os.path.join(temp_dir, os.path.basename(filepath) + ".annotated")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(annotated_content)
    except Exception as e:
        print(f"Error writing temporary file for {filepath}: {e}")
        return

    try:
        subprocess.run(["code", "--diff", filepath, temp_file])
    except Exception as e:
        print(f"Failed to open VS Code diff for {filepath}: {e}")

def display_all_file_diffs(change_info):
    """
    For every file that has inline comments (across all patch sets), if the file exists
    locally, generate an annotated copy with all comments and open VS Code diff view.
    If a file does not exist, note it.
    """
    comments = change_info.get("comments", {})
    for file_path, comm_list in comments.items():
        if not os.path.exists(file_path):
            print(f"File not found locally: {file_path}")
            continue
        annotated = annotate_file_with_all_comments(file_path, comm_list)
        if annotated is None:
            continue
        show_diff_in_vscode(file_path, annotated)

# ----------------------------
# Main Function
# ----------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Gerrit Comments Fetcher via SSH with VS Code Diff Display"
    )
    parser.add_argument("--config", help="Path to config file (JSON)")
    parser.add_argument(
        "--identifier",
        choices=["change", "commit"],
        default="change",
        help="Search by raw change id (default) or commit hash"
    )
    parser.add_argument(
        "--commit",
        help="Commit hash to use (if identifier is commit or for extracting Change-Id)",
    )
    parser.add_argument(
        "--output-format",
        choices=["json", "markdown", "text"],
        nargs="+",
        default=["json"],
        help="Output formats to save (one or more)"
    )
    parser.add_argument(
        "--vscode",
        action="store_true",
        help="Open inline diff views in VS Code (using built-in diff)"
    )
    # SSH connection overrides
    parser.add_argument("--ssh-user", help="SSH user for Gerrit")
    parser.add_argument("--ssh-host", help="SSH host for Gerrit")
    parser.add_argument("--ssh-port", type=int, help="SSH port for Gerrit")
    args = parser.parse_args()

    # Load config if provided.
    config = {}
    if args.config:
        if not os.path.exists(args.config):
            print("Config file not found:", args.config)
            sys.exit(1)
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            print("Error loading config:", e)
            sys.exit(1)
    gerrit_config = config.get("gerrit", {})

    # Override SSH parameters from CLI if provided.
    if args.ssh_user:
        gerrit_config["ssh_user"] = args.ssh_user
    if args.ssh_host:
        gerrit_config["ssh_host"] = args.ssh_host
    if args.ssh_port:
        gerrit_config["ssh_port"] = args.ssh_port

    # Ensure required SSH parameters.
    for param in ["ssh_user", "ssh_host"]:
        if param not in gerrit_config:
            val = input(f"Enter Gerrit {param.replace('_', ' ')}: ").strip()
            gerrit_config[param] = val

    # Determine the identifier value.
    if args.identifier == "commit":
        commit_hash = args.commit if args.commit else get_current_commit()
        print("Using commit:", commit_hash)
        identifier_value = commit_hash
    else:
        # Default is "change". Extract Change-Id from commit message.
        commit_hash = args.commit if args.commit else get_current_commit()
        commit_msg = get_commit_message(commit_hash)
        change_id = extract_change_id(commit_msg)
        if not change_id:
            print("No Change-Id found in commit message. Cannot map to a Gerrit change.")
            sys.exit(1)
        print("Found Change-Id:", change_id)
        identifier_value = change_id

    # Run SSH query.
    print("Querying Gerrit with identifier:", identifier_value)
    change_info = run_ssh_query(gerrit_config, identifier_value)
    change_number = change_info.get("_number", "Unknown")
    subject = change_info.get("subject", "No subject")
    print(f"Found Gerrit Change {change_number}: {subject}")

    # Save output files (all patch sets, messages, inline comments, etc.)
    for fmt in args.output_format:
        if fmt == "json":
            output = format_output_json(change_info)
            ext = "json"
        elif fmt == "markdown":
            output = format_output_markdown(change_info)
            ext = "md"
        elif fmt == "text":
            output = format_output_text(change_info)
            ext = "txt"
        else:
            continue
        filename = f"comments_{change_number}.{ext}"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"Output saved to {filename}")
        except Exception as e:
            print(f"Error writing to {filename}: {e}")

    # Use VS Code diff view to show inline comments without modifying files.
    if args.vscode:
        print("Launching VS Code diff views for files with inline comments...")
        display_all_file_diffs(change_info)

if __name__ == "__main__":
    main()
