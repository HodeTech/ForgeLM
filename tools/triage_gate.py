#!/usr/bin/env python3
"""Keep ``needs-triage`` on every issue until a maintainer removes it.

Run by ``.github/workflows/triage-gate.yml``: on an ``issues`` event (the default mode) and hourly as a sweep
(``triage_gate.py sweep``). Triage is a maintainer's review of a report: the claim is checked against ``development``,
the issue is classified, and content that does not belong in a public tracker is caught. ``needs-triage`` marks an
issue nobody has done that for yet, and a contributor does not work on such an issue. This script makes the marker
reliable; it never removes it.

What the script decides is a function of the issue's *state*, never of which event woke it up, so a replayed, delayed
or cancelled run reaches the same answer. An issue is **untriaged** unless a maintainer's removal of the label is the
latest thing that happened to it, and no one who is not a maintainer has reopened it since. A missing label on an
untriaged issue is added back. The events decide only who may end the matter without a look at the history:

- ``opened``: the label is added however the issue was created (the issue forms add it themselves; the API,
  ``gh issue create`` and other tools do not), unless the history shows a maintainer already removed it (a maintainer
  can triage faster than the workflow starts);
- ``reopened`` by anyone but a maintainer: the issue goes back to triage, with one comment, unless a maintainer has
  removed the label since the reopening;
- ``needs-triage`` removed by anyone but a maintainer: the label is put back, with one comment, unless a maintainer
  has removed it since. Only a maintainer completes triage;
- ``sweep`` (hourly): every open issue created since ``TRIAGE_SWEEP_SINCE`` that lacks the label gets the same
  decision. This repairs what an event run could not: a failed API call, a disabled workflow, an issue that a
  workflow created with its own ``GITHUB_TOKEN`` (such events start no other workflow). The candidates are found with
  the search API, which filters on the server (open, an issue, no label, created since), so issues that already have
  the label never use up a run's page budget. A missing label is the only state there is: whatever one run does not
  reach (the cap of ``_SWEEP_MAX_PAGES`` pages, a failed call) is found again by the next, newest first, and nothing
  is checkpointed. Issues created before ``TRIAGE_SWEEP_SINCE`` were classified by hand and are never candidates.

A maintainer is a login listed in ``MAINTAINERS.md`` (``MAINTAINER_LOGINS``; ``tests/test_triage_gate.py`` fails if
the two drift apart). GitHub's own permission names are not used: they would also count a triage-role or write-role
collaborator who is not a maintainer. A former maintainer (``FORMER_MAINTAINER_LOGINS``) keeps their past removals
valid, so stepping down does not reopen the issues they triaged. Apps and bots are never maintainers, and their
issues are labelled like anyone else's.

Triage is a review of the report as it stood: editing an issue after triage does not bring the label back. A
maintainer who doubts an edited report puts the label back by hand, and this script leaves it there.

Trust boundary. Issue titles and bodies are written by anyone, so the script never reads, executes or prints them:
the decision uses the event's action, the sender's login, the label's name and the issue's label history. The
workflow passes nothing through the shell, every subprocess call is a list without a shell, and the only text
written to the log is a fixed message, an issue number or a validated login. GitHub treats a log line that starts
with ``::`` as a workflow command, so error text from ``gh`` is flattened to one line and defanged before it is
printed.

Exit codes (per ``tools/`` contract, not the public 0-6 surface of ``forgelm/``):

- ``0`` — handled: the label was added, or nothing needed to change.
- ``1`` — an API call failed, or GitHub did not confirm the label (a sweep goes on to the next issue first).
- ``2`` — the event payload, the arguments or the environment are unusable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import quote

TRIAGE_LABEL = "needs-triage"
# The maintainers, as MAINTAINERS.md lists them (tests/test_triage_gate.py fails if the two drift apart).
MAINTAINER_LOGINS = frozenset({"cemililik"})
# Maintainers who stepped down: what they removed stays triaged, but they no longer end a triage by acting now.
FORMER_MAINTAINER_LOGINS: frozenset[str] = frozenset()
HANDLED_ACTIONS = frozenset({"opened", "reopened", "unlabeled"})
SWEEP_ACTION = "sweep"
SINCE_ENV = "TRIAGE_SWEEP_SINCE"
_SINCE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_GH_TIMEOUT_SECONDS = 30
_MAX_LOGIN_LENGTH = 39
_BOT_SUFFIX = "[bot]"
_PAGE_SIZE = 100
# 11 pages: ten full pages and a short or empty one, so an issue with exactly 1000 events is still read in full.
_MAX_PAGES = 11
_SWEEP_MAX_PAGES = 5  # 500 issues created since the sweep date; far above the intake rate
_COMMENT_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 2.0
_ALLOWED_METHODS = frozenset({"GET", "POST"})
_NAME_EXTRA_CHARS = frozenset("-_.")

# What the label history of an issue says about its triage label (only the latest label event counts).
HISTORY_NEVER = "never"  # the label was never added
HISTORY_LABELLED = "labelled"  # added, and not removed since
HISTORY_MAINTAINER_REMOVED = "maintainer-removed"  # a maintainer removed it: triage is done
HISTORY_OTHER_REMOVED = "other-removed"  # someone who is not a maintainer removed it

MSG_REOPENED = (
    "This issue was reopened, so it goes back to triage: `needs-triage` was added, and a maintainer will look at it "
    "again. Please do not start work on it before the label is gone."
)
MSG_RESTORED = (
    "`needs-triage` was added back: only a maintainer completes triage, so the label stays until one removes it. "
    "If you think this issue is ready, say so in a comment and a maintainer will take a look."
)


class EventError(Exception):
    """The event payload, the arguments or the environment are not usable."""


class GhError(RuntimeError):
    """A ``gh api`` call failed, or GitHub's answer was not what the API documents."""


