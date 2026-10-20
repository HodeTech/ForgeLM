#!/usr/bin/env python3
"""Keep the ``blocked`` label in step with GitHub's "Blocked by" links, and stop pull requests on blocked issues.

Three commands, run by two workflows:

- ``sync`` — ``.github/workflows/issue-dependencies.yml``, when an issue is closed or reopened, every 15 minutes, and
  on demand. It brings the ``blocked`` label of every issue in line with its native "Blocked by" links, removes the
  label from pull requests (it describes issues only), and re-runs the "Issue dependencies" check of every open pull
  request whose last result no longer matches its linked issues.
- ``check-pr`` — ``.github/workflows/pr-dependencies.yml``, on every pull-request change. It fails while the pull
  request links an issue that is still blocked.
- ``record-override`` — the same workflow, when a ``dependency-override`` label is added. It records what a
  maintainer's override accepts: the head commit and the issues the pull request links at that moment, in a comment.

Rules (documented in ``CONTRIBUTING.md``, "Dependencies between issues"):

- A dependency is a native "Blocked by" link, set by a maintainer in the issue's sidebar. Prose such as "after #12"
  is not one.
- A blocker is resolved only when it is closed as completed. While it is open the dependent waits. When it is closed
  as not planned or as a duplicate, the work the dependent waits for was not done: the dependent stays blocked, gets
  ``needs-triage`` and one comment asking a maintainer to re-plan it (remove the link, or link the issue that replaces
  the blocker).
- ``blocked`` is derived and never set by hand: ``sync`` adds it to every open issue with an unresolved blocker and
  removes it everywhere else — from a closed issue, from one where someone set it by hand, and from a pull request.
  When it removes the label from an open issue it posts one comment (again each time the issue is unblocked anew); it
  does not comment when an issue becomes blocked.
- ``check-pr`` fails while the pull request links a blocked issue — unless the same pull request closes every open
  blocker, or a maintainer listed in ``MAINTAINER_LOGINS`` has labelled it ``dependency-override`` and the pull request
  has not changed since. The override covers the head commit and the linked issues ``record-override`` recorded when
  the label was set; a new commit or a link to another issue ends it, and the label set by anyone else is ignored.
  The check reads the links themselves, not the ``blocked`` label, so it never trails behind ``sync``.

Which issues a pull request links. Two sources, merged:

- *What GitHub will close*: the pull request's ``closingIssuesReferences`` — GitHub's own list (the "Development"
  sidebar), built from its closing keywords and manual links. Only this list can exempt a blocker as "closed by the
  same pull request", and only when the pull request targets the default branch, where GitHub closes on merge. The
  check never guesses closing syntax, so an exemption is never broader than what GitHub will actually do.
- *Everything else it mentions*: ``Fixes`` / ``Closes`` / ``Resolves`` / ``Refs`` references read leniently from the
  title and the description *as GitHub renders them* (``POST /markdown``), so code, HTML comments, escapes and nested
  blocks are exactly what GitHub makes of them and a reference a reader can see is one the check sees; and from every
  commit message as plain text, because GitHub closes an issue from a commit message whatever it looks like. Reading
  too many links can only make the check stricter. The template's own examples sit in HTML comments, which GitHub
  drops.

The check sees what a pull request declares: it cannot see work on an issue nothing links, so it guards against
honest mistakes, and a maintainer's review remains the backstop. At most ``_MAX_LINKED`` linked issues are read; more
fails the check without reading any, so a description cannot spend the repository's API budget.

Trust boundary. Issue and pull-request text is written by anyone, so the script never executes or prints it: it
reads GitHub's rendering of it with the standard library's HTML parser, matches references in linear time and writes
only fixed messages, issue numbers and commit SHAs to the log. The workflows pass nothing through the shell; the
script reads the event file itself. Every subprocess call is a list without a shell, limited to fixed endpoints:
``repos/`` paths built from integers, validated SHAs and timestamps and the repository name; ``graphql`` with a query
constant from this module; and ``markdown``, which only renders text. Only reads are retried.

Exit codes (per ``tools/`` contract, not the public 0-6 surface of ``forgelm/``):

- ``0`` — done: labels in step, the pull request may merge, or the override was recorded (or there was nothing to
  record).
- ``1`` — unexpected failure: an API call failed, or GitHub's answer was not what its API documents. ``sync`` keeps
  going after one issue fails, reconciles the pull-request checks anyway, and exits 1 at the end.
- ``2`` — the event payload, the arguments or the environment are unusable.
- ``3`` — ``check-pr`` only: the pull request links an issue that is still blocked.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence
from urllib.parse import quote

BLOCKED_LABEL = "blocked"
OVERRIDE_LABEL = "dependency-override"
TRIAGE_LABEL = "needs-triage"
CHECK_NAME = "Issue dependencies"  # the job name in pr-dependencies.yml, and the required check's context
BOT_LOGIN = "github-actions[bot]"  # the author of every comment a workflow of this repository posts with its token
# Hidden markers. Only a comment by BOT_LOGIN counts, so neither a person nor another app can forge one. `{blocker}` /
# `{blockers}` hold `owner/repo#N` references, so blockers in two repositories never collide; `{since}` is when the
# `blocked` label was last added, so an issue unblocked a second time gets a second notice.
NOT_COMPLETED_MARKER = "<!-- issue-dependencies:not-completed:{blocker} -->"
UNBLOCKED_MARKER = "<!-- issue-dependencies:unblocked:{blockers}@{since} -->"
OVERRIDE_MARKER = "<!-- issue-dependencies:override:{head}:{issues} -->"
_OVERRIDE_MARKER_RE = re.compile(r"<!-- issue-dependencies:override:([0-9a-f]{40}):([0-9,]*) -->")
# Conclusions branch protection accepts as a passing check.
PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})

_EXIT_BLOCKED = 3
_GH_TIMEOUT_SECONDS = 60
_ATTEMPTS = 3  # tries of an idempotent read that fails transiently
_BACKOFF_SECONDS = 2.0  # the wait before the second try, doubled before the third, unless GitHub names one
_MAX_WAIT_SECONDS = 60  # GitHub asking for a longer wait fails the call rather than stall the job
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
_PAGE_SIZE = 100
_MAX_PAGES = 30  # 3,000 items: far above the backlog; bounds every paginated read
_MAX_BLOCKERS = 50  # GitHub shows at most this many "Blocked by" links per issue
_MAX_LABELS = 100  # GitHub allows at most this many labels per issue
_MAX_LINKED = 50  # issues one pull request may link; each costs one API call
_MAX_COMMITS = 100  # commits one pull request may have and still be read in full
_RERUN_DAYS = 30  # GitHub re-runs a workflow run only within this many days
_ALLOWED_METHODS = frozenset({"GET", "POST", "DELETE"})
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_REPO_RE = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
# A terminal control sequence (CSI). Parameter, intermediate and final bytes are three disjoint ASCII ranges, so no
# two quantifiers compete for a character (docs/standards/regex.md rule 4).
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

MSG_UNBLOCKED = (
    "This issue is no longer blocked: every issue it waited for is closed as completed ({blockers}). It can be "
    "picked up now; see [Working on an issue]({contributing})."
)
MSG_UNBLOCKED_TRIAGE = (
    "This issue is no longer blocked by another issue ({blockers}). It still carries `needs-triage`, so a maintainer "
    "has to finish triaging it before it can be picked up."
)
MSG_UNBLOCKED_NO_LINKS = (
    'This issue is no longer blocked: it has no open "Blocked by" link. The `blocked` label follows those links '
    "and is set and removed automatically; see [Dependencies between issues]({contributing})."
)
MSG_NOT_COMPLETED = (
    "{blocker}, which this issue is blocked by, was closed as {reason}, not as completed, so the work this issue "
    "waits for was not done and the issue stays blocked. {mention}, please re-plan it: remove the link if the "
    "dependency no longer holds, or link the issue that replaces {blocker}."
)
MSG_OVERRIDE_RECORDED = (
    "`dependency-override` is recorded for commit `{short}` and the issues this pull request links now ({issues}). "
    "The **Issue dependencies** check accepts their dependencies while both stay as they are. A new commit or a link "
    "to another issue ends the override; a maintainer then reads the pull request again, removes the label and sets "
    "it again."
)
MSG_OVERRIDE_NOT_MAINTAINER = "`dependency-override` is ignored: only a maintainer listed in MAINTAINERS.md can set it."
MSG_OVERRIDE_UNRECORDED = (
    "`dependency-override` is ignored: it was not recorded for this pull request (the *Record dependency override* "
    "job did not run, failed, or saw new commits). A maintainer removes the label and sets it again."
)
MSG_OVERRIDE_NEW_COMMITS = (
    "`dependency-override` no longer counts: it was set for commit `{short}`, and the pull request's head has changed "
    "since. A maintainer reads it again, removes the label and sets it again."
)
MSG_OVERRIDE_NEW_LINKS = (
    "`dependency-override` no longer counts: since it was set, the pull request also links {issues}. A maintainer "
    "reads it again, removes the label and sets it again."
)

MAINTAINER_LOGINS = frozenset({"cemililik"})  # MAINTAINERS.md; tests/test_issue_dependencies.py keeps them equal
MAINTAINER_MENTION = " ".join(f"@{login}" for login in sorted(MAINTAINER_LOGINS))

# A reference after a keyword: `#12`, `owner/repo#12` or an issue URL. Every class is explicit ASCII (keywords,
# logins and repository names are ASCII; docs/standards/regex.md rule 1), and a number is a whole token, so `#5abc`
# and `#５` are not references.
_KEYWORD = r"(?P<kw>close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|references)"
_OWNER, _NAME, _NUM = r"[A-Za-z0-9-]+", r"[A-Za-z0-9._-]+", r"[0-9]+(?![A-Za-z0-9_])"
_REF_RE = re.compile(
    rf"(?:(?P<owner>{_OWNER})/(?P<repo>{_NAME}))?#(?P<num>{_NUM})"
    rf"|https?://github\.com/(?P<uowner>{_OWNER})/(?P<urepo>{_NAME})/issues/(?P<unum>{_NUM})"
)
_REF_PLAIN = rf"(?:(?:{_OWNER}/{_NAME})?#{_NUM}|https?://github\.com/{_OWNER}/{_NAME}/issues/{_NUM})"
# Horizontal whitespace only (rule 5), and the optional colon owns the whitespace after it, so no two quantifiers
# compete for the same characters (rule 4).
_LINK_RE = re.compile(
    rf"(?<![A-Za-z0-9_-]){_KEYWORD}(?P<sep>[ \t]*(?::[ \t]*)?)"
    rf"(?P<refs>{_REF_PLAIN}(?:[ \t]*(?:,|and|&)[ \t]*{_REF_PLAIN})*)",
    re.IGNORECASE,
)
# Elements whose text runs on in the line around them. Every other element is a block or a line break and ends the
# line, so a keyword at the end of one paragraph, cell or list item never links a reference at the start of the next.
_INLINE_ELEMENTS = frozenset(
    "a abbr b bdi bdo cite del dfn em g-emoji i img input ins kbd mark q s samp small span strike strong sub sup u "
    "var".split()
)
_CODE_ELEMENTS = frozenset({"code", "pre"})

# Every GraphQL document this script may send. A query is chosen by name; only integer and cursor variables vary.
_ISSUE_FIELDS = """
        labels(first: 100) { totalCount nodes { name } }
        blockedBy(first: 50) { totalCount nodes { number state stateReason repository { nameWithOwner } } }"""
_PULL_FIELDS = """
        number title body headRefOid baseRefName
        labels(first: 100) { totalCount nodes { name } }
        closingIssuesReferences(first: 50) { totalCount nodes { number repository { nameWithOwner } } }
        commits(first: 100) { totalCount nodes { commit { message } } }"""
# The most recent 100 label changes, oldest first, so a long history never makes the read fail.
_LABEL_CHANGES = """
        timelineItems(last: 100, itemTypes: [LABELED_EVENT, UNLABELED_EVENT]) {
          nodes {
            __typename
            ... on LabeledEvent { createdAt actor { login } label { name } }
            ... on UnlabeledEvent { createdAt actor { login } label { name } }
          }
        }"""
QUERIES = {
    "open_issues": """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number"""
    + _ISSUE_FIELDS
    + """
      }
    }
  }
}""",
    "issue": """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issueOrPullRequest(number: $number) {
      __typename
      ... on Issue {
        number
        state"""
    + _ISSUE_FIELDS
    + """
      }
    }
  }
}""",
    "label_changes": """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issueOrPullRequest(number: $number) {
      __typename
      ... on Issue {"""
    + _LABEL_CHANGES
    + """
      }
      ... on PullRequest {"""
    + _LABEL_CHANGES
    + """
      }
    }
  }
}""",
    "pull": """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    pullRequest(number: $number) {"""
    + _PULL_FIELDS
    + """
    }
  }
}""",
    "open_pulls": """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    pullRequests(states: OPEN, first: 25, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {"""
    + _PULL_FIELDS
    + """
      }
    }
  }
}""",
}


class EventError(Exception):
    """The event payload, the arguments or the environment are not usable."""


class GhError(RuntimeError):
    """A ``gh api`` call failed, or GitHub's answer was not what the API documents."""


