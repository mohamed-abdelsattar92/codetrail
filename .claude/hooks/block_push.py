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
HISTORY = 'rewriting history, deleting branches or tags, or releasing'
# Configuration that can turn a command into a push, reach credentials or move remotes (AGENTS.md, rules 1 and 2).
RISKY_CONFIG = ('alias.', 'credential', 'remote.', 'url.', 'pushurl', 'core.sshcommand', 'core.hookspath', 'include.',
                'includeif.')
# Environment variables that configure or redirect git (security review of Phase 0, finding 1).
# Any GIT_* variable, or an editor or pager, could configure git or make it run a program (second review, finding 1).
RISKY_GIT_ENV = re.compile(r'^(GIT_\w+|EDITOR|VISUAL|PAGER)$')
# Programs that write to a file named in their arguments; with a path in .git/ they could move refs or hooks.
WRITERS = {'cp', 'mv', 'tee', 'ln', 'rm', 'install', 'truncate', 'dd', 'touch', 'sed', 'perl', 'python3', 'python',
           'chmod', 'rsync'}
TAG_READ_OPTIONS = {'-l', '--list', '-n', '--contains', '--no-contains', '--points-at', '--merged', '--no-merged',
                    '--sort', '--format', '--column', '--no-column', '-i', '--ignore-case'}
BRANCH_WRITE_OPTIONS = {'-d', '-D', '--delete', '-f', '--force', '-m', '-M', '--move', '-c', '-C', '--copy',
                        '--set-upstream-to', '-u', '--unset-upstream', '--edit-description'}
PROTECTED_BRANCHES = {'main'}
BRANCH_LONG_OPTIONS = ('--delete', '--force', '--move', '--copy', '--set-upstream-to', '--unset-upstream',
                       '--edit-description', '--remotes', '--all', '--list', '--show-current', '--verbose', '--contains',
                       '--merged', '--no-merged', '--sort', '--format', '--color', '--no-color', '--track', '--no-track')
RESET_LONG_OPTIONS = ('--hard', '--merge', '--keep', '--soft', '--mixed', '--quiet', '--patch')
TAG_LONG_OPTIONS = ('--list', '--contains', '--no-contains', '--points-at', '--merged', '--no-merged', '--sort',
                    '--format', '--column', '--no-column', '--annotate', '--sign', '--force', '--delete', '--verify',
                    '--message', '--file', '--ignore-case', '--create-reflog', '--local-user', '--cleanup', '--edit')
# Options under which git tag only lists (git's documentation: they imply --list).
TAG_LIST_OPTIONS = {'-l', '--list', '-n', '--contains', '--no-contains', '--points-at', '--merged', '--no-merged'}
PROTECTED_REFS = {'develop', 'main'}
# git config may write only these keys (third review of Phase 0): any other key could make git run a program
# (core.fsmonitor, core.editor, diff.external...), push, or reach credentials.
CONFIG_WRITABLE = re.compile(
    r'^(user\.(name|email)|gitflow\.(version|initialized'
    r'|branch\.[a-z]+\.(type|parent|startpoint|prefix|upstreamstrategy|downstreamstrategy|autoupdate|tag)))$', re.I)
# Values after these options are messages, not commands; a $(...) in them is still checked as a subshell.
MESSAGE_OPTIONS = {'-m', '-M', '-F', '--message', '--file'}
CONFIG_SECTION_WORDS = {'--rename-section', '--remove-section', 'rename-section', 'remove-section'}
CONFIG_SUBCOMMANDS = {'set', 'unset', 'rename-section', 'remove-section', 'edit', 'get', 'list'}
CONFIG_WRITE_WORDS = {'--add', '--replace-all', '--unset', '--unset-all', '--rename-section', '--remove-section', '-e',
                      '--edit', 'set', 'unset', 'rename-section', 'remove-section', 'edit'}


def expand_options(args, long_options):
    """Options as git reads them: bundled short options split (-df is -d -f), long ones expanded from a prefix."""
    expanded = []
    for arg in args:
        if arg == '--': break
        name = arg.split('=', 1)[0]
        if name.startswith('--'):
            matches = [option for option in long_options if option.startswith(name)]
            expanded.extend(matches or [name])
        elif name.startswith('-') and len(name) > 1:
            if name[1:].isdigit(): expanded.append(name)
            else: expanded.extend(f'-{letter}' for letter in name[1:])
    return expanded
COMMIT_VALUE_OPTS = {'-m', '-F', '-C', '-c', '-t', '--message', '--file', '--template', '--author', '--date'}

def skips_hooks_env(assignments):
    for a in assignments:
        name, _, value = a.partition('=')
        if name == 'LEFTHOOK' and value.strip('\'"').lower() in ('0', 'false', 'no', 'off'): return True
        if name == 'LEFTHOOK_EXCLUDE': return True
    return False


