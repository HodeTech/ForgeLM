"""Tests for tools/triage_gate.py and .github/workflows/triage-gate.yml.

The tool keeps ``needs-triage`` on every issue until a maintainer removes it, from events anyone can cause, with a
token that can write to issues. These tests pin the rules (CONTRIBUTING.md, "Issue triage"), the invariant that the
tool never removes the label, the failure paths, that no issue text reaches the log, and the security posture of the
workflow file itself, since no other guard reads it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import assign_on_comment  # noqa: E402
import issue_dependencies  # noqa: E402
import triage_gate as tool  # noqa: E402

REPO = "HodeTech/ForgeLM"
TRIAGE = tool.TRIAGE_LABEL


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool.time, "sleep", lambda seconds: None)


def label_event(kind: str, login: str, label: str = TRIAGE) -> Dict[str, Any]:
    """One entry of the issue events API."""
    return {"event": kind, "label": {"name": label}, "actor": {"login": login}}


class FakeGitHub:
    """A small stand-in for the REST API: one issue (number 7), its events, and a call log."""

    def __init__(self, labels: Optional[List[str]] = None, events: Optional[List[Dict[str, Any]]] = None) -> None:
        self.labels = list(labels or [])
        self.events = list(events or [])
        self.calls: List[tuple] = []
        self.comments: List[str] = []
        self.fail: Dict[tuple, str] = {}  # (method, endpoint prefix) -> reason
        self.fail_comment_times = 0  # fail this many comment posts before one succeeds
        self.drop_label = False  # accept the POST but do not apply it

    def __call__(self, method: str, endpoint: str, payload: Any = None) -> Any:
        self.calls.append((method, endpoint, payload))
        for (m, prefix), reason in self.fail.items():
            if m == method and endpoint.startswith(prefix):
                raise tool.GhError(f"{method} {endpoint} failed: {reason}")
        if method == "GET" and endpoint == f"repos/{REPO}/issues/7":
            return {"labels": [{"name": n} for n in self.labels]}
        if method == "GET" and endpoint.startswith(f"repos/{REPO}/issues/7/events?"):
            page = int(endpoint.rsplit("page=", 1)[1])
            return self.events[(page - 1) * tool._PAGE_SIZE : page * tool._PAGE_SIZE]
        if method == "POST" and endpoint == f"repos/{REPO}/issues/7/labels":
            if not self.drop_label:
                self.labels += [n for n in payload["labels"] if n not in self.labels]
            return [{"name": n} for n in self.labels]
        if method == "POST" and endpoint == f"repos/{REPO}/issues/7/comments":
            if self.fail_comment_times:
                self.fail_comment_times -= 1
                raise tool.GhError("POST comments failed: boom")
            self.comments.append(payload["body"])
            return {}
        raise AssertionError(f"unexpected call {method} {endpoint}")

    def writes(self) -> List[tuple]:
        """Every call that changes the issue (labels or comments)."""
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


def test_an_issue_that_already_has_the_label_is_left_alone() -> None:
    gh = FakeGitHub(labels=["bug", TRIAGE])
    assert run(gh) == 0
    assert gh.writes() == [] and gh.history_reads() == []  # decided on the labels alone, no history read


def test_an_issue_opened_by_a_maintainer_gets_the_label_too() -> None:
    gh = FakeGitHub()
    assert run(gh, login="cemililik") == 0
    assert gh.labels == [TRIAGE]


def test_an_issue_opened_by_a_bot_gets_the_label_too() -> None:
    gh = FakeGitHub()
    assert run(gh, login="coderabbitai-bot") == 0
    assert gh.labels == [TRIAGE]


def test_a_maintainer_who_triaged_before_the_workflow_started_is_not_overruled() -> None:
    history = [label_event("labeled", "cemililik"), label_event("unlabeled", "cemililik")]
    gh = FakeGitHub(events=history)
    assert run(gh) == 0
    assert gh.writes() == [] and gh.labels == []


def test_other_labels_on_an_issue_do_not_count_as_triage() -> None:
    gh = FakeGitHub(labels=["bug", "area: cli", "severity: low"])
    assert run(gh) == 0
    assert TRIAGE in gh.labels


# --- reopened ---------------------------------------------------------------------------------------------------


def test_an_issue_reopened_by_a_contributor_goes_back_to_triage_with_a_notice() -> None:
    history = [label_event("labeled", "newcomer"), label_event("unlabeled", "cemililik")]
    gh = FakeGitHub(events=history)  # triaged earlier by a maintainer, closed, now reopened by its author
    assert run(gh, "reopened") == 0
    assert gh.labels == [TRIAGE]
    assert gh.comments == [tool.MSG_REOPENED]


def test_an_issue_reopened_by_a_maintainer_keeps_the_maintainers_decision() -> None:
    gh = FakeGitHub()
    assert run(gh, "reopened", login="cemililik") == 0
    assert gh.writes() == []


def test_a_reopened_issue_that_still_has_the_label_gets_no_second_notice() -> None:
    gh = FakeGitHub(labels=[TRIAGE])
    assert run(gh, "reopened") == 0
    assert gh.writes() == []


# --- unlabeled --------------------------------------------------------------------------------------------------


def test_a_removal_by_a_contributor_is_undone_with_a_notice() -> None:
    gh = FakeGitHub(events=[label_event("labeled", "newcomer"), label_event("unlabeled", "helper")])
    assert run(gh, "unlabeled", login="helper") == 0
    assert gh.labels == [TRIAGE]
    assert gh.comments == [tool.MSG_RESTORED]


def test_a_removal_by_a_bot_is_undone_too() -> None:
    gh = FakeGitHub(events=[label_event("labeled", "newcomer"), label_event("unlabeled", "some-app")])
    assert run(gh, "unlabeled", login="some-app") == 0
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
    gh = FakeGitHub(labels=[TRIAGE])
    for action in ("opened", "reopened", "unlabeled"):
        run(gh, action, login="cemililik")
    assert all(call[0] in ("GET", "POST") for call in gh.calls)


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
        (
            [label_event("unlabeled", "cemililik"), label_event("labeled", "a")],
            tool.HISTORY_LABELLED,
        ),  # only the latest event counts
        (
            [label_event("unlabeled", "other"), label_event("unlabeled", "cemililik")],
            tool.HISTORY_MAINTAINER_REMOVED,
        ),
        ([label_event("unlabeled", "cemililik", "NEEDS-TRIAGE")], tool.HISTORY_MAINTAINER_REMOVED),
        ([{"event": "unlabeled", "label": {"name": TRIAGE}, "actor": None}], tool.HISTORY_OTHER_REMOVED),
        ([{"event": "unlabeled", "label": {"name": TRIAGE}}], tool.HISTORY_OTHER_REMOVED),
        ([{"event": "closed"}, "not a dict", None, {"event": "labeled", "label": None}], tool.HISTORY_NEVER),
    ],
)
def test_triage_history_follows_the_latest_label_event(history: List[Any], expected: str) -> None:
    assert tool.triage_history(history) == expected


def test_the_history_is_read_across_pages() -> None:
    filler = [label_event("labeled", "x", "bug")] * tool._PAGE_SIZE
    history = filler + [label_event("labeled", "a"), label_event("unlabeled", "cemililik")]
    gh = FakeGitHub(events=history)
    assert run(gh) == 0  # opened; a maintainer removed the label on the second page
    assert gh.writes() == []
    assert len(gh.history_reads()) == 2


def test_a_history_too_long_to_read_fails_loudly_and_changes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub(events=[label_event("labeled", "x", "bug")] * (tool._MAX_PAGES * tool._PAGE_SIZE))
    assert run(gh, "unlabeled", login="helper") == 1
    assert gh.writes() == []
    assert "more than" in capsys.readouterr().err


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
    def odd(method: str, endpoint: str, payload: Any = None) -> Any:
        if endpoint.endswith("/labels") and method == "POST":
            return {"message": "not a list"}
        return FakeGitHub()(method, endpoint, payload)

    assert tool.run(event("opened"), REPO, odd) == 1


@pytest.mark.parametrize("answer", [None, {}, {"labels": None}, {"labels": [{"nope": 1}]}])
def test_an_issue_answer_without_labels_is_an_error(answer: Any) -> None:
    with pytest.raises(tool.GhError):
        tool.current_labels(lambda m, e, p=None: answer, REPO, 7)


def test_an_events_answer_that_is_not_a_list_is_an_error() -> None:
    with pytest.raises(tool.GhError):
        tool.read_history(lambda m, e, p=None: {"message": "x"}, REPO, 7)


def test_a_notice_is_retried_once() -> None:
    gh = FakeGitHub(events=[label_event("unlabeled", "helper")])
    gh.fail_comment_times = 1
    assert run(gh, "unlabeled", login="helper") == 0
    assert gh.comments == [tool.MSG_RESTORED]


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
    ],
)
def test_an_unusable_payload_is_an_event_error(payload: Any) -> None:
    with pytest.raises(tool.EventError):
        tool.run(payload, REPO, FakeGitHub())


@pytest.mark.parametrize(
    "login, ok",
    [("alice", True), ("a-b-1", True), ("A", True), ("", False), ("a b", False), ("a/b", False), ("é", False),
     ("x" * 40, False), ("x" * 39, True), ("a;rm", False), ("a\nb", False)],
)  # fmt: skip
def test_valid_login(login: str, ok: bool) -> None:
    assert tool.valid_login(login) is ok


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
        ("GET", "user"),
        ("GET", "/repos/o/r/issues/1"),
        ("GET", "repos/o/r/issues/1 --jq x"),
        ("GET", "-X"),
    ],
)
def test_gh_api_refuses_anything_outside_the_fixed_endpoints(method: str, endpoint: str) -> None:
    with pytest.raises(tool.GhError):
        tool.gh_api(method, endpoint)


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


def test_main_rejects_an_unreadable_event_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "missing.json"))
    assert tool.main() == 2
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(broken))
    assert tool.main() == 2


def test_main_reads_the_event_and_adds_the_label(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import json

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


@pytest.fixture(scope="module")
def workflow() -> Dict[Any, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_workflow_triggers_only_on_the_issue_events_the_tool_handles(workflow: Dict[Any, Any]) -> None:
    triggers = workflow[True]  # YAML 1.1 reads the key `on` as a boolean
    assert set(triggers) == {"issues"}
    assert set(triggers["issues"]["types"]) == set(tool.HANDLED_ACTIONS)


def test_workflow_token_is_least_privilege(workflow: Dict[Any, Any]) -> None:
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["gate"]
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    assert "timeout-minutes" in job


def test_workflow_has_no_concurrency_group_because_pending_runs_would_be_cancelled(workflow: Dict[Any, Any]) -> None:
    assert "concurrency" not in workflow and "concurrency" not in workflow["jobs"]["gate"]


def test_workflow_skips_the_removal_of_any_other_label_before_a_runner_starts(workflow: Dict[Any, Any]) -> None:
    condition = workflow["jobs"]["gate"]["if"]
    assert "github.event.action != 'unlabeled'" in condition
    assert f"github.event.label.name == '{TRIAGE}'" in condition


def test_workflow_never_puts_an_expression_into_a_shell_line(workflow: Dict[Any, Any]) -> None:
    runs = [s["run"] for s in workflow["jobs"]["gate"]["steps"] if "run" in s]
    assert runs == ["python3 tools/triage_gate.py"]
    assert all("${{" not in run for run in runs)


def test_workflow_uses_one_action_pinned_to_a_full_commit_sha(workflow: Dict[Any, Any]) -> None:
    uses = [s["uses"] for s in workflow["jobs"]["gate"]["steps"] if "uses" in s]
    assert len(uses) == 1 and uses[0].startswith("actions/checkout@")
    sha = uses[0].split("@", 1)[1]
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


def test_workflow_checkout_takes_one_file_and_keeps_no_credentials(workflow: Dict[Any, Any]) -> None:
    checkout = next(s for s in workflow["jobs"]["gate"]["steps"] if "uses" in s)
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["sparse-checkout"] == "tools/triage_gate.py"
    assert "ref" not in checkout["with"]  # the default branch, never an event-supplied ref


def test_workflow_only_secret_is_the_github_token(workflow: Dict[Any, Any]) -> None:
    step = next(s for s in workflow["jobs"]["gate"]["steps"] if "run" in s)
    assert step["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("secrets.") == 1 and "pull_request_target" not in text and "workflow_run" not in text


# --- every way an issue is created carries the label ------------------------------------------------------------


FORMS = sorted(p for p in (ROOT / ".github/ISSUE_TEMPLATE").glob("*.yml") if p.name != "config.yml")


def test_there_are_issue_forms_to_check() -> None:
    assert len(FORMS) >= 3


@pytest.mark.parametrize("form", FORMS, ids=lambda p: p.name)
def test_every_issue_form_adds_the_label_and_tells_the_reporter_about_triage(form: Path) -> None:
    loaded = yaml.safe_load(form.read_text(encoding="utf-8"))
    assert TRIAGE in loaded["labels"]
    assert "CONTRIBUTING.md#issue-triage" in form.read_text(encoding="utf-8")


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
    assert "'needs-triage'" in creating[0].split("issues.create(", 1)[1]


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
        "pull request",
        "will not be reviewed",
        "maintainers.md",
    ):
        assert needle in section, needle


def test_the_agent_guides_carry_the_same_triage_rule() -> None:
    for name in ("CLAUDE.md", "AGENTS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert f"`{TRIAGE}`" in text.split("## Branches, issues and the project board", 1)[1].split("\n## ", 1)[0], name


def test_the_pull_request_template_asks_about_triage() -> None:
    text = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    assert f"`{TRIAGE}`" in text