class PageLimitError(GhError):
    """A paginated read went on past ``_MAX_PAGES``."""


# (method, endpoint, optional JSON body) -> parsed JSON answer (None for an empty body; text for ``markdown``).
GhApi = Callable[[str, str, Optional[Mapping[str, Any]]], Any]
# Markdown -> GitHub's HTML rendering of it.
Render = Callable[[str], str]


@dataclass(frozen=True)
class Blocker:
    """One "Blocked by" link: the issue it points at and how that issue stands."""

    number: int
    repo: str  # owner/name of the blocker's repository
    state: str  # OPEN or CLOSED
    reason: Optional[str]  # COMPLETED, NOT_PLANNED, DUPLICATE, REOPENED or None

    @property
    def status(self) -> str:
        """``waiting`` (open), ``resolved`` (closed as completed) or ``not-completed`` (closed any other way).

        GitHub records a reason for every close since 2022; an older issue closed before that has none and counts as
        completed, which is what closing meant then.
        """
        if self.state != "CLOSED":
            return "waiting"
        if self.reason in (None, "COMPLETED"):
            return "resolved"
        return "not-completed"

    @property
    def key(self) -> str:
        """``owner/repo#N``, lower-cased: identifies the blocker across repositories."""
        return f"{self.repo.lower()}#{self.number}"

    def ref(self, home: str) -> str:
        return f"#{self.number}" if self.repo.lower() == home.lower() else f"{self.repo}#{self.number}"


