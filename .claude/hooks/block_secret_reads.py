#!/usr/bin/env python3
"""Claude Code PreToolUse hook: agents never read or print secrets (AGENTS.md, Never do, rule 2).

The permission rules in .claude/settings.json keep the Read tool away from secret files, but not Bash, so this hook
refuses Bash commands that name one:
- env files other than examples (.env, .env.local, .dev.vars), private keys and certificates (.p8, .p12, .pem,
  .keystore, .jks, .key, .pfx, .ppk, id_rsa, id_ed25519), Terraform state (.tfstate) and variables (.tfvars);
- credential stores in the home folder: ~/.ssh, gh's configuration, gcloud, wrangler, .netrc, .npmrc, .git-credentials,
  Terraform's credentials, Docker and AWS, named with or without a trailing slash;
- the Keychain (security find-*-password, dump-keychain, export) and `gcloud secrets versions access`.
A glob naming one of these (.dev.vars*) counts, and so does a shell's -c command (bash -lc) behind wrappers such as env.
Commands that only look at a file's existence or metadata (ls, test, stat, git check-ignore) are allowed.
It guards against mistakes: a command built to hide a name, such as a quoted heredoc fed to a shell, gets past it.
Exit code 2 blocks the call and shows the reason to the agent.
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from block_push import SHELLS, WRAPPERS, segments, strip_heredocs, subshells  # noqa: E402

SECRET_NAME = re.compile(
    r'^(\.env|\.env\.(?!example).+|\.dev\.vars|.+\.(p8|p12|pem|keystore|jks|key|pfx|ppk)|id_rsa.*|id_ed25519.*|.+\.tfstate(\.backup)?|.+\.tfvars(\.json)?)$', re.I)
SECRET_PATH = re.compile(
    r'(^|/)\.ssh(/|$)|\.config/gh(/|$)|\.config/gcloud(/|$)|\.wrangler/config|Preferences/\.wrangler'
    r'|(^|/)\.netrc$|(^|/)\.npmrc$|(^|/)\.git-credentials$|\.terraform\.d/credentials|\.docker/config\.json'
    r'|\.aws(/|$)|(^|/)\.pypirc$')
METADATA_ONLY = {'ls', 'test', '[', 'stat'}
KEYCHAIN = {'find-generic-password', 'find-internet-password', 'dump-keychain', 'export', 'export-item'}


def secret_in(token):
    # A token may carry the path after an option or a redirection: --env-file=.env, <.dev.vars, 2>~/.netrc.
    path = re.sub(r'^\d*[<>]+&?', '', token.split('=', 1)[-1]).strip('\'"')
    if re.search(r'\s', path): return None  # prose, such as a commit message; no secret path here has spaces
    if SECRET_PATH.search(path): return path
    name = os.path.basename(path)
    if SECRET_NAME.match(name) or SECRET_NAME.match(re.split(r'[*?\[]', name)[0]): return path
    return None


def check(cmd, depth=0):
    if depth > 4: return 'a command nested too deeply to read'
    try:
        cmd, bodies = strip_heredocs(cmd)
        segs = list(segments(cmd))
    except ValueError:
        return None  # block_push.py refuses commands it can't read
    for inner in subshells(cmd) + [s for body in bodies for s in subshells(body)]:
        r = check(inner, depth + 1)
        if r: return r
    for seg in segs:
        toks = [t for t in seg if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', t)] or seg
        while len(toks) > 1 and (os.path.basename(toks[0]) in WRAPPERS or toks[0].startswith('-') or toks[0].isdigit()):
            toks = toks[1:]  # env, timeout 5, nice -n 5 and the like run the command after them
        prog = os.path.basename(toks[0])
        if prog in METADATA_ONLY or toks[:2] == ['git', 'check-ignore']: continue
        if prog == 'security' and len(toks) > 1 and toks[1] in KEYCHAIN: return f'the Keychain (security {toks[1]})'
        if prog == 'gcloud' and toks[1:4] == ['secrets', 'versions', 'access']: return 'a Secret Manager value'
        command_option = next((i for i, t in enumerate(toks[1:-1], 1) if re.fullmatch(r'-[a-zA-Z]*c[a-zA-Z]*', t)), None)
        if prog in SHELLS and command_option:
            r = check(toks[command_option + 1], depth + 1)
            if r: return r
        if prog == 'eval':
            r = check(' '.join(toks[1:]), depth + 1)
            if r: return r
        for t in seg:
            found = secret_in(t)
            if found: return found
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        print('Blocked: the secrets guard could not read its input, so it refuses the call.', file=sys.stderr)
        return 2
    if data.get('tool_name') != 'Bash': return 0
    found = check((data.get('tool_input') or {}).get('command') or '')
    if found:
        print(f'Blocked: this command would touch a secret ({found}). Coding agents never read, print or copy secrets '
              f'in codetrail (AGENTS.md, Never do, rule 2). If a task seems to need one, stop and tell the '
              f'founder. Do not try another form of the same command.', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:  # fail closed: an error in the guard must block (security review of Phase 0, finding 4)
        print(f'Blocked: the secrets guard failed ({type(error).__name__}), so it refuses the call.', file=sys.stderr)
        sys.exit(2)