def in_git_folder(token):
    """A path in .git/ or in git's own configuration files (~/.gitconfig, ~/.config/git/)."""
    path = re.sub(r'^\d*[<>]+&?', '', token).strip('\'"')
    return bool(re.search(r'(^|/)\.git(/|$)|(^|/)\.gitconfig$|(^|/)\.config/git(/|$)', path))


def unclear_target(target):
    """A protected path, or one only the shell can work out: an expansion, or a glob that could reach a dot folder."""
    glob = re.search(r'[*?\[]', target) and (target.startswith(('.', '~')) or '/.' in target)
    return in_git_folder(target) or '$' in target or '`' in target or bool(glob)


def writes_into_git_folder(seg, prog):
    """A redirection into .git/ (or to a target only the shell can work out), a writing program given a path there,
    or a cd into .git, after which any relative write lands there (security reviews of Phase 0)."""
    if prog in ('cd', 'pushd') and any(unclear_target(t) for t in seg[1:]):
        return True
    for i, tok in enumerate(seg):
        if '>' not in tok: continue
        target = tok.rsplit('>', 1)[1].lstrip('&|')
        if not target and i + 1 < len(seg): target = seg[i + 1]
        if unclear_target(target): return True
    return prog in WRITERS and any(unclear_target(t) for t in seg[1:])


