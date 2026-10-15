#!/usr/bin/env python3
"""Refuse failure-masking wrappers around required CI commands.

`docs/standards/testing.md` forbids `|| true` in CI, and CLAUDE.md principle 6
forbids fake-green status, but only the three documented local gauntlets were
checked, and only for the literal `|| true`. Appending `|| true` to the
validation job's `node --test` command left every wiring test green, and so did
`|| :`, `|| exit 0`, `| tee` without `pipefail`, `set +e`, a step-level
`continue-on-error`, or `cmd && echo ok` followed by another line — bash's
errexit ignores a failure anywhere in an `&&` / `||` list except its last
command.

This guard parses every GitHub Actions `run:` block (workflows and composite
actions) and the three gauntlet blocks with a small POSIX-shell lexer, so
comments, quoted prose, `python -c` programs and here-documents are never
mistaken for commands. For every *required* command — `pytest`, `node --test`,
`ruff`, `mypy`, the `forgelm` CLI and the `tools/` guards — it proves that a
non-zero exit fails the step (or the gauntlet chain):

- the command is not followed by an `||` fallback that can exit 0, does not feed
  a pipe without `pipefail`, is not negated, backgrounded, wrapped in a command
  substitution or a shell function, and is not run conditionally;
- where its failure does not end the shell (errexit is off, or ignored inside an
  `&&` / `||` list), nothing after it decides the exit status instead;
- the step has no `continue-on-error` and runs under a shell this guard can
  model (bash or sh).

Independently of required commands, a `|| true` / `|| :` / `|| exit 0` fallback
is refused everywhere except the documented scanner + severity-helper pattern:
`bandit` or `pip-audit` writing a report, `|| true`, and immediately after it
`tools/check_bandit.py` / `tools/check_pip_audit.py` reading that same report,
itself a required command. A helper that reads another file — or is missing —
does not gate the scanner, so the exception does not apply.

Exit codes follow the tools/ contract: 0 clean, 1 findings.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple, Union

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GAUNTLET_DOCS = ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md")

# Same guard-name grammar as tests/test_guard_wiring.py's _GUARD_NAME_RE.
_GUARD_SCRIPT_RE = re.compile(r"(?:^|/)tools/(check_[a-z0-9_]+\.py|update_site_version\.py)$")
_PYTHON_RE = re.compile(r"python(?:3(?:\.\d{1,2})?)?")
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}(?:\[[^\]\n]{0,64}\])?\+?=")
_GATE_MODULES = ("pytest", "mypy", "ruff", "forgelm")
# Each scanner's severity helper, with the helper's value-taking options so its
# positional report argument is found wherever the options are placed.
_SCANNER_HELPERS = {
    "bandit": ("tools/check_bandit.py", ()),
    "pip-audit": ("tools/check_pip_audit.py", ("--ignores",)),
}
_SHELLS = ("bash", "sh", "dash", "ksh", "zsh")
# The fragment of each required command kind that ci.yml and every gauntlet must
# contain. A scan that recognises none of them is broken, not clean.
_EXPECTED_KINDS = ("pytest", "node --test", "ruff", "tools/check_")

_OPERATORS = ("&&", "||", ";;", "|&", ";", "|", "&", "(", ")")
_REDIRECTIONS = ("&>>", "&>", "<<<", "<<-", "<<", ">>", ">|", "<>", ">&", "<&", "<", ">")
_METACHARACTERS = frozenset(" \t\n;&|()<>")
_NOOP_FALLBACKS = (("true",), (":",), ("/bin/true",), ("/usr/bin/true",), ("exit", "0"))

TERMINATES = "terminates"  # every failure of a required command inside ends the shell
STATUS = "status"  # some failure only leaves a non-zero exit status behind


class ShellParseError(Exception):
    """A run block this guard cannot parse, reported as a finding (fail closed)."""

    def __init__(self, message: str, line: int) -> None:
        super().__init__(f"line {line}: {message}")


# ── Lexer ────────────────────────────────────────────────────────────────


@dataclass
class _Word:
    text: str  # value after quote removal; expansions are kept as written
    raw: str  # source spelling, used to tell `NAME=value` from `"NAME=value"`
    line: int
    subs: List["_Script"] = field(default_factory=list)  # $(...), `...`, <(...)


@dataclass
class _Token:
    kind: str  # word | op | redir | nl | eof
    value: str
    line: int
    start: int
    end: int
    word: Optional[_Word] = None
    heredoc: Optional[str] = None


class _Lexer:
    """Tokenise a POSIX-shell subset: quoting, escapes, line continuations,
    comments, operators, redirections, here-documents and nested substitutions."""

    def __init__(self, src: str, start: int = 0, line: int = 1) -> None:
        self.src = src
        self.i = start
        self.line = line
        self.tokens: List[_Token] = []
        self.pending_heredocs: List[Tuple[_Token, str, bool]] = []
        self.heredoc_delimiter: Optional[Tuple[_Token, bool]] = None

    def run(self, closing_paren: bool = False) -> int:
        """Tokenise to the end, or to the ``)`` closing a ``$(`` substitution."""
        depth = 0
        while True:
            self._skip_blanks()
            if self.i >= len(self.src):
                if closing_paren:
                    raise ShellParseError("unterminated $( command substitution", self.line)
                if self.pending_heredocs:
                    raise ShellParseError("unterminated here-document", self.line)
                self._emit("eof", "", self.i, self.i)
                return self.i
            char = self.src[self.i]
            if char == "\n":
                self._emit("nl", "\n", self.i, self.i + 1)
                self.i += 1
                self.line += 1
                self._read_heredoc_bodies()
            elif char == "#":
                while self.i < len(self.src) and self.src[self.i] != "\n":
                    self.i += 1
            elif closing_paren and char == ")" and depth == 0:
                self._emit("eof", "", self.i, self.i)
                return self.i + 1
            elif self.src.startswith(("<(", ">("), self.i):
                self._read_word()
            else:
                matched = self._operator()
                if matched == "(":
                    depth += 1
                elif matched == ")":
                    depth -= 1
                elif matched is None:
                    self._read_word()

    def _emit(self, kind: str, value: str, start: int, end: int, word: Optional[_Word] = None) -> _Token:
        token = _Token(kind, value, self.line, start, end, word)
        self.tokens.append(token)
        return token

    def _skip_blanks(self) -> None:
        while self.i < len(self.src):
            if self.src[self.i] in " \t\r":
                self.i += 1
            elif self.src.startswith("\\\n", self.i):
                self.i += 2
                self.line += 1
            else:
                return

    def _operator(self) -> Optional[str]:
        for redirection in _REDIRECTIONS:
            if self.src.startswith(redirection, self.i):
                token = self._emit("redir", redirection, self.i, self.i + len(redirection))
                self.i += len(redirection)
                if redirection in ("<<", "<<-"):
                    self.heredoc_delimiter = (token, redirection == "<<-")
                return redirection
        for operator in _OPERATORS:
            if self.src.startswith(operator, self.i):
                self._emit("op", operator, self.i, self.i + len(operator))
                self.i += len(operator)
                return operator
        return None

    def _read_heredoc_bodies(self) -> None:
        for token, delimiter, strip_tabs in self.pending_heredocs:
            body: List[str] = []
            while True:
                if self.i >= len(self.src):
                    raise ShellParseError(f"here-document {delimiter!r} has no closing line", self.line)
                end = self.src.find("\n", self.i)
                end = len(self.src) if end < 0 else end
                text = self.src[self.i : end]
                self.i = min(end + 1, len(self.src))
                self.line += 1
                if (text.lstrip("\t") if strip_tabs else text) == delimiter:
                    break
                body.append(text)
            token.heredoc = "\n".join(body) + "\n"
        self.pending_heredocs = []

    def _read_word(self) -> None:
        start, line = self.i, self.line
        text: List[str] = []
        subs: List[_Script] = []
        src = self.src
        while self.i < len(src):
            char = src[self.i]
            if src.startswith(("<(", ">("), self.i) and self.i == start:
                subs.append(self._substitution(self.i + 2))
                text.append(src[start : self.i])
                continue
            if char == "(" and _ASSIGNMENT_RE.fullmatch(src[start : self.i]):
                text.append(self._balanced(self.i, "(", ")"))  # bash array assignment: NAME=(...)
                continue
            if char in _METACHARACTERS:
                break
            if char == "\\":
                if src.startswith("\\\n", self.i):
                    self.line += 1
                else:
                    text.append(src[self.i + 1 : self.i + 2])
                self.i += 2
            elif char == "'":
                end = src.find("'", self.i + 1)
                if end < 0:
                    raise ShellParseError("unterminated single quote", line)
                text.append(src[self.i + 1 : end])
                self.line += src.count("\n", self.i, end)
                self.i = end + 1
            elif char == '"':
                self._double_quoted(text, subs, line)
            elif char == "$" or char == "`":
                self._expansion(text, subs)
            else:
                text.append(char)
                self.i += 1
        raw = src[start : self.i]
        word = _Word("".join(text), raw, line, subs)
        self._emit("word", word.text, start, self.i, word)
        if self.heredoc_delimiter is not None:
            redirection, strip_tabs = self.heredoc_delimiter
            self.pending_heredocs.append((redirection, word.text, strip_tabs))
            self.heredoc_delimiter = None

    def _double_quoted(self, text: List[str], subs: List["_Script"], line: int) -> None:
        src = self.src
        self.i += 1
        while True:
            if self.i >= len(src):
                raise ShellParseError("unterminated double quote", line)
            char = src[self.i]
            if char == '"':
                self.i += 1
                return
            if char == "\\" and src[self.i + 1 : self.i + 2] in ("$", "`", '"', "\\", "\n"):
                if src[self.i + 1] == "\n":
                    self.line += 1
                else:
                    text.append(src[self.i + 1])
                self.i += 2
            elif char == "$" or char == "`":
                self._expansion(text, subs)
            else:
                if char == "\n":
                    self.line += 1
                text.append(char)
                self.i += 1

    def _expansion(self, text: List[str], subs: List["_Script"]) -> None:
        src, start = self.src, self.i
        if src.startswith("$((", start):
            text.append(self._balanced(start + 1, "(", ")", prefix="$"))
        elif src.startswith("$(", start):
            subs.append(self._substitution(start + 2))
            text.append(src[start : self.i])
        elif src.startswith("${", start):
            text.append(self._balanced(start + 1, "{", "}", prefix="$"))
        elif src.startswith("$'", start):
            end = start + 2
            while end < len(src) and src[end] != "'":
                end += 2 if src[end] == "\\" else 1
            if end >= len(src):
                raise ShellParseError("unterminated $'...' quote", self.line)
            text.append(src[start + 2 : end])
            self.i = end + 1
        elif src[start] == "`":
            end = start + 1
            while end < len(src) and src[end] != "`":
                end += 2 if src[end] == "\\" else 1
            if end >= len(src):
                raise ShellParseError("unterminated backquote", self.line)
            inner = re.sub(r"\\([`$\\])", r"\1", src[start + 1 : end])
            subs.append(_parse(inner, self.line))
            self.line += src.count("\n", start, end)
            self.i = end + 1
            text.append(src[start : self.i])
        else:
            text.append("$")
            self.i += 1

    def _substitution(self, body_start: int) -> "_Script":
        """Parse the script inside ``$( ... )`` / ``<( ... )``; leave ``self.i`` after its ``)``."""
        inner = _Lexer(self.src, body_start, self.line)
        self.i = inner.run(closing_paren=True)
        self.line = inner.line
        return _Parser(inner.tokens).parse()

    def _balanced(self, open_at: int, opening: str, closing: str, prefix: str = "") -> str:
        """Consume a bracketed run (``$((..))``, ``${..}``, ``NAME=(..)``), skipping quoted text."""
        src, depth, pos = self.src, 0, open_at
        while pos < len(src):
            char = src[pos]
            if char in "'\"":
                end = src.find(char, pos + 1)
                pos = len(src) if end < 0 else end
            elif char == "\\":
                pos += 1
            elif char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    break
            pos += 1
        if pos >= len(src):
            raise ShellParseError(f"unterminated {prefix}{opening}", self.line)
        self.line += src.count("\n", self.i, pos)
        value = src[self.i : pos + 1]
        self.i = pos + 1
        return value


# ── Parser ───────────────────────────────────────────────────────────────


@dataclass
class _Redirect:
    op: str
    target: _Word
    fd: Optional[str]
    heredoc: Optional[str]


@dataclass
class _Simple:
    words: List[_Word]
    assignments: List[_Word]
    redirects: List[_Redirect]
    line: int


@dataclass
class _Group:
    body: "_Script"
    subshell: bool
    line: int


@dataclass
class _If:
    conditions: List["_Script"]
    bodies: List["_Script"]
    line: int


@dataclass
class _Loop:
    kind: str  # for | while | until
    condition: Optional["_Script"]
    body: "_Script"
    words: List[_Word]
    line: int


@dataclass
class _Case:
    subject: _Word
    bodies: List["_Script"]
    line: int


@dataclass
class _Function:
    name: str
    body: "_Command"
    line: int


@dataclass
class _Test:  # [[ ... ]] and (( ... )): expressions, never commands
    line: int


_Command = Union[_Simple, _Group, _If, _Loop, _Case, _Function, _Test]


@dataclass
class _Pipeline:
    commands: List[_Command]
    negated: bool


@dataclass
class _AndOr:
    pipelines: List[_Pipeline]
    operators: List[str]


@dataclass
class _Item:
    and_or: _AndOr
    background: bool


@dataclass
class _Script:
    items: List[_Item]


def _parse(src: str, line: int = 1) -> _Script:
    lexer = _Lexer(src, 0, line)
    lexer.run()
    return _Parser(lexer.tokens).parse()


class _Parser:
    """Recursive descent over the POSIX shell grammar (lists, pipelines, compounds)."""

    _RESERVED = ("then", "elif", "else", "fi", "do", "done", "esac", "}", "in")

    def __init__(self, tokens: List[_Token]) -> None:
        self.tokens = tokens
        self.k = 0

    def parse(self) -> _Script:
        script = self._script((), ())
        token = self._peek()
        if token.kind != "eof":
            raise ShellParseError(f"unexpected {token.value!r}", token.line)
        return script

    def _peek(self, offset: int = 0) -> _Token:
        return self.tokens[min(self.k + offset, len(self.tokens) - 1)]

    def _next(self) -> _Token:
        token = self._peek()
        self.k += 1
        return token

    def _is_reserved(self, token: _Token, *values: str) -> bool:
        return token.kind == "word" and token.word is not None and token.word.raw in values

    def _is_op(self, token: _Token, *values: str) -> bool:
        return token.kind == "op" and token.value in values

    def _skip_newlines(self) -> None:
        while self._peek().kind == "nl":
            self.k += 1

    def _expect_reserved(self, value: str) -> None:
        token = self._next()
        if not self._is_reserved(token, value):
            raise ShellParseError(f"expected {value!r}, found {token.value!r}", token.line)

    def _expect_op(self, value: str) -> None:
        token = self._next()
        if not self._is_op(token, value):
            raise ShellParseError(f"expected {value!r}, found {token.value!r}", token.line)

    def _script(self, stop_words: Tuple[str, ...], stop_ops: Tuple[str, ...]) -> _Script:
        items: List[_Item] = []
        while True:
            self._skip_newlines()
            token = self._peek()
            if token.kind == "eof" or self._is_op(token, *stop_ops) or self._is_reserved(token, *stop_words):
                return _Script(items)
            and_or = self._and_or()
            token = self._peek()
            background = self._is_op(token, "&")
            if background or self._is_op(token, ";"):
                self.k += 1
            elif not (
                token.kind in ("nl", "eof") or self._is_op(token, *stop_ops) or self._is_reserved(token, *stop_words)
            ):
                raise ShellParseError(f"unexpected {token.value!r}", token.line)
            items.append(_Item(and_or, background))

    def _and_or(self) -> _AndOr:
        pipelines = [self._pipeline()]
        operators: List[str] = []
        while self._is_op(self._peek(), "&&", "||"):
            operators.append(self._next().value)
            self._skip_newlines()
            pipelines.append(self._pipeline())
        return _AndOr(pipelines, operators)

    def _pipeline(self) -> _Pipeline:
        negated = self._is_reserved(self._peek(), "!")
        if negated:
            self.k += 1
        commands = [self._command()]
        while self._is_op(self._peek(), "|", "|&"):
            self.k += 1
            self._skip_newlines()
            commands.append(self._command())
        return _Pipeline(commands, negated)

    def _command(self) -> _Command:
        token = self._peek()
        if self._is_op(token, "("):
            following = self._peek(1)
            if self._is_op(following, "(") and following.start == token.end:
                return self._arithmetic()
            self.k += 1
            body = self._script((), (")",))
            self._expect_op(")")
            return self._redirected(_Group(body, True, token.line))
        if self._is_reserved(token, "{"):
            self.k += 1
            body = self._script(("}",), ())
            self._expect_reserved("}")
            return self._redirected(_Group(body, False, token.line))
        if self._is_reserved(token, "if"):
            return self._if()
        if self._is_reserved(token, "while", "until", "for"):
            return self._loop()
        if self._is_reserved(token, "case"):
            return self._case()
        if self._is_reserved(token, "[["):
            while not self._is_reserved(self._next(), "]]"):
                if self._peek().kind == "eof":
                    raise ShellParseError("unterminated [[", token.line)
            return _Test(token.line)
        if self._is_reserved(token, "function"):
            self.k += 1
            name = self._next()
            if self._is_op(self._peek(), "(") and self._is_op(self._peek(1), ")"):
                self.k += 2
            self._skip_newlines()
            return _Function(name.value, self._command(), token.line)
        if self._is_reserved(token, *self._RESERVED):
            raise ShellParseError(f"unexpected {token.value!r}", token.line)
        if token.kind == "word" and self._is_op(self._peek(1), "(") and self._is_op(self._peek(2), ")"):
            self.k += 3
            self._skip_newlines()
            return _Function(token.value, self._command(), token.line)
        return self._simple()

    def _arithmetic(self) -> _Test:
        line, depth = self._peek().line, 0
        while True:
            token = self._next()
            if token.kind == "eof":
                raise ShellParseError("unterminated ((", line)
            depth += 1 if self._is_op(token, "(") else -1 if self._is_op(token, ")") else 0
            if depth == 0:
                return _Test(line)

    def _redirected(self, command: _Command) -> _Command:
        while self._peek().kind == "redir":
            self._redirect(None)
        return command

    def _redirect(self, fd: Optional[str]) -> _Redirect:
        operator = self._next()
        target = self._next()
        if target.kind != "word" or target.word is None:
            raise ShellParseError(f"redirection {operator.value!r} has no target", operator.line)
        return _Redirect(operator.value, target.word, fd, operator.heredoc)

    def _simple(self) -> _Simple:
        line = self._peek().line
        words: List[_Word] = []
        assignments: List[_Word] = []
        redirects: List[_Redirect] = []
        while True:
            token = self._peek()
            if token.kind == "redir":
                redirects.append(self._redirect(None))
            elif token.kind == "word" and token.word is not None:
                following = self._peek(1)
                if token.word.raw.isdigit() and following.kind == "redir" and following.start == token.end:
                    self.k += 1
                    redirects.append(self._redirect(token.word.raw))
                    continue
                self.k += 1
                if not words and _ASSIGNMENT_RE.match(token.word.raw):
                    assignments.append(token.word)
                else:
                    words.append(token.word)
            else:
                break
        if not (words or assignments or redirects):
            raise ShellParseError(f"expected a command, found {self._peek().value!r}", line)
        return _Simple(words, assignments, redirects, line)

    def _if(self) -> _If:
        line = self._next().line
        conditions: List[_Script] = []
        bodies: List[_Script] = []
        while True:
            conditions.append(self._script(("then",), ()))
            self._expect_reserved("then")
            bodies.append(self._script(("elif", "else", "fi"), ()))
            token = self._next()
            if self._is_reserved(token, "elif"):
                continue
            if self._is_reserved(token, "else"):
                bodies.append(self._script(("fi",), ()))
                self._expect_reserved("fi")
            elif not self._is_reserved(token, "fi"):
                raise ShellParseError(f"expected 'fi', found {token.value!r}", token.line)
            return self._redirected(_If(conditions, bodies, line))

    def _loop(self) -> _Loop:
        keyword = self._next()
        condition: Optional[_Script] = None
        words: List[_Word] = []
        if keyword.value == "for":
            self._next()  # loop variable
            self._skip_newlines()
            if self._is_reserved(self._peek(), "in"):
                self.k += 1
                while self._peek().kind == "word":
                    words.append(self._next().word)  # type: ignore[arg-type]
            if self._is_op(self._peek(), ";"):
                self.k += 1
            self._skip_newlines()
        else:
            condition = self._script(("do",), ())
        self._expect_reserved("do")
        body = self._script(("done",), ())
        self._expect_reserved("done")
        return self._redirected(_Loop(keyword.value, condition, body, words, keyword.line))

    def _case(self) -> _Case:
        line = self._next().line
        subject = self._next()
        if subject.word is None:
            raise ShellParseError("case needs a subject word", line)
        self._skip_newlines()
        self._expect_reserved("in")
        bodies: List[_Script] = []
        while True:
            self._skip_newlines()
            if self._is_reserved(self._peek(), "esac"):
                self.k += 1
                return self._redirected(_Case(subject.word, bodies, line))
            if self._is_op(self._peek(), "("):
                self.k += 1
            while True:
                if self._next().kind != "word":
                    raise ShellParseError("case pattern expected", line)
                if not self._is_op(self._peek(), "|"):
                    break
                self.k += 1
            self._expect_op(")")
            bodies.append(self._script(("esac",), (";;",)))
            if self._is_op(self._peek(), ";;"):
                self.k += 1


# ── Analysis ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShellOptions:
    """The two shell options that decide whether a failure ends the step."""

    errexit: bool
    pipefail: bool


GITHUB_DEFAULT = ShellOptions(errexit=True, pipefail=False)  # `bash -e {0}` when no shell is set
INTERACTIVE = ShellOptions(errexit=False, pipefail=False)  # a gauntlet pasted into a terminal


_WRAPPER_VALUE_OPTIONS = {
    "timeout": ("-s", "-k", "--signal", "--kill-after"),
    "nice": ("-n",),
    "env": ("-u", "-C", "-S"),
    "sudo": ("-u", "-g", "-C", "-h", "-p"),
    "xargs": ("-a", "-d", "-E", "-I", "-L", "-n", "-P"),
}
_WRAPPERS = ("env", "time", "timeout", "nice", "command", "exec", "nohup", "sudo", "xargs")
_RUNNERS = ("uv", "poetry", "pipenv", "hatch", "pdm")  # `<runner> run CMD` propagates CMD's status


def _argv(command: _Simple) -> List[str]:
    """Words of *command* with transparent wrappers (``env``, ``timeout``, ``uv run``, ...) removed."""
    argv = [word.text for word in command.words]
    while argv:
        program = posixpath.basename(argv[0])
        if program in _RUNNERS and argv[1:2] == ["run"]:
            argv = argv[2:]
            while argv and argv[0].startswith("-"):
                argv = argv[1:]
            continue
        if program not in _WRAPPERS:
            return argv
        if program == "command" and argv[1:2] in (["-v"], ["-V"]):
            return []  # a lookup: `command -v pytest` runs nothing
        argv = argv[1:]
        while argv and (argv[0].startswith("-") or (program == "env" and "=" in argv[0])):
            consumes = argv[0] in _WRAPPER_VALUE_OPTIONS.get(program, ())
            argv = argv[2:] if consumes else argv[1:]
        if program == "timeout" and argv:
            argv = argv[1:]  # the duration
    return argv


def _python_target(args: List[str]) -> Tuple[str, str]:
    """``("module", name)``, ``("script", path)`` or ``("code", "")`` for a Python argv tail."""
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if not arg.startswith("-") or arg == "-":
            return ("script", arg) if arg != "-" else ("code", "")
        if arg.startswith("--"):
            continue
        # A cluster of one-letter flags: `-m`/`-c` end it, and `-X`/`-W` take a value.
        for position, letter in enumerate(arg[1:], start=2):
            rest = arg[position:]
            if letter == "m":
                return "module", rest or (args[index] if index < len(args) else "")
            if letter == "c":
                return "code", ""
            if letter in "XW":
                index += 0 if rest else 1
                break
    return "code", ""


def _required_argv(argv: List[str]) -> Optional[str]:
    if not argv:
        return None
    program = posixpath.basename(argv[0])
    guard = _GUARD_SCRIPT_RE.search(argv[0])
    if guard:
        return "tools/" + guard.group(1)
    if program in ("pytest", "py.test"):
        return "pytest"
    if program in ("ruff", "mypy", "forgelm"):
        return program
    if program == "node" and "--test" in argv[1:]:
        return "node --test"
    if _PYTHON_RE.fullmatch(program):
        kind, target = _python_target(argv[1:])
        if kind == "module" and target in _GATE_MODULES:
            return target
        if kind == "script":
            guard = _GUARD_SCRIPT_RE.search(target)
            return "tools/" + guard.group(1) if guard else None
    return None


def _find_exec(argv: List[str]) -> Optional[Tuple[List[str], str]]:
    """The command ``find -exec``/``-execdir`` runs, and its terminator (``;`` or ``+``)."""
    if not argv or posixpath.basename(argv[0]) != "find":
        return None
    for index, arg in enumerate(argv):
        if arg in ("-exec", "-execdir", "-ok", "-okdir"):
            for end in range(index + 1, len(argv)):
                if argv[end] in (";", "+"):
                    return argv[index + 1 : end], argv[end]
            return argv[index + 1 :], ""
    return None


def _required(command: _Simple) -> Optional[str]:
    """The kind of required command *command* runs, or None."""
    argv = _argv(command)
    found = _find_exec(argv)
    return _required_argv(found[0] if found else argv)


def _scanner(command: _Simple) -> Optional[str]:
    argv = _argv(command)
    if not argv:
        return None
    program = posixpath.basename(argv[0])
    if program in _SCANNER_HELPERS:
        return program
    if _PYTHON_RE.fullmatch(program):
        kind, target = _python_target(argv[1:])
        if kind == "module" and target in ("bandit", "pip_audit"):
            return target.replace("_", "-")
    return None


def _report_path(command: _Simple) -> Optional[str]:
    """Where a scanner writes its report: ``-o``/``--output`` or a stdout redirection."""
    args = _argv(command)[1:]
    for index, arg in enumerate(args):
        if arg in ("-o", "--output") and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--output="):
            return arg.split("=", 1)[1]
        if arg.startswith("-o") and len(arg) > 2 and not arg.startswith("--"):
            return arg[2:]
    for redirect in command.redirects:
        if redirect.op in (">", ">|") and redirect.fd in (None, "1"):
            return redirect.target.text
    return None


def _helper_report(command: _Simple, value_options: Tuple[str, ...]) -> Optional[str]:
    """The first positional argument of a severity helper: the report it reads."""
    argv = _argv(command)
    script = argv[0]
    if _PYTHON_RE.fullmatch(posixpath.basename(argv[0])):
        script = _python_target(argv[1:])[1]
    tail = argv[argv.index(script) + 1 :] if script in argv else []
    index = 0
    while index < len(tail):
        arg = tail[index]
        if arg in value_options:
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            return arg
    return None


def _apply_set(options: ShellOptions, args: List[str]) -> ShellOptions:
    """Fold ``set``-style flags (``-e``, ``+e``, ``-o pipefail``, ``-euo pipefail``) into *options*."""
    errexit, pipefail = options.errexit, options.pipefail
    index = 0
    while index < len(args):
        arg = args[index]
        if arg[:1] in ("-", "+") and not arg.startswith("--") and len(arg) > 1:
            enable = arg[0] == "-"
            for letter in arg[1:]:
                if letter == "e":
                    errexit = enable
                elif letter == "o" and index + 1 < len(args):
                    index += 1
                    if args[index] == "errexit":
                        errexit = enable
                    elif args[index] == "pipefail":
                        pipefail = enable
        index += 1
    return ShellOptions(errexit, pipefail)


def _set_args(item: _Item) -> Optional[List[str]]:
    """The arguments of a bare ``set`` builtin item, else None."""
    pipelines = item.and_or.pipelines
    if len(pipelines) != 1 or len(pipelines[0].commands) != 1:
        return None
    command = pipelines[0].commands[0]
    if isinstance(command, _Simple) and command.words and command.words[0].text == "set":
        return [word.text for word in command.words[1:]]
    return None


def _children(node: Any) -> Iterator[Any]:
    if isinstance(node, _Script):
        yield from node.items
    elif isinstance(node, _Item):
        yield node.and_or
    elif isinstance(node, _AndOr):
        yield from node.pipelines
    elif isinstance(node, _Pipeline):
        yield from node.commands
    elif isinstance(node, _Group):
        yield node.body
    elif isinstance(node, _If):
        yield from node.conditions
        yield from node.bodies
    elif isinstance(node, _Loop):
        if node.condition is not None:
            yield node.condition
        yield node.body
    elif isinstance(node, _Case):
        yield from node.bodies
    elif isinstance(node, _Function):
        yield node.body


def _words(node: Any) -> Iterator[_Word]:
    """Every word in *node*, including assignment values and redirection targets."""
    if isinstance(node, _Simple):
        yield from node.assignments
        yield from node.words
        yield from (redirect.target for redirect in node.redirects)
    elif isinstance(node, _Loop):
        yield from node.words
    elif isinstance(node, _Case):
        yield node.subject
    for child in _children(node):
        yield from _words(child)


def _text(node: Any, limit: int = 90) -> str:
    """A short source-like rendering of a command, for messages."""
    if isinstance(node, _Simple):
        text = " ".join([word.raw for word in node.assignments + node.words])
    elif isinstance(node, _Pipeline):
        text = ("! " if node.negated else "") + " | ".join(_text(command, limit) for command in node.commands)
    elif isinstance(node, _Group):
        text = "( … )" if node.subshell else "{ … }"
    else:
        text = type(node).__name__.strip("_").lower()
    return text if len(text) <= limit else text[: limit - 1] + "…"


class _Analysis:
    """Walk one parsed script and record every way a required command's failure can be lost."""

    def __init__(self, where: str) -> None:
        self.where = where
        self.findings: List[str] = []
        self.required: List[str] = []
        self._nested: dict = {}

    def report(self, node: Any, reason: str) -> None:
        line = getattr(node, "line", None)
        if isinstance(node, _Pipeline):
            line = getattr(node.commands[0], "line", None)
        at = f" (line {line})" if line else ""
        self.findings.append(f"{self.where}{at}: `{_text(node)}` {reason}")

    # -- what a node contains --------------------------------------------

    def nested_shell(self, command: _Simple) -> Optional[Tuple[_Script, ShellOptions]]:
        """The script and options of ``bash -c '...'`` / ``sh <<'EOF'`` style invocations."""
        if id(command) not in self._nested:
            self._nested[id(command)] = self._nested_shell(command)
        return self._nested[id(command)]

    def _nested_shell(self, command: _Simple) -> Optional[Tuple[_Script, ShellOptions]]:
        argv = _argv(command)
        if not argv or posixpath.basename(argv[0]) not in _SHELLS:
            return None
        options, script, index = INTERACTIVE, None, 1
        while index < len(argv):
            arg = argv[index]
            if arg in ("-o", "+o"):
                options = _apply_set(options, argv[index : index + 2])
                index += 2
                continue
            if arg.startswith("--"):
                index += 1
                continue
            if not arg.startswith(("-", "+")):
                return None  # a script file this guard cannot read
            options = _apply_set(options, [arg])
            if "c" in arg[1:]:
                script = argv[index + 1] if index + 1 < len(argv) else ""
                break
            index += 1
        if script is None:
            for redirect in command.redirects:
                if redirect.heredoc is not None:
                    script = redirect.heredoc
                elif redirect.op == "<<<":
                    script = redirect.target.text
        if script is None:
            return None
        try:
            return _parse(script, command.line), options
        except ShellParseError as exc:
            self.report(command, f"runs a nested shell script this guard cannot parse ({exc})")
            return None

    def has_required(self, node: Any) -> bool:
        if isinstance(node, _Simple):
            if _required(node):
                return True
            nested = self.nested_shell(node)
            return nested is not None and self.has_required(nested[0])
        return any(self.has_required(child) for child in _children(node))

    def first_required(self, node: Any) -> Any:
        if isinstance(node, _Simple):
            return node
        for child in _children(node):
            if self.has_required(child):
                return self.first_required(child)
        return node

    # -- does a failure reach the exit status? ---------------------------

    def scope(self, script: _Script, options: ShellOptions, tail_counts: bool, tail_reason: str) -> Optional[str]:
        """Check a command list; return STATUS when its exit status carries a failure, else TERMINATES.

        Returns None when the list runs no required command, or when a problem
        inside it was already reported (so enclosing levels do not repeat it).
        """
        outcome: Optional[str] = None
        reported = len(self.findings)
        for index, item in enumerate(script.items):
            last = index == len(script.items) - 1
            set_args = _set_args(item)
            if set_args is not None:
                options = _apply_set(options, set_args)
                continue
            result = self.and_or(item.and_or, options)
            options = self.weakened(item, options)
            if result is None:
                continue
            culprit = self.first_required(item.and_or)
            if item.background:
                self.report(culprit, "runs in the background (`&`), so nothing waits for its exit status")
            elif result == STATUS and not (last and tail_counts):
                self.report(culprit, tail_reason if last else self.lost_reason(options))
            elif result == STATUS:
                outcome = STATUS
            else:
                outcome = outcome or TERMINATES
        return None if len(self.findings) > reported else outcome

    def lost_reason(self, options: ShellOptions) -> str:
        if options.errexit:
            return (
                "can fail inside a `&&` / `||` list, which errexit ignores, while a later command "
                "decides the exit status"
            )
        return (
            "can fail without stopping the script: errexit is off or ignored here, so a later command "
            "decides the exit status"
        )

    def weakened(self, item: _Item, options: ShellOptions) -> ShellOptions:
        """Options after *item*: any nested ``set +e`` / ``set +o pipefail`` may have run."""
        for command in self.commands_in(item):
            if isinstance(command, _Simple) and command.words and command.words[0].text == "set":
                turned = _apply_set(ShellOptions(True, True), [word.text for word in command.words[1:]])
                options = ShellOptions(options.errexit and turned.errexit, options.pipefail and turned.pipefail)
        return options

    def commands_in(self, node: Any) -> Iterator[Any]:
        for child in _children(node):
            if isinstance(child, _Group) and child.subshell:
                continue
            if not isinstance(child, (_Script, _Item, _AndOr, _Pipeline)):
                yield child
            yield from self.commands_in(child)

    def and_or(self, and_or: _AndOr, options: ShellOptions) -> Optional[str]:
        count = len(and_or.pipelines)
        result: Optional[str] = None
        reported = len(self.findings)
        for position, pipeline in enumerate(and_or.pipelines):
            if not self.has_required(pipeline):
                continue
            # errexit is ignored inside a compound placed anywhere but last in the list.
            inner = replace(options, errexit=options.errexit and position == count - 1)
            outcome = self.pipeline(pipeline, inner)
            result = result or TERMINATES
            if outcome is None:
                continue
            culprit = self.first_required(pipeline)
            if position and and_or.operators[position - 1] == "||":
                self.report(culprit, "runs only when the command before `||` fails, so it is usually skipped")
                continue
            # Its own failure (unless it already ends the shell), and a predecessor's
            # failure that skips it: either must still fail the list.
            paths = [] if outcome == TERMINATES else [(self.after_failure(and_or, position), "")]
            if position:
                skipped = "is skipped when the command before `&&` fails, and "
                paths.append((self.after_failure(and_or, position - 1), skipped))
            for (kind, fallback), prefix in paths:
                if kind == "masked":
                    self.report(culprit, f"{prefix}is masked by `|| {fallback}`, which can exit 0")
                    break
                if kind == "terminates" or (kind == "errexit" and options.errexit):
                    continue
                result = STATUS
        return None if len(self.findings) > reported else result

    def after_failure(self, and_or: _AndOr, failed: int) -> Tuple[str, str]:
        """Follow the list after pipeline *failed* exits non-zero.

        Returns ``("masked", fallback)``, ``("terminates", "")`` when an ``exit``
        ends the shell, ``("errexit", "")`` when the list's last command is the
        one that fails (errexit applies), or ``("silent", "")`` when only the
        list's exit status records the failure.
        """
        last = failed
        for index in range(failed, len(and_or.operators)):
            if and_or.operators[index] == "&&":
                continue
            fallback = and_or.pipelines[index + 1]
            kind = self.fallback(fallback)
            if kind == "succeeds":
                return "masked", _text(fallback)
            if kind == "exits":
                return "terminates", ""
            last = index + 1
        return ("errexit" if last == len(and_or.pipelines) - 1 else "silent"), ""

    def fallback(self, pipeline: _Pipeline) -> str:
        """``exits`` (ends the shell non-zero), ``fails`` (exits non-zero) or ``succeeds`` (may exit 0)."""
        if pipeline.negated or len(pipeline.commands) != 1:
            return "succeeds"
        command = pipeline.commands[0]
        if isinstance(command, _Simple):
            argv = [word.text for word in command.words]
            if argv[:1] == ["exit"]:
                # A bare `exit`, `exit $?` or `exit "$rc"` re-raises; only a literal 0 (mod 256) succeeds.
                code = argv[1] if len(argv) > 1 else "$?"
                return "succeeds" if code.isdigit() and int(code) % 256 == 0 else "exits"
            return "fails" if argv in (["false"], ["/bin/false"], ["/usr/bin/false"]) else "succeeds"
        if isinstance(command, _Group) and command.body.items:
            last = command.body.items[-1].and_or
            if all(operator == "&&" for operator in last.operators):
                tail = self.fallback(last.pipelines[-1])
                if tail == "exits" and not command.subshell and len(last.pipelines) == 1:
                    return "exits"
                if tail in ("exits", "fails"):
                    return "fails"
        return "succeeds"

    def pipeline(self, pipeline: _Pipeline, options: ShellOptions) -> Optional[str]:
        culprit = self.first_required(pipeline)
        if pipeline.negated:
            self.report(culprit, "is negated with `!`, so its failure counts as success")
            return None
        outcomes: List[str] = []
        for position, command in enumerate(pipeline.commands):
            if not self.has_required(command):
                continue
            if position < len(pipeline.commands) - 1 and not options.pipefail:
                self.report(
                    command, "feeds a pipe without `pipefail`, so the pipeline reports the last command's status"
                )
                return None
            outcome = self.command(command, options)
            if outcome is None:
                return None
            # Each element of a multi-command pipeline runs in a subshell: an exit ends only that subshell.
            outcomes.append(STATUS if len(pipeline.commands) > 1 else outcome)
        return STATUS if STATUS in outcomes else TERMINATES

    def command(self, command: _Command, options: ShellOptions) -> Optional[str]:
        if isinstance(command, _Simple):
            kind = _required(command)
            if kind:
                self.required.append(kind)
                found = _find_exec(_argv(command))
                if found and found[1] != "+":
                    self.report(command, "runs under `find -exec … ;`, which ignores the command's exit status")
                    return None
                return STATUS
            nested = self.nested_shell(command)
            if nested is not None and self.scope(nested[0], nested[1], True, ""):
                return STATUS  # the nested shell's exit status carries the failure
            return None
        if isinstance(command, _Group):
            outcome = self.scope(command.body, options, True, "")
            return STATUS if command.subshell and outcome else outcome
        culprit = self.first_required(command)
        if isinstance(command, _If):
            if any(self.has_required(condition) for condition in command.conditions):
                self.report(culprit, "is an `if`/`elif` condition, so the test consumes its exit status")
            else:
                self.report(culprit, "runs on one branch of an `if` only; gate the whole step with `if:` instead")
        elif isinstance(command, _Loop) and command.kind == "for":
            reason = "is the last command of a `for` loop body without errexit, so only the final iteration counts"
            self.scope(command.body, options, False, reason)
            return TERMINATES
        elif isinstance(command, _Loop):
            self.report(culprit, f"runs inside a `{command.kind}` loop, which may run it zero times")
        elif isinstance(command, _Case):
            self.report(culprit, "runs on one `case` branch only")
        elif isinstance(command, _Function):
            self.report(culprit, "is wrapped in a shell function; call required commands directly")
        return None

    # -- `|| true` outside required commands, and the scanner exception ----

    def noop_fallbacks(self, script: _Script, follower: Optional[_Pipeline]) -> None:
        for index, item in enumerate(script.items):
            after = script.items[index + 1].and_or.pipelines[0] if index + 1 < len(script.items) else follower
            and_or = item.and_or
            for position, operator in enumerate(and_or.operators):
                fallback = and_or.pipelines[position + 1]
                if operator != "||" or not self.is_noop(fallback):
                    continue
                left = and_or.pipelines[: position + 1]
                if any(self.has_required(pipeline) for pipeline in left):
                    continue  # reported with the required command's own reason
                if len(and_or.pipelines) == 2 and self.scanner_exception(and_or, after):
                    continue
                self.report(
                    left[-1],
                    f"is followed by `|| {_text(fallback)}`, which discards its failure; the only sanctioned "
                    "`|| true` is a bandit/pip-audit scanner whose next command is its severity helper reading "
                    "the same report (docs/standards/testing.md)",
                )
            for position, pipeline in enumerate(and_or.pipelines):
                for command in pipeline.commands:
                    if isinstance(command, _Group):
                        chained = position < len(and_or.operators) and and_or.operators[position] == "&&"
                        follows = (
                            and_or.pipelines[position + 1]
                            if chained
                            else after
                            if position == len(and_or.operators)
                            else None
                        )
                        self.noop_fallbacks(command.body, follows)
                    elif isinstance(command, _Simple):
                        nested = self.nested_shell(command)
                        if nested is not None:
                            self.noop_fallbacks(nested[0], None)
                    else:
                        for child in _children(command):
                            if isinstance(child, _Script):
                                self.noop_fallbacks(child, None)
                            elif isinstance(child, _Group):
                                self.noop_fallbacks(child.body, None)

    @staticmethod
    def is_noop(pipeline: _Pipeline) -> bool:
        if pipeline.negated or len(pipeline.commands) != 1 or not isinstance(pipeline.commands[0], _Simple):
            return False
        return tuple(word.text for word in pipeline.commands[0].words) in _NOOP_FALLBACKS

    def scanner_exception(self, and_or: _AndOr, after: Optional[_Pipeline]) -> bool:
        scanned = and_or.pipelines[0]
        if scanned.negated or len(scanned.commands) != 1 or not isinstance(scanned.commands[0], _Simple):
            return False
        scanner_command = scanned.commands[0]
        scanner = _scanner(scanner_command)
        if scanner is None:
            return False
        fallback = [word.text for word in and_or.pipelines[1].commands[0].words]  # type: ignore[union-attr]
        helper, value_options = _SCANNER_HELPERS[scanner]
        report = _report_path(scanner_command)
        problem = None
        if fallback != ["true"]:
            problem = f"use exactly `|| true` before {helper}"
        elif report is None:
            problem = f"writes no report (`-o`/`--output` or `> FILE`) for {helper} to read"
        elif after is None or after.negated or len(after.commands) != 1 or not isinstance(after.commands[0], _Simple):
            problem = f"is not immediately followed by `python3 {helper} {report}`"
        elif _required(after.commands[0]) != helper:
            problem = f"is followed by `{_text(after)}` instead of `python3 {helper} {report}`"
        elif _helper_report(after.commands[0], value_options) != report:
            problem = f"writes {report} but {helper} reads {_helper_report(after.commands[0], value_options)}"
        if problem:
            self.report(scanned, f"`|| true` is sanctioned only when the severity helper gates this report: {problem}")
        return True

    def substitutions(self, script: _Script) -> None:
        for word in _words(script):
            for sub in word.subs:
                if self.has_required(sub):
                    self.report(
                        self.first_required(sub), "runs inside a command substitution, which discards its exit status"
                    )
                self.substitutions(sub)