@dataclass(frozen=True)
class IssueState:
    """What ``sync`` needs to know about one issue."""

    number: int
    is_open: bool
    labels: frozenset[str]
    blockers: tuple[Blocker, ...]


@dataclass(frozen=True)
class PullFacts:
    """What the check needs to know about one pull request."""

    number: int
    head_sha: str
    labels: frozenset[str]
    title: str
    body: str
    messages: tuple[str, ...]  # every commit message
    closing: frozenset[str]  # `owner/repo#N` keys GitHub will close on merge (empty off the default branch)
    listed: frozenset[str]  # every key in GitHub's closing list, whatever the base branch
    complete: bool  # False when GitHub's lists were longer than one page, so not everything could be read


@dataclass(frozen=True)
class LabelChange:
    """A label added to or removed from an issue or a pull request, as its timeline records it."""

    added: bool
    login: str  # who changed it; empty for a deleted account
    at: str  # when, as GitHub writes it (``2026-10-20T14:30:26Z``), so the strings sort by time


@dataclass(frozen=True)
class Override:
    """``dependency-override`` on a pull request: ``counts`` only when a maintainer set it and the head commit and
    the linked issues are still those that were recorded; ``why`` says why not."""

    counts: bool
    why: str = ""


@dataclass(frozen=True)
class Plan:
    """What ``sync`` does to one issue. ``comment`` is the unblocked notice and ``marker`` its idempotency key;
    ``not_completed`` lists blockers that were closed without being completed (each gets one notice and
    ``needs-triage``)."""

    add_label: bool = False
    remove_label: bool = False
    comment: str = ""
    marker: str = ""
    not_completed: tuple[Blocker, ...] = ()

    @property
    def changes(self) -> bool:
        return self.add_label or self.remove_label or bool(self.not_completed)


def contributing_url(repo: str, anchor: str = "dependencies-between-issues") -> str:
    """A link into CONTRIBUTING.md on the default branch, whatever it is called (``HEAD`` resolves to it)."""
    return f"https://github.com/{repo}/blob/HEAD/CONTRIBUTING.md#{anchor}"


def is_blocked(blockers: Iterable[Blocker]) -> bool:
    """Blocked while any blocker is not resolved: still open, or closed without being completed."""
    return any(b.status != "resolved" for b in blockers)


def plan_issue(issue: IssueState, repo: str, blocked_since: str = "unknown") -> Plan:
    """The rules for one issue: is it blocked, and what changes. ``blocked_since`` is when ``blocked`` was last
    added; it keeps the notice of one unblocking apart from the next."""
    if not issue.is_open:
        return Plan(remove_label=BLOCKED_LABEL in issue.labels)
    has_label = BLOCKED_LABEL in issue.labels
    if is_blocked(issue.blockers):
        not_completed = tuple(b for b in issue.blockers if b.status == "not-completed")
        return Plan(add_label=not has_label, not_completed=not_completed)
    if not has_label:
        return Plan()
    if issue.blockers:
        refs = ", ".join(b.ref(repo) for b in issue.blockers)
        template = MSG_UNBLOCKED_TRIAGE if TRIAGE_LABEL in issue.labels else MSG_UNBLOCKED
        message = template.format(blockers=refs, contributing=contributing_url(repo, "working-on-an-issue"))
        keys = ",".join(sorted(b.key for b in issue.blockers))
    else:
        message = MSG_UNBLOCKED_NO_LINKS.format(contributing=contributing_url(repo))
        keys = "none"
    marker = UNBLOCKED_MARKER.format(blockers=keys, since=blocked_since)
    return Plan(remove_label=True, comment=f"{message}\n\n{marker}", marker=marker)


def not_completed_notice(blocker: Blocker, repo: str) -> str:
    reason = "a duplicate" if blocker.reason == "DUPLICATE" else "not planned"
    text = MSG_NOT_COMPLETED.format(blocker=blocker.ref(repo), reason=reason, mention=MAINTAINER_MENTION)
    return f"{text}\n\n{NOT_COMPLETED_MARKER.format(blocker=blocker.key)}"


class _ProseReader(HTMLParser):
    """The text of GitHub's rendering outside code, with a line break wherever a block starts or ends."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.code_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _CODE_ELEMENTS:
            self.code_depth += 1
        elif tag not in _INLINE_ELEMENTS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _CODE_ELEMENTS:
            self.code_depth = max(0, self.code_depth - 1)
        elif tag not in _INLINE_ELEMENTS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.code_depth:
            self.parts.append(data)


def prose(html: str) -> str:
    """What a reader sees of GitHub's rendering outside ``<code>`` and ``<pre>``. HTML comments are already gone:
    GitHub drops them, as it drops anything its sanitizer does not allow."""
    reader = _ProseReader()
    reader.feed(html)
    reader.close()
    return "".join(reader.parts)


def linked_issues(text: str, repo: str) -> frozenset[int]:
    """Issue numbers of this repository that plain ``text`` references after a keyword, read leniently: a commit
    message, or the ``prose`` of a rendered title or description.

    A colon or a missing space after the keyword still counts, and so does every issue of a list (`Fixes #1, #2`):
    these links can only make the check stricter. Whether GitHub *closes* an issue is never decided here — see
    ``PullFacts.closing``. A reference to another repository is ignored: this repository's links cannot block on it.
    """
    owner, name = repo.lower().split("/")
    found: set[int] = set()
    for match in _LINK_RE.finditer(text or ""):
        if not match.group("sep") and not match.group("refs").startswith("#"):
            continue  # `refsfoo/bar#1` is a word, not a keyword followed by a reference
        for ref in _REF_RE.finditer(match.group("refs")):
            ref_owner = (ref.group("owner") or ref.group("uowner") or owner).lower()
            ref_repo = (ref.group("repo") or ref.group("urepo") or name).lower()
            if (ref_owner, ref_repo) == (owner, name):
                found.add(int(ref.group("num") or ref.group("unum")))
    return frozenset(found)


def pull_links(pull: PullFacts, repo: str, render: Render) -> frozenset[int]:
    """Every issue of this repository the pull request links: what its title and description show once GitHub has
    rendered them, what its commit messages say, and what GitHub lists as closed by it."""
    home = repo.lower()
    linked: set[int] = set()
    for text in (pull.title, pull.body):
        linked |= linked_issues(prose(render(text)), repo)
    for message in pull.messages:
        linked |= linked_issues(message, repo)
    linked |= {int(key.rsplit("#", 1)[1]) for key in pull.listed if key.rsplit("#", 1)[0] == home}
    return frozenset(linked)


def _issue_list(numbers: Iterable[int]) -> str:
    return ", ".join(f"#{n}" for n in sorted(numbers)) or "no issue"


@dataclass(frozen=True)
class Verdict:
    """``check-pr``'s answer: may the pull request merge, and why not."""

    ok: bool
    lines: tuple[str, ...]


