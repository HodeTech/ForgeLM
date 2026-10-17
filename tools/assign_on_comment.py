#!/usr/bin/env python3
"""Assign or release an issue when a contributor comments ``/assign`` or ``/unassign``.

Run by ``.github/workflows/assign-on-comment.yml`` on an ``issue_comment`` event. A contributor claims an
issue by commenting ``/assign`` and gives it back with ``/unassign``; a maintainer no longer has to assign
by hand. Anything the rules below do not cover stays a maintainer's decision and gets a reply saying so.

Rules (see ``CONTRIBUTING.md``, "Working on an issue"):

- only issues labelled ``good first issue`` are open to ``/assign``, and never one that also carries
  ``security``, ``needs-triage``, ``deferred``, ``epic`` or ``phase: 16``;
- an issue that already has an assignee is refused, whoever the assignee is and however they were assigned:
  a later ``/assign`` never replaces, adds to or removes an existing assignment;
- a contributor holds at most ``MAX_OPEN_ASSIGNMENTS`` open issues at a time (the maintainers in
  ``MAINTAINER_LOGINS`` are exempt);
- ``/unassign`` only ever removes the person who wrote it;
- both commands act on the person who wrote them: naming someone else (``/assign @someone``) is refused.

Changing an assignment is a maintainer's decision, made in the issue's sidebar. This script reacts to
comments only: it never re-reads an issue later to "correct" an assignment a maintainer made.

Trust boundary. The comment is written by anyone, so the script never executes or prints it. The workflow
passes nothing through the shell: this script reads the event file itself, every subprocess call is a list
without a shell, and the only text written to the log is a fixed message, an issue number or a validated
login. GitHub treats a log line that starts with ``::`` as a workflow command, so error text from ``gh`` is
flattened to one line and defanged before it is printed.

Exit codes (per ``tools/`` contract, not the public 0-6 surface of ``forgelm/``):

- ``0`` — handled: assigned, released, refused with a reply, or not a command at all.
- ``1`` — unexpected failure (an API call failed, or GitHub did not confirm the assignment). A reply saying
  a maintainer will assign the issue by hand is attempted first, so the contributor is never left waiting.
- ``2`` — the event payload or the environment is unusable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import quote

# Only the starter tasks. `help wanted` is deliberately not here: most of those issues are grouped sweeps, which
# CONTRIBUTING asks contributors to take one finding ID at a time (assigning the whole issue to one person would
# lock the rest), or larger tasks that need a maintainer's answer first.
ELIGIBLE_LABELS = frozenset({"good first issue"})
EXCLUDED_LABELS = frozenset({"security", "needs-triage", "deferred", "epic", "phase: 16"})
# The maintainers, as MAINTAINERS.md lists them (tests/test_assign_on_comment.py fails if the two drift apart).
# GitHub's own "author association" is not used: it also calls a read-only organisation member a MEMBER.
MAINTAINER_LOGINS = frozenset({"cemililik"})
MAINTAINER_MENTION = " ".join(f"@{login}" for login in sorted(MAINTAINER_LOGINS))
MAX_OPEN_ASSIGNMENTS = 2
COMMANDS = ("/assign", "/unassign")
FRONT_DOOR_URL = "https://github.com/orgs/HodeTech/projects/5/views/16"
_GH_TIMEOUT_SECONDS = 60
_MAX_LOGIN_LENGTH = 39

MSG_CLOSED = "This issue is closed, so it cannot be assigned."
MSG_NOT_OPEN = (
    "Only `good first issue` issues can be claimed with `/assign`; this one needs a maintainer's decision. "
    "{mention}, could you take a look?"
)
MSG_TAKEN = (
    "This issue is already assigned to [{owner}](https://github.com/{owner}), so nothing was changed. You can "
    "pick another one from the "
    "[Front door view]({front_door}). Only a maintainer can reassign an issue; if you think it should change, "
    "leave a comment here saying why."
)
MSG_TAKEN_UNKNOWN = (
    "This issue is already assigned, so nothing was changed. You can pick another one from the "
    "[Front door view]({front_door}). Only a maintainer can reassign an issue; if you think it should change, "
    "leave a comment here saying why."
)
MSG_SELF_ONLY = (
    "`/assign` and `/unassign` act on the person who writes them, so nothing was changed. To assign someone "
    "else, a maintainer sets it in the issue's sidebar."
)
MSG_ALREADY_YOURS = "This issue is already assigned to you."
MSG_LIMIT = (
    "You already have {count} open assignments, which is the limit at a time. Finish one, or comment `/unassign` "
    "on one you will not do, then try again. If you need more than {limit}, comment here mentioning `{mention}` "
    "and say what you would take on."
)
MSG_ASSIGNED = (
    "Assigned to @{login}. Thank you! Please open the pull request against `development` and link it with "
    "`Fixes #{number}` (or `Refs #{number}` if it resolves only part of a grouped issue); see "
    "[Working on an issue]({contributing}). If you cannot continue, comment `/unassign` so someone else can take it."
)
MSG_SEVERAL_ASSIGNEES = " More than one person is assigned now ({names}). {mention}, please check."
MSG_RELEASED = "Released. Thank you for letting us know, @{login}; the issue is open for others again."
MSG_NOT_YOURS = "This issue is not assigned to you, so there is nothing to release."
MSG_FAILED = "Automatic assignment did not complete. {mention} will assign this issue by hand."


class EventError(Exception):
    """The event payload or the environment is not usable."""


class GhError(RuntimeError):
    """A ``gh api`` call failed, or GitHub's answer was not what the API documents."""


