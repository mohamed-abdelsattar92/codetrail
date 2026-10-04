#!/usr/bin/env python3
"""Claude Code PreToolUse hook: agents never push (AGENTS.md, Never do, rule 1).

Blocks any Bash command that would push code or write to GitHub, in whatever form:
git push (including git -C, env wrappers, bash -c, eval, $(...)), git send-pack,
git subtree push, push aliases, remote or credential changes, and gh commands that
create or merge pull requests, sync repositories, expose tokens or call the API with
a write method. For git-flow (git flow or git-flow), it blocks publish, the push options,
branch deletion, a feature finish without --no-push and --keepremote, and finishing a
release or hotfix; agents finish features into develop and stop.
It also blocks skipping the git hooks (AGENTS.md, Never do, rule 8): --no-verify on commits,
merges and git-flow finishes, `git commit -n`, LEFTHOOK=0 or LEFTHOOK_EXCLUDE, and core.hooksPath.
It blocks the plain ways of moving refs by hand (git update-ref, or a fetch that writes refs/remotes), since the
security review reads origin/develop; the review also reads it by its full name with replace objects off.
A command it can't read is blocked too, rather than let through: unclosed quotes, $'...' quoting, a heredoc
without its end line, or a brace expansion beside git. A quoted heredoc fed to a shell is still read as data. Quoted heredoc bodies are data (a commit message); unquoted ones are checked for $(...).
Exit code 2 blocks the call and shows the reason to the agent.
The git pre-push hook (tools/git-hooks/pre-push) is the backstop for anything missed.
"""
import json, os, re, shlex, sys

GIT_OPTS_WITH_VALUE = {'-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path', '--config-env', '--super-prefix'}
WRAPPERS = {'env', 'command', 'exec', 'sudo', 'nohup', 'time', 'nice', 'xcrun', 'caffeinate', 'timeout'}
SHELLS = {'bash', 'sh', 'zsh', 'dash', 'fish', 'ksh'}
GH_WRITE = {
    'pr': {'create', 'merge', 'close', 'reopen', 'edit', 'comment', 'review', 'ready'},
    'repo': {'sync', 'create', 'delete', 'edit', 'rename', 'archive', 'fork'},
    'release': {'create', 'delete', 'edit', 'upload'},
    'auth': {'token', 'git-credential', 'setup-git', 'login', 'logout', 'refresh'},
    'workflow': {'run', 'enable', 'disable'},
    'run': {'rerun', 'cancel', 'delete'},
    'issue': {'create', 'close', 'reopen', 'edit', 'comment', 'delete', 'transfer'},
    'label': {'create', 'edit', 'delete', 'clone'},
    'gist': {'create', 'edit', 'delete'},
    'secret': None, 'variable': None, 'ssh-key': None, 'gpg-key': None, 'ruleset': None,
}

HEREDOC = re.compile(r"<<(-?)[ \t]*(\\?)(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\3")

def heredoc_starts(line, stack):
    """The heredocs a line starts, as (dash, quoted, word), skipping << inside quotes or comments and <<<.

    `stack` holds the quoting open at the start of the line ("'", '"', '(' for $(...), '`') and is updated in place,
    so a string that spans lines is followed across them."""
    starts, i = [], 0
    while i < len(line):
        top, c = (stack[-1] if stack else None), line[i]
        if top == "'":
            if c == "'": stack.pop()
            i += 1; continue
        if c == '\\':
            i += 2; continue
        if top == '"':
            if c == '"': stack.pop()
            elif line.startswith('$(', i): stack.append('('); i += 2; continue
            elif c == '`': stack.append('`')
            i += 1; continue
        if c in '\'"': stack.append(c)
        elif line.startswith('$(', i): stack.append('('); i += 2; continue
        elif c == ')' and top == '(': stack.pop()
        elif c == '`': stack.pop() if top == '`' else stack.append('`')
        elif c == '#' and (i == 0 or line[i - 1] in ' \t;&|('): break
        elif line.startswith('<<<', i): i += 3; continue
        elif line.startswith('<<', i):
            m = HEREDOC.match(line, i)
            if m:
                starts.append((m.group(1) == '-', bool(m.group(2) or m.group(3)), m.group(4)))
                i = m.end(); continue
        i += 1
    return starts

def strip_heredocs(cmd):
    """The command without its heredoc bodies, and the unquoted bodies, which the shell still expands.

    Raises ValueError for a heredoc whose end line never comes, since the shell and this guard would disagree."""
    out, bodies, lines, stack, i = [], [], cmd.split('\n'), [], 0
    while i < len(lines):
        line = lines[i]; out.append(line); i += 1
        for dash, quoted, word in heredoc_starts(line, stack):
            body = []
            while i < len(lines) and (lines[i].lstrip('\t') if dash else lines[i]) != word:
                body.append(lines[i]); i += 1
            if i == len(lines): raise ValueError(f'heredoc {word} has no end line')
            out.append(lines[i]); i += 1
            if not quoted: bodies.append('\n'.join(body))
    return '\n'.join(out), bodies

