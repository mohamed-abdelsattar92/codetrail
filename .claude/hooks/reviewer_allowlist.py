#!/usr/bin/env python3
"""PreToolUse hook for the security-reviewer agent only (its frontmatter in .claude/agents/security-reviewer.md).

The reviewer reads untrusted branches and web pages, so its limits are enforced here rather than trusted to
instructions (docs/security/review-checklist.md). Everything is refused unless listed below, options included:
- git: the read-only subcommands in GIT_OPTIONS with the options listed there (values attached, as in -n5 or
  --format=%h), after --no-pager, --no-replace-objects or -C to the repository; no path outside the repository.
  git is refused altogether while its configuration names a program a read command would run (a diff driver,
  a text conversion, a filter, fsmonitor or gpg).
- filters that read a pipe (head, tail, wc, cut, tr, grep, sort, uniq) with the options in FILTERS and no files,
  except that grep may count or list matches in mise's configuration files.
- `test -e|-f|-d <path>`, `cd` to the repository, and `mise which <tool>`.
- gitleaks, run by mise's install path so no mise setting reaches it, and the dependency audit, exactly as the
  checklist writes them, with only commit ids varying.
- `export MISE_EXEC_AUTO_INSTALL=false`, which the checklist starts each call with; the only other variables are
  GITLEAKS_CONFIG and GITLEAKS_CONFIG_TOML, set empty. No `mise exec`: the audit is matched whole, and gitleaks runs
  by path.
- WebFetch: https pages on the standards and documentation sites below.
Shell expansions ($, backticks, braces, globs outside a git pathspec) and redirections other than to /dev/null are
refused.
Anything else, input it can't read, or an error in this hook exits with code 2, which blocks the call.
"""
import json, os, re, subprocess, sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.environ.get('CLAUDE_PROJECT_DIR') or os.path.dirname(os.path.dirname(HERE))

# The checklist's dependency audit, allowed only exactly; tools/tests/test_reviewer_allowlist.py keeps the two equal.
AUDIT = ('export MISE_EXEC_AUTO_INSTALL=false; js=$(mktemp -d) py=$(mktemp -d) && git show <sha>:pnpm-lock.yaml > '
         '"$js/pnpm-lock.yaml" && git show <sha>:pyproject.toml > "$py/pyproject.toml" && git show '
         '<sha>:uv.lock > "$py/uv.lock" && mise exec -- pnpm --dir "$js" audit '
         '--registry=https://registry.npmjs.org/; mise exec -- uv audit --frozen --no-build --no-config --directory '
         '"$py"; rm -rf "$js" "$py"')
_AUDIT_PARTS = re.escape(AUDIT).split('<sha>')  # the same 40-character commit id in all three places
AUDIT_PATTERN = re.compile(_AUDIT_PARTS[0] + '(?P<sha>[0-9a-f]{40})' + '(?P=sha)'.join(_AUDIT_PARTS[1:]))
GITLEAKS_ARGS = ['git', '$(git rev-parse --absolute-git-dir)', '--redact', '--no-banner', '--ignore-gitleaks-allow',
                 '--gitleaks-ignore-path', '/dev/null']
MISE_DATA = os.environ.get('MISE_DATA_DIR') or os.path.expanduser('~/.local/share/mise')
GITLEAKS_PATH = re.compile(re.escape(os.path.join(MISE_DATA, 'installs', 'gitleaks')) + r'/[0-9][\w.]*/gitleaks')
LOG_RANGE = re.compile(r'--log-opts=[0-9a-f]{7,40}\.\.[0-9a-f]{7,40}')
VARIABLES = {'MISE_EXEC_AUTO_INSTALL=false', 'GITLEAKS_CONFIG=', 'GITLEAKS_CONFIG_TOML='}

DIFF_OPTIONS = {'--stat', '--numstat', '--shortstat', '--name-only', '--name-status', '-p', '--patch', '-U',
                '--unified', '-w', '--diff-filter', '--word-diff', '--no-color', '--no-ext-diff', '--no-textconv',
                '-M', '--find-renames'}
SHOW_OPTIONS = DIFF_OPTIONS | {'--oneline', '--format', '--pretty', '--date', '-s', '--no-patch', '--abbrev-commit'}
GIT_OPTIONS = {
    'rev-parse': {'--short', '--abbrev-ref', '--verify', '--quiet', '-q', '--symbolic-full-name', '--absolute-git-dir',
                  '--show-toplevel'},
    'log': SHOW_OPTIONS | {'-n', '--max-count', '--no-merges', '--merges', '--first-parent', '--reverse', '--graph',
                           '--follow', '--since', '--until', '--author', '--grep', '-G', '-S', '--decorate'},
    'show': SHOW_OPTIONS,
    'diff': DIFF_OPTIONS,
    'merge-base': {'--is-ancestor', '--all'},
    'ls-tree': {'-r', '-t', '-d', '-l', '--long', '--name-only', '--full-tree'},
    'cat-file': {'-p', '-t', '-s', '-e'},
    'for-each-ref': {'--format', '--sort', '--count', '--contains', '--points-at'},
    'describe': {'--tags', '--always', '--abbrev', '--exact-match'},
    'shortlog': {'-s', '-n', '-e', '--summary', '--numbered', '--email'},
    'blame': {'-L', '-w', '-s', '-e', '--porcelain', '--line-porcelain', '--no-textconv'},
}
ATTACHED = {'-n', '-U', '-L', '-G', '-S'}  # short options whose value is written straight after them
# Configuration under which a read command runs a program: a diff driver, text conversion, filter, fsmonitor, or
# gpg for a signature that --format or log.showSignature shows.
RUNS_PROGRAMS = (r'^(diff\.external|diff\..*\.(command|textconv)|filter\..*\.(clean|smudge|process)|core\.fsmonitor'
                 r'|gpg\.program|gpg\..*\.program|log\.showsignature)$')