def check_pull_request(
    override: Optional[Override],
    pull: PullFacts,
    linked: frozenset[int],
    blockers_of: Callable[[int], Optional[tuple[Blocker, ...]]],
    repo: str,
) -> Verdict:
    """The rules for one pull request that links ``linked``.

    ``override`` is ``None`` without the override label, and says whether it counts otherwise. ``blockers_of``
    returns an open issue's links, or ``None`` for a pull request, a closed issue or a number that does not exist
    (none of those can be blocked work); it may raise ``GhError``.
    """
    accepted = override is not None and override.counts
    notes: tuple[str, ...] = (override.why,) if override is not None and not accepted else ()
    if len(linked) > _MAX_LINKED or not pull.complete:
        why = (
            f"links {len(linked)} issues; the check reads at most {_MAX_LINKED}"
            if len(linked) > _MAX_LINKED
            else f"has more than {_MAX_COMMITS} commits or 50 closing references, so not all of its links can be read"
        )
        if accepted:
            return Verdict(True, (f"This pull request {why}; `{OVERRIDE_LABEL}` was set by a maintainer.",))
        return Verdict(False, (f"This pull request {why}. Split it, or ask a maintainer.",) + notes)
    problems: list[str] = []
    waiting = checked = 0
    for number in sorted(linked):
        blockers = blockers_of(number)
        if blockers is None:
            continue
        checked += 1
        for b in blockers:
            if b.status == "resolved" or (b.status == "waiting" and b.key in pull.closing):
                continue  # completed, or closed by this pull request as well
            if b.status == "waiting":
                waiting += 1
                problems.append(f"#{number} is blocked by {b.ref(repo)}, which is still open.")
            else:
                problems.append(
                    f"#{number} is blocked by {b.ref(repo)}, which was closed without being completed: a maintainer "
                    "has to re-plan that dependency first (the issue is flagged `needs-triage`)."
                )
    if accepted:
        return Verdict(
            True,
            (f"`{OVERRIDE_LABEL}` was set by a maintainer, who accepts:",) + tuple(problems or ["nothing is blocked"]),
        )
    if not problems:
        return Verdict(True, (f"No linked issue is blocked ({checked} issue(s) checked).",) + notes)
    hints = (
        "If this pull request does not work on that issue, remove the reference (title, description and commit "
        "messages count).",
    )
    if waiting:
        hints = (
            "Wait until the blocker is closed as completed (this check is re-run within 15 minutes), close it in this "
            "pull request too (GitHub must list it under Development as closed by this pull request), or ask a "
            "maintainer.",
        ) + hints
    return Verdict(False, tuple(problems) + notes + hints + (f"See {contributing_url(repo)}",))


# ------------------------------------------------------------------------------------------------ GitHub access
def _clean(text: str, limit: int = 200) -> str:
    """One line, bounded, without terminal escapes or control and format characters (a bidirectional override could
    disguise a log line), and with ``::`` defanged so it can never open a workflow command."""
    text = "".join(c if c.isprintable() else " " for c in _ANSI_RE.sub("", text))
    return " ".join(text.split())[:limit].replace("::", ": :")


class Page(list):
    """A list answer, and whether GitHub announced another page after it (``Link: rel="next"``)."""

    has_next = False


def _has_next(raw: list) -> bool:
    """A ``Page`` knows; a plain list (a stand-in in the tests) is taken to go on while it is full."""
    return raw.has_next if isinstance(raw, Page) else len(raw) >= _PAGE_SIZE


def _split_response(stdout: str) -> tuple[Optional[int], dict[str, str], str]:
    """``gh api --include`` output: the HTTP status, the headers (lower-cased names) and the body. Output without a
    status line — ``gh`` failed before GitHub answered — has no status and no headers."""
    if not stdout.startswith("HTTP/"):
        return None, {}, stdout
    head: list[str] = []
    pos = 0
    while True:
        end = stdout.find("\n", pos)
        if end < 0:
            return None, {}, stdout
        line = stdout[pos:end].rstrip("\r")
        pos = end + 1
        if not line:
            break
        head.append(line)
    try:
        status = int(head[0].split()[1])
    except (IndexError, ValueError):
        return None, {}, stdout
    headers: dict[str, str] = {}
    for line in head[1:]:
        name, colon, value = line.partition(":")
        if colon:
            headers[name.strip().lower()] = value.strip()
    return status, headers, stdout[pos:]


def _retry_wait(status: Optional[int], headers: Mapping[str, str], attempt: int) -> Optional[float]:
    """Seconds to wait before reading again after a failure, or ``None`` when the failure is not a transient one.

    A transient failure is no answer at all, a 429 or a 5xx, or a 403 that is a rate limit. GitHub's ``Retry-After``
    or rate-limit reset is honoured, but a wait over ``_MAX_WAIT_SECONDS`` fails the call instead.
    """
    remaining = headers.get("x-ratelimit-remaining")
    limited = status == 403 and ("retry-after" in headers or remaining == "0")
    if status is not None and status not in _TRANSIENT_STATUSES and not limited:
        return None
    wait = _BACKOFF_SECONDS * 2 ** (attempt - 1)
    retry_after, reset = headers.get("retry-after", ""), headers.get("x-ratelimit-reset", "")
    if retry_after.isdigit():
        wait = float(retry_after)
    elif remaining == "0" and reset.isdigit():
        wait = max(0.0, int(reset) - _now().timestamp()) + 1
    return wait if wait <= _MAX_WAIT_SECONDS else None


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _check_call(method: str, endpoint: str, payload: Optional[Mapping[str, Any]]) -> None:
    """Refuse anything outside the fixed endpoints."""
    if method not in _ALLOWED_METHODS or any(c.isspace() for c in endpoint):
        raise GhError("refusing an API call outside the fixed endpoints")
    if endpoint == "graphql":
        if method != "POST" or not isinstance(payload, Mapping) or payload.get("query") not in QUERIES.values():
            raise GhError("refusing a GraphQL document that is not one of this module's queries")
    elif endpoint == "markdown":
        if (
            method != "POST"
            or not isinstance(payload, Mapping)
            or set(payload) != {"text", "mode", "context"}
            or payload["mode"] != "gfm"
            or not isinstance(payload["text"], str)
            or not _REPO_RE.fullmatch(str(payload["context"]))
        ):
            raise GhError("refusing a markdown call that does not render text for this repository")
    elif not endpoint.startswith("repos/"):
        raise GhError("refusing an API call outside the fixed endpoints")


