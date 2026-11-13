#!/usr/bin/env python3
"""Claude Code PreToolUse hook: agents never read or print secrets (AGENTS.md, Never do, rule 2).

The permission rules in .claude/settings.json keep the Read tool away from secret files, but not Bash, so this hook
refuses Bash commands that name one:
- env files other than examples (.env, .env.local, .dev.vars), private keys and certificates (.p8, .p12, .pem,
  .keystore, .jks, .key, .pfx, .ppk, id_rsa, id_ed25519), Terraform state (.tfstate) and variables (.tfvars);
- credential stores in the home folder: ~/.ssh, gh's configuration, gcloud, wrangler, .netrc, .npmrc, .git-credentials,
  Terraform's credentials, Docker and AWS, named with or without a trailing slash;
- the sign-ins of the programs Codetrail drives: Codex's home (~/.codex or $CODEX_HOME, whose configuration, history
  and sessions can hold keys too), az's ~/.azure and glab's glab-cli folders;
- the Keychain (security find-*-password, dump-keychain, export), `gcloud secrets versions access`, and commands that
  print a token (`az account get-access-token`, `gh` or `glab auth status --show-token`);
- a file at a revision or in the index (git show HEAD:.env, git show :0:.env) by the file's name;
- a recursive search (grep -r, or rg showing hidden files) of a folder that holds one of these files, since it prints
  the file's lines without naming it; a search of folders that hold none, and rg's default, which skips hidden and
  ignored files, are allowed.
A glob naming one of these (.dev.vars*) counts, and so does a shell's -c command (bash -lc) behind wrappers such as env.
Commands that only look at a file's existence or metadata (ls, test, stat, git check-ignore) are allowed.
It guards against mistakes: a command built to hide a name, such as a quoted heredoc fed to a shell, gets past it.
Exit code 2 blocks the call and shows the reason to the agent.
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from block_push import SHELLS, WRAPPERS, segments, strip_heredocs, subshells  # noqa: E402
    IMPORT_FAILED = False
except Exception:  # fail closed below: a guard that can't load must block (second review, finding 4)
    IMPORT_FAILED = True

SECRET_NAME = re.compile(
    r'^(\.env|\.env\.(?!example).+|\.dev\.vars|.+\.(p8|p12|pem|keystore|jks|key|pfx|ppk)|id_rsa.*|id_ed25519.*|.+\.tfstate(\.backup)?|.+\.tfvars(\.json)?)$', re.I)
SECRET_PATH = re.compile(
    r'(^|/)\.ssh(/|$)|\.config/gh(/|$)|\.config/gcloud(/|$)|\.wrangler/config|Preferences/\.wrangler'
    r'|(^|/)\.netrc$|(^|/)\.npmrc$|(^|/)\.git-credentials$|\.terraform\.d/credentials|\.docker/config\.json'
    r'|\.aws(/|$)|(^|/)\.pypirc$'
    r'|(^~|HOME\}?|^/Users/[^/]+|^/home/[^/]+)/\.codex(/|$)|CODEX_HOME\}?/|(^|/)\.azure(/|$)|(^|/)glab-cli(/|$)')
# Commands that print a token: the sign-ins of the hosting programs Codetrail drives (ADR 0020), as the program and
# its words with options left out.
TOKEN_PRINTERS = (('az', 'account', 'get-access-token'), ('glab', 'config', 'get', 'token'))


def prints_token(toks):
    prog = os.path.basename(toks[0])
    words = [t for t in toks[1:] if not t.startswith('-')]
    for printer in TOKEN_PRINTERS:  # in order, with anything between: an option's value can come before or among them
        remaining = iter(words)
        if printer[0] == prog and all(word in remaining for word in printer[1:]):
            return f'a token ({" ".join(printer)})'
    if prog in ('gh', 'glab') and words[:2] == ['auth', 'status']:
        flags = [t.split('=', 1)[0] for t in toks[1:] if t.startswith('-')]
        if '--show-token' in flags or any(not f.startswith('--') and 't' in f[1:] for f in flags):
            return f'a token ({prog} auth status --show-token)'
    return None
METADATA_ONLY = {'ls', 'test', '[', 'stat'}
KEYCHAIN = {'find-generic-password', 'find-internet-password', 'dump-keychain', 'export', 'export-item'}


# grep's options that take a value, as one cluster's last letter (-e PATTERN, -m1) or as a long option (--include x).
GREP_SHORT_WITH_VALUE = 'efmABCdD'
GREP_LONG_WITH_VALUE = {'--regexp', '--file', '--max-count', '--after-context', '--before-context', '--context',
                        '--include', '--exclude', '--exclude-dir', '--exclude-from', '--label', '--binary-files',
                        '--directories', '--devices', '--color', '--colour'}
# Folders a search walks without the guard looking for secret files in them: git's own, and installed packages, whose
# certificate bundles (cacert.pem) aren't the reader's secrets.
NOT_SEARCHED_FOR_SECRETS = {'.git', '.venv', 'node_modules'}
# The files a search may walk before the guard gives up and refuses, rather than take too long.
MAX_FILES_SEARCHED = 200_000


def grep_folders(args):
    """The folders a recursive grep searches, or None when it doesn't recurse."""
    recursive = pattern_given = False
    operands, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            operands += args[i + 1:]
            break
        if arg.startswith('--'):
            name, has_value, value = arg.partition('=')
            if name in GREP_LONG_WITH_VALUE and not has_value:
                value = args[i + 1] if i + 1 < len(args) else ''
                i += 1
            recursive |= name in ('--recursive', '--dereference-recursive') or (name, value) == ('--directories', 'recurse')
            pattern_given |= name in ('--regexp', '--file')
        elif arg.startswith('-') and len(arg) > 1:
            for position, letter in enumerate(arg[1:], start=2):
                recursive |= letter in 'rR'
                if letter in GREP_SHORT_WITH_VALUE:  # the rest of the cluster, or the next word, is its value
                    value = arg[position:] or (args[i + 1] if i + 1 < len(args) else '')
                    i += 0 if arg[position:] else 1
                    pattern_given |= letter in 'ef'
                    recursive |= (letter, value) == ('d', 'recurse')
                    break
        else:
            operands.append(arg)
        i += 1
    if not recursive: return None
    return (operands if pattern_given else operands[1:]) or ['.']


