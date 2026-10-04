# Publish your own copy on GitHub

This guide explains how to publish your own copy of Daydesk, starting from a downloaded ZIP or a local source folder. If you are viewing an existing GitHub repository, you can also use its **Fork** button to create a copy in your account.

Use a **private repository** to start. Keep personal records, backups, exports, API keys, and account passwords outside it. Daydesk stores its records in `~/.daydesk` by default, separate from this project.

## One-time setup

Install [Git](https://git-scm.com/downloads) and optionally the [GitHub CLI](https://cli.github.com/). Open a terminal in the project folder containing `README.md` and `daydesk/`.

For a downloaded ZIP that has no Git history, create the first commit:

```bash
git init -b main
git add .
git status
git commit -m 'Initial Daydesk daily assistant'
```

Read the `git status` output before committing. It should contain application code, documentation, example configuration, and tests. It should not contain your real records or credentials.

If Git asks for your identity, configure your own name and an email address you want associated with your commits. GitHub provides a private noreply address in your account's email settings if you prefer it.

## Publish with the GitHub CLI

After installing `gh`, sign in and create a new private repository for your copy:

```bash
gh auth login
gh repo create daydesk --private --source=. --remote=origin --push
```

Follow the sign-in prompts on your own device. If you already own a repository called `daydesk`, choose a different name in the second command.

The command creates a repository, sets its `origin` remote, and pushes your local commit. You can then open it with:

```bash
gh repo view --web
```

## Publish without the GitHub CLI

1. Sign in at [github.com](https://github.com/) and create a new private repository.
2. Give it a name such as `daydesk`. Leave the README, license, and `.gitignore` initialization options empty because those files already exist locally.
3. Copy the repository's URL from GitHub.
4. Replace the example URL below with your own, then run:

```bash
git remote add origin https://github.com/YOUR-USERNAME/daydesk.git
git push -u origin main
```

GitHub may prompt you to authenticate through a credential manager or other supported method. Do not put a password or token inside a Git remote URL or source file.

## Save future code changes

If you used GitHub's Fork button, clone your fork and work from that folder. Replace the example URL with your fork's URL:

```bash
git clone https://github.com/YOUR-USERNAME/daydesk.git
cd daydesk
```

After making and checking a change:

```bash
python3 -m unittest discover -s tests -v
git status
git add .
git commit -m 'Describe the change'
git push
```

Only commit intended code and documentation changes. The supplied workflow runs the test suite on GitHub after pushes and pull requests; it does not operate your assistant or store your daily records.

## Official references

- [GitHub CLI: `gh repo create`](https://cli.github.com/manual/gh_repo_create)
- [GitHub: add locally hosted code to GitHub](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)