class HistoryTooLong(GhError):
    """The issue has more events than the label history can be read from."""


# (method, endpoint, optional JSON body) -> parsed JSON answer (None for an empty body).
GhApi = Callable[[str, str, Optional[Mapping[str, Any]]], Any]


@dataclass(frozen=True)
class Context:
    """What the event says: which issue, what happened (or ``sweep``), and whether a maintainer did it."""

    repo: str
    number: int
    action: str
    is_maintainer: bool


@dataclass(frozen=True)
class History:
    """The issue's triage-label story. Positions are indexes in time order (``created_at``, then ``id``); -1 is none."""

    state: str = HISTORY_NEVER
    label_pos: int = -1  # the latest label event
    reopened_pos: int = -1  # the latest reopening by someone who is not a maintainer


@dataclass(frozen=True)
class Decision:
    """``add_label`` is whether to add ``needs-triage``; ``message`` is the comment to post with it ('' for none)."""

    add_label: bool
    message: str = ""


KEEP = Decision(add_label=False)


def _name_ok(name: str) -> bool:
    """A GitHub user, owner or repository name: ASCII letters, digits, hyphen, underscore and dot."""
    return bool(name) and name.isascii() and all(c.isalnum() or c in _NAME_EXTRA_CHARS for c in name)


def valid_login(login: str) -> bool:
    """The shape of a GitHub login: ASCII letters, digits and hyphens, at most 39 characters; an App ends in [bot]."""
    name = login[: -len(_BOT_SUFFIX)] if login.endswith(_BOT_SUFFIX) else login
    return 0 < len(name) <= _MAX_LOGIN_LENGTH and name.isascii() and all(c.isalnum() or c == "-" for c in name)


def valid_repository(repo: str) -> bool:
    owner, _, name = repo.partition("/")
    return _name_ok(owner) and _name_ok(name) and "/" not in name


def _clean(text: str, limit: int = 200) -> str:
    """One line, bounded, with ``::`` defanged so it can never open a workflow command."""
    return " ".join(text.split())[:limit].replace("::", ": :")


def _is_maintainer(login: Any) -> bool:
    return isinstance(login, str) and login.lower() in MAINTAINER_LOGINS


def _is_maintainer_or_former(login: Any) -> bool:
    return _is_maintainer(login) or (isinstance(login, str) and login.lower() in FORMER_MAINTAINER_LOGINS)


def _sort_key(event: Mapping[str, Any]) -> tuple[str, int]:
    created, ident = event.get("created_at"), event.get("id")
    return (created if isinstance(created, str) else "", ident if isinstance(ident, int) else 0)


