#!/usr/bin/env python3
"""Keep the ``blocked`` label in step with GitHub's "Blocked by" links, and stop pull requests on blocked issues.

Two workflows run this script:

- ``.github/workflows/issue-dependencies.yml`` runs ``sync`` when an issue is closed or reopened, hourly, and on
  demand. It brings the ``blocked`` label of every issue in line with its native "Blocked by" links, and re-runs the
  "Issue dependencies" check of every open pull request whose last result no longer matches its linked issues.
- ``.github/workflows/pr-dependencies.yml`` runs ``check-pr`` on every pull-request change. It fails while the pull
  request links an issue that is still blocked.

Rules (documented in ``CONTRIBUTING.md``, "Dependencies between issues"):

- A dependency is a native "Blocked by" link, set by a maintainer in the issue's sidebar. Prose such as "after #12"
  is not one.
- A blocker is resolved only when it is closed as completed. While it is open the dependent waits. When it is closed
  as not planned or as a duplicate, the work the dependent waits for was not done: the dependent stays blocked, gets
  ``needs-triage`` and one comment asking a maintainer to re-plan it (remove the link, or link the issue that replaces
  the blocker).
- ``blocked`` is derived and never set by hand: ``sync`` adds it to every open issue with an unresolved blocker and
  removes it everywhere else, from a closed issue and from one where someone set it by hand. When it removes the
  label from an open issue it posts one comment; it does not comment when an issue becomes blocked.
- ``check-pr`` fails when the pull request's description links a blocked issue with ``Fixes``, ``Closes``,
  ``Resolves`` or ``Refs`` — unless the same pull request closes every open blocker, or a maintainer listed in
  ``MAINTAINER_LOGINS`` has labelled it ``dependency-override`` (the label set by anyone else is ignored). It reads
  the links themselves, not the label, so it never trails behind ``sync``.

Reading a description. A *link* is read leniently (a colon or a missing space after the keyword still counts),
because reading too many links can only make the check stricter. A *closing* link is read exactly as GitHub closes
issues — ``Fixes #12`` or ``Fixes owner/repo#12``, the keyword followed by whitespace, one keyword per issue — because
it exempts a blocker, and an exemption GitHub would not honour lets blocked work through. Fenced code, inline code
and HTML comments are skipped (the template's own examples live there); an unclosed fence or comment runs to the end,
as in CommonMark. The check sees what a description declares: it cannot see work on an issue the description does not
link, so it guards against honest mistakes, and a maintainer's review remains the backstop.

Trust boundary. Issue and pull-request text is written by anyone, so the script never executes or prints it: it
parses the description in linear time and writes only fixed messages and issue numbers to the log. The workflows pass
nothing through the shell; the script reads the event file itself. Every subprocess call is a list without a shell,
limited to fixed endpoints (``repos/`` paths built from integers, and ``graphql`` with a query constant from this
module).

Exit codes (per ``tools/`` contract, not the public 0-6 surface of ``forgelm/``):

- ``0`` — done: labels in step, or the pull request may merge.
- ``1`` — unexpected failure: an API call failed, or GitHub's answer was not what its API documents.
- ``2`` — the event payload, the arguments or the environment are unusable.
- ``3`` — ``check-pr`` only: the pull request links an issue that is still blocked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import quote

BLOCKED_LABEL = "blocked"
OVERRIDE_LABEL = "dependency-override"
TRIAGE_LABEL = "needs-triage"
CHECK_NAME = "Issue dependencies"  # the job name in pr-dependencies.yml, and the required check's context
# Hidden markers that make a notice idempotent: only a bot's comment counts, so a person cannot forge one to silence
# a notice. `{blocker}` / `{blockers}` hold `owner/repo#N` references, so blockers in two repositories never collide.
NOT_COMPLETED_MARKER = "<!-- issue-dependencies:not-completed:{blocker} -->"
UNBLOCKED_MARKER = "<!-- issue-dependencies:unblocked:{blockers} -->"

_GH_TIMEOUT_SECONDS = 60
_PAGE_SIZE = 100
_MAX_PAGES = 30  # 3,000 items: far above the backlog; bounds every paginated read
_MAX_BLOCKERS = 50  # GitHub shows at most this many "Blocked by" links per issue
_MAX_LABELS = 100  # GitHub allows at most this many labels per issue
_ALLOWED_METHODS = frozenset({"GET", "POST", "DELETE"})

MSG_UNBLOCKED = (
    "This issue is no longer blocked: every issue it waited for is closed as completed ({blockers}). It can be "
    "picked up now; see [Working on an issue]({contributing})."
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

MAINTAINER_LOGINS = frozenset({"cemililik"})  # MAINTAINERS.md; tests/test_issue_dependencies.py keeps them equal
MAINTAINER_MENTION = " ".join(f"@{login}" for login in sorted(MAINTAINER_LOGINS))

# A reference in a pull-request description: `#12`, `owner/repo#12` or an issue URL, after a keyword. GitHub closes
# an issue for close/fix/resolve and their inflections; `Refs` is this project's word for "part of". Every class is
# explicit ASCII (GitHub's keywords, logins and repository names are ASCII; docs/standards/regex.md rule 1).
_KEYWORD = r"(?P<kw>close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|references)"
_OWNER, _NAME = r"[A-Za-z0-9-]+", r"[A-Za-z0-9._-]+"
_REF_RE = re.compile(
    rf"(?:(?P<owner>{_OWNER})/(?P<repo>{_NAME}))?#(?P<num>\d+)"
    rf"|https://github\.com/(?P<uowner>{_OWNER})/(?P<urepo>{_NAME})/issues/(?P<unum>\d+)"
)
_REF_PLAIN = rf"(?:(?:{_OWNER}/{_NAME})?#\d+|https://github\.com/{_OWNER}/{_NAME}/issues/\d+)"
# Horizontal whitespace only (rule 5), and the optional colon owns the whitespace after it, so no two quantifiers
# compete for the same characters (rule 4).
_LINK_RE = re.compile(
    rf"(?<![A-Za-z0-9_-]){_KEYWORD}(?P<sep>[ \t]*(?::[ \t]*)?)"
    rf"(?P<refs>{_REF_PLAIN}(?:[ \t]*(?:,|and|&)[ \t]*{_REF_PLAIN})*)",
    re.IGNORECASE,
)
_CLOSING = frozenset({"close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves", "resolved"})
_FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})")  # matched at the start of one line by the walker below
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")  # stops at the next backtick or newline: linear

# Every GraphQL document this script may send. A query is chosen by name; only integer and cursor variables vary.
_ISSUE_FIELDS = """
        labels(first: 100) { totalCount nodes { name } }
        blockedBy(first: 50) { totalCount nodes { number state stateReason repository { nameWithOwner } } }"""
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
}


class EventError(Exception):
    """The event payload, the arguments or the environment are not usable."""


class GhError(RuntimeError):
    """A ``gh api`` call failed, or GitHub's answer was not what the API documents."""


