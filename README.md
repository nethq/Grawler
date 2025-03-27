./script.py --json-file dump.json --patchset 3 --indent "    " --comment-syntax "//" --comment-fields "patchset,reviewer,message" --order oldest --diff-tool code

Usage Overview

• Provide a JSON dump with --json-file dump.json or a saved comments file via --load-comments filename.json.

• Specify (or be prompted for) the patchset number whose version you want to virtualize.

• All operations occur in temporary folders; nothing is written to your working directory unless you use options to save outputs.

• The script clones your working directory (if mode is “clone”) and checks out the patchset’s revision. Then, for every file with inline comments, it creates an annotated copy that appends comment lines (using the indent, comment syntax, and fields you specify) to the affected lines. The order of comment display (oldest first or latest first) is controlled with --order.

• Finally, it launches VS Code’s diff view (using “code --diff”) to compare the annotated file against the current working file. You can navigate between diffs.

• Temporary directories are cleaned up unless you specify --no-cleanup.