def rg_folders(args, base):
    """The folders an rg that shows hidden files searches (--hidden, -., -uu, --unrestricted twice), or None.

    rg skips hidden and ignored files by default. Its many options with values aren't listed here, so any word that
    names an existing path counts as a folder, and the working folder when none does."""
    hidden = sum(2 if a in ('--hidden', '--unrestricted') else 0 for a in args)
    hidden += sum(a.count('u') + 2 * a.count('.') for a in args if re.fullmatch(r'-[A-Za-z.]+', a))
    if hidden < 2: return None
    return [a for a in args if not a.startswith('-') and os.path.exists(os.path.join(base, a))] or ['.']


def secret_file_under(folders, base):
    """The first secret file under the folders, or a reason the guard can't tell; None when there is none."""
    searched = 0
    for folder in folders:
        folder = os.path.expanduser(os.path.expandvars(folder.strip('\'"')))
        if '$' in folder or re.search(r'[*?\[]', folder): return f'a search of {folder}, which only the shell can resolve'
        for root, dirs, files in os.walk(os.path.join(base, folder)):
            dirs[:] = [name for name in dirs if name not in NOT_SEARCHED_FOR_SECRETS]
            searched += len(files)
            if searched > MAX_FILES_SEARCHED: return f'a search of {folder}, too large to check for secret files'
            for name in files + dirs:
                found = os.path.join(root, name)
                if SECRET_NAME.match(name) or SECRET_PATH.search(found): return found
    return None


def secret_in(token):
    # A token may carry the path after an option or a redirection: --env-file=.env, <.dev.vars, 2>~/.netrc.
    path = re.sub(r'^\d*[<>]+&?', '', token.split('=', 1)[-1]).strip('\'"')
    if re.search(r'Library/Application Support/glab-cli(/|$)', path): return path  # the one secret path with a space
    if re.search(r'\s', path): return None  # prose, such as a commit message; no other secret path here has spaces
    if SECRET_PATH.search(path): return path
    if re.match(r'^[^\s:/]*(/[^\s:/]+)*:[^/]', path):  # a revision and a path, as git show HEAD:.env names them
        path = re.sub(r'^[0-3]:', '', path.split(':', 1)[1])  # :0:.env names the index's stage 0
        if SECRET_PATH.search(path): return path
    name = os.path.basename(path)
    if SECRET_NAME.match(name) or SECRET_NAME.match(re.split(r'[*?\[]', name)[0]): return path
    return None


def check(cmd, depth=0, base='.'):
    if depth > 4: return 'a command nested too deeply to read'
    try:
        cmd, bodies = strip_heredocs(cmd)
        segs = list(segments(cmd))
    except ValueError:
        return None  # block_push.py refuses commands it can't read
    for inner in subshells(cmd) + [s for body in bodies for s in subshells(body)]:
        r = check(inner, depth + 1, base)
        if r: return r
    for seg in segs:
        toks = [t for t in seg if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', t)] or seg
        while len(toks) > 1 and (os.path.basename(toks[0]) in WRAPPERS or toks[0].startswith('-') or toks[0].isdigit()):
            toks = toks[1:]  # env, timeout 5, nice -n 5 and the like run the command after them
        prog = os.path.basename(toks[0])
        if prog in ('cd', 'pushd') and len(toks) == 2:
            base = os.path.join(base, os.path.expanduser(toks[1]))  # a later search runs there
        folders = grep_folders(toks[1:]) if prog == 'grep' else rg_folders(toks[1:], base) if prog == 'rg' else None
        if folders:
            found = secret_file_under(folders, base)
            if found: return found
        if prog in METADATA_ONLY or toks[:2] == ['git', 'check-ignore']: continue
        if prog == 'security' and len(toks) > 1 and toks[1] in KEYCHAIN: return f'the Keychain (security {toks[1]})'
        if prog == 'gcloud' and toks[1:4] == ['secrets', 'versions', 'access']: return 'a Secret Manager value'
        token = prints_token(toks)
        if token: return token
        command_option = next((i for i, t in enumerate(toks[1:-1], 1) if re.fullmatch(r'-[a-zA-Z]*c[a-zA-Z]*', t)), None)
        if prog in SHELLS and command_option:
            r = check(toks[command_option + 1], depth + 1, base)
            if r: return r
        if prog == 'eval':
            r = check(' '.join(toks[1:]), depth + 1, base)
            if r: return r
        for t in seg:
            found = secret_in(t)
            if found: return found
    return None


def main():
    if IMPORT_FAILED:
        print('Blocked: the secrets guard could not load block_push.py, so it refuses the call.', file=sys.stderr)
        return 2
    try:
        data = json.load(sys.stdin)
    except Exception:
        print('Blocked: the secrets guard could not read its input, so it refuses the call.', file=sys.stderr)
        return 2
    if data.get('tool_name') != 'Bash': return 0
    found = check((data.get('tool_input') or {}).get('command') or '', base=data.get('cwd') or os.getcwd())
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
