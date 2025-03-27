#!/usr/bin/env python3
"""
Gerrit Diff Wrapper

This script processes a Gerrit JSON dump (or saved comments file) and visualizes the
inline comments for a chosen patchset. It groups comments by file and, for each file,
it produces an annotated copy (by appending comment text to the end of the affected line,
thus not altering the line count) and launches VS Code’s diff view comparing that annotated
file with the current working file. The script can operate in two modes:
  • Clone mode: it copies your current working directory to a temporary location and uses that.
  • Git mode: it regenerates file content from Git using the patchset’s revision.
You may also save the downloaded comments to a file (and later load them), and no files
are written in your working directory unless you explicitly request an output.

Usage:
  $ ./gerrit_diff_wrapper.py --json-file dump.json [options]

Options:
  --json-file PATH         Path to a Gerrit JSON dump.
  --load-comments PATH     Path to a saved comments JSON file.
  --save-comments PATH     Save the processed comments to this file.
  --patchset NUM           Only use the specified patchset (if not provided, you’ll be prompted).
  --file FILTER            Only process files whose names contain this substring.
  --output-format FMT      Output summary format(s): json, markdown, text (default: json).
  --summary-file PATH      Save the summary output to this file.
  --vscode                 Use VS Code diff view (invokes "code --diff").
  --mode MODE              "clone" to copy your working directory or "git" to use Git to regenerate file content.
  --no-cleanup             Do not delete temporary directories (for debugging).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import shutil

def load_file(path):
    if not os.path.exists(path):
        sys.exit(f"Error: File '{path}' not found.")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        sys.exit(f"Error reading '{path}': {e}")

def parse_json_dump(path):
    content = load_file(path)
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
        sys.exit("Error: No valid change object found in JSON dump.")
    if len(valid) > 1:
        print("Warning: Multiple change objects found; using the first one.")
    return valid[0]

def load_comments_json(path):
    content = load_file(path)
    try:
        return json.loads(content)
    except Exception as e:
        sys.exit(f"Error parsing JSON from '{path}': {e}")

def get_key(data, key, default=None):
    if key in data:
        return data[key]
    lk = key.lower()
    for k, v in data.items():
        if k.lower() == lk:
            return v
    return default

def get_patchsets(data):
    ps = get_key(data, "patchSets") or get_key(data, "patch_sets", [])
    return ps if isinstance(ps, list) else []

def get_comments(data):
    cm = get_key(data, "comments")
    if isinstance(cm, list):
        return cm
    return []

def get_messages(data):
    ms = get_key(data, "messages", [])
    return ms if isinstance(ms, list) else []

def get_patchset_revision(data, ps_num):
    for ps in get_patchsets(data):
        if str(ps.get("number")) == str(ps_num):
            return ps.get("revision")
    return None

def save_comments(data, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Comments saved to '{path}'.")
    except Exception as e:
        sys.exit(f"Error saving comments to '{path}': {e}")

def prompt_patchset(data, chosen):
    ps_list = get_patchsets(data)
    if not ps_list:
        sys.exit("Error: No patchsets found in the JSON.")
    if chosen:
        for ps in ps_list:
            if str(ps.get("number")) == str(chosen):
                return str(ps.get("number"))
        sys.exit(f"Error: Patchset {chosen} not found.")
    print("Available patchsets:")
    for ps in ps_list:
        print(f"  Patchset {ps.get('number')} (revision: {str(ps.get('revision'))[:7]})")
    sel = input("Enter patchset number: ").strip()
    if not sel:
        sys.exit("No patchset selected.")
    return sel

def group_comments_by_file(comments, ps_filter=None):
    files = {}
    for c in comments:
        ps = c.get("patchset") or c.get("patchSet") or None
        if ps_filter and ps is not None and str(ps) != str(ps_filter):
            continue
        f = c.get("file")
        if not f:
            continue
        files.setdefault(f, []).append(c)
    return files

def clone_working_directory():
    cwd = os.getcwd()
    temp_dir = tempfile.mkdtemp(prefix="gerrit_clone_")
    try:
        shutil.copytree(cwd, os.path.join(temp_dir, "clone"), symlinks=True, dirs_exist_ok=True)
        return os.path.join(temp_dir, "clone"), temp_dir
    except Exception as e:
        sys.exit(f"Error cloning working directory: {e}")

def annotate_lines(content, comments):
    lines = content.splitlines()
    line_map = {}
    for c in comments:
        ln = c.get("line")
        if not ln:
            continue
        reviewer = c.get("reviewer", {}).get("name", "Unknown")
        msg = c.get("message", "")
        text = f" // [Gerrit] {reviewer}: {msg}"
        line_map.setdefault(ln, []).append(text)
    new_lines = []
    for i, line in enumerate(lines, start=1):
        if i in line_map:
            # Append all comment texts without adding new lines
            new_line = line + "".join(line_map[i])
            new_lines.append(new_line)
        else:
            new_lines.append(line)
    return "\n".join(new_lines) + "\n"

def get_file_from_git(rev, file_path):
    try:
        out = subprocess.check_output(["git", "show", f"{rev}:{file_path}"],
                                      stderr=subprocess.STDOUT)
        return out.decode("utf-8")
    except subprocess.CalledProcessError as e:
        print(f"Error: retrieving '{file_path}' from revision {rev}: {e.output.decode()}")
        return None

def diff_in_vscode(annotated, original, diff_tool="code"):
    annotated = os.path.abspath(annotated)
    original = os.path.abspath(original)
    try:
        subprocess.run([diff_tool, "--diff", annotated, original])
    except Exception as e:
        print(f"Error launching diff: {e}")

def process_files(data, ps_filter, file_filter, mode, diff_tool, clone_dir=None, temp_dir=None):
    all_comments = get_comments(data)
    files_comments = group_comments_by_file(all_comments, ps_filter)
    if file_filter:
        files_comments = {f: cs for f, cs in files_comments.items() if file_filter in f}
    if not files_comments:
        print("No inline comments found for the selected patchset/file filter.")
        return
    if mode == "git":
        rev = get_patchset_revision(data, ps_filter)
        if not rev:
            print(f"Warning: No revision found for patchset {ps_filter}; skipping git mode.")
            mode = "local"
    for f, comms in files_comments.items():
        if mode == "git":
            content = get_file_from_git(rev, f)
            if content is None:
                print(f"Skipping file '{f}' (unable to retrieve from git).")
                continue
        elif mode == "clone":
            clone_file = os.path.join(clone_dir, f)
            if not os.path.exists(clone_file):
                print(f"File '{f}' not found in cloned directory.")
                continue
            try:
                with open(clone_file, "r", encoding="utf-8") as fp:
                    content = fp.read()
            except Exception as e:
                print(f"Error reading cloned file '{f}': {e}")
                continue
        else:
            if not os.path.exists(f):
                print(f"File '{f}' not found locally.")
                continue
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    content = fp.read()
            except Exception as e:
                print(f"Error reading file '{f}': {e}")
                continue
        annotated_content = annotate_lines(content, comms)
        annotated_temp = os.path.join(temp_dir, os.path.basename(f) + ".annotated")
        try:
            with open(annotated_temp, "w", encoding="utf-8") as fp:
                fp.write(annotated_content)
        except Exception as e:
            print(f"Error writing annotated file for '{f}': {e}")
            continue
        # Launch VS Code diff between the annotated version and the current working file.
        if os.path.exists(f):
            diff_in_vscode(annotated_temp, f, diff_tool)
        else:
            print(f"Original file '{f}' not found for diffing.")

def main():
    parser = argparse.ArgumentParser(description="Gerrit Diff Wrapper (Modular, Connectionless)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json-file", help="Path to Gerrit JSON dump")
    group.add_argument("--load-comments", help="Path to saved comments file")
    parser.add_argument("--save-comments", help="Save downloaded comments to file")
    parser.add_argument("--patchset", help="Patchset number to select", type=str)
    parser.add_argument("--file", help="Filter by file name substring", type=str)
    parser.add_argument("--output-format", choices=["json", "markdown", "text"], nargs="+", default=["json"])
    parser.add_argument("--summary-file", help="File to save summary output")
    parser.add_argument("--vscode", action="store_true", help="Launch diffs in VS Code (using 'code --diff')")
    parser.add_argument("--mode", choices=["clone", "git", "local"], help="Diff mode: 'clone' to clone working dir, 'git' to use git revision, 'local' to use current files", required=False)
    parser.add_argument("--diff-tool", default="code", help="Diff tool executable (default: code)")
    parser.add_argument("--no-cleanup", action="store_true", help="Do not delete temporary directories")
    args = parser.parse_args()

    if args.load_comments:
        data = load_comments_json(args.load_comments)
    else:
        data = parse_json_dump(args.json_file)
    if args.save_comments:
        save_comments(data, args.save_comments)
    # Output summaries
    summaries = {}
    for fmt in args.output_format:
        if fmt == "json":
            summaries["json"] = json.dumps(data, indent=2)
        elif fmt == "markdown":
            # Minimal markdown summary: patchset and number of comments.
            summaries["markdown"] = f"# Gerrit Change Summary\nPatchsets: {get_patchsets(data)}\nComments count: {len(get_comments(data))}"
        elif fmt == "text":
            summaries["text"] = f"Gerrit Change Summary\nPatchsets: {get_patchsets(data)}\nComments count: {len(get_comments(data))}"
    for fmt, summ in summaries.items():
        print(f"\n--- {fmt.upper()} SUMMARY ---\n{summ}\n")
    if args.summary_file:
        try:
            with open(args.summary_file, "w", encoding="utf-8") as f:
                for fmt, summ in summaries.items():
                    f.write(f"--- {fmt.upper()} SUMMARY ---\n{summ}\n\n")
            print(f"Summary saved to '{args.summary_file}'.")
        except Exception as e:
            print(f"Error writing summary file: {e}")
    selected_ps = args.patchset if args.patchset else prompt_patchset(data, None)
    mode = args.mode
    if not mode:
        choice = input("Choose diff mode: [1] Clone working dir, [2] Git revision, [3] Local current file. Enter 1, 2, or 3: ").strip()
        if choice == "1":
            mode = "clone"
        elif choice == "2":
            mode = "git"
        else:
            mode = "local"
    clone_dir = None
    temp_root = tempfile.mkdtemp(prefix="gerrit_diff_")
    if mode == "clone":
        print("Cloning current working directory...")
        clone_dir, clone_temp = clone_working_directory()
        print(f"Working directory cloned to '{clone_dir}'.")
    try:
        process_files(data, selected_ps, args.file, mode, args.diff_tool, clone_dir, temp_root)
    except Exception as e:
        print(f"Error processing diffs: {e}")
    if args.no_cleanup:
        preserved = os.path.join(os.getcwd(), "gerrit_temp_preserved")
        shutil.copytree(temp_root, preserved)
        print(f"Temporary files preserved at '{preserved}'.")
    else:
        input("Press Enter to clean up temporary files and exit...")
        shutil.rmtree(temp_root, ignore_errors=True)
        if mode == "clone" and clone_dir:
            shutil.rmtree(os.path.dirname(clone_dir), ignore_errors=True)

if __name__ == "__main__":
    main()
