"""Tests for tools/triage_gate.py and .github/workflows/triage-gate.yml.

The tool keeps ``needs-triage`` on every issue until a maintainer removes it, from events anyone can cause and from
an hourly sweep, with a token that can write to issues. These tests pin the rules (CONTRIBUTING.md, "Issue
triage"), the invariant that the tool never removes the label, that the answer depends on the issue's state and not
on which event woke the run, the failure paths, that no issue text reaches the log, and the security posture of the
workflow file itself, since no other guard reads it.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import assign_on_comment  # noqa: E402
import issue_dependencies  # noqa: E402
import triage_gate as tool  # noqa: E402

REPO = "HodeTech/ForgeLM"
TRIAGE = tool.TRIAGE_LABEL
SINCE = "2026-10-21T00:00:00Z"
BOTS = ["dependabot[bot]", "github-actions[bot]", "renovate[bot]", "coderabbitai[bot]"]


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool.time, "sleep", lambda seconds: None)


_clock = itertools.count(1)
_BASE = datetime(2026, 10, 22)


def stamp() -> str:
    return (_BASE + timedelta(seconds=next(_clock))).strftime("%Y-%m-%dT%H:%M:%SZ")


def label_event(
    kind: str, login: str, label: str = TRIAGE, at: Optional[str] = None, ident: Optional[int] = None
) -> Dict[str, Any]:
    """One entry of the issue events API; later calls are later in time unless ``at`` says otherwise."""
    return {
        "event": kind,
        "label": {"name": label},
        "actor": {"login": login},
        "created_at": at or stamp(),
        "id": ident if ident is not None else next(_clock),
    }


def reopened_event(login: str, at: Optional[str] = None) -> Dict[str, Any]:
    return {"event": "reopened", "actor": {"login": login}, "created_at": at or stamp(), "id": next(_clock)}


def filler(count: int) -> List[Dict[str, Any]]:
    """Events about another label: they take up pages and nothing else."""
    return [label_event("labeled", "x", "bug") for _ in range(count)]


class FakeGitHub:
    """A small stand-in for the REST API: issues with labels, events and a creation time, and a call log."""

    def __init__(self, labels: Optional[List[str]] = None, events: Optional[List[Dict[str, Any]]] = None) -> None:
        self.issues: Dict[int, Dict[str, Any]] = {}
        self.add_issue(7, labels, events)
        self.calls: List[tuple] = []
        self.comments: List[str] = []
        self.fail: Dict[tuple, str] = {}  # (method, endpoint prefix) -> reason
        self.fail_comment_times = 0  # fail this many comment posts before one succeeds
        self.drop_label = False  # accept the POST but do not apply it
        self.after_events_read: Optional[Callable[[], None]] = None  # runs once, after the first events read
        self._events_reads = 0

    def add_issue(
        self,
        number: int,
        labels: Optional[List[str]] = None,
        events: Optional[List[Dict[str, Any]]] = None,
        created_at: str = "2026-10-22T00:00:00Z",
        pull_request: bool = False,
    ) -> None:
        self.issues[number] = {
            "labels": list(labels or []),
            "events": list(events or []),
            "created_at": created_at,
            "pull_request": pull_request,
        }

    # the single-issue tests use issue 7
    @property
    def labels(self) -> List[str]:
        return self.issues[7]["labels"]

    @property
    def events(self) -> List[Dict[str, Any]]:
        return self.issues[7]["events"]

    def __call__(self, method: str, endpoint: str, payload: Any = None) -> Any:
        self.calls.append((method, endpoint, payload))
        for (m, prefix), reason in self.fail.items():
            if m == method and endpoint.startswith(prefix):
                raise tool.GhError(f"{method} {endpoint} failed: {reason}")
        base = f"repos/{REPO}/issues"
        rest = endpoint[len(base) :]
        if method == "GET" and rest.startswith("?"):
            return self._open_issues(rest)
        number = int(rest.lstrip("/").split("/")[0].split("?")[0])
        issue = self.issues[number]
        if method == "GET" and rest == f"/{number}":
            return {"labels": [{"name": n} for n in issue["labels"]]}
        if method == "GET" and rest.startswith(f"/{number}/events?"):
            return self._events(issue, rest)
        if method == "POST" and rest == f"/{number}/labels":
            if not self.drop_label:
                issue["labels"] += [n for n in payload["labels"] if n not in issue["labels"]]
            return [{"name": n} for n in issue["labels"]]
        if method == "POST" and rest == f"/{number}/comments":
            if self.fail_comment_times:
                self.fail_comment_times -= 1
                raise tool.GhError("POST comments failed: boom")
            self.comments.append(payload["body"])
            return {}
        raise AssertionError(f"unexpected call {method} {endpoint}")

    def _events(self, issue: Dict[str, Any], rest: str) -> List[Dict[str, Any]]:
        page = int(rest.rsplit("page=", 1)[1])
        answer = list(issue["events"][(page - 1) * tool._PAGE_SIZE : page * tool._PAGE_SIZE])
        self._events_reads += 1
        if self._events_reads == 1 and self.after_events_read:
            self.after_events_read()  # after the answer was taken: this read does not see what the hook does
        return answer

    def _open_issues(self, query: str) -> List[Dict[str, Any]]:
        page = int(query.rsplit("page=", 1)[1])
        newest_first = sorted(self.issues.items(), key=lambda item: item[1]["created_at"], reverse=True)
        items = []
        for number, issue in newest_first:
            entry: Dict[str, Any] = {
                "number": number,
                "created_at": issue["created_at"],
                "labels": [{"name": n} for n in issue["labels"]],
            }
            if issue["pull_request"]:
                entry["pull_request"] = {"url": "x"}
            items.append(entry)
        return items[(page - 1) * tool._PAGE_SIZE : page * tool._PAGE_SIZE]

    def writes(self) -> List[tuple]:
        """Every call that changes an issue (labels or comments)."""
        return [c for c in self.calls if c[0] == "POST"]

    def history_reads(self) -> List[tuple]:
        return [c for c in self.calls if "/events?" in c[1]]


def event(
    action: str,
    login: str = "newcomer",
    number: int = 7,
    label: Optional[str] = None,
    pull_request: bool = False,
    title: str = "A new report",
) -> Dict[str, Any]:
    issue: Dict[str, Any] = {"number": number, "title": title, "body": title}
    if pull_request:
        issue["pull_request"] = {"url": "x"}
    payload: Dict[str, Any] = {"action": action, "issue": issue, "sender": {"login": login}}
    if label is not None:
        payload["label"] = {"name": label}
    return payload


def run(gh: FakeGitHub, action: str = "opened", **kwargs: Any) -> int:
    if action == "unlabeled":
        kwargs.setdefault("label", TRIAGE)
    return tool.run(event(action, **kwargs), REPO, gh)


# --- opened -----------------------------------------------------------------------------------------------------


def test_an_issue_created_outside_a_form_gets_the_label() -> None:
    gh = FakeGitHub()
    assert run(gh) == 0
    assert gh.labels == [TRIAGE]
    assert gh.comments == []  # a new issue is not told anything; the issue forms and CONTRIBUTING explain triage


def test_the_label_is_added_with_exactly_this_request() -> None:
    gh = FakeGitHub()
    run(gh)
    assert gh.writes() == [("POST", f"repos/{REPO}/issues/7/labels", {"labels": [TRIAGE]})]


def test_an_issue_that_already_has_the_label_is_left_alone() -> None:
    gh = FakeGitHub(labels=["bug", TRIAGE])
    assert run(gh) == 0
    assert gh.writes() == [] and gh.history_reads() == []  # decided on the labels alone, no history read


def test_an_issue_opened_by_a_maintainer_gets_the_label_too() -> None:
    gh = FakeGitHub()
    assert run(gh, login="cemililik") == 0
    assert gh.labels == [TRIAGE]


@pytest.mark.parametrize("bot", BOTS)
def test_an_issue_opened_by_an_app_gets_the_label_too(bot: str) -> None:
    gh = FakeGitHub()
    assert run(gh, login=bot) == 0
    assert gh.labels == [TRIAGE]


@pytest.mark.parametrize("bot", BOTS)
def test_an_app_reopening_or_removing_the_label_is_handled_like_any_contributor(bot: str) -> None:
    reopened = FakeGitHub(
        events=[label_event("labeled", "x"), label_event("unlabeled", "cemililik"), reopened_event(bot)]
    )
    assert run(reopened, "reopened", login=bot) == 0
    assert reopened.labels == [TRIAGE] and reopened.comments == [tool.MSG_REOPENED]
    removed = FakeGitHub(events=[label_event("labeled", "x"), label_event("unlabeled", bot)])
    assert run(removed, "unlabeled", login=bot) == 0
    assert removed.labels == [TRIAGE] and removed.comments == [tool.MSG_RESTORED]


def test_a_maintainer_who_triaged_before_the_workflow_started_is_not_overruled() -> None:
    gh = FakeGitHub(events=[label_event("labeled", "cemililik"), label_event("unlabeled", "cemililik")])
    assert run(gh) == 0
    assert gh.writes() == [] and gh.labels == []


def test_other_labels_on_an_issue_do_not_count_as_triage() -> None:
    gh = FakeGitHub(labels=["bug", "area: cli", "severity: low"])
    assert run(gh) == 0
    assert TRIAGE in gh.labels


# --- reopened ---------------------------------------------------------------------------------------------------


def test_an_issue_reopened_by_a_contributor_goes_back_to_triage_with_a_notice() -> None:
    history = [label_event("labeled", "newcomer"), label_event("unlabeled", "cemililik"), reopened_event("newcomer")]
    gh = FakeGitHub(events=history)  # triaged earlier by a maintainer, closed, now reopened by its author
    assert run(gh, "reopened") == 0
    assert gh.labels == [TRIAGE]
    assert gh.comments == [tool.MSG_REOPENED]


def test_a_maintainers_removal_after_the_reopening_is_not_overruled() -> None:
    history = [
        label_event("labeled", "newcomer"),
        label_event("unlabeled", "cemililik"),
        reopened_event("newcomer"),
        label_event("labeled", "cemililik"),
        label_event("unlabeled", "cemililik"),
    ]
    gh = FakeGitHub(events=history)
    assert run(gh, "reopened") == 0  # the delayed run for the reopening, or a manual re-run of it
    assert gh.writes() == [] and gh.labels == []


def test_an_issue_reopened_by_a_maintainer_keeps_the_maintainers_decision() -> None:
    gh = FakeGitHub()
    assert run(gh, "reopened", login="cemililik") == 0
    assert gh.writes() == [] and gh.history_reads() == []


def test_a_reopening_by_a_maintainer_does_not_send_the_issue_back_to_triage() -> None:
    history = [label_event("labeled", "x"), label_event("unlabeled", "cemililik"), reopened_event("cemililik")]
    gh = FakeGitHub(events=history)
    assert run(gh, "opened") == 0  # any later run reads the history: the maintainer's reopening is no contributor's
    assert gh.writes() == []


def test_a_reopened_issue_that_still_has_the_label_gets_no_second_notice() -> None:
    gh = FakeGitHub(labels=[TRIAGE])
    assert run(gh, "reopened") == 0
    assert gh.writes() == []


# --- unlabeled --------------------------------------------------------------------------------------------------


def test_a_removal_by_a_contributor_is_undone_with_a_notice() -> None:
    gh = FakeGitHub(events=[label_event("labeled", "newcomer"), label_event("unlabeled", "helper")])
    assert run(gh, "unlabeled", login="helper") == 0
    assert gh.writes() == [
        ("POST", f"repos/{REPO}/issues/7/labels", {"labels": [TRIAGE]}),
        ("POST", f"repos/{REPO}/issues/7/comments", {"body": tool.MSG_RESTORED}),
    ]
    assert gh.labels == [TRIAGE]


def test_a_removal_by_a_maintainer_completes_triage_and_is_never_undone() -> None:
    gh = FakeGitHub(events=[label_event("labeled", "newcomer"), label_event("unlabeled", "cemililik")])
    assert run(gh, "unlabeled", login="cemililik") == 0
    assert gh.writes() == [] and gh.labels == []
    assert gh.history_reads() == []  # the sender alone decides: no API read can fail it


def test_a_maintainer_login_is_matched_without_regard_to_case() -> None:
    gh = FakeGitHub()
    assert run(gh, "unlabeled", login="CemilIlik") == 0
    assert gh.writes() == []


def test_a_contributors_removal_is_not_undone_when_a_maintainer_removed_it_afterwards() -> None:
    history = [
        label_event("labeled", "newcomer"),
        label_event("unlabeled", "helper"),
        label_event("labeled", "cemililik"),
        label_event("unlabeled", "cemililik"),
    ]
    gh = FakeGitHub(events=history)
    assert run(gh, "unlabeled", login="helper") == 0
    assert gh.writes() == []


def test_a_label_that_was_put_back_is_not_added_twice() -> None:
    gh = FakeGitHub(labels=[TRIAGE], events=[label_event("labeled", "x"), label_event("unlabeled", "helper")])
    assert run(gh, "unlabeled", login="helper") == 0
    assert gh.writes() == []


def test_the_removal_of_another_label_is_ignored() -> None:
    gh = FakeGitHub()
    assert run(gh, "unlabeled", label="bug") == 0
    assert gh.calls == []


def test_the_tool_never_removes_a_label() -> None:
    gh = FakeGitHub(events=[label_event("labeled", "x"), label_event("unlabeled", "helper")])
    for action in ("opened", "reopened", "unlabeled"):
        run(gh, action, login="helper")
    run(gh, "unlabeled", login="cemililik")
    assert all(call[0] in ("GET", "POST") for call in gh.calls)
    assert all(call[1].endswith(("/labels", "/comments")) for call in gh.writes())


# --- the answer depends on the state, not on the event -----------------------------------------------------------


@pytest.mark.parametrize("action", ["opened", "reopened", "unlabeled"])
def test_every_event_puts_the_label_back_on_an_issue_a_contributor_stripped(action: str) -> None:
    gh = FakeGitHub(events=[label_event("labeled", "x"), label_event("unlabeled", "helper")])
    assert run(gh, action, login="helper") == 0
    assert gh.labels == [TRIAGE]


@pytest.mark.parametrize("action", ["opened", "unlabeled"])
def test_a_delayed_or_replayed_event_leaves_a_triaged_issue_alone(action: str) -> None:
    gh = FakeGitHub(events=[label_event("labeled", "x"), label_event("unlabeled", "cemililik")])
    assert run(gh, action, login="helper") == 0
    assert gh.writes() == []


def test_a_maintainer_removal_that_lands_between_the_read_and_the_write_wins() -> None:
    gh = FakeGitHub(events=[label_event("labeled", "x"), label_event("unlabeled", "helper")])

    def maintainer_removes_it() -> None:
        gh.events.extend([label_event("labeled", "cemililik"), label_event("unlabeled", "cemililik")])

    gh.after_events_read = maintainer_removes_it
    assert run(gh, "unlabeled", login="helper") == 0
    assert gh.writes() == [] and gh.labels == []  # the second look, right before writing, saw the removal


def test_a_label_that_appears_between_the_read_and_the_write_is_not_added_again() -> None:
    gh = FakeGitHub(events=[label_event("labeled", "x"), label_event("unlabeled", "helper")])
    gh.after_events_read = lambda: gh.labels.append(TRIAGE)
    assert run(gh, "unlabeled", login="helper") == 0
    assert gh.writes() == []


# --- events the tool does not handle ----------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["edited", "labeled", "closed", "assigned", "transferred", "", "OPENED"])
def test_other_actions_are_ignored(action: str) -> None:
    gh = FakeGitHub()
    assert run(gh, action) == 0
    assert gh.calls == []


def test_a_pull_request_is_ignored() -> None:
    gh = FakeGitHub()
    assert run(gh, pull_request=True) == 0
    assert gh.calls == []


# --- label history ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "history, expected",
    [
        ([], tool.HISTORY_NEVER),
        ([label_event("labeled", "a", "bug")], tool.HISTORY_NEVER),  # another label
        ([label_event("labeled", "a")], tool.HISTORY_LABELLED),
        ([label_event("labeled", "a"), label_event("unlabeled", "cemililik")], tool.HISTORY_MAINTAINER_REMOVED),
        ([label_event("labeled", "a"), label_event("unlabeled", "other")], tool.HISTORY_OTHER_REMOVED),
        ([label_event("unlabeled", "cemililik"), label_event("labeled", "a")], tool.HISTORY_LABELLED),
        ([label_event("unlabeled", "other"), label_event("unlabeled", "cemililik")], tool.HISTORY_MAINTAINER_REMOVED),
        ([label_event("unlabeled", "cemililik", "NEEDS-TRIAGE")], tool.HISTORY_MAINTAINER_REMOVED),
        ([{"event": "unlabeled", "label": {"name": TRIAGE}, "actor": None}], tool.HISTORY_OTHER_REMOVED),
        ([{"event": "unlabeled", "label": {"name": TRIAGE}}], tool.HISTORY_OTHER_REMOVED),
        ([{"event": "closed"}, "not a dict", None, {"event": "labeled", "label": None}], tool.HISTORY_NEVER),
    ],
)
def test_triage_history_follows_the_latest_label_event(history: List[Any], expected: str) -> None:
    assert tool.triage_history(history).state == expected


def test_events_are_ordered_by_time_not_by_the_order_they_arrive_in() -> None:
    removed = label_event("unlabeled", "cemililik", at="2026-10-22T00:00:02Z", ident=2)
    added = label_event("labeled", "x", at="2026-10-22T00:00:01Z", ident=1)
    assert tool.triage_history([removed, added]).state == tool.HISTORY_MAINTAINER_REMOVED
    assert tool.triage_history([added, removed]).state == tool.HISTORY_MAINTAINER_REMOVED
    # the other way round: the maintainer's removal came first and the label was added after it
    removed_first = label_event("unlabeled", "cemililik", at="2026-10-22T00:00:01Z", ident=1)
    added_later = label_event("labeled", "x", at="2026-10-22T00:00:02Z", ident=2)
    assert tool.triage_history([added_later, removed_first]).state == tool.HISTORY_LABELLED


def test_events_in_the_same_second_are_ordered_by_id() -> None:
    same = "2026-10-22T00:00:01Z"
    later_id = label_event("unlabeled", "cemililik", at=same, ident=9)
    earlier_id = label_event("labeled", "x", at=same, ident=3)
    assert tool.triage_history([later_id, earlier_id]).state == tool.HISTORY_MAINTAINER_REMOVED


def test_a_reopening_counts_only_when_someone_other_than_a_maintainer_did_it() -> None:
    history = tool.triage_history([label_event("unlabeled", "cemililik"), reopened_event("cemililik")])
    assert history.reopened_pos == -1 and tool.untriaged(history) is False
    history = tool.triage_history([label_event("unlabeled", "cemililik"), reopened_event("newcomer")])
    assert history.reopened_pos > history.label_pos and tool.untriaged(history) is True


def test_a_former_maintainers_removal_still_counts_as_triage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "FORMER_MAINTAINER_LOGINS", frozenset({"oldhand"}))
    gh = FakeGitHub(events=[label_event("labeled", "x"), label_event("unlabeled", "oldhand")])
    assert run(gh) == 0
    assert gh.writes() == []
    assert not tool._is_maintainer("oldhand")  # but acting now does not end a triage


def test_a_former_maintainers_reopening_is_not_a_contributors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "FORMER_MAINTAINER_LOGINS", frozenset({"oldhand"}))
    history = tool.triage_history([label_event("unlabeled", "cemililik"), reopened_event("oldhand")])
    assert history.reopened_pos == -1 and not tool.untriaged(history)


def test_the_current_and_former_maintainer_lists_do_not_overlap() -> None:
    assert not tool.MAINTAINER_LOGINS & tool.FORMER_MAINTAINER_LOGINS


@pytest.mark.parametrize("count", [999, 1000, 1001, 1099])
def test_the_history_is_read_in_full_up_to_the_page_limit(count: int) -> None:
    history = filler(count - 2) + [label_event("labeled", "a"), label_event("unlabeled", "cemililik")]
    gh = FakeGitHub(events=history)
    assert run(gh) == 0  # opened; a maintainer removed the label in the very last event
    assert gh.writes() == []


def test_a_history_too_long_to_read_puts_the_label_back_when_someone_else_acted() -> None:
    long_history = filler(1098) + [label_event("labeled", "a"), label_event("unlabeled", "cemililik")] + filler(2)
    gh = FakeGitHub(events=long_history)  # 1102 events: the eleventh page is full
    assert run(gh, "unlabeled", login="helper") == 0
    assert gh.labels == [TRIAGE] and gh.comments == [tool.MSG_RESTORED]  # fail closed: the safe direction


def test_a_history_too_long_to_read_does_not_overrule_a_maintainer() -> None:
    gh = FakeGitHub(events=filler(1200))
    assert run(gh, "unlabeled", login="cemililik") == 0
    assert gh.writes() == [] and gh.history_reads() == []


# --- failures ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failing",
    [
        ("GET", f"repos/{REPO}/issues/7"),
        ("GET", f"repos/{REPO}/issues/7/events"),
        ("POST", f"repos/{REPO}/issues/7/labels"),
    ],
)
def test_an_api_failure_exits_one_and_never_half_labels(failing: tuple, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub()
    gh.fail[failing] = "HTTP 502"
    assert run(gh) == 1
    assert gh.labels == [] and gh.comments == []
    assert "HTTP 502" in capsys.readouterr().err


def test_a_label_github_did_not_apply_is_an_error() -> None:
    gh = FakeGitHub()
    gh.drop_label = True
    assert run(gh) == 1


def test_an_add_label_answer_without_labels_is_an_error() -> None:
    inner = FakeGitHub()

    def odd(method: str, endpoint: str, payload: Any = None) -> Any:
        if endpoint.endswith("/labels") and method == "POST":
            return {"message": "not a list"}
        return inner(method, endpoint, payload)

    assert tool.run(event("opened"), REPO, odd) == 1


@pytest.mark.parametrize("answer", [None, {}, {"labels": None}, {"labels": [{"nope": 1}]}])
def test_an_issue_answer_without_labels_is_an_error(answer: Any) -> None:
    with pytest.raises(tool.GhError):
        tool.current_labels(lambda m, e, p=None: answer, REPO, 7)


def test_an_events_answer_that_is_not_a_list_is_an_error() -> None:
    with pytest.raises(tool.GhError):
        tool.read_history(lambda m, e, p=None: {"message": "x"}, REPO, 7)


def test_a_notice_is_retried_exactly_once() -> None:
    gh = FakeGitHub(events=[label_event("unlabeled", "helper")])
    gh.fail_comment_times = 1
    assert run(gh, "unlabeled", login="helper") == 0
    assert gh.comments == [tool.MSG_RESTORED]
    gh = FakeGitHub(events=[label_event("unlabeled", "helper")])
    gh.fail_comment_times = 9
    assert run(gh, "unlabeled", login="helper") == 1
    assert len([c for c in gh.calls if c[1].endswith("/comments")]) == tool._COMMENT_ATTEMPTS == 2


def test_a_notice_that_cannot_be_posted_exits_one_but_the_label_is_back(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub(events=[label_event("unlabeled", "helper")])
    gh.fail_comment_times = 5
    assert run(gh, "unlabeled", login="helper") == 1
    assert gh.labels == [TRIAGE] and gh.comments == []
    assert "could not post the notice" in capsys.readouterr().err


# --- the event payload ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"action": "opened"},
        {"action": "opened", "issue": {"number": 7}},
        {"action": "opened", "issue": {"number": 7}, "sender": {}},
        {"action": "unlabeled", "issue": {"number": 7}, "sender": {"login": "a"}},  # no label
        {"action": "opened", "issue": {"number": 0}, "sender": {"login": "a"}},
        {"action": "opened", "issue": {"number": True}, "sender": {"login": "a"}},
        {"action": "opened", "issue": {"number": "7"}, "sender": {"login": "a"}},
        {"action": "opened", "issue": {"number": 7}, "sender": {"login": "a b"}},
        {"action": "opened", "issue": {"number": 7}, "sender": {"login": 7}},
        {"action": "opened", "issue": {"number": 7}, "sender": {"login": "x" * 40}},
        {"action": "opened", "issue": {"number": 7}, "sender": {"login": "[bot]"}},
    ],
)
def test_an_unusable_payload_is_an_event_error(payload: Any) -> None:
    with pytest.raises(tool.EventError):
        tool.run(payload, REPO, FakeGitHub())


@pytest.mark.parametrize("action", [["opened"], {"x": 1}, 7, None])
def test_an_action_that_is_not_a_string_is_ignored_not_a_crash(action: Any) -> None:
    payload = {"action": action, "issue": {"number": 7}, "sender": {"login": "a"}}
    assert tool.run(payload, REPO, FakeGitHub()) == 0


@pytest.mark.parametrize(
    "login, ok",
    [("alice", True), ("a-b-1", True), ("A", True), ("", False), ("a b", False), ("a/b", False), ("é", False),
     ("x" * 40, False), ("x" * 39, True), ("a;rm", False), ("a\nb", False),
     ("dependabot[bot]", True), ("github-actions[bot]", True), ("renovate[bot]", True), ("coderabbitai[bot]", True),
     ("[bot]", False), ("a[bot]x", False), ("a[bot][bot]", False), ("a]b", False), ("x" * 40 + "[bot]", False)],
)  # fmt: skip
def test_valid_login(login: str, ok: bool) -> None:
    assert tool.valid_login(login) is ok


@pytest.mark.parametrize(
    "repo, ok",
    [("HodeTech/ForgeLM", True), ("a/b", True), ("a-b/c_d.e", True), ("", False), ("a", False), ("a/b/c", False),
     ("a b/c", False), ("/b", False), ("a/", False), ("a/b;x", False), ("é/b", False)],
)  # fmt: skip
def test_valid_repository(repo: str, ok: bool) -> None:
    assert tool.valid_repository(repo) is ok


# --- nothing from the issue reaches the log ---------------------------------------------------------------------


def test_the_issue_text_never_reaches_the_log(capsys: pytest.CaptureFixture[str]) -> None:
    hostile = "::error::pwned ::add-mask::secret `$(touch /tmp/x)` ${{ secrets.GITHUB_TOKEN }}"
    gh = FakeGitHub()
    assert tool.run(event("opened", title=hostile), REPO, gh) == 0
    out = capsys.readouterr()
    for text in (out.out, out.err):
        assert "::error::" not in text and "pwned" not in text and "secret" not in text and "$(" not in text
    assert out.out.strip() == "handled opened on issue #7: added needs-triage"


def test_gh_error_text_is_flattened_and_defanged_before_it_is_printed() -> None:
    assert tool._clean("::error::boom\nsecond line  with   gaps") == ": :error: :boom second line with gaps"
    assert len(tool._clean("x" * 1000)) == 200


# --- the sweep --------------------------------------------------------------------------------------------------


def test_the_sweep_labels_recent_issues_that_lack_the_label_and_nothing_else() -> None:
    gh = FakeGitHub()
    gh.issues.clear()
    gh.add_issue(7, created_at="2026-10-22T10:00:00Z")  # recent, no label -> labelled
    gh.add_issue(8, labels=[TRIAGE], created_at="2026-10-22T09:00:00Z")  # already has it
    gh.add_issue(9, created_at="2026-10-22T08:00:00Z", pull_request=True)  # a pull request
    gh.add_issue(10, labels=["bug"], created_at="2026-10-20T08:00:00Z")  # created before the gate went live
    triaged = [label_event("labeled", "x"), label_event("unlabeled", "cemililik")]
    gh.add_issue(11, events=triaged, created_at="2026-10-22T07:00:00Z")  # a maintainer triaged it
    assert tool.run_sweep(REPO, gh, SINCE) == 0
    assert gh.issues[7]["labels"] == [TRIAGE]
    assert gh.issues[8]["labels"] == [TRIAGE]
    assert gh.issues[9]["labels"] == [] and gh.issues[10]["labels"] == ["bug"] and gh.issues[11]["labels"] == []
    assert gh.comments == []  # a missing label on a new issue is repaired silently


def test_the_sweep_restores_a_label_a_contributor_removed_and_says_so() -> None:
    gh = FakeGitHub()
    gh.issues.clear()
    removed = [label_event("labeled", "x"), label_event("unlabeled", "helper")]
    gh.add_issue(7, events=removed, created_at="2026-10-22T10:00:00Z")
    assert tool.run_sweep(REPO, gh, SINCE) == 0
    assert gh.issues[7]["labels"] == [TRIAGE] and gh.comments == [tool.MSG_RESTORED]


def test_the_sweep_sends_a_reopened_issue_back_but_not_a_triaged_one() -> None:
    gh = FakeGitHub()
    gh.issues.clear()
    reopened = [label_event("labeled", "x"), label_event("unlabeled", "cemililik"), reopened_event("newcomer")]
    gh.add_issue(7, events=reopened, created_at="2026-10-22T10:00:00Z")
    triaged = [label_event("labeled", "x"), label_event("unlabeled", "cemililik")]
    gh.add_issue(8, events=triaged, created_at="2026-10-22T09:00:00Z")
    assert tool.run_sweep(REPO, gh, SINCE) == 0
    assert gh.issues[7]["labels"] == [TRIAGE] and gh.issues[8]["labels"] == []
    assert gh.comments == [tool.MSG_REOPENED]


def test_the_sweep_reads_every_page_of_recent_issues() -> None:
    gh = FakeGitHub()
    gh.issues.clear()
    start = datetime(2026, 10, 22)
    for number in range(1, 251):
        created = (start + timedelta(minutes=number)).strftime("%Y-%m-%dT%H:%M:%SZ")
        gh.add_issue(number, created_at=created)
    assert tool.run_sweep(REPO, gh, SINCE) == 0
    assert all(issue["labels"] == [TRIAGE] for issue in gh.issues.values())


def test_the_sweep_carries_on_after_one_issue_fails_and_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub()
    gh.issues.clear()
    gh.add_issue(7, created_at="2026-10-22T10:00:00Z")
    gh.add_issue(8, created_at="2026-10-22T09:00:00Z")
    gh.fail[("POST", f"repos/{REPO}/issues/7/labels")] = "HTTP 502"
    assert tool.run_sweep(REPO, gh, SINCE) == 1
    assert gh.issues[8]["labels"] == [TRIAGE] and gh.issues[7]["labels"] == []
    out = capsys.readouterr()
    assert "issue #7" in out.err and "1 failed" in out.out


def test_the_sweep_does_not_guess_when_the_history_is_too_long(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub()
    gh.issues.clear()
    gh.add_issue(7, events=filler(1200), created_at="2026-10-22T10:00:00Z")
    gh.add_issue(8, created_at="2026-10-22T09:00:00Z")
    assert tool.run_sweep(REPO, gh, SINCE) == 1
    assert gh.issues[7]["labels"] == [] and gh.issues[8]["labels"] == [TRIAGE]
    assert "more than" in capsys.readouterr().err


def test_a_sweep_that_cannot_list_the_issues_exits_one() -> None:
    gh = FakeGitHub()
    gh.fail[("GET", f"repos/{REPO}/issues?")] = "HTTP 500"
    assert tool.run_sweep(REPO, gh, SINCE) == 1


@pytest.mark.parametrize(
    "entry",
    [
        {"number": 1, "labels": []},  # no created_at
        {"created_at": "2026-10-22T00:00:00Z", "labels": []},  # no number
        {"number": True, "created_at": "2026-10-22T00:00:00Z", "labels": []},
        {"number": 1, "created_at": "2026-10-22T00:00:00Z"},  # no labels
    ],
)
def test_the_sweep_rejects_an_entry_it_cannot_read(entry: Dict[str, Any]) -> None:
    with pytest.raises(tool.GhError):
        tool.untriaged_candidates(lambda m, e, p=None: [entry], REPO, SINCE)


def test_the_sweep_refuses_to_page_forever() -> None:
    page = [{"number": n, "created_at": "2026-10-23T00:00:00Z", "labels": []} for n in range(tool._PAGE_SIZE)]
    with pytest.raises(tool.GhError):
        tool.untriaged_candidates(lambda m, e, p=None: page, REPO, SINCE)


# --- the gh wrapper ---------------------------------------------------------------------------------------------


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_gh_api_runs_a_list_command_without_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Dict[str, Any] = {}

    def fake_run(command: Any, **kwargs: Any) -> _Completed:
        seen["command"], seen["kwargs"] = command, kwargs
        return _Completed(stdout='[{"name": "needs-triage"}]')

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    assert tool.gh_api("POST", "repos/o/r/issues/1/labels", {"labels": [TRIAGE]}) == [{"name": TRIAGE}]
    assert seen["command"] == ["gh", "api", "--method", "POST", "repos/o/r/issues/1/labels", "--input", "-"]
    assert seen["kwargs"]["input"] == '{"labels": ["needs-triage"]}'
    assert not seen["kwargs"].get("shell") and seen["kwargs"]["timeout"] == tool._GH_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "method, endpoint",
    [
        ("DELETE", "repos/o/r/issues/1/labels/needs-triage"),  # the tool never removes a label
        ("PATCH", "repos/o/r/issues/1"),
        ("PUT", "repos/o/r/issues/1/labels"),
        ("GET", "user"),
        ("GET", "/repos/o/r/issues/1"),
        ("GET", "repos/o/r"),
        ("GET", "repos/o/r/pulls/1"),
        ("GET", "repos/o/r/contents/x"),
        ("GET", "repos/o/r/issues/1 --jq x"),
        ("GET", "repos/o/r/issues/../../x"),
        ("GET", "repos/o/r/issues//1"),
        ("GET", "repos/o r/x/issues/1"),
        ("GET", "repos/o/r/issues\n/1"),
        ("GET", "-X"),
        ("GET", "graphql"),
    ],
)
def test_gh_api_refuses_anything_outside_the_fixed_endpoints(
    monkeypatch: pytest.MonkeyPatch, method: str, endpoint: str
) -> None:
    def must_not_run(*args: Any, **kwargs: Any) -> None:
        pytest.fail("gh was started for an endpoint that must be refused")

    monkeypatch.setattr(tool.subprocess, "run", must_not_run)
    with pytest.raises(tool.GhError, match="refusing"):
        tool.gh_api(method, endpoint)


def test_gh_api_accepts_the_issue_endpoints_the_tool_uses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: _Completed(stdout="[]"))
    for endpoint in (
        "repos/o/r/issues/1",
        "repos/o/r/issues/1/events?per_page=100&page=2",
        "repos/o/r/issues?state=open&sort=created&direction=desc&per_page=100&page=1",
        "repos/o/r/issues/1/comments",
    ):
        assert tool.gh_api("GET", endpoint) == []


def test_gh_api_turns_failures_into_gherror_without_leaking_a_multiline_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tool.subprocess, "run", lambda *a, **k: _Completed(returncode=1, stderr="::error::first\nsecond")
    )
    with pytest.raises(tool.GhError) as info:
        tool.gh_api("GET", "repos/o/r/issues/1?x=y")
    message = str(info.value)
    assert "\n" not in message and "::error::" not in message and "?x=y" not in message


@pytest.mark.parametrize("exc", [OSError("no gh"), subprocess.TimeoutExpired("gh", 60)])
def test_gh_api_turns_a_missing_binary_or_a_timeout_into_gherror(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise exc

    monkeypatch.setattr(tool.subprocess, "run", boom)
    with pytest.raises(tool.GhError):
        tool.gh_api("GET", "repos/o/r/issues/1")


def test_gh_api_rejects_an_answer_that_is_not_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: _Completed(stdout="<html>"))
    with pytest.raises(tool.GhError):
        tool.gh_api("GET", "repos/o/r/issues/1")


def test_gh_api_returns_none_for_an_empty_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: _Completed(stdout="  \n"))
    assert tool.gh_api("POST", "repos/o/r/issues/1/comments", {"body": "x"}) is None


# --- main -------------------------------------------------------------------------------------------------------


def test_main_needs_the_workflow_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert tool.main() == 2
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    assert tool.main() == 2  # no event file


@pytest.mark.parametrize("repo", ["a/b/c", "a b/c", "", "owner", "own;er/x"])
def test_main_rejects_a_repository_name_that_is_not_one(monkeypatch: pytest.MonkeyPatch, repo: str) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", repo)
    monkeypatch.setenv("GITHUB_EVENT_PATH", "unused.json")
    assert tool.main() == 2


def test_main_rejects_an_unreadable_event_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "missing.json"))
    assert tool.main() == 2
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(broken))
    assert tool.main() == 2


def test_main_reads_the_event_and_adds_the_label(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event("opened")), encoding="utf-8")
    gh = FakeGitHub()
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setattr(tool, "gh_api", gh)
    assert tool.main() == 0
    assert gh.labels == [TRIAGE]


def test_main_turns_an_event_error_into_exit_two(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "event.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    assert tool.main() == 2


def test_main_runs_the_sweep_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGitHub()
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv(tool.SINCE_ENV, SINCE)
    monkeypatch.setattr(tool, "gh_api", gh)
    assert tool.main(["sweep"]) == 0
    assert gh.labels == [TRIAGE]


@pytest.mark.parametrize("since", ["", "2026-10-21", "garbage", "2026-10-21T00:00:00", "2026-13-40T00:00:00Z"])
def test_main_refuses_a_sweep_without_a_valid_since(monkeypatch: pytest.MonkeyPatch, since: str) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv(tool.SINCE_ENV, since)
    monkeypatch.setattr(tool, "gh_api", FakeGitHub())
    assert tool.main(["sweep"]) == 2


def test_main_refuses_an_unknown_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    assert tool.main(["--help"]) == 2
    assert tool.main(["sweep", "extra"]) == 2


# --- the maintainers --------------------------------------------------------------------------------------------


def test_the_maintainer_list_matches_maintainers_md() -> None:
    text = (ROOT / "MAINTAINERS.md").read_text(encoding="utf-8")
    table = text.split("## Current maintainers", 1)[1].split("\n## ", 1)[0]
    listed = {
        row.split("[@", 1)[1].split("]", 1)[0].lower()
        for row in table.splitlines()
        if row.startswith("|") and "[@" in row
    }
    assert listed == set(tool.MAINTAINER_LOGINS)


def test_the_issue_tools_agree_on_who_a_maintainer_is() -> None:
    assert tool.MAINTAINER_LOGINS == assign_on_comment.MAINTAINER_LOGINS == issue_dependencies.MAINTAINER_LOGINS


def test_the_label_is_the_one_the_other_issue_tools_use() -> None:
    assert tool.TRIAGE_LABEL in assign_on_comment.EXCLUDED_LABELS
    assert tool.TRIAGE_LABEL == issue_dependencies.TRIAGE_LABEL


# --- the workflow file ------------------------------------------------------------------------------------------

WORKFLOW = ROOT / ".github/workflows/triage-gate.yml"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"  # v7.0.1


@pytest.fixture(scope="module")
def workflow() -> Dict[Any, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_workflow_triggers_only_on_the_issue_events_the_tool_handles_the_sweep_and_a_manual_run(
    workflow: Dict[Any, Any],
) -> None:
    triggers = workflow[True]  # YAML 1.1 reads the key `on` as a boolean
    assert set(triggers) == {"issues", "schedule", "workflow_dispatch"}
    assert set(triggers["issues"]["types"]) == set(tool.HANDLED_ACTIONS)
    assert len(triggers["schedule"]) == 1 and triggers["schedule"][0]["cron"].split()[1:] == ["*", "*", "*", "*"]


def test_workflow_has_exactly_the_event_job_and_the_sweep_job(workflow: Dict[Any, Any]) -> None:
    assert set(workflow["jobs"]) == {"gate", "sweep"}


@pytest.mark.parametrize("name", ["gate", "sweep"])
def test_workflow_token_is_least_privilege_and_the_job_is_bounded(workflow: Dict[Any, Any], name: str) -> None:
    assert workflow["permissions"] == {}
    job = workflow["jobs"][name]
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    assert job["timeout-minutes"] == 10 and job["runs-on"] == "ubuntu-latest"


def test_workflow_gate_job_runs_only_for_the_events_the_script_handles(workflow: Dict[Any, Any]) -> None:
    condition = workflow["jobs"]["gate"]["if"]
    assert condition == (
        "${{ github.event_name == 'issues' && "
        "(github.event.action != 'unlabeled' || github.event.label.name == 'needs-triage') }}"
    )
    assert workflow["jobs"]["sweep"]["if"] == "${{ github.event_name != 'issues' }}"


def test_workflow_queues_runs_per_issue_and_never_cancels_one_in_progress(workflow: Dict[Any, Any]) -> None:
    gate = workflow["jobs"]["gate"]["concurrency"]
    assert gate == {"group": "triage-gate-${{ github.event.issue.number }}", "cancel-in-progress": False}
    sweep = workflow["jobs"]["sweep"]["concurrency"]
    assert sweep == {"group": "triage-sweep", "cancel-in-progress": False}
    assert "concurrency" not in workflow  # a workflow-wide group would queue unrelated issues behind each other


@pytest.mark.parametrize("name", ["gate", "sweep"])
def test_workflow_never_puts_an_expression_into_a_shell_line(workflow: Dict[Any, Any], name: str) -> None:
    runs = [s["run"] for s in workflow["jobs"][name]["steps"] if "run" in s]
    assert runs == ["python3 tools/triage_gate.py" + (" sweep" if name == "sweep" else "")]
    assert all("${{" not in run for run in runs)


@pytest.mark.parametrize("name", ["gate", "sweep"])
def test_workflow_uses_one_action_pinned_to_the_v7_0_1_commit(workflow: Dict[Any, Any], name: str) -> None:
    uses = [s["uses"] for s in workflow["jobs"][name]["steps"] if "uses" in s]
    assert uses == [CHECKOUT]


@pytest.mark.parametrize("name", ["gate", "sweep"])
def test_workflow_checkout_takes_one_file_of_the_default_branch_and_keeps_no_credentials(
    workflow: Dict[Any, Any], name: str
) -> None:
    checkout = next(s for s in workflow["jobs"][name]["steps"] if "uses" in s)
    assert checkout["with"] == {
        "persist-credentials": False,
        "sparse-checkout": "tools/triage_gate.py",
        "sparse-checkout-cone-mode": False,
    }  # no `ref`: the default branch, never an event-supplied ref


def test_workflow_only_secret_is_the_github_token(workflow: Dict[Any, Any]) -> None:
    for name in ("gate", "sweep"):
        step = next(s for s in workflow["jobs"][name]["steps"] if "run" in s)
        assert step["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
        assert set(step["env"]) == {"GH_TOKEN"} | ({tool.SINCE_ENV} if name == "sweep" else set())
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("secrets.") == 2 and "pull_request_target" not in text and "workflow_run" not in text


def test_workflow_sweep_date_is_a_valid_timestamp(workflow: Dict[Any, Any]) -> None:
    step = next(s for s in workflow["jobs"]["sweep"]["steps"] if "run" in s)
    assert datetime.strptime(step["env"][tool.SINCE_ENV], tool._SINCE_FORMAT)


# --- every way an issue is created carries the label ------------------------------------------------------------

FORMS = sorted(p for p in (ROOT / ".github/ISSUE_TEMPLATE").glob("*.yml") if p.name != "config.yml")


def test_there_are_issue_forms_to_check() -> None:
    assert len(FORMS) >= 3


@pytest.mark.parametrize("form", FORMS, ids=lambda p: p.name)
def test_every_issue_form_adds_the_label_and_tells_the_reporter_about_triage(form: Path) -> None:
    loaded = yaml.safe_load(form.read_text(encoding="utf-8"))
    assert TRIAGE in loaded["labels"]
    assert "CONTRIBUTING.md#issue-triage" in form.read_text(encoding="utf-8")


def _labels_of_the_create_call(script: str) -> List[str]:
    """The string literals of the ``labels: [...]`` array that follows an ``issues.create(`` call."""
    after = script.split("issues.create(", 1)[1]
    array = after.split("labels:", 1)[1].split("[", 1)[1].split("]", 1)[0]
    return [part.strip().strip("'\"") for part in array.split(",") if part.strip()]


def test_the_nightly_failure_issue_is_created_with_the_label() -> None:
    nightly = yaml.safe_load((ROOT / ".github/workflows/nightly.yml").read_text(encoding="utf-8"))
    scripts = [
        step["with"]["script"]
        for job in nightly["jobs"].values()
        for step in job.get("steps", [])
        if "script" in step.get("with", {})
    ]
    creating = [s for s in scripts if "issues.create(" in s]
    assert len(creating) == 1
    assert _labels_of_the_create_call(creating[0]) == ["nightly-failure", "bug", TRIAGE]


def test_no_workflow_creates_an_issue_without_the_label() -> None:
    for workflow_file in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
        text = workflow_file.read_text(encoding="utf-8")
        for marker in ("issues.create(", "gh issue create"):
            for part in text.split(marker)[1:]:
                assert TRIAGE in part[:900], f"{workflow_file.name}: {marker} without {TRIAGE}"


# --- the documentation promises what the tool does --------------------------------------------------------------


def test_contributing_explains_triage_and_the_rule_for_contributors() -> None:
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    section = text.split("### Issue triage", 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0].lower()
    for needle in (
        f"`{TRIAGE}`",
        "every new issue",
        "only a maintainer",
        "back with a comment",
        "reopens",
        "every hour",
        "pull request",
        "will not review",
        "not an automated check",
        "maintainers.md",
    ):
        assert needle in section, needle


def test_the_maintainer_guide_covers_the_routine_the_exception_and_stepping_down() -> None:
    text = (ROOT / "MAINTAINERS.md").read_text(encoding="utf-8")
    section = text.split("\n## Triage", 1)[1].split("\n## ", 1)[0]
    for needle in ("Rotate any exposed secret", "deleted", "FORMER_MAINTAINER_LOGINS", "hourly sweep", "by hand"):
        assert needle in section, needle


def test_the_agent_guides_carry_the_same_triage_rule() -> None:
    for name in ("CLAUDE.md", "AGENTS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        section = text.split("## Branches, issues and the project board", 1)[1].split("\n## ", 1)[0]
        assert f"`{TRIAGE}`" in section and "removes the label in GitHub" in section, name


def test_the_pull_request_template_links_the_triage_rule() -> None:
    text = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    assert f"`{TRIAGE}`" in text and "CONTRIBUTING.md#issue-triage" in text


def test_the_review_standard_lists_triage_as_a_merge_precondition() -> None:
    text = (ROOT / "docs/standards/code-review.md").read_text(encoding="utf-8")
    preconditions = text.split("A PR merges only after:", 1)[1].split("No exceptions", 1)[0]
    assert "linked issue is triaged" in preconditions and "no check enforces it" in preconditions