def _answer(endpoint: str, label: str, headers: Mapping[str, str], body: str) -> Any:
    if endpoint == "markdown":
        return body  # HTML
    if not body.strip():
        return None
    try:
        answer = json.loads(body)
    except ValueError as exc:
        raise GhError(f"{label} returned something that is not JSON") from exc
    if isinstance(answer, list):
        page = Page(answer)
        page.has_next = 'rel="next"' in headers.get("link", "")
        return page
    return answer


def gh_api(method: str, endpoint: str, payload: Optional[Mapping[str, Any]] = None) -> Any:
    """Call the GitHub API through the ``gh`` CLI (authenticated by ``GH_TOKEN``); no shell is involved.

    ``--include`` adds the status line and the headers: a read that fails transiently is tried again (``_retry_wait``),
    and a list answer knows from ``Link`` whether another page follows. Only reads are tried again — a GET, a GraphQL
    query, a markdown rendering — never a write. ``gh`` exits non-zero when a GraphQL answer carries errors, even
    partial ones; that answer is returned so that ``graphql`` can decide which errors it accepts.
    """
    _check_call(method, endpoint, payload)
    command = ["gh", "api", "--include", "--method", method, endpoint]
    stdin = None
    if payload is not None:
        command += ["--input", "-"]
        stdin = json.dumps(payload)
    label = f"{method} {endpoint.split('?')[0]}"
    attempts = _ATTEMPTS if method == "GET" or endpoint in ("graphql", "markdown") else 1
    for attempt in range(1, attempts + 1):
        try:
            # A list without a shell; the method and endpoint were validated above, and an endpoint is built only
            # from integers, the repository name, a validated commit SHA or timestamp and fixed path segments.
            proc = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
                command, input=stdin, capture_output=True, text=True, timeout=_GH_TIMEOUT_SECONDS, check=False
            )
        except OSError as exc:
            raise GhError(f"{label} could not run: {type(exc).__name__}") from exc
        except subprocess.TimeoutExpired as exc:
            if attempt == attempts:
                raise GhError(f"{label} could not run: {type(exc).__name__}") from exc
            failure, wait = "timed out", _BACKOFF_SECONDS * 2 ** (attempt - 1)
        else:
            status, headers, body = _split_response(proc.stdout)
            if proc.returncode == 0:
                return _answer(endpoint, label, headers, body)
            if endpoint == "graphql":
                try:
                    answer = json.loads(body)
                except ValueError:
                    answer = None
                if isinstance(answer, Mapping) and "errors" in answer:
                    return answer
            retry = _retry_wait(status, headers, attempt) if attempt < attempts else None
            failure = f"exit {proc.returncode}" + (f", HTTP {status}" if status else "")
            if retry is None:
                raise GhError(f"{label} failed ({failure}): {_clean(proc.stderr)}")
            wait = retry
        print(f"warning: {label} failed ({failure}); trying again in {wait:.0f} s", file=sys.stderr)
        _sleep(wait)
    raise AssertionError("unreachable: the last attempt returns or raises")


def _only_missing_issue(errors: Any) -> bool:
    """Exactly one NOT_FOUND error, for ``repository.issueOrPullRequest``: the number names no issue."""
    return (
        isinstance(errors, list)
        and len(errors) == 1
        and isinstance(errors[0], Mapping)
        and errors[0].get("type") == "NOT_FOUND"
        and errors[0].get("path") == ["repository", "issueOrPullRequest"]
    )


def graphql(gh: GhApi, query: str, *, allow_missing: bool = False, **variables: Any) -> Mapping[str, Any]:
    """Run one of ``QUERIES``. ``allow_missing`` accepts the one error a missing issue number produces; any other
    error, including one for the repository itself, raises."""
    answer = gh("POST", "graphql", {"query": QUERIES[query], "variables": variables})
    errors = answer.get("errors") if isinstance(answer, Mapping) else None
    if errors and allow_missing and _only_missing_issue(errors):
        errors = None
    if not isinstance(answer, Mapping) or errors or not isinstance(answer.get("data"), Mapping):
        first = (
            errors[0].get("message", "")
            if isinstance(errors, list) and errors and isinstance(errors[0], Mapping)
            else ""
        )
        raise GhError(f"GraphQL {query} failed: {_clean(str(first))}")
    return answer["data"]


def _pages(gh: GhApi, endpoint: str, what: str) -> Iterator[list]:
    """Each page of a REST list (``endpoint`` without paging parameters), until GitHub announces no next one."""
    joiner = "&" if "?" in endpoint else "?"
    for page in range(1, _MAX_PAGES + 1):
        raw = gh("GET", f"{endpoint}{joiner}per_page={_PAGE_SIZE}&page={page}", None)
        if not isinstance(raw, list):
            raise GhError(f"the {what} answer is not a list")
        yield raw
        if not _has_next(raw):
            return
    raise PageLimitError(f"more than {_MAX_PAGES} pages of {what}")


def _by_bot(comment: Any) -> bool:
    """A comment this repository's workflows posted: only those carry a marker that counts."""
    user = comment.get("user") if isinstance(comment, Mapping) else None
    return isinstance(user, Mapping) and user.get("login") == BOT_LOGIN and user.get("type") == "Bot"


def _page(connection: Mapping[str, Any], limit: int, what: str, number: Any) -> list:
    if connection["totalCount"] > len(connection["nodes"]):
        raise GhError(f"#{number} has more than {limit} {what}")
    return list(connection["nodes"])


def _issue_state(node: Mapping[str, Any], is_open: bool) -> IssueState:
    try:
        number = int(node["number"])
        labels = frozenset(str(label["name"]) for label in _page(node["labels"], _MAX_LABELS, "labels", number))
        blockers = tuple(
            Blocker(int(b["number"]), str(b["repository"]["nameWithOwner"]), str(b["state"]), b.get("stateReason"))
            for b in _page(node["blockedBy"], _MAX_BLOCKERS, "blockers", number)
        )
        return IssueState(number, is_open, labels, blockers)
    except (KeyError, TypeError, ValueError) as exc:
        raise GhError("an issue answer lacks a field") from exc


def fetch_open_issues(gh: GhApi, repo: str) -> tuple[list[IssueState], set[int]]:
    """Every open issue, and the numbers of those whose links could not be read (each reported to stderr)."""
    owner, name = repo.split("/")
    out: list[IssueState] = []
    broken: set[int] = set()
    after: Optional[str] = None
    for _ in range(_MAX_PAGES):
        data = graphql(gh, "open_issues", owner=owner, name=name, after=after)
        try:
            page = data["repository"]["issues"]
            nodes, info = page["nodes"], page["pageInfo"]
        except (KeyError, TypeError) as exc:
            raise GhError("the open-issues answer lacks a field") from exc
        for node in nodes:
            try:
                out.append(_issue_state(node, True))
            except GhError as exc:
                broken.add(int(node.get("number") or 0))
                print(f"error: {_clean(str(exc))}", file=sys.stderr)
        if not info.get("hasNextPage"):
            return out, broken
        after = str(info["endCursor"])
    raise GhError(f"more than {_MAX_PAGES} pages of open issues")