# (method, endpoint, optional JSON body) -> parsed JSON answer (None for an empty body).
GhApi = Callable[[str, str, Optional[Mapping[str, Any]]], Any]


@dataclass(frozen=True)
class Context:
    """What the event says: who asked for what on which issue."""

    repo: str
    number: int
    login: str
    is_maintainer: bool
    command: str
    names_someone_else: bool


@dataclass(frozen=True)
class IssueFacts:
    """The parts of an issue the rules need."""

    state: str
    labels: frozenset[str]
    assignees: tuple[str, ...]


@dataclass(frozen=True)
class Decision:
    """``action`` is ``ignore``, ``reply``, ``assign`` or ``unassign``; ``message`` is the reply text."""

    action: str
    message: str = ""


def parse_command(body: str) -> Optional[str]:
    """Return ``/assign`` or ``/unassign`` when the first word of the comment is that command, else ``None``.

    Only the first word of the first non-empty line counts, case-insensitively; the rest is ignored.
    """
    lines = body.strip().splitlines()
    if not lines:
        return None
    words = lines[0].split()
    if not words:
        return None
    word = words[0].lower()
    return word if word in COMMANDS else None


def names_someone_else(body: str) -> bool:
    """True when the command line carries an ``@name``: the commenter meant to act on somebody else."""
    lines = body.strip().splitlines()
    return bool(lines) and any(word.startswith("@") for word in lines[0].split()[1:])


def valid_login(login: str) -> bool:
    """GitHub logins are ASCII letters, digits and hyphens, at most 39 characters."""
    return 0 < len(login) <= _MAX_LOGIN_LENGTH and login.isascii() and all(c.isalnum() or c == "-" for c in login)


def _clean(text: str, limit: int = 200) -> str:
    """One line, bounded, with ``::`` defanged so it can never open a workflow command."""
    return " ".join(text.split())[:limit].replace("::", ": :")


def contributing_url(repo: str) -> str:
    return f"https://github.com/{repo}/blob/development/CONTRIBUTING.md#working-on-an-issue"


def decide(ctx: Context, issue: IssueFacts, count_open_assignments: Callable[[], int]) -> Decision:
    """The rules: what to do about ``ctx.command`` from ``ctx.login`` on ``issue``.

    ``count_open_assignments`` is called only when the answer depends on it (the last check of ``/assign``), so a
    refusal for any earlier reason makes no API call and cannot fail because of one.
    """
    login = ctx.login
    if ctx.names_someone_else:
        return Decision("reply", MSG_SELF_ONLY)
    if ctx.command == "/unassign":
        if login in issue.assignees:
            return Decision("unassign", MSG_RELEASED.format(login=login))
        return Decision("reply", MSG_NOT_YOURS)
    if issue.state != "open":
        return Decision("reply", MSG_CLOSED)
    if issue.labels & EXCLUDED_LABELS or not issue.labels & ELIGIBLE_LABELS:
        return Decision("reply", MSG_NOT_OPEN.format(mention=MAINTAINER_MENTION))
    if login in issue.assignees:
        return Decision("reply", MSG_ALREADY_YOURS)
    if issue.assignees:
        owner = issue.assignees[0]
        if valid_login(owner):
            return Decision("reply", MSG_TAKEN.format(owner=owner, front_door=FRONT_DOOR_URL))
        return Decision("reply", MSG_TAKEN_UNKNOWN.format(front_door=FRONT_DOOR_URL))
    if not ctx.is_maintainer:
        held = count_open_assignments()
        if held >= MAX_OPEN_ASSIGNMENTS:
            return Decision(
                "reply", MSG_LIMIT.format(count=held, limit=MAX_OPEN_ASSIGNMENTS, mention=MAINTAINER_MENTION)
            )
    return Decision(
        "assign",
        MSG_ASSIGNED.format(login=login, number=ctx.number, contributing=contributing_url(ctx.repo)),
    )


