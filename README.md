# Grawler
Gerrit automation scriptlet

How to Use

1. Configuration:
Create a JSON config file (e.g., gerrit_config.json) with contents such as:

{
  "gerrit": {
    "base_url": "https://gerrit.example.com",
    "username": "yourusername",
    "password": "yourHTTPpassword_or_token",
    "method": "rest"
  }
}

Alternatively, if you do not provide a config file, the script will prompt you for the required values.


2. Running the Script:
In a local Git repository that was pushed to Gerrit, simply run:

./gerrit_comments.py --config gerrit_config.json --output-format markdown text --vscode

This will use the HEAD commit, extract the Change-Id, query Gerrit for matching changes and patchsets, and then output the comments in Markdown and plain text. The --vscode flag will open the generated file(s) in VS Code.


3. CLI Options:
You can override the commit hash (--commit), specify a patchset (--patchset), and choose different output formats (--output-format json, etc.).