def fetch_closed_labelled(gh: GhApi, repo: str) -> list[int]:
    """Closed issues and pull requests that still carry ``blocked`` (neither waits for anything)."""
    endpoint = f"repos/{repo}/issues?state=closed&labels={quote(BLOCKED_LABEL)}"
    return [int(i["number"]) for raw in _pages(gh, endpoint, "closed issues") for i in raw if isinstance(i, Mapping)]


def fetch_issue_state(gh: GhApi, repo: str, number: int) -> Optional[IssueState]:
    """One issue as it stands now, or ``None`` when ``number`` is a pull request or names no issue."""
    owner, name = repo.split("/")
    data = graphql(gh, "issue", allow_missing=True, owner=owner, name=name, number=number)
    node = (data.get("repository") or {}).get("issueOrPullRequest")
    if not isinstance(node, Mapping) or node.get("__typename") != "Issue":
        return None
    return _issue_state(node, node.get("state") == "OPEN")


def fetch_blockers_of(gh: GhApi, repo: str, number: int) -> Optional[tuple[Blocker, ...]]:
    """An open issue's links, or ``None`` when ``number`` is a pull request, a closed issue or names no issue."""
    state = fetch_issue_state(gh, repo, number)
    return state.blockers if state is not None and state.is_open else None


def fetch_label_changes(gh: GhApi, repo: str, number: int, label: str) -> list[LabelChange]:
    """The changes of ``label`` among the last 100 label changes of an issue or a pull request, oldest first."""
    owner, name = repo.split("/")
    data = graphql(gh, "label_changes", owner=owner, name=name, number=number)
    changes: list[LabelChange] = []
    try:
        for item in data["repository"]["issueOrPullRequest"]["timelineItems"]["nodes"]:
            if not isinstance(item, Mapping) or (item.get("label") or {}).get("name") != label:
                continue
            at = str(item["createdAt"])
            if not _TIMESTAMP_RE.fullmatch(at):
                raise GhError("a label change carries a malformed time")
            login = str((item.get("actor") or {}).get("login") or "")
            changes.append(LabelChange(item["__typename"] == "LabeledEvent", login, at))
    except (KeyError, TypeError) as exc:
        raise GhError("the label-changes answer lacks a field") from exc
    return sorted(changes, key=lambda change: change.at)  # stable: one second keeps the timeline's order


def blocked_since(gh: GhApi, repo: str, number: int) -> str:
    """When ``blocked`` was last added to the issue, or ``unknown`` when that is not among the changes read."""
    added = [change for change in fetch_label_changes(gh, repo, number, BLOCKED_LABEL) if change.added]
    return added[-1].at if added else "unknown"


def _pull_facts(node: Mapping[str, Any], default_branch: str) -> PullFacts:
    try:
        number, sha = int(node["number"]), str(node["headRefOid"])
        if not _SHA_RE.fullmatch(sha):
            raise GhError(f"pull request #{number} has a malformed head sha")
        labels = frozenset(str(label["name"]) for label in _page(node["labels"], _MAX_LABELS, "labels", number))
        refs, commits = node["closingIssuesReferences"], node["commits"]
        complete = refs["totalCount"] <= len(refs["nodes"]) and commits["totalCount"] <= len(commits["nodes"])
        listed = frozenset(f"{str(r['repository']['nameWithOwner']).lower()}#{int(r['number'])}" for r in refs["nodes"])
        messages = tuple(str((c.get("commit") or {}).get("message") or "") for c in commits["nodes"])
        # GitHub closes on merge only into the default branch; elsewhere nothing is exempted.
        closing = listed if node["baseRefName"] == default_branch else frozenset()
        title, body = str(node.get("title") or ""), str(node.get("body") or "")
        return PullFacts(number, sha, labels, title, body, messages, closing, listed, complete)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise GhError("a pull-request answer lacks a field") from exc


def _default_branch(data: Mapping[str, Any]) -> str:
    try:
        return str(data["repository"]["defaultBranchRef"]["name"])
    except (KeyError, TypeError) as exc:
        raise GhError("the answer lacks the default branch") from exc


def fetch_pull(gh: GhApi, repo: str, number: int) -> PullFacts:
    owner, name = repo.split("/")
    data = graphql(gh, "pull", owner=owner, name=name, number=number)
    node = (data.get("repository") or {}).get("pullRequest")
    if not isinstance(node, Mapping):
        raise GhError(f"pull request #{number} was not found")
    return _pull_facts(node, _default_branch(data))


def render_markdown(gh: GhApi, repo: str, text: str) -> str:
    """GitHub's own rendering of a title or a description, as the pull request shows it."""
    if not text.strip():
        return ""
    html = gh("POST", "markdown", {"text": text, "mode": "gfm", "context": repo})
    if not isinstance(html, str):
        raise GhError("the markdown answer is not text")
    return html


def _maintainer_set_it(changes: Sequence[LabelChange]) -> Optional[LabelChange]:
    """The last change, when it is a maintainer adding the label. Who may *add* a label is a repository permission,
    not a maintainer decision, so the label alone proves nothing; the timeline records who changed it."""
    last = changes[-1] if changes else None
    return last if last is not None and last.added and last.login.lower() in MAINTAINER_LOGINS else None


def _override_record(gh: GhApi, repo: str, number: int, since: str) -> Optional[tuple[str, frozenset[int]]]:
    """The head commit and the linked issues of the latest override record posted at or after ``since``."""
    found: Optional[tuple[str, frozenset[int]]] = None
    for raw in _pages(gh, f"repos/{repo}/issues/{number}/comments?since={quote(since)}", "comments"):
        for comment in raw:
            if not _by_bot(comment) or str(comment.get("created_at") or "") < since:
                continue
            match = _OVERRIDE_MARKER_RE.search(str(comment.get("body") or ""))
            if match:
                found = (match.group(1), frozenset(int(n) for n in match.group(2).split(",") if n))
    return found


def override_status(gh: GhApi, repo: str, pull: PullFacts, linked: frozenset[int]) -> Optional[Override]:
    """Whether ``dependency-override`` counts on this pull request; ``None`` without the label.

    It counts when the last change of the label is a maintainer adding it, and ``record-override`` recorded, after
    that change, the head commit the pull request still has and a set of linked issues that still holds all of
    ``linked``. Removing a link keeps it; anything else the maintainer did not see ends it.
    """
    if OVERRIDE_LABEL not in pull.labels:
        return None
    set_by_maintainer = _maintainer_set_it(fetch_label_changes(gh, repo, pull.number, OVERRIDE_LABEL))
    if set_by_maintainer is None:
        return Override(False, MSG_OVERRIDE_NOT_MAINTAINER)
    record = _override_record(gh, repo, pull.number, set_by_maintainer.at)
    if record is None:
        return Override(False, MSG_OVERRIDE_UNRECORDED)
    head, accepted = record
    if head != pull.head_sha:
        return Override(False, MSG_OVERRIDE_NEW_COMMITS.format(short=head[:7]))
    if linked - accepted:
        return Override(False, MSG_OVERRIDE_NEW_LINKS.format(issues=_issue_list(linked - accepted)))
    return Override(True)