def subshells(cmd):
    return re.findall(r'\$\(([^()]*)\)', cmd) + re.findall(r'`([^`]*)`', cmd)

def segments(cmd):
    # A newline ends a command, as ; does; a backslash before it continues the line, as in the shell.
    lex = shlex.shlex(cmd.replace('\\\n', ''), posix=True, punctuation_chars=';&|()\n')
    lex.whitespace = ' \t\r'
    # No comment handling: shlex would swallow the newline after a comment. A # is read as a word instead, which only
    # errs towards blocking.
    lex.commenters = ''
    lex.whitespace_split = True
    seg = []
    for tok in lex:
        if tok and set(tok) <= set(';&|()\n'):
            if seg: yield seg
            seg = []
        else:
            seg.append(tok)
    if seg: yield seg

FLOW_BAD_FLAGS = {'--push', '--pushtag', '--no-keep', '--no-keepremote'}
HOOK_SKIP = 'skipping the git hooks'
UNREADABLE = 'a command the guard cannot read'
BRACES = re.compile(r'\{[^{}]*(,|\.\.)[^{}]*\}')
REF_MOVE = 'moving git refs by hand'
VERIFYING_COMMANDS = {'commit', 'merge', 'cherry-pick', 'revert', 'am', 'rebase', 'pull'}
COMMIT_VALUE_OPTS = {'-m', '-F', '-C', '-c', '-t', '--message', '--file', '--template', '--author', '--date'}

def skips_hooks_env(assignments):
    for a in assignments:
        name, _, value = a.partition('=')
        if name == 'LEFTHOOK' and value.strip('\'"').lower() in ('0', 'false', 'no', 'off'): return True
        if name == 'LEFTHOOK_EXCLUDE': return True
    return False


def check_flow(args):
    words = [a for a in args if not a.startswith('-')]
    flags = {a.split('=', 1)[0] for a in args if a.startswith('-')}
    if 'publish' in words: return 'git flow publish'
    if '--no-verify' in flags: return f'{HOOK_SKIP}: git flow with --no-verify'
    bad = flags & FLOW_BAD_FLAGS
    if bad: return f'git flow with {sorted(bad)[0]}'
    if 'delete' in words: return 'git flow delete (finish removes a finished branch)'
    if 'finish' in words:
        if words[0] in ('release', 'hotfix', 'support'): return f'git flow {words[0]} finish (the founder releases)'
        if '--abort' in flags: return None
        if '--no-push' not in flags or not (flags & {'--keepremote', '--keep', '-k'}):
            return 'git flow finish without --no-push and --keepremote'
    return None

def check_git(args):
    i = 0
    while i < len(args) and args[i].startswith('-'):
        opt = args[i].split('=', 1)[0]
        if opt == '-c' and i + 1 < len(args) and args[i + 1].lower().startswith('core.hookspath'):
            return f'{HOOK_SKIP}: core.hooksPath'
        i += 2 if (opt in GIT_OPTS_WITH_VALUE and '=' not in args[i]) else 1
    if i >= len(args): return None
    sub, rest = args[i], args[i + 1:]
    if sub in ('push', 'send-pack'): return f'git {sub}'
    if sub == 'subtree' and 'push' in rest: return 'git subtree push'
    if sub == 'credential': return 'git credential'
    if sub == 'update-ref': return f'{REF_MOVE}: git update-ref'
    if sub == 'fetch' and any('refs/remotes' in t or t == '.' for t in rest): return f'{REF_MOVE}: git fetch into refs/remotes'
    if sub == 'flow': return check_flow(rest)
    if sub in VERIFYING_COMMANDS:
        if '--no-verify' in rest: return f'{HOOK_SKIP}: git {sub} --no-verify'
        if sub == 'commit':
            prev = ''
            for t in rest:
                if t.startswith('-') and not t.startswith('--') and 'n' in t[1:] and prev not in COMMIT_VALUE_OPTS:
                    return f'{HOOK_SKIP}: git commit -n'
                prev = t
    if sub == 'remote' and rest and rest[0] in ('add', 'set-url', 'rename', 'remove', 'rm'): return f'git remote {rest[0]}'
    if sub == 'config' and any(t.startswith(('alias.', 'credential', 'remote.', 'url.', 'pushurl', 'core.sshcommand', 'core.hookspath')) or 'pushurl' in t.lower() for t in rest):
        return 'git config change to aliases, remotes, credentials or hooks'
    if sub == 'config' and any(t.lower().startswith('core.hookspath') for t in rest):
        return f'{HOOK_SKIP}: core.hooksPath'
    if sub == 'config' and any(re.match(r'^gitflow\..*(push|keep|deleteremote)', t, re.I) for t in rest):
        return 'git config change to git-flow push or branch-keeping settings'
    return None