# (method, endpoint, optional JSON body) -> parsed JSON answer (None for an empty body).
GhApi = Callable[[str, str, Optional[Mapping[str, Any]]], Any]


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
    return f"https://github.com/{repo}/blob/development/CONTRIBUTING.md#{anchor}"


def is_blocked(blockers: Iterable[Blocker]) -> bool:
    """Blocked while any blocker is not resolved: still open, or closed without being completed."""
    return any(b.status != "resolved" for b in blockers)


def plan_issue(issue: IssueState, repo: str) -> Plan:
    """The rules for one issue: is it blocked, and what changes."""
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
        message = MSG_UNBLOCKED.format(blockers=refs, contributing=contributing_url(repo, "working-on-an-issue"))
        marker = UNBLOCKED_MARKER.format(blockers=",".join(sorted(b.key for b in issue.blockers)))
    else:
        message = MSG_UNBLOCKED_NO_LINKS.format(contributing=contributing_url(repo))
        marker = UNBLOCKED_MARKER.format(blockers="none")
    return Plan(remove_label=True, comment=f"{message}\n\n{marker}", marker=marker)


def not_completed_notice(blocker: Blocker, repo: str) -> str:
    reason = "a duplicate" if blocker.reason == "DUPLICATE" else "not planned"
    text = MSG_NOT_COMPLETED.format(blocker=blocker.ref(repo), reason=reason, mention=MAINTAINER_MENTION)
    return f"{text}\n\n{NOT_COMPLETED_MARKER.format(blocker=blocker.key)}"