def _already_noticed(gh: GhApi, repo: str, number: int, marker: str) -> bool:
    """True when the bot has posted ``marker`` on the issue. Past the page bound it answers True (and says so), so a
    very long thread cannot make the bot repeat a notice every run."""
    try:
        for raw in _pages(gh, f"repos/{repo}/issues/{number}/comments", "comments"):
            if any(_by_bot(comment) and marker in str(comment.get("body")) for comment in raw):
                return True
    except PageLimitError:
        print(
            f"warning: issue #{number}: too many comments to look for an earlier notice; not posting", file=sys.stderr
        )
        return True
    return False


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _names_pull(run: Mapping[str, Any], number: int) -> bool:
    return any(isinstance(p, Mapping) and p.get("number") == number for p in run.get("pull_requests") or [])


def _reconcile_one(
    gh: GhApi,
    repo: str,
    pull: PullFacts,
    blockers_of: Callable[[int], Optional[tuple[Blocker, ...]]],
    dry_run: bool,
) -> bool:
    """Re-run one pull request's dependency check if its last result no longer matches; True when it did."""
    linked = pull_links(pull, repo, lambda text: render_markdown(gh, repo, text))
    want_ok = check_pull_request(override_status(gh, repo, pull, linked), pull, linked, blockers_of, repo).ok
    runs = gh(
        "GET", f"repos/{repo}/commits/{pull.head_sha}/check-runs?check_name={quote(CHECK_NAME)}&per_page=100", None
    )
    candidates = [
        r for r in (runs or {}).get("check_runs") or [] if (r.get("app") or {}).get("slug") == "github-actions"
    ]
    if not candidates:
        return False  # the check has not run on this commit; the next pull-request event runs it
    # Two pull requests with one head commit share its check runs; prefer the ones GitHub ties to this pull request.
    # A run from a fork often names no pull request at all, so the latest of all is the fallback.
    latest = max([r for r in candidates if _names_pull(r, pull.number)] or candidates, key=lambda r: int(r["id"]))
    if latest.get("status") != "completed":
        return False  # still running on what it read; the next sync compares again
    if (latest.get("conclusion") in PASSING_CONCLUSIONS) == want_ok:
        return False
    started = datetime.datetime.fromisoformat(str(latest.get("started_at") or "").replace("Z", "+00:00"))
    if _now() - started > datetime.timedelta(days=_RERUN_DAYS):
        print(
            f"warning: pull request #{pull.number}: its dependency check is too old to re-run; a push or an edit of the "
            "pull request runs it again",
            file=sys.stderr,
        )
        return False
    if not dry_run:
        # An Actions check run's id is its job's id.
        gh("POST", f"repos/{repo}/actions/jobs/{int(latest['id'])}/rerun", None)
    print(f"{'would re-run' if dry_run else 'did re-run'} the dependency check of pull request #{pull.number}")
    return True


def _unlabel_pull(gh: GhApi, repo: str, pull: PullFacts, dry_run: bool) -> None:
    """``blocked`` describes issues only; on a pull request, where it was set by hand, it goes."""
    if BLOCKED_LABEL not in pull.labels:
        return
    if not dry_run:
        gh("DELETE", f"repos/{repo}/issues/{pull.number}/labels/{quote(BLOCKED_LABEL)}", None)
    print(f"{'would' if dry_run else 'did'} unlabel pull request #{pull.number} {BLOCKED_LABEL}")


def reconcile_pr_checks(
    gh: GhApi, repo: str, open_issues: Mapping[int, IssueState], broken: Iterable[int] = (), dry_run: bool = False
) -> tuple[int, int]:
    """Re-run the dependency check of every open pull request whose last result no longer matches its linked issues:
    red although every blocker is now completed, or green although a linked issue became blocked. A ``blocked``
    label on an open pull request is removed on the way.

    It compares state on every run instead of reacting to a transition, so a failed re-run, a check that was still
    running, or a sync that stopped half-way is simply caught by the next run. Optional by design: a failure is a
    warning, never a failed sync. Returns ``(pull requests looked at, checks re-run)``.
    """
    unreadable = frozenset(broken)

    def blockers_of(number: int) -> Optional[tuple[Blocker, ...]]:
        if number in unreadable:
            raise GhError(f"the Blocked-by links of #{number} could not be read")
        issue = open_issues.get(number)
        return issue.blockers if issue is not None else None

    owner, name = repo.split("/")
    seen = rerun = 0
    try:
        after: Optional[str] = None
        for _ in range(_MAX_PAGES):
            data = graphql(gh, "open_pulls", owner=owner, name=name, after=after)
            default_branch = _default_branch(data)
            page = data["repository"]["pullRequests"]
            for node in page["nodes"]:
                seen += 1
                try:
                    pull = _pull_facts(node, default_branch)
                except GhError as exc:
                    print(f"warning: could not read an open pull request: {_clean(str(exc))}", file=sys.stderr)
                    continue
                try:
                    rerun += _reconcile_one(gh, repo, pull, blockers_of, dry_run)
                except (GhError, AttributeError, KeyError, TypeError, ValueError) as exc:
                    print(f"warning: could not reconcile a pull request's check: {_clean(str(exc))}", file=sys.stderr)
                try:
                    _unlabel_pull(gh, repo, pull, dry_run)
                except GhError as exc:
                    print(f"warning: pull request #{pull.number}: {_clean(str(exc))}", file=sys.stderr)
            if not page["pageInfo"].get("hasNextPage"):
                break
            after = str(page["pageInfo"]["endCursor"])
        else:
            print(f"warning: more than {_MAX_PAGES} pages of open pull requests; the rest wait", file=sys.stderr)
    except (GhError, AttributeError, KeyError, TypeError, ValueError) as exc:
        print(f"warning: could not list the open pull requests: {_clean(str(exc))}", file=sys.stderr)
    return seen, rerun


# ----------------------------------------------------------------------------------------------------- commands
def _sync_issue(gh: GhApi, repo: str, issue: IssueState, dry_run: bool) -> bool:
    """Apply the rules to one issue; True when an open issue was unblocked."""
    plan = plan_issue(issue, repo)
    if plan.remove_label and issue.is_open and not dry_run:
        # Read the issue again right before acting on it: a blocker reopened since the snapshot must not lead to an
        # "unblocked" notice that a later run cannot take back.
        fresh = fetch_issue_state(gh, repo, issue.number)
        if fresh is None:
            print(f"skipped #{issue.number}: no longer an issue")
            return False
        issue, plan = fresh, plan_issue(fresh, repo)
        if plan.comment:
            plan = plan_issue(fresh, repo, blocked_since(gh, repo, fresh.number))
    verb = "would" if dry_run else "did"
    if plan.add_label:
        if not dry_run:
            gh("POST", f"repos/{repo}/issues/{issue.number}/labels", {"labels": [BLOCKED_LABEL]})
        print(f"{verb} label #{issue.number} {BLOCKED_LABEL}")
    if plan.remove_label:
        if not dry_run:
            # The notice goes first and is idempotent through its marker, and the label goes last: if anything fails in
            # between, the label is still there and the next run finishes the job without a second notice.
            if plan.comment and not _already_noticed(gh, repo, issue.number, plan.marker):
                gh("POST", f"repos/{repo}/issues/{issue.number}/comments", {"body": plan.comment})
            gh("DELETE", f"repos/{repo}/issues/{issue.number}/labels/{quote(BLOCKED_LABEL)}", None)
        print(f"{verb} unlabel #{issue.number} {BLOCKED_LABEL}" + (" and comment" if plan.comment else ""))
    for b in plan.not_completed:
        if dry_run:
            print(f"would check #{issue.number}: blocker {b.ref(repo)} was not completed")
            continue
        if _already_noticed(gh, repo, issue.number, NOT_COMPLETED_MARKER.format(blocker=b.key)):
            continue
        gh("POST", f"repos/{repo}/issues/{issue.number}/labels", {"labels": [TRIAGE_LABEL]})
        gh("POST", f"repos/{repo}/issues/{issue.number}/comments", {"body": not_completed_notice(b, repo)})
        print(f"did flag #{issue.number}: blocker {b.ref(repo)} was not completed")
    return plan.remove_label and issue.is_open