FILTERS = {  # program: (flags, options that take a value)
    'head': (set(), {'-n', '-c'}),
    'tail': (set(), {'-n', '-c'}),
    'wc': ({'-l', '-c', '-w', '-m'}, set()),
    'cut': ({'-s'}, {'-d', '-f', '-c', '-b'}),
    'tr': ({'-d', '-s', '-c', '-C'}, set()),
    'sort': ({'-n', '-r', '-u', '-f', '-V', '-h', '-b'}, {'-k', '-t'}),
    'uniq': ({'-c', '-d', '-u', '-i'}, set()),
    'grep': ({'-n', '-i', '-v', '-c', '-l', '-L', '-q', '-s', '-h', '-H', '-o', '-w', '-x', '-E', '-F'},
             {'-A', '-B', '-C', '-m', '-e'}),
}
# The checklist's check for mise environment settings in any spelling: [env], env.NAME = or env = {...}.
ENV_PATTERN = '^[[:space:]]*(\\[env|env[[:space:]]*[.=])'
GREP_BRACKETS = {ENV_PATTERN}  # nothing else may look like a glob
# The configuration files mise loads from a folder, for any MISE_ENV and in conf.d.
MISE_FILE = re.compile(r'(\.?mise|\.config/mise|\.config/mise/config|\.mise/config|mise/config)(\.[\w-]+)*\.toml'
                       r'|(\.config/mise|\.mise|mise)/conf\.d/[\w.-]+\.toml|\.tool-versions')
SITES = ('owasp.org', 'mitre.org', 'nist.gov', 'osv.dev', 'github.com', 'githubusercontent.com', 'first.org',
         'docs.github.com', 'code.claude.com', 'docs.claude.com', 'mise.jdx.dev', 'docs.astral.sh', 'pnpm.io',
         'nodejs.org', 'developer.mozilla.org', 'python.org', 'fastapi.tiangolo.com', 'gitleaks.io', 'npmjs.com',
         'pypi.org', 'jinja.palletsprojects.com', 'markdown-it-py.readthedocs.io', 'mermaid.js.org',
         'tree-sitter.github.io', 'uvicorn.org', 'pyyaml.org', 'babel.pocoo.org')


def outside_repository(token):
    return token.startswith(('/', '~')) or token == '..' or token.startswith('../') or '/../' in token