def _strip_fences(body: str) -> str:
    """Drop fenced code blocks with a line walker (linear; docs/standards/regex.md rule 6).

    A fence opens with three or more backticks or tildes after at most three spaces (a backtick fence's info string
    may not contain a backtick) and closes on a line holding the same character at least as many times and nothing
    else. A fence that never closes runs to the end, as in CommonMark.
    """
    kept: list[str] = []
    open_fence: Optional[tuple[str, int]] = None
    for line in body.splitlines():
        match = _FENCE_RE.match(line)
        if open_fence is None:
            if match and not (match.group(1)[0] == "`" and "`" in line[match.end() :]):
                open_fence = (match.group(1)[0], len(match.group(1)))
                continue
            kept.append(line)
        elif (
            match
            and match.group(1)[0] == open_fence[0]
            and len(match.group(1)) >= open_fence[1]
            and not line[match.end() :].strip(" \t")
        ):
            open_fence = None
    return "\n".join(kept)


def _strip_html_comments(text: str) -> str:
    """Drop ``<!-- … -->`` with ``str.find`` (linear). A comment that never closes runs to the end."""
    out: list[str] = []
    start = 0
    while True:
        opening = text.find("<!--", start)
        if opening < 0:
            out.append(text[start:])
            return "".join(out)
        out.append(text[start:opening] + " ")
        closing = text.find("-->", opening + 4)
        if closing < 0:
            return "".join(out)
        start = closing + 3


def _strip_non_prose(body: str) -> str:
    """Drop fenced code, HTML comments and inline code: the template's own examples live there."""
    return _INLINE_CODE_RE.sub(" ", _strip_html_comments(_strip_fences(body)))


def linked_issues(body: str, repo: str) -> tuple[frozenset[int], frozenset[int]]:
    """``(closing, all)``: issue numbers of this repository linked from a pull-request description.

    ``all`` holds every linked issue, read leniently; ``closing`` the ones GitHub will close on merge, read strictly
    (see the module docstring). A reference to another repository is ignored: this repository's links cannot block on
    it, and its workflow cannot close it.
    """
    owner, name = repo.lower().split("/")
    closing: set[int] = set()
    every: set[int] = set()
    for match in _LINK_RE.finditer(_strip_non_prose(body or "")):
        keyword, sep, refs = match.group("kw").lower(), match.group("sep"), match.group("refs")
        if not sep and not refs.startswith("#"):
            continue  # `refsfoo/bar#1` is a word, not a keyword followed by a reference
        # GitHub closes only `KEYWORD #N` / `KEYWORD owner/repo#N`: whitespace after the keyword, no colon, one
        # keyword per issue — so only the first reference of a list, and never a URL.
        strict = keyword in _CLOSING and bool(sep) and not sep.strip(" \t")
        for position, ref in enumerate(_REF_RE.finditer(refs)):
            ref_owner = (ref.group("owner") or ref.group("uowner") or owner).lower()
            ref_repo = (ref.group("repo") or ref.group("urepo") or name).lower()
            if (ref_owner, ref_repo) != (owner, name):
                continue
            number = int(ref.group("num") or ref.group("unum"))
            every.add(number)
            if strict and position == 0 and ref.group("num"):
                closing.add(number)
    return frozenset(closing), frozenset(every)


@dataclass(frozen=True)
class Verdict:
    """``check-pr``'s answer: may the pull request merge, and why not."""

    ok: bool
    lines: tuple[str, ...]


