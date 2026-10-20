#!/usr/bin/env python3
"""Keep the ``blocked`` label in step with GitHub's "Blocked by" links, and stop pull requests on blocked issues.

Two workflows run this script:

- ``.github/workflows/issue-dependencies.yml`` runs ``sync`` when an issue is closed or reopened, hourly, and on
  demand. It brings the ``blocked`` label of every issue in line with its native "Blocked by" links.
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
  ``Resolves`` or ``Refs`` — unless the same pull request closes every open blocker (``Fixes``, ``Closes`` or
  ``Resolves``), or a maintainer has labelled it ``dependency-override``. It reads the links themselves, not the
  label, so it never trails behind ``sync``. When ``sync`` unblocks an issue it re-runs a failed check on the open
  pull requests that link it.

Trust boundary. Issue and pull-request text is written by anyone, so the script never executes or prints it: it
parses the description with regular expressions and writes only fixed messages and issue numbers to the log. The
workflows pass nothing through the shell; the script reads the event file itself. Every subprocess call is a list
without a shell, limited to fixed endpoints (``repos/`` paths built from integers, and ``graphql`` with a query
constant from this module).

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
PR_WORKFLOW_FILE = "pr-dependencies.yml"
# Hidden marker in the "a blocker was not completed" notice, one per blocker. Only a bot's comment counts, so a
# person cannot forge it to silence the notice.
CANCELLED_MARKER = "<!-- issue-dependencies:not-completed:{blocker} -->"

_GH_TIMEOUT_SECONDS = 60
_PAGE_SIZE = 100
_MAX_PAGES = 30  # 3,000 issues: far above the backlog; bounds every paginated read
_MAX_BLOCKERS = 50  # GitHub shows at most this many "Blocked by" links per issue
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
    "#{blocker}, which this issue is blocked by, was closed as {reason}, not as completed, so the work this issue "
    "waits for was not done and the issue stays blocked. {mention}, please re-plan it: remove the link if the "
    "dependency no longer holds, or link the issue that replaces #{blocker}.\n\n{marker}"
)

MAINTAINER_LOGINS = frozenset({"cemililik"})  # MAINTAINERS.md; tests/test_issue_dependencies.py keeps them equal
MAINTAINER_MENTION = " ".join(f"@{login}" for login in sorted(MAINTAINER_LOGINS))

# A reference in a pull-request description: `#12`, `owner/repo#12` or an issue URL, after a keyword. GitHub closes
# an issue for close/fix/resolve and their inflections; `Refs` is this project's word for "part of".
_KEYWORD = r"(?P<kw>close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|references)"
_OWNER, _NAME = r"[A-Za-z0-9-]+", r"[A-Za-z0-9._-]+"
_REF_RE = re.compile(
    rf"(?:(?P<owner>{_OWNER})/(?P<repo>{_NAME}))?#(?P<num>\d+)"
    rf"|https://github\.com/(?P<uowner>{_OWNER})/(?P<urepo>{_NAME})/issues/(?P<unum>\d+)"
)
_REF_PLAIN = rf"(?:(?:{_OWNER}/{_NAME})?#\d+|https://github\.com/{_OWNER}/{_NAME}/issues/\d+)"
# Over-matching is the safe direction for a blocking check, so a colon or a missing space after the keyword counts.
_LINK_RE = re.compile(
    rf"(?<![\w-]){_KEYWORD}\s*:?\s*(?P<refs>{_REF_PLAIN}(?:\s*(?:,|and|&)\s*{_REF_PLAIN})*)", re.IGNORECASE
)
_CLOSING = frozenset({"close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves", "resolved"})
_FENCE_RE = re.compile(r"^ {0,3}(```|~~~).*?^ {0,3}\1[^\S\n]*$", re.MULTILINE | re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# Every GraphQL document this script may send. A query is chosen by name; only integer and cursor variables vary.
QUERIES = {
    "open_issues": """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        labels(first: 50) { nodes { name } }
        blockedBy(first: 50) { totalCount nodes { number state stateReason repository { nameWithOwner } } }
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
        state
        labels(first: 50) { nodes { name } }
        blockedBy(first: 50) { totalCount nodes { number state stateReason repository { nameWithOwner } } }
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
    """What ``sync`` does to one issue. ``comment`` is the unblocked notice; ``not_completed`` lists blockers that
    were closed without being completed (each gets one notice and ``needs-triage``)."""

    add_label: bool = False
    remove_label: bool = False
    comment: str = ""
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
    else:
        message = MSG_UNBLOCKED_NO_LINKS.format(contributing=contributing_url(repo))
    return Plan(remove_label=True, comment=message)


