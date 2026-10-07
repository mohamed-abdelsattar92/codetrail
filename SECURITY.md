# Security policy

Codetrail runs an assistant over repositories that hold secrets, and serves a local page that can drive it, so security problems matter here. Thank you for reporting them privately.

## Supported versions

Security fixes go into the latest release. Codetrail has had one release so far:

| Version | Supported |
|---|---|
| 1.0.x | Yes |

## Reporting a vulnerability

Report it through GitHub's private vulnerability reporting: open [a new security advisory](https://github.com/mohamed-abdelsattar92/codetrail/security/advisories/new) (the repository's **Security** tab, then **Report a vulnerability**). Only the maintainer sees it. Please don't open a public issue, pull request or discussion about it.

Include what you can:
- what an attacker can do, and what they need first (for example: a web page the reader visits, a file in a repository the reader adds, or a local process);
- the steps to reproduce it, with the Codetrail version (`codetrail --version`), your operating system, and the assistant provider in use;
- a proof of concept, if you have one. Please use a test repository, never someone else's data or a real secret.

Codetrail is maintained by one person. I aim to acknowledge a report within seven days, agree on a fix and a disclosure date with you, and credit you in the advisory unless you'd rather not be named.

## What counts

The threat model is in [design section 7.4](docs/design/2026-10-05-codetrail-design.md#74-security-model). In short, a report is in scope when content Codetrail doesn't trust can break one of its guarantees:

- another website reading the page or driving it, for example starting paid work or asking a question, or a server on another `127.0.0.1` port reading the page or going beyond the limits below;
- a repository's content (files, commit messages, names) running script in the page, escaping the assistant's read-only tools, writing to the repository, or reaching a file Codetrail excluded;
- a secret from an excluded file reaching an extractor, the assistant, the guide or the page;
- Codetrail reading, storing or passing a key or token outside what the documentation says (`auth = "api_key"` hands the provider its own key variable, by name);
- paid work starting without the estimate being confirmed, or spending past the update's budget.

These are known and documented, so they aren't vulnerabilities on their own:

- **Codex, for a target that opts in to it,** can read anything the reader can, including the files Codetrail hides, because Codetrail can't confine it ([design section 15.5](docs/design/2026-10-05-codetrail-design.md#155-security)).
- **A local server on another port that you visit** receives the session cookie, because browsers send a cookie to every port of a host, so it can start paid work: an update within its budget and cooldown, one question at a time, and grading within its own budget ([design section 7.4](docs/design/2026-10-05-codetrail-design.md#74-security-model)).
- **Other processes running as you** are inside the trust boundary: they can read Codetrail's files directly.
- **Claude Code's answers** stream to the page before the final secret scan, because its reads are confined to the filtered sources.