def check_pull_request(
    override: Optional[bool],
    closing: frozenset[int],
    linked: frozenset[int],
    blockers_of: Callable[[int], Optional[tuple[Blocker, ...]]],
    repo: str,
) -> Verdict:
    """The rules for one pull request.

    ``override`` is ``None`` without the override label, ``True`` when a maintainer set it and ``False`` when someone
    else did (then it is ignored). ``blockers_of`` returns an open issue's links, or ``None`` for a pull request, a
    closed issue or a number that does not exist (none of those can be blocked work).
    """
    if override:
        return Verdict(True, (f"`{OVERRIDE_LABEL}` was set by a maintainer, who accepted the linked dependencies.",))
    notes: tuple[str, ...] = ()
    if override is False:
        notes = (f"`{OVERRIDE_LABEL}` is ignored: only a maintainer listed in MAINTAINERS.md can set it.",)
    problems: list[str] = []
    checked = 0
    for number in sorted(linked):
        blockers = blockers_of(number)
        if blockers is None:
            continue
        checked += 1
        for b in blockers:
            if b.status == "resolved":
                continue
            if b.status == "waiting" and b.repo.lower() == repo.lower() and b.number in closing:
                continue  # this pull request closes the blocker as well
            why = "is still open" if b.status == "waiting" else "was closed without being completed"
            problems.append(f"#{number} is blocked by {b.ref(repo)}, which {why}.")
    if problems:
        return Verdict(
            False,
            tuple(problems)
            + notes
            + (
                "Wait until the blocker is closed as completed (this check is re-run within the hour), close it in "
                f"this pull request too (`Fixes #N` for each), or ask a maintainer. See {contributing_url(repo)}",
            ),
        )
    return Verdict(True, (f"No linked issue is blocked ({checked} issue(s) checked).",) + notes)


# ------------------------------------------------------------------------------------------------ GitHub access
def _clean(text: str, limit: int = 200) -> str:
    """One line, bounded, with ``::`` defanged so it can never open a workflow command."""
    return " ".join(text.split())[:limit].replace("::", ": :")


def gh_api(method: str, endpoint: str, payload: Optional[Mapping[str, Any]] = None) -> Any:
    """Call the GitHub API through the ``gh`` CLI (authenticated by ``GH_TOKEN``); no shell is involved."""
    if method not in _ALLOWED_METHODS or any(c.isspace() for c in endpoint):
        raise GhError("refusing an API call outside the fixed endpoints")
    if endpoint == "graphql":
        if method != "POST" or not isinstance(payload, Mapping) or payload.get("query") not in QUERIES.values():
            raise GhError("refusing a GraphQL document that is not one of this module's queries")
    elif not endpoint.startswith("repos/"):
        raise GhError("refusing an API call outside the fixed endpoints")
    command = ["gh", "api", "--method", method, endpoint]
    stdin = None
    if payload is not None:
        command += ["--input", "-"]
        stdin = json.dumps(payload)
    label = f"{method} {endpoint.split('?')[0]}"
    try:
        # A list without a shell; the method and endpoint were validated above, and an endpoint is built only from
        # integers, the repository name, a validated commit SHA and fixed path segments.
        proc = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
            command, input=stdin, capture_output=True, text=True, timeout=_GH_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GhError(f"{label} could not run: {type(exc).__name__}") from exc
    if proc.returncode != 0:
        raise GhError(f"{label} failed (exit {proc.returncode}): {_clean(proc.stderr)}")
    if not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise GhError(f"{label} returned something that is not JSON") from exc


def graphql(gh: GhApi, query: str, **variables: Any) -> Mapping[str, Any]:
    answer = gh("POST", "graphql", {"query": QUERIES[query], "variables": variables})
    if not isinstance(answer, Mapping) or answer.get("errors") or not isinstance(answer.get("data"), Mapping):
        errors = answer.get("errors") if isinstance(answer, Mapping) else None
        first = (
            errors[0].get("message", "")
            if isinstance(errors, list) and errors and isinstance(errors[0], Mapping)
            else ""
        )
        raise GhError(f"GraphQL {query} failed: {_clean(str(first))}")
    return answer["data"]