def check_gh(args):
    if not args: return None
    group = args[0]
    if group == 'api':
        joined = ' '.join(args[1:])
        if re.search(r'(?:^|\s)(?:-X\s*|--method[=\s]+)(POST|PUT|PATCH|DELETE)\b', joined, re.I): return 'gh api with a write method'
        if any(a in ('-f', '-F', '--field', '--raw-field', '--input') or a.startswith(('--field=', '--raw-field=', '--input=')) for a in args[1:]):
            return 'gh api with fields (implies POST)'
        return None
    if group in GH_WRITE:
        allowed = GH_WRITE[group]
        sub = args[1] if len(args) > 1 else ''
        if allowed is None or sub in allowed: return f'gh {group} {sub}'.strip()
    return None

def check(cmd, depth=0):
    if depth > 4: return None
    try:
        cmd, bodies = strip_heredocs(cmd)
        segs = list(segments(cmd))
    except ValueError:
        return UNREADABLE
    for inner in subshells(cmd) + [s for body in bodies for s in subshells(body)]:
        r = check(inner, depth + 1)
        if r: return r
    for seg in segs:
        toks = list(seg)
        assigns = []
        while toks and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', toks[0]): assigns.append(toks.pop(0))
        while toks and os.path.basename(toks[0]) in WRAPPERS:
            toks.pop(0)
            while toks and (toks[0].startswith('-') or re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', toks[0]) or re.match(r'^\d+[smhd]?$', toks[0])):
                t = toks.pop(0)
                if '=' in t and not t.startswith('-'): assigns.append(t)
        if not toks: continue
        # The shell expands {a,b} and {1..3} after this guard reads the words: git {push,--no-verify} is git push.
        if any(BRACES.search(t) for t in toks) and any(re.search(r'\b(git|gh|git-flow)\b', t) for t in toks):
            return f'{UNREADABLE}: a brace expansion beside git'
        prog = os.path.basename(toks[0])
        if prog == 'export' and skips_hooks_env(toks[1:]): return f'{HOOK_SKIP}: exported LEFTHOOK setting'
        if prog in ('git', 'git-flow') and skips_hooks_env(assigns): return f'{HOOK_SKIP}: LEFTHOOK setting'
        if prog == 'git':
            r = check_git(toks[1:])
        elif prog == 'git-flow':
            r = check_flow(toks[1:])
        elif prog == 'gh':
            r = check_gh(toks[1:])
        elif prog in SHELLS:
            r = None
            for j, t in enumerate(toks[1:], start=1):
                if t.startswith('-') and 'c' in t[1:] and j + 1 < len(toks):
                    r = check(toks[j + 1], depth + 1); break
        elif prog == 'eval':
            r = check(' '.join(toks[1:]), depth + 1)
        elif prog == 'xargs':
            r = 'xargs running git push' if ('git' in toks and 'push' in toks) else None
        else:
            r = None
        if r: return r
    return None

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get('tool_name') != 'Bash': return 0
    reason = check((data.get('tool_input') or {}).get('command') or '')
    if reason == UNREADABLE:
        print(f'Blocked: {reason} (an unclosed quote, $\'...\' quoting, or a heredoc without its end line). '
              f'Rewrite it with plain quotes, or pass text through a quoted heredoc (<<\'EOF\').', file=sys.stderr)
        return 2
    if reason and reason.startswith(REF_MOVE):
        print(f'Blocked: {reason}. Coding agents never move refs directly in codetrail: origin/develop must stay '
              f'what the founder pushed, because the security review trusts it (AGENTS.md, Workflow).', file=sys.stderr)
        return 2
    if reason and reason.startswith(HOOK_SKIP):
        print(f'Blocked: {reason}. Coding agents never skip the git hooks in codetrail '
              f'(AGENTS.md, Never do, rule 8). If a hook fails, fix the cause; if the hook itself is wrong, '
              f'say so and stop. Do not try another form of the same command.', file=sys.stderr)
        return 2
    if reason:
        print(f'Blocked: {reason}. Coding agents never push or write to GitHub in codetrail '
              f'(AGENTS.md, Never do, rule 1). Commit and finish features locally, then stop; the founder pushes. '
              f'Do not try another form of the same command.', file=sys.stderr)
        return 2
    return 0

if __name__ == '__main__':
    sys.exit(main())
