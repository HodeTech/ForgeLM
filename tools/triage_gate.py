#!/usr/bin/env python3
"""Keep ``needs-triage`` on every issue until a maintainer removes it.

Run by ``.github/workflows/triage-gate.yml`` on an ``issues`` event. Triage is a maintainer's review of a report:
the claim is checked against ``development``, the issue is classified, and content that does not belong in a public
tracker is caught. ``needs-triage`` marks an issue nobody has done that for yet, and a contributor does not work on
such an issue. This script makes the marker reliable; it never removes it.

Rules (see ``CONTRIBUTING.md``, "Issue triage"):

- ``opened``: the issue gets ``needs-triage``, whoever opened it and however it was created (the issue forms add the
  label themselves; the API, ``gh issue create`` and other tools do not), unless its history shows that a maintainer
  has already removed it (a maintainer can triage faster than this workflow starts);
- ``reopened`` by anyone but a maintainer: the issue goes back to triage, with one comment saying so;
- ``needs-triage`` removed by anyone but a maintainer: the label is put back, with one comment, unless a maintainer
  has removed it since. Only a maintainer completes triage.

A maintainer is a login listed in ``MAINTAINERS.md`` (``MAINTAINER_LOGINS``; ``tests/test_triage_gate.py`` fails if
the two drift apart). GitHub's own permission names are not used: they would also count a triage-role or write-role
collaborator who is not a maintainer.

What this does not see: events caused by a workflow's own ``GITHUB_TOKEN`` start no other workflow. An issue that a
workflow creates must therefore carry the label itself (``nightly.yml`` does).

Trust boundary. Issue titles and bodies are written by anyone, so the script never reads, executes or prints them:
the decision uses the event's action, the sender's login, the label's name and the issue's label history. The
workflow passes nothing through the shell, every subprocess call is a list without a shell, and the only text
written to the log is a fixed message, an issue number or a validated login. GitHub treats a log line that starts
with ``::`` as a workflow command, so error text from ``gh`` is flattened to one line and defanged before it is
printed.

Exit codes (per ``tools/`` contract, not the public 0-6 surface of ``forgelm/``):

- ``0`` — handled: the label was added, or nothing needed to change.
- ``1`` — an API call failed, or GitHub did not confirm the label.
- ``2`` — the event payload or the environment is unusable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

TRIAGE_LABEL = "needs-triage"
# The maintainers, as MAINTAINERS.md lists them (tests/test_triage_gate.py fails if the two drift apart).
MAINTAINER_LOGINS = frozenset({"cemililik"})
HANDLED_ACTIONS = frozenset({"opened", "reopened", "unlabeled"})
_GH_TIMEOUT_SECONDS = 60
_MAX_LOGIN_LENGTH = 39
_PAGE_SIZE = 100
_MAX_PAGES = 10  # bounds the label-history read; an issue with more than 1000 events fails loudly instead
_COMMENT_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 2.0
_ALLOWED_METHODS = frozenset({"GET", "POST"})

# What the label history of an issue says about its triage label (only the latest event counts).
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
    """The event payload or the environment is not usable."""


class GhError(RuntimeError):
    """A ``gh api`` call failed, or GitHub's answer was not what the API documents."""


# (method, endpoint, optional JSON body) -> parsed JSON answer (None for an empty body).
GhApi = Callable[[str, str, Optional[Mapping[str, Any]]], Any]


@dataclass(frozen=True)
class Context:
    """What the event says: which issue, what happened, and whether a maintainer did it."""

    repo: str
    number: int
    action: str
    is_maintainer: bool


@dataclass(frozen=True)
class Decision:
    """``add_label`` is whether to add ``needs-triage``; ``message`` is the comment to post with it ('' for none)."""

    add_label: bool
    message: str = ""


KEEP = Decision(add_label=False)


def valid_login(login: str) -> bool:
    """GitHub logins are ASCII letters, digits and hyphens, at most 39 characters."""
    return 0 < len(login) <= _MAX_LOGIN_LENGTH and login.isascii() and all(c.isalnum() or c == "-" for c in login)


def _clean(text: str, limit: int = 200) -> str:
    """One line, bounded, with ``::`` defanged so it can never open a workflow command."""
    return " ".join(text.split())[:limit].replace("::", ": :")


def _is_maintainer(login: Any) -> bool:
    return isinstance(login, str) and login.lower() in MAINTAINER_LOGINS


