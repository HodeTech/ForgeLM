"""A POSIX-shell lexer and parser for the tools/ guards.

It turns shell text into a tree of commands: quoting, escapes, line
continuations, comments, here-documents, command and process substitutions,
arithmetic, ``[[ … ]]``, compound commands, functions and redirections. It
parses a subset of bash, enough for CI ``run:`` blocks and the documented
gauntlets, and raises :class:`ShellParseError` on anything outside it, so a
caller can fail closed. It never runs anything.

``tools/check_ci_failure_masking.py`` is the guard that uses it; this module
is not a CI guard itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional, Tuple, Union

_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}(?:\[[^\]\n]{0,64}\])?\+?=")
_OPERATORS = ("&&", "||", ";;&", ";;", ";&", "|&", ";", "|", "&", "(", ")")
_CASE_TERMINATORS = (";;", ";&", ";;&")
_REDIRECTIONS = ("&>>", "&>", "<<<", "<<-", "<<", ">>", ">|", "<>", ">&", "<&", "<", ">")
_METACHARACTERS = frozenset(" \t\n;&|()<>")


class ShellParseError(Exception):
    """Shell text outside the parsed subset; a guard reports it as a finding and fails closed."""

    def __init__(self, message: str, line: int) -> None:
        super().__init__(f"line {line}: {message}")


# ── Lexer ────────────────────────────────────────────────────────────────


@dataclass
class Word:
    text: str  # value after quote removal; expansions are kept as written
    raw: str  # source spelling, used to tell `NAME=value` from `"NAME=value"`
    line: int
    subs: List["Script"] = field(default_factory=list)  # $(...), `...`, <(...)


@dataclass
class Token:
    kind: str  # word | op | redir | nl | eof
    value: str
    line: int
    start: int
    end: int
    word: Optional[Word] = None
    heredoc: Optional[str] = None


class Lexer:
    """Tokenise a POSIX-shell subset: quoting, escapes, line continuations,
    comments, operators, redirections, here-documents and nested substitutions."""

    def __init__(self, src: str, start: int = 0, line: int = 1) -> None:
        self.src = src
        self.i = start
        self.line = line
        self.tokens: List[Token] = []
        self.pending_heredocs: List[Tuple[Token, str, bool, Word]] = []
        self.heredoc_delimiter: Optional[Tuple[Token, bool]] = None

    def run(self, closing_paren: bool = False) -> int:
        """Tokenise to the end, or to the ``)`` closing a ``$(`` substitution."""
        depth = 0
        cases = 0  # open `case` statements, whose pattern `)` closes no parenthesis
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
            elif closing_paren and char == ")" and depth == 0 and not cases:
                self._emit("eof", "", self.i, self.i)
                return self.i + 1
            elif self.src.startswith(("<(", ">("), self.i):
                self._read_word()
            elif self.src.startswith("((", self.i) and self._arithmetic(self.i):
                # An arithmetic command or a C-style `for` header: one word, whose `$(…)` still run.
                start, line = self.i, self.line
                text = self._balanced(self.i, "(", ")")
                word = Word(text, text, line)
                Lexer(text[2:-2], 0, line).expansions(word.subs, quoted=True)
                self._emit("word", text, start, self.i, word)
            else:
                matched = self._operator()
                if matched == "(":
                    depth += 1
                elif matched == ")" and depth:
                    depth -= 1
                elif matched is None:
                    self._read_word()
                    raw = self.tokens[-1].word.raw  # type: ignore[union-attr]
                    if raw in ("case", "esac") and self._command_position(raw):
                        cases += 1 if raw == "case" else -1 if cases else 0

    def _command_position(self, raw: str) -> bool:
        """Whether the word just read starts a command, so ``case`` / ``esac`` is a keyword there."""
        if len(self.tokens) < 2:
            return True
        previous = self.tokens[-2]
        if previous.kind in ("nl", "op"):
            return True
        starters = ("then", "do", "else", "elif", "if", "while", "until", "{", "!", "time") + (
            ("in",) if raw == "esac" else ()
        )
        return previous.word is not None and previous.word.raw in starters

    def _matching(self, open_at: int) -> int:
        """Index of the ``)`` closing the ``(`` at *open_at*, skipping quoted text; -1 when there is none."""
        src, depth, pos = self.src, 0, open_at
        while pos < len(src):
            char = src[pos]
            if char in "'\"":
                end = src.find(char, pos + 1)
                pos = len(src) if end < 0 else end
            elif char == "\\":
                pos += 1
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return pos
            pos += 1
        return -1

    def _arithmetic(self, open_at: int) -> bool:
        """Whether the ``((`` at *open_at* is arithmetic: its inner ``(`` closes right before the outer one.

        Otherwise bash reads two nested subshells, as in ``((pytest) || true)``.
        """
        outer = self._matching(open_at)
        return outer > 0 and self._matching(open_at + 1) == outer - 1

    def _emit(self, kind: str, value: str, start: int, end: int, word: Optional[Word] = None) -> Token:
        token = Token(kind, value, self.line, start, end, word)
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
        for token, delimiter, strip_tabs, word in self.pending_heredocs:
            body: List[str] = []
            first_line = self.line
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
            if not any(mark in word.raw for mark in "'\"\\"):
                # An unquoted delimiter: the body is expanded, so its `$(...)` commands run.
                Lexer(token.heredoc, 0, first_line).expansions(word.subs)
        self.pending_heredocs = []

    def expansions(self, subs: List["Script"], quoted: bool = False) -> None:
        """Parse the command substitutions of text that is only expanded, such as a here-document body.

        *quoted* skips single-quoted text, which a here-document body does not have.
        """
        scratch: List[str] = []
        while self.i < len(self.src):
            char = self.src[self.i]
            if char == "\\":
                self.i += 2
            elif char == "'" and quoted:
                end = self.src.find("'", self.i + 1)
                self.i = len(self.src) if end < 0 else end + 1
            elif char in "$`":
                self._expansion(scratch, subs)
            else:
                if char == "\n":
                    self.line += 1
                self.i += 1

    def _read_word(self) -> None:
        start, line = self.i, self.line
        text: List[str] = []
        subs: List[Script] = []
        src = self.src
        while self.i < len(src):
            char = src[self.i]
            if src.startswith(("<(", ">("), self.i) and self.i == start:
                subs.append(self._substitution(self.i + 2))
                text.append(src[start : self.i])
                continue
            if char == "(" and _ASSIGNMENT_RE.fullmatch(src[start : self.i]):
                value = self._balanced(self.i, "(", ")")  # bash array assignment: NAME=(...)
                Lexer(value[1:-1], 0, line).expansions(subs, quoted=True)
                text.append(value)
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
        word = Word("".join(text), raw, line, subs)
        self._emit("word", word.text, start, self.i, word)
        if self.heredoc_delimiter is not None:
            redirection, strip_tabs = self.heredoc_delimiter
            self.pending_heredocs.append((redirection, word.text, strip_tabs, word))
            self.heredoc_delimiter = None

    def _double_quoted(self, text: List[str], subs: List["Script"], line: int) -> None:
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

    def _expansion(self, text: List[str], subs: List["Script"]) -> None:
        src, start = self.src, self.i
        if src.startswith("$((", start) and self._arithmetic(start + 1):
            line = self.line
            value = self._balanced(start + 1, "(", ")", prefix="$")
            Lexer(value[3:-2], 0, line).expansions(subs, quoted=True)
            text.append(value)
        elif src.startswith("$(", start):
            subs.append(self._substitution(start + 2))
            text.append(src[start : self.i])
        elif src.startswith("${", start):
            self._parameter(text, subs)
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
            subs.append(parse(inner, self.line))
            self.line += src.count("\n", start, end)
            self.i = end + 1
            text.append(src[start : self.i])
        else:
            text.append("$")
            self.i += 1

    def _parameter(self, text: List[str], subs: List["Script"]) -> None:
        """Consume ``${...}``, parsing any command substitution inside it (``${X:-$(cmd)}``)."""
        src, start, line = self.src, self.i, self.line
        self.i += 2
        depth = 1
        scratch: List[str] = []
        while self.i < len(src):
            char = src[self.i]
            if char == "\\":
                self.i += 2
            elif char == "'":
                end = src.find("'", self.i + 1)
                if end < 0:
                    raise ShellParseError("unterminated single quote", line)
                self.line += src.count("\n", self.i, end)
                self.i = end + 1
            elif char == '"':
                self._double_quoted(scratch, subs, line)
            elif src.startswith("${", self.i):
                depth += 1
                self.i += 2
            elif char in "$`":
                self._expansion(scratch, subs)
            elif char == "}":
                depth -= 1
                self.i += 1
                if depth == 0:
                    text.append(src[start : self.i])
                    return
            else:
                if char == "\n":
                    self.line += 1
                self.i += 1
        raise ShellParseError("unterminated ${", line)

    def _substitution(self, body_start: int) -> "Script":
        """Parse the script inside ``$( ... )`` / ``<( ... )``; leave ``self.i`` after its ``)``."""
        inner = Lexer(self.src, body_start, self.line)
        self.i = inner.run(closing_paren=True)
        self.line = inner.line
        return Parser(inner.tokens).parse()

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
class Redirect:
    op: str
    target: Word
    fd: Optional[str]
    heredoc: Optional[str]


@dataclass
class Simple:
    words: List[Word]
    assignments: List[Word]
    redirects: List[Redirect]
    line: int


@dataclass
class Group:
    body: "Script"
    subshell: bool
    line: int


@dataclass
class If:
    conditions: List["Script"]
    bodies: List["Script"]
    line: int


@dataclass
class Loop:
    kind: str  # for | while | until
    condition: Optional["Script"]
    body: "Script"
    words: List[Word]
    line: int
    header: Optional[Word] = None  # a C-style `for ((…))` header


@dataclass
class Case:
    subject: Word
    bodies: List["Script"]
    line: int


@dataclass
class Function:
    name: str
    body: "Command"
    line: int


@dataclass
class Expression:  # [[ ... ]] and (( ... )): expressions, never commands, whose words may hold substitutions
    line: int
    words: List[Word] = field(default_factory=list)


Command = Union[Simple, Group, If, Loop, Case, Function, Expression]


@dataclass
class Pipeline:
    commands: List[Command]
    negated: bool


@dataclass
class AndOr:
    pipelines: List[Pipeline]
    operators: List[str]


@dataclass
class Item:
    and_or: AndOr
    background: bool


@dataclass
class Script:
    items: List[Item]


def parse(src: str, line: int = 1) -> Script:
    lexer = Lexer(src, 0, line)
    lexer.run()
    return Parser(lexer.tokens).parse()


class Parser:
    """Recursive descent over the POSIX shell grammar (lists, pipelines, compounds)."""

    _RESERVED = ("then", "elif", "else", "fi", "do", "done", "esac", "}", "in")

    def __init__(self, tokens: List[Token]) -> None:
        self.tokens = tokens
        self.k = 0

    def parse(self) -> Script:
        script = self._script((), ())
        token = self._peek()
        if token.kind != "eof":
            raise ShellParseError(f"unexpected {token.value!r}", token.line)
        return script

    def _peek(self, offset: int = 0) -> Token:
        return self.tokens[min(self.k + offset, len(self.tokens) - 1)]

    def _next(self) -> Token:
        token = self._peek()
        self.k += 1
        return token

    def _is_reserved(self, token: Token, *values: str) -> bool:
        return token.kind == "word" and token.word is not None and token.word.raw in values

    def _is_op(self, token: Token, *values: str) -> bool:
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

    def _script(self, stop_words: Tuple[str, ...], stop_ops: Tuple[str, ...]) -> Script:
        items: List[Item] = []
        while True:
            self._skip_newlines()
            token = self._peek()
            if token.kind == "eof" or self._is_op(token, *stop_ops) or self._is_reserved(token, *stop_words):
                return Script(items)
            and_or = self._and_or()
            token = self._peek()
            background = self._is_op(token, "&")
            if background or self._is_op(token, ";"):
                self.k += 1
            elif not (
                token.kind in ("nl", "eof") or self._is_op(token, *stop_ops) or self._is_reserved(token, *stop_words)
            ):
                raise ShellParseError(f"unexpected {token.value!r}", token.line)
            items.append(Item(and_or, background))

    def _and_or(self) -> AndOr:
        pipelines = [self._pipeline()]
        operators: List[str] = []
        while self._is_op(self._peek(), "&&", "||"):
            operators.append(self._next().value)
            self._skip_newlines()
            pipelines.append(self._pipeline())
        return AndOr(pipelines, operators)

    def _pipeline(self) -> Pipeline:
        if self._is_reserved(self._peek(), "time") and (
            self._peek(1).kind == "word" or self._is_op(self._peek(1), "(")
        ):
            self.k += 1  # `time [-p] PIPELINE` reports the pipeline's own status
            if self._is_reserved(self._peek(), "-p"):
                self.k += 1
        negated = self._is_reserved(self._peek(), "!")
        if negated:
            self.k += 1
        commands = [self._command()]
        while self._is_op(self._peek(), "|", "|&"):
            self.k += 1
            self._skip_newlines()
            commands.append(self._command())
        return Pipeline(commands, negated)

    def _command(self) -> Command:
        token = self._peek()
        if token.kind == "word" and token.word is not None and token.word.raw.startswith("(("):
            self.k += 1  # an arithmetic command, lexed as one word
            return self._redirected(Expression(token.line, [token.word]))
        if self._is_op(token, "("):
            self.k += 1
            body = self._script((), (")",))
            self._expect_op(")")
            return self._redirected(Group(body, True, token.line))
        if self._is_reserved(token, "{"):
            self.k += 1
            body = self._script(("}",), ())
            self._expect_reserved("}")
            return self._redirected(Group(body, False, token.line))
        if self._is_reserved(token, "if"):
            return self._if()
        if self._is_reserved(token, "while", "until", "for"):
            return self._loop()
        if self._is_reserved(token, "case"):
            return self._case()
        if self._is_reserved(token, "[["):
            words: List[Word] = []
            while not self._is_reserved(self._next(), "]]"):
                if self.tokens[self.k - 1].word is not None:
                    words.append(self.tokens[self.k - 1].word)  # type: ignore[arg-type]
                if self._peek().kind == "eof":
                    raise ShellParseError("unterminated [[", token.line)
            return Expression(token.line, words)
        if self._is_reserved(token, "function"):
            self.k += 1
            name = self._next()
            if self._is_op(self._peek(), "(") and self._is_op(self._peek(1), ")"):
                self.k += 2
            self._skip_newlines()
            return Function(name.value, self._command(), token.line)
        if self._is_reserved(token, *self._RESERVED):
            raise ShellParseError(f"unexpected {token.value!r}", token.line)
        if token.kind == "word" and self._is_op(self._peek(1), "(") and self._is_op(self._peek(2), ")"):
            self.k += 3
            self._skip_newlines()
            return Function(token.value, self._command(), token.line)
        return self._simple()

    def _fd_prefix(self) -> Optional[str]:
        """The file descriptor of a ``2>&1``-style redirection starting here, if any."""
        token, following = self._peek(), self._peek(1)
        if token.kind == "word" and token.word is not None and token.word.raw.isdigit():
            if following.kind == "redir" and following.start == token.end:
                return token.word.raw
        return None

    def _redirected(self, command: Command) -> Command:
        while True:
            fd = self._fd_prefix()
            if fd is not None:
                self.k += 1
                self._redirect(fd)
            elif self._peek().kind == "redir":
                self._redirect(None)
            else:
                return command

    def _redirect(self, fd: Optional[str]) -> Redirect:
        operator = self._next()
        target = self._next()
        if target.kind != "word" or target.word is None:
            raise ShellParseError(f"redirection {operator.value!r} has no target", operator.line)
        return Redirect(operator.value, target.word, fd, operator.heredoc)

    def _simple(self) -> Simple:
        line = self._peek().line
        words: List[Word] = []
        assignments: List[Word] = []
        redirects: List[Redirect] = []
        while True:
            token = self._peek()
            fd = self._fd_prefix()
            if fd is not None:
                self.k += 1
                redirects.append(self._redirect(fd))
            elif token.kind == "redir":
                redirects.append(self._redirect(None))
            elif token.kind == "word" and token.word is not None:
                self.k += 1
                if not words and _ASSIGNMENT_RE.match(token.word.raw):
                    assignments.append(token.word)
                else:
                    words.append(token.word)
            else:
                break
        if not (words or assignments or redirects):
            raise ShellParseError(f"expected a command, found {self._peek().value!r}", line)
        return Simple(words, assignments, redirects, line)

    def _if(self) -> If:
        line = self._next().line
        conditions: List[Script] = []
        bodies: List[Script] = []
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
            return self._redirected(If(conditions, bodies, line))

    def _loop(self) -> Loop:
        keyword = self._next()
        condition: Optional[Script] = None
        words: List[Word] = []
        header = keyword
        if keyword.value == "for":
            header = self._next()  # the loop variable, or a C-style ((init; test; step)) header
            self._skip_newlines()
            if not header.value.startswith("((") and self._is_reserved(self._peek(), "in"):
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
        arithmetic = header.word if keyword.value == "for" and header.value.startswith("((") else None
        return self._redirected(Loop(keyword.value, condition, body, words, keyword.line, arithmetic))

    def _case(self) -> Case:
        line = self._next().line
        subject = self._next()
        if subject.word is None:
            raise ShellParseError("case needs a subject word", line)
        self._skip_newlines()
        self._expect_reserved("in")
        bodies: List[Script] = []
        while True:
            self._skip_newlines()
            if self._is_reserved(self._peek(), "esac"):
                self.k += 1
                return self._redirected(Case(subject.word, bodies, line))
            if self._is_op(self._peek(), "("):
                self.k += 1
            while True:
                if self._next().kind != "word":
                    raise ShellParseError("case pattern expected", line)
                if not self._is_op(self._peek(), "|"):
                    break
                self.k += 1
            self._expect_op(")")
            bodies.append(self._script(("esac",), _CASE_TERMINATORS))
            if self._is_op(self._peek(), *_CASE_TERMINATORS):
                self.k += 1


# ── Tree walking ─────────────────────────────────────────────────────────


def children(node: Any) -> Iterator[Any]:
    """The nodes directly inside *node*: items, lists, pipelines, commands and compound bodies."""
    if isinstance(node, Script):
        yield from node.items
    elif isinstance(node, Item):
        yield node.and_or
    elif isinstance(node, AndOr):
        yield from node.pipelines
    elif isinstance(node, Pipeline):
        yield from node.commands
    elif isinstance(node, Group):
        yield node.body
    elif isinstance(node, If):
        yield from node.conditions
        yield from node.bodies
    elif isinstance(node, Loop):
        if node.condition is not None:
            yield node.condition
        yield node.body
    elif isinstance(node, Case):
        yield from node.bodies
    elif isinstance(node, Function):
        yield node.body


def own_words(node: Any) -> List[Word]:
    """The words *node* itself holds (not those of the commands nested in it)."""
    if isinstance(node, Simple):
        return node.assignments + node.words + [redirect.target for redirect in node.redirects]
    if isinstance(node, Loop):
        return node.words + ([node.header] if node.header is not None else [])
    if isinstance(node, Case):
        return [node.subject]
    if isinstance(node, Expression):
        return node.words
    return []