def _blockers(node: Mapping[str, Any]) -> tuple[Blocker, ...]:
    try:
        links = node["blockedBy"]
        if links["totalCount"] > len(links["nodes"]):
            raise GhError(f"issue #{node['number']} has more than {_MAX_BLOCKERS} blockers")
        return tuple(
            Blocker(int(b["number"]), str(b["repository"]["nameWithOwner"]), str(b["state"]), b.get("stateReason"))
            for b in links["nodes"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GhError("a blockedBy answer lacks a field") from exc


def _labels(node: Mapping[str, Any]) -> frozenset[str]:
    try:
        labels = node["labels"]
        if labels["totalCount"] > len(labels["nodes"]):
            raise GhError(f"issue #{node['number']} has more than {_MAX_LABELS} labels")
        return frozenset(str(label["name"]) for label in labels["nodes"])
    except (KeyError, TypeError) as exc:
        raise GhError("a labels answer lacks a field") from exc


def _issue_state(node: Mapping[str, Any], is_open: bool) -> IssueState:
    try:
        return IssueState(int(node["number"]), is_open, _labels(node), _blockers(node))
    except (KeyError, TypeError, ValueError) as exc:
        raise GhError("an issue answer lacks a field") from exc


def fetch_open_issues(gh: GhApi, repo: str) -> list[IssueState]:
    owner, name = repo.split("/")
    out: list[IssueState] = []
    after: Optional[str] = None
    for _ in range(_MAX_PAGES):
        data = graphql(gh, "open_issues", owner=owner, name=name, after=after)
        try:
            page = data["repository"]["issues"]
            nodes, info = page["nodes"], page["pageInfo"]
        except (KeyError, TypeError) as exc:
            raise GhError("the open-issues answer lacks a field") from exc
        out += [_issue_state(n, True) for n in nodes]
        if not info.get("hasNextPage"):
            return out
        after = str(info["endCursor"])
    raise GhError(f"more than {_MAX_PAGES} pages of open issues")


def fetch_closed_labelled(gh: GhApi, repo: str) -> list[int]:
    """Closed issues that still carry ``blocked`` (a closed issue no longer waits for anything)."""
    numbers: list[int] = []
    for page in range(1, _MAX_PAGES + 1):
        raw = gh(
            "GET",
            f"repos/{repo}/issues?state=closed&labels={quote(BLOCKED_LABEL)}&per_page={_PAGE_SIZE}&page={page}",
            None,
        )
        if not isinstance(raw, list):
            raise GhError("the closed-issues answer is not a list")
        numbers += [int(i["number"]) for i in raw if isinstance(i, Mapping) and "pull_request" not in i]
        if len(raw) < _PAGE_SIZE:
            return numbers
    raise GhError(f"more than {_MAX_PAGES} pages of closed issues")


def fetch_issue_state(gh: GhApi, repo: str, number: int) -> Optional[IssueState]:
    """One issue as it stands now, or ``None`` when ``number`` is a pull request or does not exist."""
    owner, name = repo.split("/")
    data = graphql(gh, "issue", owner=owner, name=name, number=number)
    node = (data.get("repository") or {}).get("issueOrPullRequest")
    if not isinstance(node, Mapping) or node.get("__typename") != "Issue":
        return None
    return _issue_state(node, node.get("state") == "OPEN")


def fetch_blockers_of(gh: GhApi, repo: str, number: int) -> Optional[tuple[Blocker, ...]]:
    """An open issue's links, or ``None`` when ``number`` is a pull request, a closed issue or does not exist."""
    state = fetch_issue_state(gh, repo, number)
    return state.blockers if state is not None and state.is_open else None


def override_authorized(gh: GhApi, repo: str, number: int) -> bool:
    """True when the latest change of the override label on this pull request is a maintainer adding it.

    Who may *add* a label is a repository permission, not a maintainer decision, so the label alone proves nothing.
    The issue events (oldest first) record who added or removed it.
    """
    last: Optional[Mapping[str, Any]] = None
    for page in range(1, _MAX_PAGES + 1):
        raw = gh("GET", f"repos/{repo}/issues/{number}/events?per_page={_PAGE_SIZE}&page={page}", None)
        if not isinstance(raw, list):
            raise GhError("the events answer is not a list")
        for event in raw:
            if (
                isinstance(event, Mapping)
                and event.get("event") in ("labeled", "unlabeled")
                and (event.get("label") or {}).get("name") == OVERRIDE_LABEL
            ):
                last = event
        if len(raw) < _PAGE_SIZE:
            break
    else:
        raise GhError(f"more than {_MAX_PAGES} pages of events")
    if last is None or last.get("event") != "labeled":
        return False
    return str((last.get("actor") or {}).get("login") or "").lower() in MAINTAINER_LOGINS


def _already_noticed(gh: GhApi, repo: str, number: int, marker: str) -> bool:
    for page in range(1, _MAX_PAGES + 1):
        raw = gh("GET", f"repos/{repo}/issues/{number}/comments?per_page={_PAGE_SIZE}&page={page}", None)
        if not isinstance(raw, list):
            raise GhError("the comments answer is not a list")
        for comment in raw:
            user = comment.get("user") if isinstance(comment, Mapping) else None
            if isinstance(user, Mapping) and user.get("type") == "Bot" and marker in str(comment.get("body")):
                return True
        if len(raw) < _PAGE_SIZE:
            return False
    return False


def _reconcile_one(
    gh: GhApi,
    repo: str,
    pull: Mapping[str, Any],
    blockers_of: Callable[[int], Optional[tuple[Blocker, ...]]],
    dry_run: bool,
) -> bool:
    """Re-run one pull request's dependency check if its last result no longer matches; True when it did."""
    number, sha = int(pull["number"]), str(pull["head"]["sha"])
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GhError("a pull request's head sha is malformed")
    labels = frozenset(str(label["name"]) for label in pull["labels"])
    closing, linked = linked_issues(str(pull.get("body") or ""), repo)
    override = override_authorized(gh, repo, number) if OVERRIDE_LABEL in labels else None
    want_ok = check_pull_request(override, closing, linked, blockers_of, repo).ok
    runs = gh("GET", f"repos/{repo}/commits/{sha}/check-runs?check_name={quote(CHECK_NAME)}&per_page=100", None)
    candidates = [
        r for r in (runs or {}).get("check_runs") or [] if (r.get("app") or {}).get("slug") == "github-actions"
    ]
    if not candidates:
        return False  # the check has not run on this commit; the next pull-request event runs it
    latest = max(candidates, key=lambda r: int(r["id"]))
    if latest.get("status") != "completed":
        return False  # still running on what it read; the next sync compares again
    if (latest.get("conclusion") == "success") == want_ok:
        return False
    print(f"{'would re-run' if dry_run else 'did re-run'} the dependency check of pull request #{number}")
    if not dry_run:
        # An Actions check run's id is its job's id.
        gh("POST", f"repos/{repo}/actions/jobs/{int(latest['id'])}/rerun", None)
    return True


def reconcile_pr_checks(
    gh: GhApi, repo: str, open_issues: Mapping[int, IssueState], dry_run: bool = False
) -> tuple[int, int]:
    """Re-run the dependency check of every open pull request whose last result no longer matches its linked issues:
    red although every blocker is now completed, or green although a linked issue became blocked.

    It compares state on every run instead of reacting to a transition, so a failed re-run, a check that was still
    running, or a sync that stopped half-way is simply caught by the next run. Optional by design: a failure is a
    warning, never a failed sync. Returns ``(pull requests looked at, checks re-run)``.
    """

    def blockers_of(number: int) -> Optional[tuple[Blocker, ...]]:
        issue = open_issues.get(number)
        return issue.blockers if issue is not None else None

    seen = rerun = 0
    try:
        for page in range(1, _MAX_PAGES + 1):
            pulls = gh("GET", f"repos/{repo}/pulls?state=open&per_page={_PAGE_SIZE}&page={page}", None)
            if not isinstance(pulls, list):
                raise GhError("the pull-request list is not a list")
            for pull in pulls:
                seen += 1
                try:
                    rerun += _reconcile_one(gh, repo, pull, blockers_of, dry_run)
                except (GhError, AttributeError, KeyError, TypeError, ValueError) as exc:
                    print(f"warning: could not reconcile a pull request's check: {_clean(str(exc))}", file=sys.stderr)
            if len(pulls) < _PAGE_SIZE:
                break
        else:
            print(f"warning: more than {_MAX_PAGES} pages of open pull requests; the rest wait", file=sys.stderr)
    except (GhError, AttributeError, KeyError, TypeError, ValueError) as exc:
        print(f"warning: could not list the open pull requests: {_clean(str(exc))}", file=sys.stderr)
    return seen, rerun


# ----------------------------------------------------------------------------------------------------- commands
def run_sync(repo: str, gh: GhApi, dry_run: bool = False) -> int:
    open_issues = fetch_open_issues(gh, repo)
    closed = [IssueState(n, False, frozenset({BLOCKED_LABEL}), ()) for n in fetch_closed_labelled(gh, repo)]
    blocked = sum(1 for i in open_issues if is_blocked(i.blockers))
    verb = "would" if dry_run else "did"
    unblocked = 0
    for issue in open_issues + closed:
        plan = plan_issue(issue, repo)
        if plan.remove_label and issue.is_open and not dry_run:
            # Read the issue again right before acting on it: a blocker reopened since the snapshot must not lead to
            # an "unblocked" notice that a later run cannot take back.
            fresh = fetch_issue_state(gh, repo, issue.number)
            if fresh is None:
                print(f"skipped #{issue.number}: no longer an issue")
                continue
            issue, plan = fresh, plan_issue(fresh, repo)
        if not plan.changes:
            continue
        if plan.add_label:
            print(f"{verb} label #{issue.number} {BLOCKED_LABEL}")
            if not dry_run:
                gh("POST", f"repos/{repo}/issues/{issue.number}/labels", {"labels": [BLOCKED_LABEL]})
        if plan.remove_label:
            print(f"{verb} unlabel #{issue.number} {BLOCKED_LABEL}" + (" and comment" if plan.comment else ""))
            if not dry_run:
                # The notice goes first and is idempotent through its marker, and the label goes last: if anything
                # fails in between, the label is still there and the next run finishes the job without a second
                # notice.
                if plan.comment and not _already_noticed(gh, repo, issue.number, plan.marker):
                    gh("POST", f"repos/{repo}/issues/{issue.number}/comments", {"body": plan.comment})
                gh("DELETE", f"repos/{repo}/issues/{issue.number}/labels/{quote(BLOCKED_LABEL)}", None)
            unblocked += issue.is_open
        for b in plan.not_completed:
            if dry_run:
                print(f"would check #{issue.number}: blocker {b.ref(repo)} was not completed")
                continue
            if _already_noticed(gh, repo, issue.number, NOT_COMPLETED_MARKER.format(blocker=b.key)):
                continue
            print(f"did flag #{issue.number}: blocker {b.ref(repo)} was not completed")
            gh("POST", f"repos/{repo}/issues/{issue.number}/labels", {"labels": [TRIAGE_LABEL]})
            gh("POST", f"repos/{repo}/issues/{issue.number}/comments", {"body": not_completed_notice(b, repo)})
    seen, rerun = reconcile_pr_checks(gh, repo, {i.number: i for i in open_issues}, dry_run)
    print(
        f"open issues: {len(open_issues)}, blocked: {blocked}, unblocked now: {unblocked}, "
        f"open pull requests: {seen}, dependency checks re-run: {rerun}"
    )
    return 0


def run_check_pr(event: Mapping[str, Any], repo: str, gh: GhApi) -> int:
    try:
        number = event["pull_request"]["number"]
    except (KeyError, TypeError) as exc:
        raise EventError("event payload has no pull_request.number") from exc
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise EventError("pull request number is not a positive integer")
    # Read the pull request again rather than trust the event: a re-run replays the original payload, and the
    # description or the labels may have changed since.
    pull = gh("GET", f"repos/{repo}/pulls/{number}", None)
    try:
        body = str(pull.get("body") or "")
        labels = frozenset(str(label["name"]) for label in pull["labels"])
    except (AttributeError, KeyError, TypeError) as exc:
        raise GhError("the pull-request answer lacks body or labels") from exc
    closing, linked = linked_issues(body, repo)
    override = override_authorized(gh, repo, number) if OVERRIDE_LABEL in labels else None
    verdict = check_pull_request(override, closing, linked, lambda n: fetch_blockers_of(gh, repo, n), repo)
    for line in verdict.lines:
        print(line if verdict.ok else f"::error::{line}")
    return 0 if verdict.ok else 3


def _repo_from_env() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", repo):
        raise EventError("GITHUB_REPOSITORY must be set to owner/name")
    return repo


def main(argv: Optional[Sequence[str]] = None, gh: GhApi = gh_api) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="bring the blocked label in step with the Blocked-by links")
    sync.add_argument("--dry-run", action="store_true", help="print what would change, change nothing")
    sub.add_parser("check-pr", help="fail when the pull request links a blocked issue (reads GITHUB_EVENT_PATH)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on a usage error and 0 on --help
        return int(exc.code or 0)
    try:
        repo = _repo_from_env()
        if args.command == "sync":
            return run_sync(repo, gh, dry_run=args.dry_run)
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        if not event_path:
            raise EventError("GITHUB_EVENT_PATH must be set")
        try:
            with open(event_path, encoding="utf-8") as handle:
                event = json.load(handle)
        except (OSError, ValueError) as exc:
            raise EventError(f"cannot read the event payload: {type(exc).__name__}") from exc
        return run_check_pr(event, repo, gh)
    except EventError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