def scan_script(text: str, where: str, options: ShellOptions) -> Tuple[List[str], List[str]]:
    """Analyse one shell script; return ``(findings, required command kinds seen)``."""
    try:
        script = _parse(_github_expressions(text))
    except ShellParseError as exc:
        return [f"{where}: could not parse the shell script ({exc}); simplify it or extend this guard"], []
    analysis = _Analysis(where)
    analysis.scope(script, options, True, "")
    analysis.noop_fallbacks(script, None)
    analysis.substitutions(script)
    return analysis.findings, analysis.required


def _github_expressions(text: str) -> str:
    """Replace ``${{ ... }}`` with a plain word: GitHub substitutes them before the shell runs."""
    out: List[str] = []
    position = 0
    while True:
        start = text.find("${{", position)
        end = text.find("}}", start + 3) if start >= 0 else -1
        if start < 0 or end < 0:
            out.append(text[position:])
            return "".join(out)
        out.append(text[position:start] + "GITHUB_EXPRESSION")
        position = end + 2


# ── Workflows and gauntlets ──────────────────────────────────────────────


def _shell_options(shell: Optional[str], runner: str) -> Tuple[Optional[ShellOptions], str]:
    """The options a step's shell starts with, or None (with its name) when it cannot be modelled."""
    if shell is None:
        if runner != "posix":
            return None, "the default shell of a Windows or unresolved runner"
        return GITHUB_DEFAULT, "bash -e"
    spelling = " ".join(str(shell).split())
    if spelling == "bash":
        return ShellOptions(errexit=True, pipefail=True), spelling  # bash --noprofile --norc -eo pipefail {0}
    if spelling == "sh":
        return ShellOptions(errexit=True, pipefail=False), spelling  # sh -e {0}
    parts = spelling.split()
    if parts and posixpath.basename(parts[0]) in _SHELLS and "{0}" in parts:
        return _apply_set(INTERACTIVE, parts[1:]), spelling
    return None, f"`shell: {spelling}`"


