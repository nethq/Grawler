#!/usr/bin/env python3
"""
Gerrit Diff Wrapper

This script loads a Gerrit JSON dump (or saved comments file), prompts for a patchset,
clones your working directory into a temporary folder and checks out the patchset’s revision,
then for every file that has inline comments it annotates the file (by appending comment text
to the affected lines, preserving line numbers) according to user-specified settings and
launches VS Code’s diff view comparing the annotated file (patchset version) against the current file.
All changes occur in temporary directories; nothing is written to your working directory unless
you choose to save outputs.
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
    elif isinstance(cm, dict):
        out = []
        for v in cm.values():
            if isinstance(v, list):
                out.extend(v)
        return out
    return []

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
        rev = str(ps.get("revision")) if ps.get("revision") else "N/A"
        print(f"  {ps.get('number')} (revision: {rev[:7] if rev != 'N/A' else rev})")
    sel = input("Select patchset number to virtualize: ").strip()
    if not sel:
        sys.exit("No patchset selected.")
    return sel

def group_comments_by_file(comments, ps_filter=None):
    grouped = {}
    for c in comments:
        ps = c.get("patchSet") or c.get("patch_set")
        if ps_filter and ps and str(ps) != str(ps_filter):
            continue
        fname = c.get("file")
        if not fname:
            continue
        grouped.setdefault(fname, []).append(c)
    return grouped

def clone_working_directory(rev):
    cwd = os.getcwd()
    temp_dir = tempfile.mkdtemp(prefix="gerrit_clone_")
    clone_path = os.path.join(temp_dir, "clone")
    try:
        shutil.copytree(cwd, clone_path, symlinks=True, dirs_exist_ok=True)
        subprocess.check_call(["git", "-C", clone_path, "checkout", rev])
        return clone_path, temp_dir
    except Exception as e:
        sys.exit(f"Error cloning working directory and checking out revision {rev}: {e}")

def annotate_content(content, comments, indent, syntax, fields, order):
    lines = content.splitlines()
    ann_map = {}
    def build_ann(c):
        parts = []
        for field in fields:
            if field.lower() == "patchset":
                parts.append(f"Patchset {c.get('patchSet') or c.get('patch_set','')}")
            elif field.lower() == "reviewer":
                reviewer = c.get("reviewer", {})
                parts.append(reviewer.get("name", "Unknown"))
            elif field.lower() == "message":
                parts.append(c.get("message", ""))
            elif field.lower() == "timestamp":
                parts.append(str(c.get("timestamp", "")))
            else:
                parts.append(str(c.get(field, "")))
        return f"{syntax} " + ", ".join(parts)
    sorted_comments = sorted(comments, key=lambda c: int(c.get("patchSet") or 0))
    if order == "latest":
        sorted_comments = list(reversed(sorted_comments))
    for c in sorted_comments:
        ln = c.get("line")
        if ln is None:
            continue
        ann = build_ann(c)
        ann_map.setdefault(ln, []).append(ann)
    new_lines = []
    for i, line in enumerate(lines, start=1):
        new_lines.append(line)
        if i in ann_map:
            for a in ann_map[i]:
                new_lines.append(indent + a)
    return "\n".join(new_lines) + "\n"

def diff_in_vscode(annotated, current, diff_tool):
    annotated = os.path.abspath(annotated)
    current = os.path.abspath(current)
    try:
        subprocess.run([diff_tool, "--diff", annotated, current])
    except Exception as e:
        print(f"Error launching diff for '{current}': {e}")

def process_files(data, ps_filter, file_filter, mode, diff_tool, indent, syntax, fields, order, temp_dir, clone_dir):
    rev = get_patchset_revision(data, ps_filter) if mode in ["git", "clone"] else None
    grouped = group_comments_by_file(get_comments(data), ps_filter)
    if file_filter:
        grouped = {f: cs for f, cs in grouped.items() if file_filter in f}
    if not grouped:
        print("No inline comments found for the selected patchset or file filter.")
        return
    for f, comms in grouped.items():
        if mode == "git":
            content = get_file_from_git(rev, f)
            if content is None:
                print(f"Skipping file '{f}' (unable to retrieve from git).")
                continue
        elif mode == "clone":
            file_path = os.path.join(clone_dir, f)
            if not os.path.exists(file_path):
                print(f"File '{f}' not found in cloned directory.")
                continue
            try:
                with open(file_path, "r", encoding="utf-8") as fp:
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
        annotated = annotate_content(content, comms, indent, syntax, fields, order)
        annotated_file = os.path.join(temp_dir, os.path.basename(f) + ".annotated")
        try:
            with open(annotated_file, "w", encoding="utf-8") as fp:
                fp.write(annotated)
        except Exception as e:
            print(f"Error writing annotated file for '{f}': {e}")
            continue
        if os.path.exists(f):
            diff_in_vscode(annotated_file, f, diff_tool)
        else:
            print(f"Original file '{f}' not found for diffing.")

def main():
    parser = argparse.ArgumentParser(description="Gerrit Diff Wrapper (Virtualize Patchset with Inline Comments)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json-file", help="Path to Gerrit JSON dump")
    group.add_argument("--load-comments", help="Path to saved comments file")
    parser.add_argument("--save-comments", help="Save downloaded comments to file")
    parser.add_argument("--patchset", help="Patchset number to virtualize", type=str)
    parser.add_argument("--file", help="Filter by file name substring", type=str)
    parser.add_argument("--indent", default="    ", help="Indentation for appended comments (default: 4 spaces)")
    parser.add_argument("--comment-syntax", default="//", help="Comment syntax to prefix annotation (default: //)")
    parser.add_argument("--comment-fields", default="patchset,reviewer,message", help="Comma-separated list of fields to show in annotations (default: patchset,reviewer,message)")
    parser.add_argument("--order", choices=["oldest", "latest"], default="oldest", help="Order of comment display (default: oldest first)")
    parser.add_argument("--diff-tool", default="code", help="Diff tool executable (default: code for VS Code)")
    parser.add_argument("--mode", choices=["clone", "git", "local"], help="Diff mode: clone (clone working dir and checkout patchset), git (retrieve files from git), local (use current files)", required=True)
    parser.add_argument("--summary-file", help="File to save summary output")
    parser.add_argument("--output-format", choices=["json", "markdown", "text"], nargs="+", default=["json"])
    parser.add_argument("--no-cleanup", action="store_true", help="Do not delete temporary directories")
    args = parser.parse_args()

    if args.load_comments:
        data = load_comments_json(args.load_comments)
    else:
        data = parse_json_dump(args.json_file)
    if args.save_comments:
        save_comments(data, args.save_comments)
    summaries = {}
    for fmt in args.output_format:
        if fmt == "json":
            summaries["json"] = json.dumps(data, indent=2)
        elif fmt == "markdown":
            summaries["markdown"] = f"Patchsets: {get_patchsets(data)}\nTotal comments: {len(get_comments(data))}"
        elif fmt == "text":
            summaries["text"] = f"Patchsets: {get_patchsets(data)}\nTotal comments: {len(get_comments(data))}"
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
    fields = [x.strip() for x in args.comment_fields.split(",")]
    temp_root = tempfile.mkdtemp(prefix="gerrit_diff_")
    clone_dir = None
    if args.mode == "clone":
        rev = get_patchset_revision(data, selected_ps)
        if not rev:
            sys.exit(f"Error: Unable to retrieve revision for patchset {selected_ps}.")
        print(f"Cloning working directory and checking out revision {rev}...")
        clone_dir, clone_temp = clone_working_directory(rev)
    try:
        process_files(data, selected_ps, args.file, args.mode, args.diff_tool, args.indent, args.comment_syntax, fields, args.order, temp_root, clone_dir)
    except Exception as e:
        print(f"Error processing diffs: {e}")
    if args.no_cleanup:
        preserved = os.path.join(os.getcwd(), "gerrit_temp_preserved")
        shutil.copytree(temp_root, preserved)
        print(f"Temporary files preserved at '{preserved}'.")
    else:
        input("Press Enter to clean up temporary files and exit...")
        shutil.rmtree(temp_root, ignore_errors=True)
        if args.mode == "clone" and clone_dir:
            shutil.rmtree(os.path.dirname(clone_dir), ignore_errors=True)

if __name__ == "__main__":
    main()
