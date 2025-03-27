#!/usr/bin/env python3
"""
Gerrit Modular Diff Wrapper

This script processes a Gerrit JSON dump (or a saved comments file) and visualizes
the corresponding file versions annotated with inline comments. It supports two modes:
  (1) Directory mode – diff between a cached directory and the current working directory.
  (2) Git regeneration mode – retrieve file versions from Git based on a patchset’s revision.
It can save or load the grepped comments, and it launches VS Code’s diff view (or an alternative
GUI diff tool) to show the annotated version versus the current file.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import shutil

def load_json_file(path):
    """Load JSON data from a file. Exits on error."""
    if not os.path.exists(path):
        sys.exit(f"Error: File '{path}' not found.")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content
    except Exception as e:
        sys.exit(f"Error reading file '{path}': {e}")

def parse_gerrit_json_dump(path):
    """Parse a Gerrit JSON dump and return the first valid change object."""
    content = load_json_file(path)
    valid = []
    try:
        data = json.loads(content)
        if isinstance(data, list):
            valid = [d for d in data if isinstance(d, dict) and "rowCount" not in d]
        elif isinstance(data, dict):
            valid = [data]
    except json.JSONDecodeError:
        for line in content.splitlines():
            try:
                obj = json.loads(line.strip())
                if isinstance(obj, dict) and "rowCount" not in obj:
                    valid.append(obj)
            except json.JSONDecodeError:
                continue
    if not valid:
        sys.exit("Error: No valid Gerrit change object found in the JSON dump.")
    if len(valid) > 1:
        print("Warning: Multiple change objects found; using the first one.")
    return valid[0]

def get_key(data, key, default=None):
    """Retrieve a key from a dictionary, case-insensitively."""
    if key in data:
        return data[key]
    lower_key = key.lower()
    for k, v in data.items():
        if k.lower() == lower_key:
            return v
    return default

def get_patchsets(data):
    """Return a list of patch sets from the change object."""
    ps = get_key(data, "patchSets") or get_key(data, "patch_sets", [])
    return ps if isinstance(ps, list) else []

def get_comments(data):
    """Return a dictionary of inline comments from the change object."""
    cm = get_key(data, "comments", {})
    return cm if isinstance(cm, dict) else {}

def get_messages(data):
    """Return a list of change messages from the change object."""
    ms = get_key(data, "messages", [])
    return ms if isinstance(ms, list) else []

def get_patchset_revision(data, patchset_number):
    """Return the revision (commit hash) for a given patchset number."""
    for ps in get_patchsets(data):
        if str(ps.get("number")) == str(patchset_number):
            return ps.get("revision")
    return None

def save_comments_to_file(data, path):
    """Save the full change object (or comments) to a file in JSON format."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Saved comments to '{path}'.")
    except Exception as e:
        sys.exit(f"Error saving to '{path}': {e}")

def load_comments_from_file(path):
    """Load a change object from a saved JSON file."""
    content = load_json_file(path)
    try:
        data = json.loads(content)
        return data
    except Exception as e:
        sys.exit(f"Error parsing JSON from '{path}': {e}")

def get_git_file_from_revision(revision, file_path):
    """Retrieve file content from a given Git revision using 'git show'."""
    try:
        output = subprocess.check_output(["git", "show", f"{revision}:{file_path}"],
                                           stderr=subprocess.STDOUT)
        return output.decode("utf-8")
    except subprocess.CalledProcessError as e:
        print(f"Error retrieving '{file_path}' from revision {revision}: {e.output.decode()}")
        return None

def annotate_content(content, comments, patchset_filter=None):
    """Insert inline comment annotations into file content and return the annotated string."""
    lines = content.splitlines()
    ann_dict = {}
    for comment in comments:
        ps = comment.get("patchSet", comment.get("patch_set", "Unknown"))
        if patchset_filter and str(ps) != str(patchset_filter):
            continue
        line_no = comment.get("line")
        if not line_no:
            continue
        reviewer = comment.get("reviewer", {}).get("name", "Unknown")
        msg = comment.get("message", "")
        ann = f"[Patchset {ps}] {reviewer}: {msg}"
        ann_dict.setdefault(line_no, []).append(ann)
    annotated = []
    for i, line in enumerate(lines, start=1):
        annotated.append(line)
        if i in ann_dict:
            for a in ann_dict[i]:
                annotated.append("  >>> " + a)
    return "\n".join(annotated) + "\n"

def diff_files(annotated_file, current_file, diff_tool="code"):
    """Launch the diff tool to compare the annotated file with the current file."""
    annotated_file = os.path.abspath(annotated_file)
    current_file = os.path.abspath(current_file)
    try:
        result = subprocess.run([diff_tool, "--diff", annotated_file, current_file])
        if result.returncode != 0:
            print(f"Diff tool returned code {result.returncode} for '{current_file}'.")
    except Exception as e:
        print(f"Failed to launch diff tool for '{current_file}': {e}")