def not_completed_notice(blocker: Blocker) -> str:
    reason = "a duplicate" if blocker.reason == "DUPLICATE" else "not planned"
    return MSG_NOT_COMPLETED.format(
        blocker=blocker.number,
        reason=reason,
        mention=MAINTAINER_MENTION,
        marker=CANCELLED_MARKER.format(blocker=blocker.number),
    )


def _strip_non_prose(body: str) -> str:
    """Drop fenced code, inline code and HTML comments: the template's own examples live there."""
    for pattern in (_FENCE_RE, _HTML_COMMENT_RE, _INLINE_CODE_RE):
        body = pattern.sub(" ", body)
    return body


def linked_issues(body: str, repo: str) -> tuple[frozenset[int], frozenset[int]]:
    """``(closing, all)``: issue numbers of this repository linked from a pull-request description.

    ``closing`` holds the ones linked with a closing keyword; ``all`` adds those linked with ``Refs``. A reference to
    another repository is ignored: this repository's links cannot block on it, and its workflow cannot close it.
    """
    owner, name = repo.lower().split("/")
    closing: set[int] = set()
    every: set[int] = set()
    for match in _LINK_RE.finditer(_strip_non_prose(body or "")):
        keyword = match.group("kw").lower()
        for ref in _REF_RE.finditer(match.group("refs")):
            ref_owner = (ref.group("owner") or ref.group("uowner") or owner).lower()
            ref_repo = (ref.group("repo") or ref.group("urepo") or name).lower()
            if (ref_owner, ref_repo) != (owner, name):
                continue
            number = int(ref.group("num") or ref.group("unum"))
            every.add(number)
            if keyword in _CLOSING:
                closing.add(number)
    return frozenset(closing), frozenset(every)


@dataclass(frozen=True)
class Verdict:
    """``check-pr``'s answer: may the pull request merge, and why not."""

    ok: bool
    lines: tuple[str, ...]


def check_pull_request(
    labels: frozenset[str],
    closing: frozenset[int],
    linked: frozenset[int],
    blockers_of: Callable[[int], Optional[tuple[Blocker, ...]]],
    repo: str,
) -> Verdict:
    """The rules for one pull request. ``blockers_of`` returns an open issue's links, or ``None`` for a pull request,
    a closed issue or a number that does not exist (none of those can be blocked work)."""
    if OVERRIDE_LABEL in labels:
        return Verdict(True, (f"`{OVERRIDE_LABEL}` is set: a maintainer accepted the linked dependencies.",))
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
            + (
                "Wait until the blocker is closed as completed (this check re-runs then), close it in this pull request "
                f"too, or ask a maintainer. See {contributing_url(repo)}",
            ),
        )
    return Verdict(True, (f"No linked issue is blocked ({checked} issue(s) checked).",))


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
        # integers, the repository name and fixed path segments.
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
        return frozenset(str(label["name"]) for label in node["labels"]["nodes"])
    except (KeyError, TypeError) as exc:
        raise GhError("a labels answer lacks a field") from exc


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
        out += [IssueState(int(n["number"]), True, _labels(n), _blockers(n)) for n in nodes]
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


def fetch_blockers_of(gh: GhApi, repo: str, number: int) -> Optional[tuple[Blocker, ...]]:
    """An open issue's links, or ``None`` when ``number`` is a pull request, a closed issue or does not exist."""
    owner, name = repo.split("/")
    data = graphql(gh, "issue", owner=owner, name=name, number=number)
    node = (data.get("repository") or {}).get("issueOrPullRequest")
    if not isinstance(node, Mapping) or node.get("__typename") != "Issue" or node.get("state") != "OPEN":
        return None
    return _blockers(node)


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


