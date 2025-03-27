How to Use This Script

1. Providing a Comments File:
Pass your downloaded comments JSON with the argument
--load-comments downloaded_comments.json
(Alternatively, use --json-file if you have the full Gerrit dump.)


2. Selecting a Patchset:
If you do not provide --patchset, the script lists available patchsets and prompts you to enter one.


3. Cloning vs. Git Regeneration:
At runtime you’ll be asked whether to clone your working directory (which creates a temporary copy to annotate) or use Git (retrieving file content from the patchset’s revision) or just use the current file.


4. Annotation & Diff:
For every file that has inline comments (each comment must include “file”, “line”, “reviewer” with at least “name”, and “message”), the script edits the file content in the temporary clone (or in the generated content) by appending the comment text (preceded by a marker) to the line. It then launches VS Code’s diff view (using “code --diff”) comparing the annotated file (left) with your original file (right).
The script does not alter any files in your working directory.


5. Saving/Loading Comments and Summaries:
Use --save-comments to write the downloaded comments to a file. Use --summary-file to save a summary for further parsing.


6. Cleanup:
By default, the temporary directories are removed once you press Enter. Use --no-cleanup to preserve them.
