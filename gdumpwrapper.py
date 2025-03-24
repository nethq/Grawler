#!/usr/bin/env python3
"""
Gerrit JSON Visualizer Wrapper
==============================

This script processes a JSON dump (from a Gerrit query command) and visualizes
its Gerrit comments using VS Code's diff view. It makes no network requests and
dynamically adapts to the JSON structure without static key assumptions.

Key features:
  • Dynamically parses JSON output (whether a JSON array, one JSON object per
    line, or a single object) with robust error handling.
  • Supports filtering by patchset (--patchset NUM) and by file (--file FILTER).
  • Outputs summary information in JSON, Markdown, or plain text (--output-format).
  • Generates annotated copies of files (with inline comment markers) and launches
    VS Code's built-in diff view (via "code --diff") without modifying the original.
  • Manages temporary files using Python’s tempfile module (with optional cleanup).

Usage:
  $ ./gerrit_json_visualizer.py --json-file dump.json [options]

Options:
  --json-file PATH        Path to the dumped JSON file (required)
  --patchset NUM          Only process data for the given patchset number.
  --file FILTER           Only process files whose names include FILTER (substring match).
  --output-format FMT     One or more output formats: json, markdown, text (default: json)
  --summary-file PATH     Write summary output to the specified file.
  --vscode                Launch VS Code diff views for files with inline comments.
  --no-cleanup            Do not delete temporary files (for debugging).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import shutil

# ----------------------------
# JSON Dump Parsing Functions
# ----------------------------
def parse_gerrit_json_dump(json_path):
    """
    Reads the JSON dump file and returns a Gerrit change object.
    Handles files containing:
      • A JSON array of objects
      • Multiple JSON objects (one per line)
      • A single JSON object
    Ignores objects with a "rowCount" key (stats objects).
    Exits if no valid change object is found.
    """
    if not os.path.exists(json_path):
        sys.exit(f"Error: JSON file '{json_path}' does not exist.")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except Exception as e:
        sys.exit(f"Error reading '{json_path}': {e}")

    valid = []
    # First try to load as a complete JSON structure.
    try:
        data = json.loads(content)
        if isinstance(data, list):
            valid = [d for d in data if isinstance(d, dict) and "rowCount" not in d]
        elif isinstance(data, dict):
            valid = [data]
    except json.JSONDecodeError:
        # Fallback: process line by line.
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and "rowCount" not in obj:
                    valid.append(obj)
            except json.JSONDecodeError:
                continue

    if not valid:
        sys.exit("No valid Gerrit change object found in the JSON dump.")
    if len(valid) > 1:
        print("Warning: Multiple change objects found; using the first one.")
    return valid[0]

# ----------------------------
# Dynamic Data Extraction Helpers
# ----------------------------
def get_key(data, key, default=None):
    """
    Returns data[key] if found; otherwise, tries case-insensitive match.
    """
    if key in data:
        return data[key]
    lower_key = key.lower()
    for k, v in data.items():
        if k.lower() == lower_key:
            return v
    return default

def get_patchsets(data):
    """Returns the list of patch sets from the JSON data."""
    ps = get_key(data, "patchSets")
    if ps is None:
        ps = get_key(data, "patch_sets", [])
    if not isinstance(ps, list):
        return []
    return ps

def get_comments(data):
    """Returns the inline comments dictionary from the JSON data."""
    comments = get_key(data, "comments", {})
    if not isinstance(comments, dict):
        return {}
    return comments

def get_messages(data):
    """Returns the change messages from the JSON data (if any)."""
    messages = get_key(data, "messages", [])
    if not isinstance(messages, list):
        return []
    return messages

# ----------------------------
# Summary Formatting Functions
# ----------------------------
def format_summary_json(data, patchset_filter=None, file_filter=None):
    summary = {"change": {}, "patchSets": [], "messages": [], "comments": {}}
    summary["change"] = {k: v for k, v in data.items() if k not in ["patchSets", "patch_sets", "messages", "comments"]}
    patchsets = get_patchsets(data)
    if patchset_filter:
        patchsets = [ps for ps in patchsets if str(ps.get("number")) == str(patchset_filter)]
    summary["patchSets"] = patchsets
    summary["messages"] = get_messages(data)
    comments = get_comments(data)
    if file_filter:
        comments = {f: cs for f, cs in comments.items() if file_filter in f}
    if patchset_filter:
        for file, cs in comments.items():
            filtered = []
            for c in cs:
                ps_val = c.get("patchSet", c.get("patch_set"))
                if str(ps_val) == str(patchset_filter):
                    filtered.append(c)
            comments[file] = filtered
    summary["comments"] = comments
    return json.dumps(summary, indent=2)

def format_summary_markdown(data, patchset_filter=None, file_filter=None):
    lines = []
    change_number = get_key(data, "_number", "Unknown")
    subject = get_key(data, "subject", "No subject")
    lines.append(f"# Gerrit Change {change_number}: {subject}\n")
    lines.append("## Patch Sets")
    patchsets = get_patchsets(data)
    if patchset_filter:
        patchsets = [ps for ps in patchsets if str(ps.get("number")) == str(patchset_filter)]
    if patchsets:
        for ps in sorted(patchsets, key=lambda x: int(x.get("number", 0))):
            rev = ps.get("revision", "N/A")[:7]
            uploader = get_key(ps, "uploader", {}).get("name", "Unknown")
            created = ps.get("created", "")
            lines.append(f"- **Patchset {ps.get('number')}**: revision `{rev}`, uploader: {uploader}, created: {created}")
    else:
        lines.append("None")
    lines.append("\n## Change Messages")
    messages = get_messages(data)
    if messages:
        for m in messages:
            ps_num = m.get("_revision_number", "N/A")
            author = get_key(m, "author", {}).get("name", "Unknown")
            date = m.get("date", "")
            msg = m.get("message", "")
            lines.append(f"- **Patchset {ps_num}** by {author} on {date}: {msg}")
    else:
        lines.append("None")
    lines.append("\n## Inline Comments by File")
    comments = get_comments(data)
    if file_filter:
        comments = {f: cs for f, cs in comments.items() if file_filter in f}
    if comments:
        for file, comms in comments.items():
            lines.append(f"### File: {file}")
            grouped = {}
            for c in comms:
                ps = c.get("patchSet", c.get("patch_set", "Unknown"))
                grouped.setdefault(ps, []).append(c)
            for ps, clist in grouped.items():
                if patchset_filter and str(ps) != str(patchset_filter):
                    continue
                lines.append(f"- **Patchset {ps}**:")
                for c in clist:
                    line_no = c.get("line", "N/A")
                    reviewer = get_key(c, "reviewer", {}).get("name", "Unknown")
                    message = c.get("message", "")
                    lines.append(f"    - Line {line_no}: {reviewer}: {message}")
    else:
        lines.append("None")
    return "\n".join(lines)

def format_summary_text(data, patchset_filter=None, file_filter=None):
    lines = []
    change_number = get_key(data, "_number", "Unknown")
    subject = get_key(data, "subject", "No subject")
    lines.append(f"Gerrit Change {change_number}: {subject}\n")
    lines.append("Patch Sets:")
    patchsets = get_patchsets(data)
    if patchset_filter:
        patchsets = [ps for ps in patchsets if str(ps.get("number")) == str(patchset_filter)]
    if patchsets:
        for ps in sorted(patchsets, key=lambda x: int(x.get("number", 0))):
            rev = ps.get("revision", "N/A")[:7]
            uploader = get_key(ps, "uploader", {}).get("name", "Unknown")
            created = ps.get("created", "")
            lines.append(f"  Patchset {ps.get('number')}: revision {rev}, uploader: {uploader}, created: {created}")
    else:
        lines.append("  None")
    lines.append("\nChange Messages:")
    messages = get_messages(data)
    if messages:
        for m in messages:
            ps_num = m.get("_revision_number", "N/A")
            author = get_key(m, "author", {}).get("name", "Unknown")
            date = m.get("date", "")
            msg = m.get("message", "")
            lines.append(f"  Patchset {ps_num} by {author} on {date}: {msg}")
    else:
        lines.append("  None")
    lines.append("\nInline Comments by File:")
    comments = get_comments(data)
    if file_filter:
        comments = {f: cs for f, cs in comments.items() if file_filter in f}
    if comments:
        for file, comms in comments.items():
            lines.append(f"File: {file}")
            grouped = {}
            for c in comms:
                ps = c.get("patchSet", c.get("patch_set", "Unknown"))
                grouped.setdefault(ps, []).append(c)
            for ps, clist in grouped.items():
                if patchset_filter and str(ps) != str(patchset_filter):
                    continue
                lines.append(f"  Patchset {ps}:")
                for c in clist:
                    line_no = c.get("line", "N/A")
                    reviewer = get_key(c, "reviewer", {}).get("name", "Unknown")
                    msg = c.get("message", "")
                    lines.append(f"    Line {line_no}: {reviewer}: {msg}")
    else:
        lines.append("  None")
    return "\n".join(lines)

# ----------------------------
# Annotate Files for VS Code Diff
# ----------------------------
def annotate_file_with_comments(filepath, comments, patchset_filter=None):
    """
    Reads the original file and creates an annotated version (as a string) with inline
    comment markers. Comments are grouped by line number and optionally filtered by patchset.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            original_lines = f.readlines()
    except Exception as e:
        print(f"Error reading file '{filepath}': {e}")
        return None

    annotations = {}
    for comment in comments:
        ps = comment.get("patchSet", comment.get("patch_set", "Unknown"))
        if patchset_filter and str(ps) != str(patchset_filter):
            continue
        line_no = comment.get("line")
        if not line_no:
            continue
        reviewer = get_key(comment, "reviewer", {}).get("name", "Unknown")
        message = comment.get("message", "")
        ann = f"[Patchset {ps}] {reviewer}: {message}"
        annotations.setdefault(line_no, []).append(ann)

    annotated_lines = []
    for i, line in enumerate(original_lines, start=1):
        annotated_lines.append(line.rstrip("\n"))
        if i in annotations:
            for ann in annotations[i]:
                annotated_lines.append("  >>> " + ann)
    return "\n".join(annotated_lines) + "\n"