def context_from_event(event: Mapping[str, Any], repo: str) -> Optional[Context]:
    """``None`` when the event is not a command to act on (PR comment, bot, or no command)."""
    try:
        issue, comment = event["issue"], event["comment"]
        number, body = issue["number"], comment["body"]
        user = comment["user"]
        login, user_type = user["login"], user["type"]
        is_pull_request = "pull_request" in issue
    except (KeyError, TypeError) as exc:
        raise EventError(f"event payload lacks a field: {_clean(str(exc), 60)}") from exc
    if is_pull_request or user_type == "Bot":
        return None
    command = parse_command(body) if isinstance(body, str) else None
    if command is None:
        return None
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise EventError("issue number is not a positive integer")
    if not isinstance(login, str) or not valid_login(login):
        raise EventError("commenter login is not a valid GitHub login")
    return Context(repo, number, login, login.lower() in MAINTAINER_LOGINS, command, names_someone_else(body))


def gh_api(method: str, endpoint: str, payload: Optional[Mapping[str, Any]] = None) -> Any:
    """Call the GitHub REST API through the ``gh`` CLI (authenticated by ``GH_TOKEN``); no shell is involved."""
    command = ["gh", "api", "--method", method, endpoint]
    stdin = None
    if payload is not None:
        command += ["--input", "-"]
        stdin = json.dumps(payload)
    label = f"{method} {endpoint.split('?')[0]}"
    try:
        proc = subprocess.run(
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


def _logins(users: Any) -> tuple[str, ...]:
    if not isinstance(users, list):
        raise GhError("assignees is not a list")
    try:
        return tuple(str(u["login"]) for u in users)
    except (KeyError, TypeError) as exc:
        raise GhError("an assignee has no login") from exc


def fetch_issue(gh: GhApi, repo: str, number: int) -> IssueFacts:
    raw = gh("GET", f"repos/{repo}/issues/{number}", None)
    try:
        labels = frozenset(str(label["name"]) for label in raw["labels"])
        return IssueFacts(str(raw["state"]), labels, _logins(raw["assignees"]))
    except (KeyError, TypeError) as exc:
        raise GhError("the issue answer lacks labels, state or assignees") from exc


def count_open_assignments(gh: GhApi, repo: str, login: str) -> int:
    """Open issues (not pull requests) assigned to ``login`` in this repository."""
    raw = gh("GET", f"repos/{repo}/issues?assignee={quote(login)}&state=open&per_page=100", None)
    if not isinstance(raw, list):
        raise GhError("the assigned-issues answer is not a list")
    return sum(1 for item in raw if isinstance(item, dict) and "pull_request" not in item)


def _perform(ctx: Context, gh: GhApi) -> str:
    """Read, decide, change the assignment. Returns the reply text ('' for none)."""
    issue = fetch_issue(gh, ctx.repo, ctx.number)

    def held() -> int:
        return count_open_assignments(gh, ctx.repo, ctx.login)

    decision = decide(ctx, issue, held)
    if decision.action in ("ignore", "reply"):
        return decision.message
    if decision.action == "unassign":
        result = gh("DELETE", f"repos/{ctx.repo}/issues/{ctx.number}/assignees", {"assignees": [ctx.login]})
        if ctx.login in _logins(result.get("assignees") if isinstance(result, dict) else None):
            raise GhError("GitHub still lists the commenter as assignee after the release")
        return decision.message
    # Assign. GitHub has no "assign only if nobody is" call, so read again right before writing: this narrows the
    # window in which a maintainer's own assignment could be missed to the time between this read and the POST.
    latest = fetch_issue(gh, ctx.repo, ctx.number)
    if latest.assignees:
        return decide(ctx, latest, held).message
    result = gh("POST", f"repos/{ctx.repo}/issues/{ctx.number}/assignees", {"assignees": [ctx.login]})
    assignees = _logins(result.get("assignees") if isinstance(result, dict) else None)
    if ctx.login not in assignees:
        raise GhError("GitHub did not confirm the assignment")
    message = decision.message
    if len(assignees) > 1:
        names = ", ".join(f"@{a}" for a in assignees if valid_login(a))
        message += MSG_SEVERAL_ASSIGNEES.format(names=names, mention=MAINTAINER_MENTION)
    return message


def _comment(gh: GhApi, repo: str, number: int, body: str) -> None:
    gh("POST", f"repos/{repo}/issues/{number}/comments", {"body": body})


def run(event: Mapping[str, Any], repo: str, gh: GhApi) -> int:
    """Handle one ``issue_comment`` event. See the module docstring for the exit codes."""
    ctx = context_from_event(event, repo)
    if ctx is None:
        return 0
    try:
        reply = _perform(ctx, gh)
    except GhError as exc:
        print(f"error: issue #{ctx.number}: {exc}", file=sys.stderr)
        try:
            _comment(gh, ctx.repo, ctx.number, MSG_FAILED.format(mention=MAINTAINER_MENTION))
        except GhError as comment_exc:
            print(f"error: issue #{ctx.number}: could not tell the contributor: {comment_exc}", file=sys.stderr)
        return 1
    if reply:
        _comment(gh, ctx.repo, ctx.number, reply)
    print(f"handled {ctx.command} on issue #{ctx.number}")
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
    except GhError as exc:  # the reply to a successful change could not be posted
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
