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
import json, os, re, sys, time

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
    r'|(^~|HOME\}?|^/Users/[^/]+|^/home/[^/]+)/\.codex(/|$)|(^|/)\.codex/auth\.json$|CODEX_HOME\}?/|(^|/)\.azure(/|$)|(^|/)glab-cli(/|$)')
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


# Each search program's options that take a value, as one cluster's last letter (-e PATTERN, -m1) or as a long option
# (--include x): their values are neither the pattern nor a folder.
SEARCH_OPTIONS_WITH_VALUE = {
    'grep': ('efmABCdD', {'--regexp', '--file', '--max-count', '--after-context', '--before-context', '--context',
                          '--include', '--exclude', '--exclude-dir', '--exclude-from', '--label', '--binary-files',
                          '--directories', '--devices', '--color', '--colour'}),
    'rg': ('efgtTmMABCjdErM', {'--regexp', '--file', '--glob', '--iglob', '--type', '--type-not', '--type-add',
                               '--type-clear', '--max-count', '--max-depth', '--max-columns', '--max-filesize',
                               '--context', '--after-context', '--before-context', '--replace', '--encoding',
                               '--engine', '--threads', '--ignore-file', '--sort', '--sortr', '--pre', '--pre-glob',
                               '--color', '--colors', '--path-separator', '--context-separator',
                               '--field-match-separator', '--field-context-separator', '--dfa-size-limit',
                               '--regex-size-limit', '--hyperlink-format', '--generate'}),
    'ag': ('ABCGgmp', {'--ignore', '--ignore-dir', '--depth', '--pager', '--file-search-regex', '--max-count',
                       '--context', '--after', '--before', '--path-to-ignore'}),
    'git grep': ('efABCm', {'--max-depth', '--context', '--after-context', '--before-context', '--max-count',
                             '--threads', '--open-files-in-pager'}),
}
SEARCH_OPTIONS_WITH_VALUE['ack'] = SEARCH_OPTIONS_WITH_VALUE['ag']
GREP_NAMES = {'grep', 'egrep', 'fgrep', 'ggrep'}
# rg options that reach ignored or hidden files: any --no-ignore option, --hidden, --unrestricted, -u, -.
RG_REACHES_EVERY_FILE = re.compile(r'--(no-ignore.*|hidden|unrestricted)|-[A-Za-z]*[u.][A-Za-z.]*')
# Folders a search walks without the guard looking for secret files in them: git's own, and installed packages, whose
# certificate bundles (cacert.pem) aren't the reader's secrets.
NOT_SEARCHED_FOR_SECRETS = {'.git', '.venv', 'node_modules'}
# The files a search may walk, and the seconds it may take, before the guard gives up and refuses: well within the
# hook's own time limit (.claude/settings.json), after which Claude Code might let the command run.
MAX_FILES_SEARCHED = 200_000
MAX_SECONDS_SEARCHING = 10
UNKNOWN_FOLDER = 'a search from a folder only the shell can work out'


def search_words(program, args):
    """A search's options (with their values) and the folders it searches: the words after the pattern, else '.'."""
    short_with_value, long_with_value = SEARCH_OPTIONS_WITH_VALUE[program]
    options, operands, pattern_given, i = [], [], False, 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            operands += args[i + 1:]
            break
        if arg.startswith('--'):
            name, has_value, value = arg.partition('=')
            if name in long_with_value and not has_value:
                value = args[i + 1] if i + 1 < len(args) else ''
                i += 1
            options.append((name, value))
            pattern_given |= name in ('--regexp', '--file')
        elif arg.startswith('-') and len(arg) > 1:
            for position, letter in enumerate(arg[1:], start=2):
                value = ''
                if letter in short_with_value:  # the rest of the cluster, or the next word, is its value
                    value = arg[position:] or (args[i + 1] if i + 1 < len(args) else '')
                    i += 0 if arg[position:] else 1
                options.append((f'-{letter}', value))
                pattern_given |= letter in 'ef' and letter in short_with_value
                if value: break
        else:
            operands.append(arg)
        i += 1
    return options, (operands if pattern_given else operands[1:]) or ['.']


def searched_folders(toks):
    """What a command searches in the working tree, as (every_file, folders), or None for any other command.

    every_file is False for rg's default, which skips hidden files and folders (and ignored ones, which the guard
    doesn't read, so it counts them as searched)."""
    prog, args = os.path.basename(toks[0]), toks[1:]
    if prog in GREP_NAMES:
        options, folders = search_words('grep', args)
        recursive = any(name in ('-r', '-R', '--recursive', '--dereference-recursive') or
                        (name in ('-d', '--directories') and value == 'recurse') for name, value in options)
        return (True, folders) if recursive else None
    if prog == 'rg':
        options, folders = search_words('rg', args)
        return any(RG_REACHES_EVERY_FILE.fullmatch(a) for a in args if a.startswith('-')), folders
    if prog in ('ag', 'ack'):
        return True, search_words(prog, args)[1]
    if prog == 'git' and args[:1] == ['grep'] and any(a in ('--no-index', '--untracked') for a in args):
        return True, search_words('git grep', args[1:])[1]
    return None