def show_diff_in_vscode(original_file, annotated_content, temp_dir):
    """
    Writes the annotated content to a temporary file and opens VS Code's diff view
    between the original file and the annotated version.
    """
    base_name = os.path.basename(original_file)
    temp_file = os.path.join(temp_dir, base_name + ".annotated")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(annotated_content)
    except Exception as e:
        print(f"Error writing temporary file for '{original_file}': {e}")
        return

    try:
        subprocess.run(["code", "--diff", original_file, temp_file])
    except Exception as e:
        print(f"Failed to open VS Code diff for '{original_file}': {e}")

def process_and_show_diffs(data, patchset_filter=None, file_filter=None, temp_dir_path=None):
    """
    Processes inline comments from the JSON data. For each file (filtered by --file if provided),
    if the file exists locally, an annotated copy is generated and VS Code's diff view is launched.
    Files not found are noted.
    """
    comments = get_comments(data)
    if file_filter:
        comments = {f: cs for f, cs in comments.items() if file_filter in f}
    if not comments:
        print("No inline comments found for diff view.")
        return
    for file_path, comm_list in comments.items():
        if not os.path.exists(file_path):
            print(f"Warning: File not found locally: {file_path}")
            continue
        annotated = annotate_file_with_comments(file_path, comm_list, patchset_filter)
        if annotated is None:
            continue
        show_diff_in_vscode(file_path, annotated, temp_dir_path)

