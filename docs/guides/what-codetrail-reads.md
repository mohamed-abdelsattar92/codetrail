# What Codetrail reads

Codetrail runs an assistant over your repository, and repositories hold secrets. So it reads only what is committed on the branch, and hides every file that could hold a secret before any assistant sees a thing. This page shows what is read, what is hidden, and how to hide more.

**Before you start:** add a repository with `codetrail target add` (see [Your first guide](first-guide.md)).

## See what Codetrail can read

1. List the visible files and the hidden ones:

   ```bash
   codetrail files shop
   ```

   Replace `shop` with your target's name. The command refreshes Codetrail's private copy of the branch, scans it, and prints:

   ```
   app/charges.py
   app/main.py
   infra/main.tf
   pyproject.toml
   Excluded:
   4 visible, 0 excluded (none), commit 9e6a7cdf1390
   ```

2. Read the three parts:
   - Above `Excluded:` is every file the assistant can read.
   - Below it is every hidden file, one per line, as the reason, then the path. A file gitleaks flagged carries the gitleaks rule in brackets, for example `gitleaks (generic-api-key)`, and an `unscannable` file carries why it couldn't be read.
   - The last line counts them, groups the hidden files by reason, and names the commit it read.

3. Run it again after any change to your exclusions. It is free: no assistant is called.

Codetrail reads only what is committed on the branch you added. A file git ignores and never committed doesn't exist for it.

## What is always hidden

Some files are hidden whatever any setting says. The reason shown by `codetrail files` is in bold.

| Reason | What it hides |
|---|---|
| **secret pattern** | `.env` and `.env.*`, `.dev.vars`, `*.p8`, `*.p12`, `*.pem`, `*.keystore`, `*.jks`, `*.tfstate`, `*.tfstate.backup`, `*.tfvars`, `*.tfvars.json`, `*.key`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `*.ppk`, `.netrc`, `.npmrc`, `.pypirc`, and the `.terraform/` folder. Names match in any case. A template named `*.example` (such as `.env.example`) is not hidden. |
| **scanner settings** | `.gitleaksignore` and `.gitleaks.toml`, which gitleaks would obey. Codetrail scans with its own gitleaks settings, so these files can't hide a secret from it. |
| **gitleaks** | Any file [gitleaks](https://github.com/gitleaks/gitleaks) flags as holding a secret. Codetrail scans every file before anything reads it. |
| **symlink or submodule** | Every symlink and every submodule. |
| **unsafe path** | A path or a file name longer than a disk can store; a path with a `.git` or `..` part, a control character or bytes that aren't UTF-8; and paths that differ only in case, which would land on the same file on a Mac. |
| **too large** | A file larger than `max_file_bytes` (50 MB by default). |
| **unscannable** | A file that keeps text compressed (a zip, tar, gzip, bzip2 or xz file, or a PDF, so also a `.docx` or a `.jar`) that gitleaks couldn't read whole. The reason in brackets says why: it decodes to more than `max_inflated_bytes` (50 MB by default), or it is encrypted, damaged or encoded in a way Codetrail doesn't read. |

gitleaks also reads the text that those compressed files keep, so a secret inside an archive hides the whole archive.

A file that was committed anyway, though `.gitignore` lists it, is visible unless one of these catches it. List it in your own ignore rules too if it shouldn't be read.

PDFs and images are special. The assistant never opens a PDF or an image, since text drawn in them escapes the scan. The guide may list them, but it doesn't show what is in them.

## Hide more files

You have two places for your own rules. Both use gitignore syntax and both match in any case.

1. Your own file, which only you see. `codetrail target add` printed its path:

   ```
   ~/.config/codetrail/targets/shop.ignore
   ```

   Open it and add lines:

   ```gitignore
   # never read the fixtures or anything under legacy/
   tests/fixtures/
   legacy/**
   ```

2. Or, to share the rules with everyone who reads the repository, commit a `.codetrailignore` file at the root of the repository. This one is part of the repository, so Codetrail reads it from the branch it teaches. Your own file's lines apply after it.

3. Check the result:

   ```bash
   codetrail files shop
   ```

   The files you hid now appear below `Excluded:` with the reason `ignore rules`.

Your rules can only hide more. They can't bring back a file the built-in rules, gitleaks or the size limits hide.

## Settings

These go under `[source]` and `[tools]` in `~/.config/codetrail/config.toml`.

| Setting | Default | What it does |
|---|---|---|
| `[source] max_file_bytes` | `50000000` | A committed file larger than this is hidden as `too large`. |
| `[source] max_inflated_bytes` | `50000000` | The most text gitleaks reads from one compressed file. A file that decodes to more is hidden as `unscannable`. |
| `[source] max_ignore_lines` | `200` | The most lines with a pattern Codetrail reads in a repository's own `.codetrailignore`. |
| `[source] max_ignore_bytes` | `65536` | The largest size of a repository's own `.codetrailignore`. |
| `[source] max_ignore_match_seconds` | `60` | The time allowed to match paths against the ignore rules when the repository has its own `.codetrailignore`. |
| `[tools] gitleaks` | `gitleaks` | The gitleaks program: a name on your `PATH`, or an absolute path. |
| `[tools] gitleaks_timeout_seconds` | `600` | How long each gitleaks run may take before the refresh fails. |

The limits on `.codetrailignore` exist because every line is matched against every path. They apply to the repository's own file, not to yours.

## Troubleshooting

| You see | What it means and what to do |
|---|---|
| `gitleaks wasn't found` | Codetrail won't read a repository without gitleaks. Install it and put it on your `PATH`, or set `[tools] gitleaks`. |
| `gitleaks didn't finish within … seconds ([tools] gitleaks_timeout_seconds)` | The scan was stopped before it finished. For a large repository, raise `gitleaks_timeout_seconds`. |
| `gitleaks failed (exit code …)` | The message ends with what gitleaks printed. To run a different gitleaks, set `[tools] gitleaks` to its absolute path. |
| `The target's .codetrailignore has … lines with a pattern` | The repository's own ignore file has more lines than Codetrail reads. Ask for it to be shortened, or raise `max_ignore_lines`. |
| `The target's .codetrailignore is too slow to match` | Matching your paths against that file took longer than `max_ignore_match_seconds`. Ask for its `*` and `**` lines to be simplified, or raise the setting. |
| `too large` beside a file | The file is larger than `max_file_bytes`. Raise the setting if the assistant should see it. |
| `unscannable (…)` beside a file | gitleaks couldn't read the file's compressed text whole. Read the reason in brackets; raise `max_inflated_bytes` for a large archive. |

[Troubleshooting](troubleshooting.md) lists the other messages about gitleaks and mise.

## See also

- [Your first guide](first-guide.md)
- [Keeping the guide up to date](updating.md)
- [Settings](settings.md)
- [Troubleshooting](troubleshooting.md)