def git_runs_programs():
    if any(name in os.environ for name in ('GIT_EXTERNAL_DIFF', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT')):
        return True
    found = subprocess.run(['git', '-C', PROJECT, 'config', '--get-regexp', RUNS_PROGRAMS],
                           capture_output=True, text=True).stdout.splitlines()
    return any(not (line.startswith('core.fsmonitor ') and line.split(' ', 1)[1] in ('false', '0', 'no', 'off'))
               for line in found)


def check_git(args):
    i = 0
    while i < len(args) and args[i].startswith('-'):
        if args[i] == '-C' and i + 1 < len(args) and os.path.realpath(args[i + 1]) == os.path.realpath(PROJECT):
            i += 2
        elif args[i] in ('--no-pager', '--no-replace-objects'):
            i += 1
        else:
            return f'git {args[i]}'
    sub, rest = (args[i], args[i + 1:]) if i < len(args) else ('', [])
    if sub not in GIT_OPTIONS: return f'git {sub}'.strip()
    pathspecs = False
    for arg in rest:
        if pathspecs or not arg.startswith('-'):
            if outside_repository(arg) or '$' in arg: return f'git {sub} reading {arg}'
            if re.search(r'[*?\[]', arg) and not pathspecs: return f'a glob outside a pathspec ({arg})'
        elif arg == '--':
            pathspecs = True
        elif not (arg.split('=', 1)[0] in GIT_OPTIONS[sub] or (arg[:2] in ATTACHED & GIT_OPTIONS[sub])
                  or (sub in ('log', 'show') and re.fullmatch(r'-\d+', arg)) or re.fullmatch(r'-M\d*%?', arg)):
            return f'git {sub} {arg}'
    return 'git with a diff, text conversion or filter program configured' if git_runs_programs() else None


def check_filter(prog, args):
    flags, with_value = FILTERS[prog]
    operands, i = [], 0
    while i < len(args):
        arg = args[i]; i += 1
        if arg == '--' or not arg.startswith('-') or arg == '-':
            operands += args[i - 1:] if arg != '--' else args[i:]
            break
        if arg in with_value:
            i += 1  # its value is the next word, which these programs always take as the value
        elif not (arg[:2] in with_value or all(f'-{c}' in flags for c in arg[1:])
                  or (prog in ('head', 'tail') and re.fullmatch(r'-\d+', arg))):
            return f'{prog} {arg}'
    if any(re.search(r'[*?\[]', a) and a not in GREP_BRACKETS for a in args): return f'a glob in {prog}'
    if prog == 'tr' and len(operands) <= 2: return None
    if prog == 'grep':
        if '-e' not in args and not any(a.startswith('-e') and len(a) > 2 for a in args): operands = operands[1:]
        counts_only = any(a in ('-c', '-l', '-L', '-q') for a in args)
        if all(MISE_FILE.fullmatch(o) for o in operands) and (counts_only or not operands): return None
    return f'{prog} reading {operands[0]}' if operands else None


def check_segment(toks):
    while toks and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', toks[0]):
        assignment = toks.pop(0)
        if assignment not in VARIABLES: return f'setting {assignment.split("=", 1)[0]}'
    if not toks: return None
    prog, args = toks[0], toks[1:]
    if prog == 'export':
        return None if args == ['MISE_EXEC_AUTO_INSTALL=false'] else f'export {" ".join(args)}'
    if prog == 'cd':
        allowed = args in (['$CLAUDE_PROJECT_DIR'],) or (len(args) == 1 and os.path.realpath(args[0]) == os.path.realpath(PROJECT))
        return None if allowed else f'cd {" ".join(args)}'
    if prog in ('test', '['):
        body = args[:-1] if prog == '[' and args[-1:] == [']'] else args
        return None if len(body) == 2 and body[0] in ('-e', '-f', '-d') else f'{prog} {" ".join(body)}'
    if prog == 'mise':
        if len(args) == 2 and args[0] == 'which' and re.fullmatch(r'[a-z0-9-]+', args[1]): return None
        return f'mise {" ".join(args[:2])}'
    if os.path.basename(prog) == 'gitleaks':
        exact = GITLEAKS_PATH.fullmatch(prog) and args[:7] == GITLEAKS_ARGS and len(args) == 8 and LOG_RANGE.fullmatch(args[7])
        return None if exact else 'gitleaks in any form but the checklist\'s'
    if prog == 'git': return check_git(args)
    if prog in FILTERS: return check_filter(prog, args)
    return prog


def check_bash(cmd):
    if AUDIT_PATTERN.fullmatch(cmd.strip()): return None  # exactly: a line break would make separate commands
    from block_push import segments  # the push guard's reading of a command line: ; && || | and newlines split it
    try:
        segs = [list(seg) for seg in segments(cmd)]
    except ValueError:
        return 'a command the guard cannot read'
    for seg in segs:
        toks = []
        for i, tok in enumerate(seg):
            if re.fullmatch(r'\d*(>|>>|&>)(/dev/null|&[12])', tok): continue
            if tok in ('>', '2>', '&>') and seg[i + 1:i + 2] == ['/dev/null']: continue
            if tok == '/dev/null' and i and seg[i - 1] in ('>', '2>', '&>'): continue
            if re.search(r'[<>]', tok): return f'a redirection ({tok})'  # a space doesn't prove it was quoted
            if re.search(r'[$`]', tok) and tok not in ('$(git rev-parse --absolute-git-dir)', '$CLAUDE_PROJECT_DIR'):
                return f'a shell expansion ({tok})'
            if re.search(r'[{}]', tok): return f'a brace expansion ({tok})'  # the shell expands it after this check
            toks.append(tok)
        reason = check_segment(toks)
        if reason: return reason
    return None


def check_url(url):
    parsed = urlparse(url)
    # A backslash or a user name before the host can make the fetching tool read another host than urlparse does.
    if '\\' in url or '@' in parsed.netloc: return f'{url} (a backslash or @ in the address)'
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https': return f'{url} (https only)'
    if not any(host == site or host.endswith('.' + site) for site in SITES): return f'{host} (not a standards site)'
    return None


def main():
    data = json.load(sys.stdin)
    tool, tool_input = data.get('tool_name'), data.get('tool_input') or {}
    if tool == 'Bash':
        reason = check_bash(tool_input.get('command') or '')
    elif tool == 'WebFetch':
        reason = check_url(tool_input.get('url') or '')
    else:
        return 0
    if reason:
        print(f'Blocked for the security reviewer: {reason}. The reviewer only reads: read-only git with its listed '
              f'options, the checklist\'s gitleaks and audit commands, `mise which`, filters on a pipe, and standards '
              f'sites (.claude/hooks/reviewer_allowlist.py). Put what you could not check under "Not checked".',
              file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.path.insert(0, HERE)
    try:
        sys.exit(main())
    except Exception as error:  # fail closed: a hook that errors must block, since any other exit code allows
        print(f'Blocked for the security reviewer: the allowlist hook failed ({type(error).__name__}).', file=sys.stderr)
        sys.exit(2)