def triage_history(events: Iterable[Any]) -> History:
    """What the issue's events say about ``needs-triage``. Sorted by time rather than trusted to arrive in order."""
    relevant: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("event")
        if kind == "reopened":
            relevant.append(event)
        elif kind in ("labeled", "unlabeled"):
            label = event.get("label")
            if isinstance(label, dict) and str(label.get("name", "")).lower() == TRIAGE_LABEL:
                relevant.append(event)
    relevant.sort(key=_sort_key)  # stable: events without a timestamp keep the order they arrived in
    state, label_pos, reopened_pos = HISTORY_NEVER, -1, -1
    for position, event in enumerate(relevant):
        actor = event.get("actor")
        login = actor.get("login") if isinstance(actor, dict) else None
        if event["event"] == "reopened":
            if not _is_maintainer_or_former(login):
                reopened_pos = position
        elif event["event"] == "labeled":
            state, label_pos = HISTORY_LABELLED, position
        else:
            state = HISTORY_MAINTAINER_REMOVED if _is_maintainer_or_former(login) else HISTORY_OTHER_REMOVED
            label_pos = position
    return History(state, label_pos, reopened_pos)


def untriaged(history: History) -> bool:
    """True unless a maintainer's removal is the latest word: nobody who is not a maintainer reopened it since."""
    if history.state == HISTORY_MAINTAINER_REMOVED:
        return history.reopened_pos > history.label_pos
    return True


def _notice(action: str, history: Optional[History]) -> str:
    if action == "unlabeled":
        return MSG_RESTORED
    if action == "reopened":
        return MSG_REOPENED
    if action == SWEEP_ACTION and history is not None:
        if history.state == HISTORY_OTHER_REMOVED:
            return MSG_RESTORED
        if history.state == HISTORY_MAINTAINER_REMOVED:
            return MSG_REOPENED
    return ""


def decide(ctx: Context, has_label: bool, history: Callable[[], History]) -> Decision:
    """The rules: whether ``needs-triage`` has to be added to this issue.

    ``history`` is called only when the answer depends on it, so a decision made on the event alone makes no API
    call and cannot fail because of one.
    """
    if has_label:
        return KEEP
    if ctx.is_maintainer and ctx.action in ("reopened", "unlabeled"):
        return (
            KEEP  # a maintainer's own decision: the history is not consulted, so a lagging events list cannot undo it
        )
    try:
        story = history()
    except HistoryTooLong:
        if ctx.action == SWEEP_ACTION:
            raise
        # A maintainer's own act returned above, so this was someone else: with no way to tell whether a maintainer
        # has triaged the issue since, the label goes back (the safe direction).
        return Decision(True, _notice(ctx.action, None))
    return Decision(True, _notice(ctx.action, story)) if untriaged(story) else KEEP


def context_from_event(event: Mapping[str, Any], repo: str) -> Optional[Context]:
    """``None`` when the event is not one to act on (another action, another label, a pull request)."""
    if not isinstance(event, Mapping):
        raise EventError("event payload is not a JSON object")
    action = event.get("action")
    if not isinstance(action, str) or action not in HANDLED_ACTIONS:
        return None
    try:
        issue, sender = event["issue"], event["sender"]
        number, login = issue["number"], sender["login"]
        is_pull_request = "pull_request" in issue
        label_name = event["label"]["name"] if action == "unlabeled" else TRIAGE_LABEL
    except (KeyError, TypeError) as exc:
        raise EventError(f"event payload lacks a field: {_clean(str(exc), 60)}") from exc
    if is_pull_request or not isinstance(label_name, str) or label_name.lower() != TRIAGE_LABEL:
        return None
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise EventError("issue number is not a positive integer")
    if not isinstance(login, str) or not valid_login(login):
        raise EventError("sender login is not a valid GitHub login")
    return Context(repo, number, action, _is_maintainer(login))


def _allowed_endpoint(method: str, endpoint: str) -> bool:
    """Only the issues endpoints of one repository (``repos/<owner>/<name>/issues[/...][?query]``) and the issue search."""
    if any(c.isspace() for c in endpoint):
        return False
    path = endpoint.split("?", 1)[0]
    if path == "search/issues":  # the sweep's candidate query, read-only; the other search endpoints are refused
        return method == "GET"
    parts = path.split("/")
    if len(parts) < 4 or parts[0] != "repos" or parts[3] != "issues":
        return False
    return _name_ok(parts[1]) and _name_ok(parts[2]) and not any(p in ("", ".", "..") for p in parts)