# ----------------------------
# Main Function and Argument Parsing
# ----------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Process a Gerrit JSON dump and visualize comments using VS Code's diff view."
    )
    parser.add_argument("--json-file", required=True, help="Path to the dumped JSON file")
    parser.add_argument("--patchset", help="Filter by patchset number", type=str)
    parser.add_argument("--file", help="Filter by file name (substring match)")
    parser.add_argument(
        "--output-format",
        choices=["json", "markdown", "text"],
        nargs="+",
        default=["json"],
        help="Output summary formats (default: json)"
    )
    parser.add_argument("--summary-file", help="Path to save the summary output")
    parser.add_argument("--vscode", action="store_true", help="Launch VS Code diff views for files with inline comments")
    parser.add_argument("--no-cleanup", action="store_true", help="Do not delete temporary files (for debugging)")
    args = parser.parse_args()

    # Parse the JSON dump robustly.
    data = parse_gerrit_json_dump(args.json_file)

    # Build summaries based on requested formats.
    summaries = {}
    for fmt in args.output_format:
        if fmt == "json":
            summaries["json"] = format_summary_json(data, args.patchset, args.file)
        elif fmt == "markdown":
            summaries["markdown"] = format_summary_markdown(data, args.patchset, args.file)
        elif fmt == "text":
            summaries["text"] = format_summary_text(data, args.patchset, args.file)

    # Print summaries to stdout.
    for fmt, summary in summaries.items():
        print(f"\n--- {fmt.upper()} SUMMARY ---\n")
        print(summary)
    if args.summary_file:
        try:
            with open(args.summary_file, "w", encoding="utf-8") as f:
                for fmt, summary in summaries.items():
                    f.write(f"--- {fmt.upper()} SUMMARY ---\n\n")
                    f.write(summary + "\n\n")
            print(f"Summary written to {args.summary_file}")
        except Exception as e:
            print(f"Error writing summary file: {e}")

    # If requested, launch VS Code diff views.
    if args.vscode:
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                print(f"Using temporary directory: {temp_dir}")
                process_and_show_diffs(data, args.patchset, args.file, temp_dir)
                if args.no_cleanup:
                    preserved = os.path.join(os.getcwd(), "gerrit_temp_preserved")
                    shutil.copytree(temp_dir, preserved)
                    print(f"Temporary files preserved in: {preserved}")
                else:
                    input("Press Enter to finish and clean up temporary files...")
        except Exception as e:
            print(f"Error handling temporary files: {e}")

if __name__ == "__main__":
    main()