def _runner(job: dict) -> str:
    """``posix``, or ``other`` when runs-on may name a Windows runner or cannot be resolved."""
    runs_on = job.get("runs-on")
    labels = runs_on if isinstance(runs_on, list) else [runs_on]
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    resolved: List[str] = []
    for label in labels:
        if not isinstance(label, str):
            return "other"
        match = re.fullmatch(r"\$\{\{ ?matrix\.([\w-]{1,64}) ?\}\}", label.strip())
        if match and isinstance(matrix, dict) and isinstance(matrix.get(match.group(1)), list):
            resolved += [str(value) for value in matrix[match.group(1)]]
            resolved += [str(row[match.group(1)]) for row in matrix.get("include") or [] if match.group(1) in row]
        elif "${{" in label:
            return "other"
        else:
            resolved.append(label)
    return "other" if any("windows" in label.lower() for label in resolved) else "posix"


def _truthy_flag(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip() not in ("", "false"))


_REQUIRED_WORD_RE = re.compile(r"\b(?:pytest|ruff|mypy|forgelm|node --test|tools/check_\w{1,64}\.py)\b")


def scan_steps(steps: List[Any], where: str, job: dict, defaults: Optional[str]) -> Tuple[List[str], List[str]]:
    """Check every ``run:`` step of one job (or composite action)."""
    findings: List[str] = []
    required: List[str] = []
    job_defaults = ((job.get("defaults") or {}).get("run") or {}).get("shell")
    runner = _runner(job) if "runs-on" in job else "posix"
    for index, step in enumerate(steps or []):
        if not isinstance(step, dict) or not isinstance(step.get("run"), str):
            continue
        name = step.get("name") or step.get("id") or f"#{index + 1}"
        label = f"{where}, step {name!r}"
        options, shell = _shell_options(step.get("shell") or job_defaults or defaults, runner)
        if options is None:
            if _REQUIRED_WORD_RE.search(step["run"]):
                findings.append(
                    f"{label}: runs a required command under {shell}; use `shell: bash` so its exit status is checked"
                )
            continue
        step_findings, step_required = scan_script(step["run"], label, options)
        findings += step_findings
        required += step_required
        if step_required and (
            _truthy_flag(step.get("continue-on-error")) or _truthy_flag(job.get("continue-on-error"))
        ):
            findings.append(f"{label}: `continue-on-error` lets {step_required[0]} fail without failing the job")
    return findings, required