def triage_history(events: Iterable[Any]) -> str:
    """What the issue's events say about ``needs-triage``: the latest label event decides, ``HISTORY_*``."""
    state = HISTORY_NEVER
    for event in events:
        if not isinstance(event, dict) or event.get("event") not in ("labeled", "unlabeled"):
            continue
        label = event.get("label")
        if not isinstance(label, dict) or str(label.get("name", "")).lower() != TRIAGE_LABEL:
            continue
        if event["event"] == "labeled":
            state = HISTORY_LABELLED
            continue
        actor = event.get("actor")
        removed_by = actor.get("login") if isinstance(actor, dict) else None
        state = HISTORY_MAINTAINER_REMOVED if _is_maintainer(removed_by) else HISTORY_OTHER_REMOVED
    return state


def decide(ctx: Context, has_label: bool, history: Callable[[], str]) -> Decision:
    """The rules: whether ``needs-triage`` has to be added to this issue after ``ctx.action``.

    ``history`` is called only when the answer depends on it, so a decision made on the event alone makes no API
    call and cannot fail because of one.
    """
    if has_label:
        return KEEP
    if ctx.action == "reopened":
        return KEEP if ctx.is_maintainer else Decision(True, MSG_REOPENED)
    if ctx.action == "unlabeled" and ctx.is_maintainer:
        return KEEP
    if history() == HISTORY_MAINTAINER_REMOVED:
        return KEEP
    return Decision(True, MSG_RESTORED if ctx.action == "unlabeled" else "")


def context_from_event(event: Mapping[str, Any], repo: str) -> Optional[Context]:
    """``None`` when the event is not one to act on (another action, another label, a pull request)."""
    if not isinstance(event, Mapping):
        raise EventError("event payload is not a JSON object")
    action = event.get("action")
    if action not in HANDLED_ACTIONS:
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
    return Context(repo, number, str(action), _is_maintainer(login))


def gh_api(method: str, endpoint: str, payload: Optional[Mapping[str, Any]] = None) -> Any:
    """Call the GitHub REST API through the ``gh`` CLI (authenticated by ``GH_TOKEN``); no shell is involved."""
    if method not in _ALLOWED_METHODS or not endpoint.startswith("repos/") or any(c.isspace() for c in endpoint):
        raise GhError("refusing an API call outside the fixed repos/ endpoints")
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


def read_history(gh: GhApi, repo: str, number: int) -> str:
    """``triage_history`` of the issue's events, across pages (oldest first, as the API returns them)."""
    events: list[Any] = []
    for page in range(1, _MAX_PAGES + 1):
        raw = gh("GET", f"repos/{repo}/issues/{number}/events?per_page={_PAGE_SIZE}&page={page}", None)
        if not isinstance(raw, list):
            raise GhError("the issue events answer is not a list")
        events.extend(raw)
        if len(raw) < _PAGE_SIZE:
            return triage_history(events)
    raise GhError(f"the issue has more than {_MAX_PAGES * _PAGE_SIZE} events; its label history was not read")


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


def run(event: Mapping[str, Any], repo: str, gh: GhApi) -> int:
    """Handle one ``issues`` event. See the module docstring for the exit codes."""
    ctx = context_from_event(event, repo)
    if ctx is None:
        return 0
    try:
        has_label = TRIAGE_LABEL in current_labels(gh, repo, ctx.number)
        decision = decide(ctx, has_label, lambda: read_history(gh, repo, ctx.number))
        if decision.add_label:
            add_triage_label(gh, repo, ctx.number)
    except GhError as exc:
        print(f"error: issue #{ctx.number}: {exc}", file=sys.stderr)
        return 1
    if decision.message:
        try:
            _comment(gh, repo, ctx.number, decision.message)
        except GhError as exc:  # the label is already back; only the explanation is missing
            print(f"error: issue #{ctx.number}: could not post the notice: {exc}", file=sys.stderr)
            return 1
    outcome = "added needs-triage" if decision.add_label else "left as it is"
    print(f"handled {ctx.action} on issue #{ctx.number}: {outcome}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    del argv  # no command-line arguments: the workflow's environment is the whole input
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if repo.count("/") != 1 or not event_path:
        print("error: GITHUB_REPOSITORY and GITHUB_EVENT_PATH must be set", file=sys.stderr)
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
    sys.exit(main())