def process_diffs(data, patchset_filter, file_filter, mode, diff_tool, temp_dir):
    """For each file with comments, retrieve content, annotate it, and diff against target state."""
    cached = {}
    if mode == "directory":
        target_dir = input("Enter the directory path to diff against: ").strip()
        if not os.path.isdir(target_dir):
            sys.exit("Error: Provided directory does not exist.")
        cached["mode"] = "directory"
    else:
        cached["mode"] = "git"
        if patchset_filter:
            rev = get_patchset_revision(data, patchset_filter)
            if not rev:
                print(f"Warning: No revision found for patchset {patchset_filter}; using local files.")
                cached["mode"] = "local"
            else:
                cached["rev"] = rev
        else:
            cached["mode"] = "local"
    comments = get_comments(data)
    if file_filter:
        comments = {f: cs for f, cs in comments.items() if file_filter in f}
    if not comments:
        print("No inline comments found.")
        return
    for file_path, comm_list in comments.items():
        if patchset_filter:
            filtered = [c for c in comm_list if str(c.get("patchSet", c.get("patch_set"))) == str(patchset_filter)]
            if not filtered:
                continue
        else:
            filtered = comm_list
        if cached.get("mode") == "git":
            content = get_git_file_from_revision(cached["rev"], file_path)
            if content is None:
                print(f"Skipping file '{file_path}' due to retrieval error.")
                continue
        elif cached.get("mode") == "directory":
            target_file = os.path.join(target_dir, file_path)
            if not os.path.exists(target_file):
                print(f"File '{file_path}' not found in directory '{target_dir}'.")
                continue
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                print(f"Error reading '{target_file}': {e}")
                continue
        else:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                print(f"File '{file_path}' not found locally: {e}")
                continue
        annotated = annotate_content(content, filtered, patchset_filter)
        temp_file = os.path.join(temp_dir, os.path.basename(file_path) + ".annotated")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(annotated)
        except Exception as e:
            print(f"Error writing temporary file for '{file_path}': {e}")
            continue
        diff_files(temp_file, file_path, diff_tool)

def main():
    parser = argparse.ArgumentParser(description="Gerrit Modular Diff Wrapper")
    parser.add_argument("--json-file", help="Path to Gerrit JSON dump", required=False)
    parser.add_argument("--load-comments", help="Path to saved comments file", required=False)
    parser.add_argument("--save-comments", help="Path to save grepped comments", required=False)
    parser.add_argument("--patchset", help="Filter by patchset number", type=str)
    parser.add_argument("--file", help="Filter by file name substring", type=str)
    parser.add_argument("--output-format", choices=["json", "markdown", "text"], nargs="+", default=["json"])
    parser.add_argument("--summary-file", help="File to save summary output", type=str)
    parser.add_argument("--diff-tool", default="code", help="Diff tool executable (default: code)")
    parser.add_argument("--mode", choices=["directory", "git"], help="Diff mode: compare against a directory or use git tree", required=False)
    parser.add_argument("--no-cleanup", action="store_true", help="Do not delete temporary files")
    args = parser.parse_args()
    data = None
    if args.load_comments:
        data = load_comments_from_file(args.load_comments)
    elif args.json_file:
        data = parse_gerrit_json_dump(args.json_file)
    else:
        sys.exit("Error: You must specify either --json-file or --load-comments.")
    if args.save_comments:
        save_comments_to_file(data, args.save_comments)
    summaries = {}
    for fmt in args.output_format:
        if fmt == "json":
            summaries["json"] = format_summary_json(data, args.patchset, args.file)
        elif fmt == "markdown":
            summaries["markdown"] = format_summary_markdown(data, args.patchset, args.file)
        elif fmt == "text":
            summaries["text"] = format_summary_text(data, args.patchset, args.file)
    for fmt, summ in summaries.items():
        print(f"\n--- {fmt.upper()} SUMMARY ---\n{summ}\n")
    if args.summary_file:
        try:
            with open(args.summary_file, "w", encoding="utf-8") as f:
                for fmt, summ in summaries.items():
                    f.write(f"--- {fmt.upper()} SUMMARY ---\n\n{summ}\n\n")
            print(f"Summary saved to {args.summary_file}")
        except Exception as e:
            print(f"Error writing summary file: {e}")
    if not args.mode:
        choice = input("Diff mode: [1] Directory, [2] Git tree regeneration. Enter 1 or 2: ").strip()
        if choice == "1":
            mode = "directory"
        else:
            mode = "git"
    else:
        mode = args.mode
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Using temporary directory: {temp_dir}")
        process_diffs(data, args.patchset, args.file, mode, args.diff_tool, temp_dir)
        if args.no_cleanup:
            preserve = os.path.join(os.getcwd(), "gerrit_temp_preserved")
            shutil.copytree(temp_dir, preserve)
            print(f"Temporary files preserved at: {preserve}")
        else:
            input("Press Enter to finish and clean up temporary files...")

if __name__ == "__main__":
    main()