def gh_api(method: str, endpoint: str, payload: Optional[Mapping[str, Any]] = None) -> Any:
    """Call the GitHub REST API through the ``gh`` CLI (authenticated by ``GH_TOKEN``); no shell is involved."""
    if method not in _ALLOWED_METHODS or not _allowed_endpoint(method, endpoint):
        raise GhError("refusing an API call outside the fixed issues endpoints")
    command = ["gh", "api", "--method", method, endpoint]
    stdin = None
    if payload is not None:
        command += ["--input", "-"]
        stdin = json.dumps(payload)
    label = f"{method} {endpoint.split('?')[0]}"
    try:
        # A list without a shell; the method and endpoint were validated above (so neither can start with "-" or
        # carry whitespace), and the endpoint is built only from integers and the workflow's own repository name.
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


def current_labels(gh: GhApi, repo: str, number: int) -> frozenset[str]:
    """The issue's labels right now (the event's copy may be stale by the time the workflow runs)."""
    raw = gh("GET", f"repos/{repo}/issues/{number}", None)
    try:
        return frozenset(str(label["name"]).lower() for label in raw["labels"])
    except (KeyError, TypeError) as exc:
        raise GhError("the issue answer lacks labels") from exc


def read_history(gh: GhApi, repo: str, number: int) -> History:
    """``triage_history`` of the issue's events, across pages (oldest first, as the API returns them)."""
    events: list[Any] = []
    for page in range(1, _MAX_PAGES + 1):
        raw = gh("GET", f"repos/{repo}/issues/{number}/events?per_page={_PAGE_SIZE}&page={page}", None)
        if not isinstance(raw, list):
            raise GhError("the issue events answer is not a list")
        events.extend(raw)
        if len(raw) < _PAGE_SIZE:
            return triage_history(events)
    raise HistoryTooLong(
        f"the issue has more than {(_MAX_PAGES - 1) * _PAGE_SIZE} events; its label history was not read"
    )


def add_triage_label(gh: GhApi, repo: str, number: int) -> None:
    """Add ``needs-triage``; fail loudly unless GitHub's answer lists it."""
    result = gh("POST", f"repos/{repo}/issues/{number}/labels", {"labels": [TRIAGE_LABEL]})
    try:
        names = {str(label["name"]).lower() for label in result}
    except (KeyError, TypeError) as exc:
        raise GhError("the add-label answer does not list the issue's labels") from exc
    if TRIAGE_LABEL not in names:
        raise GhError("GitHub did not confirm the label")


def _comment(gh: GhApi, repo: str, number: int, body: str) -> None:
    """Post a notice; one retry, because the change it reports has already been made."""
    for attempt in range(1, _COMMENT_ATTEMPTS + 1):
        try:
            gh("POST", f"repos/{repo}/issues/{number}/comments", {"body": body})
            return
        except GhError as exc:
            if attempt == _COMMENT_ATTEMPTS:
                raise
            print(f"warning: issue #{number}: notice failed, retrying: {exc}", file=sys.stderr)
            time.sleep(_RETRY_DELAY_SECONDS)


def _decide_now(gh: GhApi, ctx: Context) -> Decision:
    labels = current_labels(gh, ctx.repo, ctx.number)
    return decide(ctx, TRIAGE_LABEL in labels, lambda: read_history(gh, ctx.repo, ctx.number))


def triage_issue(gh: GhApi, ctx: Context) -> Decision:
    """Decide and, when the label has to go on, decide once more on a fresh read right before writing.

    A maintainer can remove the label between the first read and the write; the second look makes that window as short
    as the two reads, and a maintainer's removal that landed in it wins.
    """
    decision = _decide_now(gh, ctx)
    if not decision.add_label:
        return decision
    decision = _decide_now(gh, ctx)
    if decision.add_label:
        add_triage_label(gh, ctx.repo, ctx.number)
    return decision