def run_sync(repo: str, gh: GhApi, dry_run: bool = False) -> int:
    open_issues, broken = fetch_open_issues(gh, repo)
    failures = len(broken)
    try:
        closed = [IssueState(n, False, frozenset({BLOCKED_LABEL}), ()) for n in fetch_closed_labelled(gh, repo)]
    except GhError as exc:
        failures += 1
        closed = []
        print(f"error: could not list closed issues that keep `{BLOCKED_LABEL}`: {_clean(str(exc))}", file=sys.stderr)
    blocked = sum(1 for i in open_issues if is_blocked(i.blockers))
    unblocked = 0
    for issue in open_issues + closed:
        # One issue that cannot be written (locked, at its label limit, ...) must not stop the others, nor the
        # pull-request reconciliation below.
        try:
            unblocked += _sync_issue(gh, repo, issue, dry_run)
        except GhError as exc:
            failures += 1
            print(f"error: issue #{issue.number}: {_clean(str(exc))}", file=sys.stderr)
    seen, rerun = reconcile_pr_checks(gh, repo, {i.number: i for i in open_issues}, broken, dry_run)
    print(
        f"open issues: {len(open_issues)}, blocked: {blocked}, unblocked now: {unblocked}, "
        f"open pull requests: {seen}, dependency checks re-run: {rerun}, failures: {failures}"
    )
    return 1 if failures else 0


def _pull_number(event: Mapping[str, Any]) -> int:
    try:
        number = event["pull_request"]["number"]
    except (KeyError, TypeError) as exc:
        raise EventError("event payload has no pull_request.number") from exc
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise EventError("pull request number is not a positive integer")
    return number


def run_check_pr(event: Mapping[str, Any], repo: str, gh: GhApi) -> int:
    number = _pull_number(event)
    # Read the pull request again rather than trust the event: a re-run replays the original payload, and the
    # description, the labels or the commits may have changed since.
    pull = fetch_pull(gh, repo, number)
    linked = pull_links(pull, repo, lambda text: render_markdown(gh, repo, text))
    override = override_status(gh, repo, pull, linked)
    verdict = check_pull_request(override, pull, linked, lambda n: fetch_blockers_of(gh, repo, n), repo)
    for line in verdict.lines:
        print(line if verdict.ok else f"::error::{line}")
    return 0 if verdict.ok else _EXIT_BLOCKED


def run_record_override(event: Mapping[str, Any], repo: str, gh: GhApi) -> int:
    """Record the head commit and the linked issues a maintainer's ``dependency-override`` accepts.

    The event is the label being added, so its payload holds the head commit and the title and description as they
    stood at that moment; the commit messages belong to that head commit. Nothing is recorded when the label was not
    set by a maintainer, or when the pull request already has a new head: then the override does not count, and the
    check says so.
    """
    number = _pull_number(event)
    try:
        pull_event = event["pull_request"]
        head = str(pull_event["head"]["sha"])
        title, body = str(pull_event.get("title") or ""), str(pull_event.get("body") or "")
    except (AttributeError, KeyError, TypeError) as exc:
        raise EventError("event payload has no pull_request.head.sha") from exc
    if not _SHA_RE.fullmatch(head):
        raise EventError("the event payload's head sha is malformed")
    label = event.get("label")
    if event.get("action") != "labeled" or not isinstance(label, Mapping) or label.get("name") != OVERRIDE_LABEL:
        print(f"not `{OVERRIDE_LABEL}` being added; nothing to record")
        return 0
    set_by_maintainer = _maintainer_set_it(fetch_label_changes(gh, repo, number, OVERRIDE_LABEL))
    if set_by_maintainer is None:
        print(f"`{OVERRIDE_LABEL}` was not set by a maintainer; nothing recorded")
        return 0
    pull = fetch_pull(gh, repo, number)
    if pull.head_sha != head:
        print(f"pull request #{number} has a new head since the label was set; nothing recorded")
        return 0
    seen = dataclasses.replace(pull, title=title, body=body)
    linked = pull_links(seen, repo, lambda text: render_markdown(gh, repo, text))
    if _override_record(gh, repo, number, set_by_maintainer.at) == (head, linked):
        print(f"`{OVERRIDE_LABEL}` on pull request #{number} is already recorded")
        return 0
    text = MSG_OVERRIDE_RECORDED.format(short=head[:7], issues=_issue_list(linked))
    marker = OVERRIDE_MARKER.format(head=head, issues=",".join(str(n) for n in sorted(linked)))
    gh("POST", f"repos/{repo}/issues/{number}/comments", {"body": f"{text}\n\n{marker}"})
    print(f"recorded `{OVERRIDE_LABEL}` on pull request #{number} for {head[:7]} and {len(linked)} linked issue(s)")
    return 0


def _repo_from_env() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not _REPO_RE.fullmatch(repo):
        raise EventError("GITHUB_REPOSITORY must be set to owner/name")
    return repo


def _event_from_env() -> Mapping[str, Any]:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        raise EventError("GITHUB_EVENT_PATH must be set")
    try:
        with open(event_path, encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, ValueError) as exc:
        raise EventError(f"cannot read the event payload: {type(exc).__name__}") from exc
    if not isinstance(event, Mapping):
        raise EventError("the event payload is not an object")
    return event


def main(argv: Optional[Sequence[str]] = None, gh: GhApi = gh_api) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="bring the blocked label in step with the Blocked-by links")
    sync.add_argument("--dry-run", action="store_true", help="print what would change, change nothing")
    sub.add_parser("check-pr", help="fail when the pull request links a blocked issue (reads GITHUB_EVENT_PATH)")
    sub.add_parser(
        "record-override", help="record what a maintainer's dependency-override accepts (reads GITHUB_EVENT_PATH)"
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on a usage error and 0 on --help
        return int(exc.code or 0)
    try:
        repo = _repo_from_env()
        if args.command == "sync":
            return run_sync(repo, gh, dry_run=args.dry_run)
        event = _event_from_env()
        if args.command == "record-override":
            return run_record_override(event, repo, gh)
        return run_check_pr(event, repo, gh)
    except EventError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