def _rerun_pr_checks(gh: GhApi, repo: str, unblocked: set[int]) -> list[int]:
    """Re-run a failed dependency check on the open pull requests that link a just-unblocked issue.

    Optional: when it fails, the check re-runs anyway on the pull request's next edit or push, so a failure is
    reported as a warning and does not fail the sync.
    """
    rerun: list[int] = []
    try:
        for page in range(1, 4):
            pulls = gh("GET", f"repos/{repo}/pulls?state=open&per_page={_PAGE_SIZE}&page={page}", None)
            if not isinstance(pulls, list):
                raise GhError("the pull-request list is not a list")
            for pull in pulls:
                _, linked = linked_issues(str(pull.get("body") or ""), repo)
                if not linked & unblocked:
                    continue
                sha = str(pull["head"]["sha"])
                if not re.fullmatch(r"[0-9a-f]{40}", sha):
                    raise GhError("a pull request's head sha is malformed")
                runs = gh(
                    "GET", f"repos/{repo}/actions/workflows/{PR_WORKFLOW_FILE}/runs?head_sha={sha}&per_page=5", None
                )
                latest = (runs or {}).get("workflow_runs") or []
                if latest and latest[0].get("conclusion") == "failure":
                    gh("POST", f"repos/{repo}/actions/runs/{int(latest[0]['id'])}/rerun-failed-jobs", None)
                    rerun.append(int(pull["number"]))
            if len(pulls) < _PAGE_SIZE:
                break
    except (GhError, KeyError, TypeError, ValueError) as exc:
        print(f"warning: could not re-run pull-request checks: {_clean(str(exc))}", file=sys.stderr)
    return rerun


# ----------------------------------------------------------------------------------------------------- commands
def run_sync(repo: str, gh: GhApi, dry_run: bool = False) -> int:
    issues = fetch_open_issues(gh, repo)
    issues += [IssueState(n, False, frozenset({BLOCKED_LABEL}), ()) for n in fetch_closed_labelled(gh, repo)]
    plans = [(issue, plan_issue(issue, repo)) for issue in issues]
    blocked = sum(1 for i in issues if i.is_open and is_blocked(i.blockers))
    unblocked: set[int] = set()
    for issue, plan in plans:
        if not plan.changes:
            continue
        verb = "would" if dry_run else "did"
        if plan.add_label:
            print(f"{verb} label #{issue.number} {BLOCKED_LABEL}")
            if not dry_run:
                gh("POST", f"repos/{repo}/issues/{issue.number}/labels", {"labels": [BLOCKED_LABEL]})
        if plan.remove_label:
            print(f"{verb} unlabel #{issue.number} {BLOCKED_LABEL}" + (" and comment" if plan.comment else ""))
            if not dry_run:
                gh("DELETE", f"repos/{repo}/issues/{issue.number}/labels/{quote(BLOCKED_LABEL)}", None)
                if plan.comment:
                    gh("POST", f"repos/{repo}/issues/{issue.number}/comments", {"body": plan.comment})
            if issue.is_open:
                unblocked.add(issue.number)
        for b in plan.not_completed:
            marker = CANCELLED_MARKER.format(blocker=b.number)
            if dry_run:
                print(f"would check #{issue.number}: blocker #{b.number} was not completed")
                continue
            if _already_noticed(gh, repo, issue.number, marker):
                continue
            print(f"did flag #{issue.number}: blocker #{b.number} was not completed")
            gh("POST", f"repos/{repo}/issues/{issue.number}/labels", {"labels": [TRIAGE_LABEL]})
            gh("POST", f"repos/{repo}/issues/{issue.number}/comments", {"body": not_completed_notice(b)})
    rerun = [] if dry_run or not unblocked else _rerun_pr_checks(gh, repo, unblocked)
    print(
        f"open issues: {sum(1 for i in issues if i.is_open)}, blocked: {blocked}, unblocked now: {len(unblocked)}, "
        f"pull-request checks re-run: {len(rerun)}"
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
    verdict = check_pull_request(labels, closing, linked, lambda n: fetch_blockers_of(gh, repo, n), repo)
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