def _handle(gh: GhApi, ctx: Context) -> str:
    """Decide for one issue, write, post the notice. Returns what happened, as a fixed phrase."""
    decision = triage_issue(gh, ctx)
    if decision.add_label and decision.message:
        try:
            _comment(gh, ctx.repo, ctx.number, decision.message)
        except GhError as exc:  # the label is already back; only the explanation is missing
            raise GhError(f"could not post the notice: {exc}") from exc
    return "added needs-triage" if decision.add_label else "left as it is"


def run(event: Mapping[str, Any], repo: str, gh: GhApi) -> int:
    """Handle one ``issues`` event. See the module docstring for the exit codes."""
    ctx = context_from_event(event, repo)
    if ctx is None:
        return 0
    try:
        outcome = _handle(gh, ctx)
    except GhError as exc:
        print(f"error: issue #{ctx.number}: {exc}", file=sys.stderr)
        return 1
    print(f"handled {ctx.action} on issue #{ctx.number}: {outcome}")
    return 0


def untriaged_candidates(gh: GhApi, repo: str, since: str) -> list[int]:
    """Open issues (not pull requests) created at or after ``since`` that lack the label, newest first.

    The server does the filtering, so at most ``_SWEEP_MAX_PAGES`` pages are read and every entry is a candidate. When
    there are more, the newest are returned and the next sweep finds the rest: handling one removes it from the query.
    """
    query = f"repo:{repo} is:issue is:open -label:{TRIAGE_LABEL} created:>={since}"
    numbers: list[int] = []
    for page in range(1, _SWEEP_MAX_PAGES + 1):
        raw = gh(
            "GET",
            f"search/issues?q={quote(query, safe='')}&sort=created&order=desc&per_page={_PAGE_SIZE}&page={page}",
            None,
        )
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            raise GhError("the issue search answer lacks items")
        for item in items:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            created, number, labels = item.get("created_at"), item.get("number"), item.get("labels")
            if not isinstance(created, str) or isinstance(number, bool) or not isinstance(number, int):
                raise GhError("a search entry lacks created_at or number")
            if not isinstance(labels, list):
                raise GhError("a search entry lacks labels")
            names = {str(label.get("name", "")).lower() for label in labels if isinstance(label, dict)}
            # The search index can lag behind the issue; the filters are checked again on what was returned.
            if created >= since and TRIAGE_LABEL not in names:
                numbers.append(number)
        if len(items) < _PAGE_SIZE:
            return numbers
    print(f"note: more than {_SWEEP_MAX_PAGES * _PAGE_SIZE} issues lack the label; the next sweep continues")
    return numbers


def run_sweep(repo: str, gh: GhApi, since: str) -> int:
    """Give every open issue created since ``since`` the decision an ``opened`` event would have had."""
    failed = 0
    try:
        candidates = untriaged_candidates(gh, repo, since)
    except GhError as exc:
        print(f"error: sweep: {exc}", file=sys.stderr)
        return 1
    for number in candidates:
        try:
            outcome = _handle(gh, Context(repo, number, SWEEP_ACTION, False))
        except GhError as exc:
            failed += 1
            print(f"error: issue #{number}: {exc}", file=sys.stderr)
            continue
        print(f"handled sweep on issue #{number}: {outcome}")
    print(f"swept {len(candidates)} open issue(s) without the label; {failed} failed")
    return 1 if failed else 0


def main(argv: Sequence[str] = ()) -> int:
    args = list(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not valid_repository(repo):
        print("error: GITHUB_REPOSITORY must be <owner>/<name>", file=sys.stderr)
        return 2
    if args == [SWEEP_ACTION]:
        since = os.environ.get(SINCE_ENV, "")
        try:
            datetime.strptime(since, _SINCE_FORMAT)
        except ValueError:
            print(f"error: {SINCE_ENV} must look like 2026-10-21T00:00:00Z", file=sys.stderr)
            return 2
        return run_sweep(repo, gh_api, since)
    if args:
        print(f"error: the only argument is '{SWEEP_ACTION}'", file=sys.stderr)
        return 2
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        print("error: GITHUB_EVENT_PATH must be set", file=sys.stderr)
        return 2
    try:
        with open(event_path, encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read the event payload: {type(exc).__name__}", file=sys.stderr)
        return 2
    try:
        return run(event, repo, gh_api)
    except EventError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