def scan_workflow(text: str, name: str) -> Tuple[List[str], List[str]]:
    doc = yaml.safe_load(text) or {}
    defaults = ((doc.get("defaults") or {}).get("run") or {}).get("shell")
    findings: List[str] = []
    required: List[str] = []
    for job_id, job in (doc.get("jobs") or {}).items():
        job = job or {}
        job_findings, job_required = scan_steps(job.get("steps") or [], f"{name}: job {job_id!r}", job, defaults)
        findings += job_findings
        required += job_required
    runs = (doc.get("runs") or {}) if isinstance(doc.get("runs"), dict) else {}
    if runs.get("steps"):
        action_findings, action_required = scan_steps(runs["steps"], f"{name}: composite action", {}, None)
        findings += action_findings
        required += action_required
    return findings, required


def gauntlet_script(text: str) -> Optional[str]:
    """The shell inside a document's ``<!-- gauntlet:begin -->`` bash fence, if any."""
    begin, end = text.find("<!-- gauntlet:begin -->"), text.find("<!-- gauntlet:end -->")
    if begin < 0 or end < begin:
        return None
    block = text[begin:end]
    fence = block.find("```bash")
    if fence < 0:
        return None
    body = block[fence + len("```bash") :]
    close = body.rfind("```")
    return body[:close] if close >= 0 else None