def codex_homes():
    homes = [os.path.expanduser('~/.codex')] + ([os.environ['CODEX_HOME']] if os.environ.get('CODEX_HOME') else [])
    return [os.path.realpath(home) for home in homes]


def in_codex_home(path):
    """An absolute path inside Codex's home (~/.codex or $CODEX_HOME), however it was named."""
    real = os.path.realpath(path)
    return any(real == home or real.startswith(home + '/') for home in codex_homes())


def moved_to(prog, args, base):
    """The folder a cd or pushd moves to, or None when only the shell can work it out."""
    targets = [a for a in args if a == '-' or not a.startswith('-')]
    if prog == 'popd' or (prog == 'pushd' and not targets): return None
    target = targets[0].strip('\'"') if targets else '~'
    if target == '-' or re.search(r'[$`*?\[]', target): return None
    target = os.path.expanduser(target)
    if target.startswith('~') or not (base or os.path.isabs(target)): return None
    folder = os.path.join(base or '/', target)
    return os.path.realpath(folder) if os.path.isdir(folder) else None


def secret_file_under(every_file, folders, base):
    """The first secret file under the folders, or a reason the guard can't tell; None when there is none.

    It follows symbolic links, as grep -R and rg -L do, and visits each real folder once."""
    if base is None: return UNKNOWN_FOLDER
    searched, visited, deadline = 0, set(), time.monotonic() + MAX_SECONDS_SEARCHING
    for folder in folders:
        folder = folder.strip('\'"')
        if re.search(r'[$`*?\[]', folder): return f'a search of {folder}, which only the shell can resolve'
        top = os.path.join(base, os.path.expanduser(folder))
        if not os.path.exists(top):
            if folder.startswith(('~', '/')): return f'a search of {folder}, which the guard can\'t find'
            continue  # a relative folder that isn't there: the search finds nothing
        for root, dirs, files in os.walk(top, followlinks=True):
            real = os.path.realpath(root)
            if real in visited:
                dirs[:] = []
                continue
            visited.add(real)
            dirs[:] = [name for name in dirs if name not in NOT_SEARCHED_FOR_SECRETS and (every_file or name[0] != '.')]
            files = [name for name in files if every_file or name[0] != '.']
            searched += len(files)
            if searched > MAX_FILES_SEARCHED or time.monotonic() > deadline:
                return f'a search of {folder}, too large to check for secret files: search a narrower folder'
            for name in files + dirs:
                found = os.path.join(root, name)
                if SECRET_NAME.match(name) or SECRET_PATH.search(found) or in_codex_home(found): return found
    return None


def secret_in(token, base=None):
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
    if base is None and re.search(r'(^|/)\.codex(/|$)', path) and not os.path.isabs(os.path.expanduser(path)):
        return path  # relative to a folder the guard can't work out, which could be the home folder
    absolute = os.path.expanduser(path)
    if path and '$' not in path and (os.path.isabs(absolute) or base) and in_codex_home(os.path.join(base or '/', absolute)):
        return path  # .codex/config.toml from the home folder, ../../.codex/history.jsonl
    return None


def check(cmd, depth=0, base=None):
    if depth > 4: return 'a command nested too deeply to read'
    try:
        cmd, bodies = strip_heredocs(cmd)
        segs = list(segments(cmd))
    except ValueError:
        return None  # block_push.py refuses commands it can't read
    for inner in [s for body in bodies for s in subshells(body)]:
        r = check(inner, depth + 1, base)
        if r: return r
    # A cd inside ( ) or $( ) doesn't move the shell, and the guard doesn't follow which one a cd is in.
    in_parentheses = '(' in cmd
    for seg in segs:
        for inner in subshells(' '.join(seg)):  # a $(...) runs in the folder the commands before it moved to
            r = check(inner, depth + 1, base)
            if r: return r
        toks = [t for t in seg if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', t)] or seg
        while len(toks) > 1 and (os.path.basename(toks[0]) in WRAPPERS or toks[0].startswith('-') or toks[0].isdigit()):
            toks = toks[1:]  # env, timeout 5, nice -n 5 and the like run the command after them
        prog = os.path.basename(toks[0])
        search = searched_folders(toks)
        if search:
            found = secret_file_under(*search, base)
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
            found = secret_in(t, base)
            if found: return found
        if prog in ('cd', 'pushd', 'popd'):
            base = None if in_parentheses else moved_to(prog, toks[1:], base)  # the commands after it run there
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
    found = check((data.get('tool_input') or {}).get('command') or '', base=os.path.realpath(data.get('cwd') or os.getcwd()))
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