def current_branch():
    """The branch checked out where the hook runs, or None."""
    import subprocess
    result = subprocess.run(['git', 'symbolic-ref', '--short', '-q', 'HEAD'], capture_output=True, text=True)
    return result.stdout.strip() or None


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
        value = args[i].split('=', 1)[1] if '=' in args[i] else (args[i + 1] if i + 1 < len(args) else '')
        if opt == '-c' and value.lower().startswith('core.hookspath'):
            return f'{HOOK_SKIP}: core.hooksPath'
        if opt == '-c':
            return f'git -c {value.split("=", 1)[0]} (configuration on the command line)'
        if opt == '--config-env':
            return 'git --config-env'
        if opt in ('--work-tree', '--git-dir'):
            return f'git {opt} (another repository or working tree)'
        i += 2 if (opt in GIT_OPTS_WITH_VALUE and '=' not in args[i]) else 1
    if i >= len(args): return None
    sub, rest = args[i], args[i + 1:]
    if '--no-verify' in rest: return f'{HOOK_SKIP}: git {sub} --no-verify'
    if any(a.startswith('--output') for a in rest): return f'{REF_MOVE}: git {sub} --output (git writing a file)'
    if sub == 'checkout-index': return f'{REF_MOVE}: git checkout-index (git writing files)'
    if sub == 'config' and any(a in CONFIG_SECTION_WORDS for a in rest): return 'git config renaming or removing a section'
    if sub in ('push', 'send-pack'): return f'git {sub}'
    if sub == 'symbolic-ref' and len([a for a in rest if not a.startswith('-')]) > 1: return f'{REF_MOVE}: git symbolic-ref'
    if sub in ('filter-branch', 'filter-repo', 'rebase', 'replace'): return f'{HISTORY}: git {sub}'
    if sub == 'reset' and set(expand_options(rest, RESET_LONG_OPTIONS)) & {'--hard', '--merge', '--keep'}:
        return f'{HISTORY}: git reset --hard'
    if sub == 'tag' and rest and not (set(expand_options(rest, TAG_LONG_OPTIONS)) & TAG_LIST_OPTIONS):
        return f'{HISTORY}: git tag (creating, moving or deleting a tag)'
    if sub == 'branch' and set(expand_options(rest, BRANCH_LONG_OPTIONS)) & BRANCH_WRITE_OPTIONS:
        return f'{HISTORY}: git branch deleting, moving or forcing a branch'
    if sub in ('checkout', 'switch') and any(a in PROTECTED_BRANCHES for a in rest): return f'{HISTORY}: checking out main'
    if sub in ('checkout', 'switch') and any(a in ('-B', '-C', '--force-create') for a in rest) and any(a in PROTECTED_REFS for a in rest):
        return f'{HISTORY}: resetting develop or main with {sub}'
    if sub in ('checkout', 'switch') and any(a == '-' or re.fullmatch(r'@\{-\d+\}', a) for a in rest):
        return f'{HISTORY}: checking out the previous branch (it could be main)'
    if sub in ('commit', 'reset') and current_branch() in PROTECTED_REFS:
        before_paths = rest[:rest.index('--')] if '--' in rest else rest
        moves = [a for a in before_paths if not a.startswith('-') and a != 'HEAD']
        if (sub == 'commit' and '--amend' in rest) or (sub == 'reset' and moves):
            return f'{HISTORY}: git {sub} {"--amend" if sub == "commit" else moves[0]} on {current_branch()}'
    if sub == 'worktree' and rest[:1] == ['add'] and any(a in PROTECTED_BRANCHES for a in rest): return f'{HISTORY}: a worktree on main'
    if sub in ('fetch', 'pull'):
        sources = [a for a in rest if not a.startswith('-')]
        if sources and not re.fullmatch(r'[A-Za-z0-9_-]+', sources[0]): return f'{REF_MOVE}: git fetch from {sources[0]}'
        if any(':' in a for a in sources[1:]): return f'{REF_MOVE}: git fetch into a named ref'
    if sub == 'merge' and any(a in PROTECTED_BRANCHES for a in rest): return f'{HISTORY}: merging into main'
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
    operands = [a for a in rest if not a.startswith('-')]
    reads_config = (sub == 'config' and any(a in ('--get', '--get-all', '--get-regexp', '-l', '--list') for a in rest)
                    and not any(a in CONFIG_WRITE_WORDS for a in rest) and len(operands) <= 2)
    if sub == 'config' and not reads_config:
        keys = operands[1:] if operands[:1] and operands[0] in CONFIG_SUBCOMMANDS else operands
        writes = any(a in CONFIG_WRITE_WORDS for a in rest) or len(keys) >= 2
        if writes and keys and keys[0].lower().startswith('core.hookspath'): return f'{HOOK_SKIP}: core.hooksPath'
        if writes and not (keys and CONFIG_WRITABLE.match(keys[0])):
            return f'git config writing {keys[0] if keys else "settings"} (agents may set only user.* and git-flow settings)'
    if sub == 'config' and not reads_config and any(t.lower().startswith(RISKY_CONFIG) or 'pushurl' in t.lower() for t in rest):
        return 'git config change to aliases, remotes, credentials or hooks'
    if sub == 'config' and not reads_config and any(t.lower().startswith('core.hookspath') for t in rest):
        return f'{HOOK_SKIP}: core.hooksPath'
    if sub == 'config' and not reads_config and any(re.match(r'^gitflow\..*(push|keep|deleteremote)', t, re.I) for t in rest):
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
    if depth > 4: return UNREADABLE
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
        # Tool runners run the command after them: mise exec [options] -- <command>, pnpm exec <command>.
        if toks[:1] == ['mise'] and ('exec' in toks or 'x' in toks):
            rest_of_mise = toks[toks.index('exec' if 'exec' in toks else 'x') + 1:]
            command_flag = next((i for i, a in enumerate(rest_of_mise) if a in ('-c', '--command')), None)
            if command_flag is not None:
                if command_flag + 1 >= len(rest_of_mise): return UNREADABLE
                r = check(rest_of_mise[command_flag + 1], depth + 1)
                if r: return r
                continue
            if '--' not in rest_of_mise: return UNREADABLE
            toks = rest_of_mise[rest_of_mise.index('--') + 1:]
        elif toks[:1] == ['pnpm'] and 'exec' in toks:
            shell_mode = any(a in ('-c', '--shell-mode') for a in toks)
            rest_of_pnpm = [a for a in toks[toks.index('exec') + 1:] if a not in ('-c', '--shell-mode')]
            if shell_mode:
                r = check(' '.join(rest_of_pnpm), depth + 1)
                if r: return r
                continue
            toks = rest_of_pnpm
        if not toks: continue
        # The shell expands {a,b} and {1..3} after this guard reads the words: git {push,--no-verify} is git push.
        if any(BRACES.search(t) for t in toks) and any(re.search(r'\b(git|gh|git-flow)\b', t) for t in toks):
            return f'{UNREADABLE}: a brace expansion beside git'
        prog = os.path.basename(toks[0])
        if prog in ('git', 'git-flow', 'gh'):
            for j, word in enumerate(toks[1:], start=1):
                is_message = toks[j - 1] in MESSAGE_OPTIONS or word.split('=', 1)[0] in ('--message', '--file')
                if ('$' in word or '`' in word) and not is_message:
                    return f'{UNREADABLE}: a shell expansion ({word}) beside {prog}'
        if prog == 'export' and skips_hooks_env(toks[1:]): return f'{HOOK_SKIP}: exported LEFTHOOK setting'
        if prog in ('git', 'git-flow') and skips_hooks_env(assigns): return f'{HOOK_SKIP}: LEFTHOOK setting'
        exported = [a for a in (toks[1:] if prog == 'export' else []) + assigns if RISKY_GIT_ENV.match(a.split('=', 1)[0])]
        if exported: return f'git configured through {exported[0].split("=", 1)[0]}'
        if writes_into_git_folder(seg, prog): return f'{REF_MOVE}: writing into .git/'
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
        print('Blocked: the push guard could not read its input, so it refuses the call.', file=sys.stderr)
        return 2
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
    if reason and reason.startswith(HISTORY):
        print(f'Blocked: {reason}. Coding agents never rewrite history, delete branches or tags, touch main, or '
              f'release in codetrail (AGENTS.md, Never do, rules 3 and 4). Finish features with git flow and stop; '
              f'the founder releases. Do not try another form of the same command.', file=sys.stderr)
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
    try:
        sys.exit(main())
    except Exception as error:  # fail closed: an error in the guard must block (security review of Phase 0, finding 4)
        print(f'Blocked: the push guard failed ({type(error).__name__}), so it refuses the call.', file=sys.stderr)
        sys.exit(2)