def _missing_kinds(required: List[str]) -> List[str]:
    return [kind for kind in _EXPECTED_KINDS if not any(item.startswith(kind) for item in required)]


def scan_repo(root: Path) -> Tuple[List[str], int, int]:
    """Scan *root*'s workflows, composite actions and gauntlets; return ``(findings, surfaces, required)``."""
    findings: List[str] = []
    workflows = sorted((root / ".github" / "workflows").glob("*.yml"))
    actions = sorted((root / ".github" / "actions").glob("*/action.y*ml"))
    if not workflows:
        findings.append(f"no workflows under {root / '.github' / 'workflows'}; a scan of nothing would pass")
    total = 0
    for path in workflows + actions:
        surface_findings, required = scan_workflow(path.read_text(encoding="utf-8"), str(path.relative_to(root)))
        findings += surface_findings
        total += len(required)
        if path.name == "ci.yml" and _missing_kinds(required):
            findings.append(
                f"ci.yml: no {', '.join(_missing_kinds(required))} invocation recognised; the scan is broken"
            )
    for doc in _GAUNTLET_DOCS:
        script = gauntlet_script((root / doc).read_text(encoding="utf-8"))
        if script is None:
            findings.append(f"{doc}: no ```bash fence between the gauntlet:begin/end markers")
            continue
        surface_findings, required = scan_script(script, f"{doc} gauntlet", INTERACTIVE)
        findings += surface_findings
        total += len(required)
        if _missing_kinds(required):
            findings.append(f"{doc} gauntlet: no {', '.join(_missing_kinds(required))} invocation recognised")
    return findings, len(workflows) + len(actions) + len(_GAUNTLET_DOCS), total


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--strict", action="store_true", help="Accepted for gauntlet symmetry; this guard is always fatal."
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the success summary.")
    parser.add_argument(
        "--root", type=Path, default=_REPO_ROOT, help="Repository root to scan (default: this checkout)."
    )
    args = parser.parse_args(argv)

    findings, surfaces, required = scan_repo(args.root)
    if findings:
        print("FAIL: a required CI command can fail without failing its step or gauntlet:")
        for finding in findings:
            print(f"  ✗ {finding}")
        print(
            "\nRun required commands as plain commands, or end any fallback with `exit 1`. "
            "`|| true` is reserved for bandit/pip-audit followed by their severity helper "
            "(docs/standards/testing.md, '`|| true` discipline')."
        )
        return 1
    if not args.quiet:
        print(
            f"OK: {required} required command invocation(s) across {surfaces} workflow/gauntlet surface(s) propagate failure."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
