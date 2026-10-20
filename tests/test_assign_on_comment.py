"""Tests for tools/assign_on_comment.py and .github/workflows/assign-on-comment.yml.

The tool decides who may claim an issue from text anyone can write, with a token that can write to issues.
These tests pin the rules (CONTRIBUTING.md, "Working on an issue"), the invariant that a later ``/assign``
never changes an existing assignment, the failure paths, that no comment text reaches the log, and the
security posture of the workflow file itself, since no other guard reads it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import assign_on_comment as tool  # noqa: E402

REPO = "HodeTech/ForgeLM"
GFI = ["good first issue"]


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool.time, "sleep", lambda seconds: None)


class FakeGitHub:
    """A small stand-in for the REST API: one issue, the issues assigned to each login, and a call log."""

    def __init__(
        self,
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
        state: str = "open",
        held: Optional[Dict[str, int]] = None,
    ) -> None:
        self.labels = list(GFI if labels is None else labels)
        self.assignees = list(assignees or [])
        self.state = state
        self.held = dict(held or {})  # login -> number of other open issues assigned to them
        self.calls: List[tuple] = []
        self.comments: List[str] = []
        self.fail: Dict[tuple, str] = {}  # (method, endpoint prefix) -> reason
        self.drop_assignment = False  # accept the POST but do not apply it (an assignee GitHub refuses)
        self.after_first_read: Optional[Callable[[], None]] = None
        self.after_assign: Optional[Callable[[], None]] = None  # runs right after our POST landed
        self.human_comments: List[str] = []  # comments by people (the bot's own are in `comments`)
        self.assigned_pages: Optional[List[List[Dict[str, Any]]]] = None  # overrides the count answer, page by page
        self.refuse_delete = False  # accept the DELETE but keep the assignee
        self.open_blockers: Optional[int] = 0  # GitHub's count of open "Blocked by" issues; None drops the summary
        self._reads = 0

    def _issue(self) -> Dict[str, Any]:
        issue: Dict[str, Any] = {
            "state": self.state,
            "labels": [{"name": n} for n in self.labels],
            "assignees": [{"login": a} for a in self.assignees],
        }
        if self.open_blockers is not None:
            issue["issue_dependencies_summary"] = {"blocked_by": self.open_blockers, "total_blocked_by": 3}
        return issue

    def __call__(self, method: str, endpoint: str, payload: Any = None) -> Any:
        self.calls.append((method, endpoint, payload))
        for (m, prefix), reason in self.fail.items():
            if m == method and endpoint.startswith(prefix):
                raise tool.GhError(f"{method} {endpoint} failed: {reason}")
        handler = self._route(method, endpoint)
        if handler is None:
            raise AssertionError(f"unexpected call {method} {endpoint}")
        return handler(endpoint, payload)

    def _route(self, method: str, endpoint: str):
        """The handler that plays the GitHub endpoint being called, or None for one the tool should never hit."""
        if method == "GET":
            if endpoint == f"repos/{REPO}/issues/7":
                return self._get_issue
            if "?assignee=" in endpoint:
                return self._get_assigned_issues
            if "/comments?" in endpoint:
                return self._get_comments
        elif endpoint.endswith("/assignees"):
            return {"POST": self._add_assignees, "DELETE": self._remove_assignees}.get(method)
        elif method == "POST" and endpoint.endswith("/comments"):
            return self._post_comment
        return None

    def _get_issue(self, endpoint: str, payload: Any) -> Dict[str, Any]:
        self._reads += 1
        answer = self._issue()
        if self._reads == 1 and self.after_first_read:
            self.after_first_read()
        return answer

    def _get_assigned_issues(self, endpoint: str, payload: Any) -> List[Dict[str, Any]]:
        login = endpoint.split("assignee=")[1].split("&")[0]
        if self.assigned_pages is not None:
            page = int(endpoint.rsplit("page=", 1)[1])
            return self.assigned_pages[page - 1] if page <= len(self.assigned_pages) else []
        return [{"number": 100 + i} for i in range(self.held.get(login, 0))] + [
            {"number": 999, "pull_request": {}}  # a PR assigned to them must not count
        ]

    def _get_comments(self, endpoint: str, payload: Any) -> List[Dict[str, Any]]:
        return [{"user": {"type": "Bot"}, "body": b} for b in self.comments] + [
            {"user": {"type": "User"}, "body": b} for b in self.human_comments
        ]

    def _add_assignees(self, endpoint: str, payload: Any) -> Dict[str, Any]:
        if not self.drop_assignment:
            self.assignees += [a for a in payload["assignees"] if a not in self.assignees]
        if self.after_assign:
            self.after_assign()
        return self._issue()

    def _remove_assignees(self, endpoint: str, payload: Any) -> Dict[str, Any]:
        if not self.refuse_delete:
            self.assignees = [a for a in self.assignees if a not in payload["assignees"]]
        return self._issue()

    def _post_comment(self, endpoint: str, payload: Any) -> Dict[str, Any]:
        self.comments.append(payload["body"])
        return {}

    def writes(self) -> List[tuple]:
        """Every call that changes an assignment (comments are not assignments)."""
        return [c for c in self.calls if c[0] in ("POST", "DELETE") and c[1].endswith("/assignees")]


def event(
    body: str,
    login: str = "newcomer",
    user_type: str = "User",
    number: int = 7,
    pull_request: bool = False,
) -> Dict[str, Any]:
    issue: Dict[str, Any] = {"number": number}
    if pull_request:
        issue["pull_request"] = {"url": "x"}
    return {
        "issue": issue,
        "comment": {"body": body, "user": {"login": login, "type": user_type}},
    }


def run(gh: FakeGitHub, body: str = "/assign", **kwargs: Any) -> int:
    return tool.run(event(body, **kwargs), REPO, gh)


# --- command parsing ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, expected",
    [
        ("/assign", "/assign"),
        ("/ASSIGN", "/assign"),
        ("  /assign  ", None),  # leading whitespace: the workflow's startsWith gate would not let it through either
        ("/assign I would like to fix this", "/assign"),
        ("\n\n/unassign", None),
        ("/unassign please", "/unassign"),
        ("/assigned", None),
        ("/assigning", None),
        ("assign", None),
        ("I think /assign works", None),
        ("> /assign", None),
        ("thanks!\n/assign", None),
        ("", None),
        ("   \n  ", None),
    ],
)
def test_parse_command_reads_only_the_first_word_at_the_very_start(body: str, expected: Optional[str]) -> None:
    assert tool.parse_command(body) == expected


def test_a_second_command_on_a_later_line_is_not_read() -> None:
    gh = FakeGitHub()
    assert run(gh, "/assign\n/unassign") == 0
    assert gh.assignees == ["newcomer"]


@pytest.mark.parametrize(
    "body, expected",
    [("/assign", False), ("/assign please", False), ("/assign @someone", True), ("/unassign @a b", True)],
)
def test_names_someone_else_looks_for_an_at_name_on_the_command_line(body: str, expected: bool) -> None:
    assert tool.names_someone_else(body) is expected


@pytest.mark.parametrize(
    "login, ok",
    [("alice", True), ("a-b-1", True), ("A", True), ("", False), ("a b", False), ("a/b", False), ("é", False),
     ("x" * 40, False), ("x" * 39, True), ("a;rm", False), ("a\nb", False)],
)  # fmt: skip
def test_valid_login(login: str, ok: bool) -> None:
    assert tool.valid_login(login) is ok


# --- the claim itself -----------------------------------------------------------------------------------------


def test_a_contributor_claims_an_unassigned_good_first_issue() -> None:
    gh = FakeGitHub()
    assert run(gh) == 0
    assert gh.assignees == ["newcomer"]
    assert len(gh.comments) == 1
    reply = gh.comments[0]
    assert "Assigned to @newcomer" in reply and "Fixes #7" in reply and "`/unassign`" in reply
    assert "CONTRIBUTING.md#working-on-an-issue" in reply


def test_help_wanted_alone_is_a_maintainers_decision_not_an_automatic_claim() -> None:
    # grouped sweeps and larger tasks carry `help wanted`; assigning a whole sweep to one person would lock the rest
    gh = FakeGitHub(labels=["help wanted", "bug"])
    assert run(gh) == 0
    assert gh.assignees == [] and gh.writes() == []
    assert "Only `good first issue` issues can be claimed" in gh.comments[0]


# --- the invariant: a later /assign never changes an existing assignment ---------------------------------------


@pytest.mark.parametrize("claimant", ["late", "someone-else", "cemililik"])  # the last one is the maintainer
def test_assign_never_changes_an_issue_that_already_has_an_assignee(claimant: str) -> None:
    gh = FakeGitHub(assignees=["first"])
    assert run(gh, login=claimant) == 0
    assert gh.assignees == ["first"]
    assert gh.writes() == []  # no POST or DELETE on the assignees at all
    assert len(gh.comments) == 1
    assert "already assigned to [first](https://github.com/first)" in gh.comments[0]
    assert "nothing was changed" in gh.comments[0]
    assert "Only a maintainer can reassign" in gh.comments[0]


def test_two_assignees_set_by_a_maintainer_are_left_alone_too() -> None:
    gh = FakeGitHub(assignees=["a", "b"])
    assert run(gh, login="late") == 0
    assert gh.assignees == ["a", "b"] and gh.writes() == []


def test_the_current_assignee_asking_again_changes_nothing() -> None:
    gh = FakeGitHub(assignees=["newcomer"])
    assert run(gh) == 0
    assert gh.assignees == ["newcomer"] and gh.writes() == []
    assert gh.comments == [tool.MSG_ALREADY_YOURS]


def test_a_maintainer_assignment_is_never_reverted_because_the_tool_only_reacts_to_comments() -> None:
    gh = FakeGitHub()
    assert run(gh, login="first") == 0
    gh.assignees = ["maintainer-pick"]  # a maintainer reassigns in the sidebar: no event reaches the tool
    assert gh.assignees == ["maintainer-pick"]
    assert run(gh, login="first") == 0  # the first claimant asks again: still refused
    assert gh.assignees == ["maintainer-pick"]


def test_a_released_issue_can_be_claimed_by_the_next_person() -> None:
    gh = FakeGitHub()
    assert run(gh, login="first") == 0
    assert run(gh, "/unassign", login="first") == 0
    assert gh.assignees == []
    assert run(gh, login="second") == 0
    assert gh.assignees == ["second"]


def test_a_maintainer_who_unassigned_someone_lets_them_claim_again() -> None:
    gh = FakeGitHub(assignees=["first"])
    gh.assignees = []  # removed in the sidebar; to keep it reserved a maintainer assigns it or removes the label
    assert run(gh, login="first") == 0
    assert gh.assignees == ["first"]


# --- /unassign ------------------------------------------------------------------------------------------------


def test_unassign_removes_only_the_person_who_wrote_it() -> None:
    gh = FakeGitHub(assignees=["first", "other"])
    assert run(gh, "/unassign", login="first") == 0
    assert gh.assignees == ["other"]
    assert gh.writes() == [("DELETE", f"repos/{REPO}/issues/7/assignees", {"assignees": ["first"]})]
    assert "Released" in gh.comments[0]


def test_unassign_by_someone_who_is_not_assigned_changes_nothing() -> None:
    gh = FakeGitHub(assignees=["first"])
    assert run(gh, "/unassign", login="stranger") == 0
    assert gh.assignees == ["first"] and gh.writes() == []
    assert gh.comments == [tool.MSG_NOT_YOURS]


def test_unassign_by_a_maintainer_still_only_releases_themselves() -> None:
    gh = FakeGitHub(assignees=["first"])
    assert run(gh, "/unassign", login="cemililik") == 0
    assert gh.assignees == ["first"] and gh.writes() == []


def test_unassign_still_works_on_a_closed_issue() -> None:
    gh = FakeGitHub(assignees=["first"], state="closed")
    assert run(gh, "/unassign", login="first") == 0
    assert gh.assignees == []


# --- naming someone else --------------------------------------------------------------------------------------


@pytest.mark.parametrize("body", ["/assign @someone", "/assign @someone please", "/unassign @someone"])
def test_naming_someone_else_is_refused_without_any_change(body: str) -> None:
    gh = FakeGitHub(assignees=["first"] if "unassign" in body else [])
    before = list(gh.assignees)
    assert run(gh, body, login="cemililik") == 0
    assert gh.assignees == before and gh.writes() == []
    assert gh.comments == [tool.MSG_SELF_ONLY]


# --- eligibility ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("excluded", ["security", "needs-triage", "deferred", "epic", "phase: 16"])
def test_an_excluded_label_beats_an_eligible_one(excluded: str) -> None:
    gh = FakeGitHub(labels=["good first issue", "help wanted", excluded])
    assert run(gh) == 0
    assert gh.assignees == [] and gh.writes() == []
    assert "needs a maintainer's decision" in gh.comments[0]
    assert tool.MAINTAINER_MENTION in gh.comments[0]  # a human decision is needed: this one is meant to ping


def test_an_issue_without_an_eligible_label_is_a_maintainers_decision() -> None:
    gh = FakeGitHub(labels=["bug", "wave: 1"])
    assert run(gh) == 0
    assert gh.assignees == [] and gh.writes() == []
    assert "needs a maintainer's decision" in gh.comments[0]


def test_a_closed_issue_cannot_be_claimed() -> None:
    gh = FakeGitHub(state="closed")
    assert run(gh) == 0
    assert gh.assignees == [] and gh.comments == [tool.MSG_CLOSED]


# --- dependencies ---------------------------------------------------------------------------------------------


def test_a_blocked_good_first_issue_cannot_be_claimed_and_says_why_without_pinging() -> None:
    gh = FakeGitHub(labels=["good first issue", "blocked"])
    assert run(gh) == 0
    assert gh.assignees == [] and gh.writes() == []
    assert gh.comments == [tool.MSG_BLOCKED.format(front_door=tool.FRONT_DOOR_URL)]
    assert tool.MAINTAINER_MENTION not in gh.comments[0]  # nothing to decide: it opens by itself


def test_an_open_blocker_refuses_the_claim_before_the_label_has_caught_up() -> None:
    gh = FakeGitHub(labels=["good first issue"])
    gh.open_blockers = 1  # linked a minute ago; the hourly sync has not labelled it yet
    assert run(gh) == 0
    assert gh.assignees == [] and gh.comments[0].startswith("This issue is blocked")


def test_blockers_that_are_all_closed_do_not_refuse_the_claim() -> None:
    gh = FakeGitHub(labels=["good first issue"])
    gh.open_blockers = 0  # `total_blocked_by` stays 3: closed blockers no longer count
    assert run(gh) == 0
    assert gh.assignees == ["newcomer"]


def test_without_a_dependency_summary_the_label_alone_decides() -> None:
    free, blocked = FakeGitHub(labels=["good first issue"]), FakeGitHub(labels=["good first issue", "blocked"])
    free.open_blockers = blocked.open_blockers = None
    assert run(free) == 0 and free.assignees == ["newcomer"]
    assert run(blocked) == 0 and blocked.assignees == []


def test_a_blocked_issue_can_still_be_released() -> None:
    gh = FakeGitHub(labels=["good first issue", "blocked"], assignees=["first"])
    assert run(gh, "/unassign", login="first") == 0
    assert gh.assignees == []


# --- the per-person limit -------------------------------------------------------------------------------------


def test_the_limit_stops_a_third_open_assignment_and_points_to_the_maintainers() -> None:
    gh = FakeGitHub(held={"newcomer": tool.MAX_OPEN_ASSIGNMENTS})
    assert run(gh) == 0
    assert gh.assignees == [] and gh.writes() == []
    reply = gh.comments[0]
    assert f"`{tool.MAINTAINER_MENTION}`" in reply
    assert tool.MAINTAINER_MENTION not in reply.replace(
        f"`{tool.MAINTAINER_MENTION}`", ""
    )  # an instruction, not a ping
    assert "`/unassign`" in reply


def test_one_below_the_limit_is_still_allowed_and_prs_do_not_count() -> None:
    gh = FakeGitHub(held={"newcomer": tool.MAX_OPEN_ASSIGNMENTS - 1})
    assert run(gh) == 0
    assert gh.assignees == ["newcomer"]


def test_maintainers_are_exempt_from_the_limit_and_skip_the_count() -> None:
    gh = FakeGitHub(held={"cemililik": 10})
    assert run(gh, login="CemilIlik") == 0  # GitHub logins are case-insensitive
    assert gh.assignees == ["CemilIlik"]
    assert not any("?assignee=" in c[1] for c in gh.calls)


def test_a_read_only_organisation_member_is_not_a_maintainer() -> None:
    gh = FakeGitHub(held={"hodesis": tool.MAX_OPEN_ASSIGNMENTS})  # a MEMBER by GitHub's association, read access only
    assert run(gh, login="hodesis") == 0
    assert gh.assignees == [] and "limit at a time" in gh.comments[0]


def test_the_maintainer_list_matches_maintainers_md() -> None:
    text = (ROOT / "MAINTAINERS.md").read_text(encoding="utf-8")
    table = text.split("## Current maintainers", 1)[1].split("\n## ", 1)[0]
    listed = {
        row.split("[@", 1)[1].split("]", 1)[0].lower()
        for row in table.splitlines()
        if row.startswith("|") and "[@" in row
    }
    assert listed == set(tool.MAINTAINER_LOGINS)


@pytest.mark.parametrize(
    "state",
    [
        {"assignees": ["first"]},  # already taken
        {"labels": ["bug"]},  # not open to automatic assignment
        {"labels": ["good first issue", "epic"]},  # excluded
        {"state": "closed"},
    ],
    ids=["taken", "ineligible", "excluded", "closed"],
)
def test_the_count_is_only_asked_for_when_it_can_change_the_answer(state: Dict[str, Any]) -> None:
    gh = FakeGitHub(**state)
    gh.fail[("GET", f"repos/{REPO}/issues?assignee=")] = "the count would fail"
    assert run(gh) == 0  # the proper refusal, not "automatic assignment did not complete"
    assert not any("?assignee=" in c[1] for c in gh.calls)
    assert "did not complete" not in gh.comments[0]


def test_a_failing_count_for_a_claim_that_needs_it_is_a_reported_failure() -> None:
    gh = FakeGitHub()
    gh.fail[("GET", f"repos/{REPO}/issues?assignee=")] = "boom"
    assert run(gh) == 1
    assert gh.comments == [tool.MSG_FAILED.format(mention=tool.MAINTAINER_MENTION)] and gh.assignees == []


def test_the_count_asks_for_open_issues_of_that_login_only() -> None:
    gh = FakeGitHub()
    run(gh, login="newcomer")
    counts = [c for c in gh.calls if "?assignee=" in c[1]]
    # the first count decides; the second, after the write, checks that a simultaneous claim did not break the limit
    assert counts[0] == ("GET", f"repos/{REPO}/issues?assignee=newcomer&state=open&per_page=100&page=1", None)
    assert all("assignee=newcomer&" in c[1] for c in counts)


# --- races and the second read --------------------------------------------------------------------------------


def test_a_maintainer_who_assigns_between_the_two_reads_wins() -> None:
    gh = FakeGitHub()
    gh.after_first_read = lambda: gh.assignees.append("maintainer-pick")
    assert run(gh) == 0
    assert gh.assignees == ["maintainer-pick"] and gh.writes() == []
    assert "already assigned" in gh.comments[0]


def test_two_assignees_after_a_simultaneous_claim_are_reported_to_the_maintainer() -> None:
    gh = FakeGitHub()
    original = gh.__call__

    def other_claim_lands_during_the_post(method: str, endpoint: str, payload: Any = None) -> Any:
        if method == "POST" and endpoint.endswith("/assignees"):
            gh.assignees.append("rival")  # the rival's POST landed first, after our second read
        return original(method, endpoint, payload)

    assert tool.run(event("/assign"), REPO, other_claim_lands_during_the_post) == 0
    assert sorted(gh.assignees) == ["newcomer", "rival"]
    assert "More than one person is assigned" in gh.comments[0]
    assert "@rival" in gh.comments[0] and tool.MAINTAINER_MENTION in gh.comments[0]


# --- who is ignored -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [{"pull_request": True}, {"user_type": "Bot"}],
    ids=["pull-request-comment", "bot"],
)
def test_pull_request_comments_and_bots_are_ignored(kwargs: Dict[str, Any]) -> None:
    gh = FakeGitHub()
    assert run(gh, **kwargs) == 0
    assert gh.calls == []


def test_a_comment_that_is_not_a_command_makes_no_api_call() -> None:
    gh = FakeGitHub()
    assert run(gh, "I'd like to work on this") == 0
    assert gh.calls == []


# --- failure paths --------------------------------------------------------------------------------------------


def test_an_api_failure_tells_the_contributor_and_fails_the_job(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub()
    gh.fail[("GET", f"repos/{REPO}/issues/7")] = "boom"
    assert run(gh) == 1
    assert gh.comments == [tool.MSG_FAILED.format(mention=tool.MAINTAINER_MENTION)]
    assert "issue #7" in capsys.readouterr().err


def test_an_assignment_github_does_not_confirm_is_a_failure_not_a_success() -> None:
    gh = FakeGitHub()
    gh.drop_assignment = True
    assert run(gh) == 1
    assert gh.comments == [tool.MSG_FAILED.format(mention=tool.MAINTAINER_MENTION)]
    assert not any("Assigned to" in c for c in gh.comments)


def test_if_the_fallback_comment_fails_too_the_job_still_fails(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub()
    gh.fail[("GET", f"repos/{REPO}/issues/7")] = "boom"
    gh.fail[("POST", f"repos/{REPO}/issues/7/comments")] = "boom"
    assert run(gh) == 1
    assert "could not tell the contributor" in capsys.readouterr().err


def test_a_reply_that_cannot_be_posted_after_a_successful_change_propagates() -> None:
    gh = FakeGitHub()
    gh.fail[("POST", f"repos/{REPO}/issues/7/comments")] = "boom"
    with pytest.raises(tool.GhError):
        run(gh)
    assert gh.assignees == ["newcomer"]  # the change stands; main() turns the error into exit 1


@pytest.mark.parametrize(
    "broken",
    [
        {"issue": {"number": 7}},
        {"comment": {"body": "/assign"}},
        {},
        event("/assign", login="a b"),
        event("/assign", number=0),
        event("/assign", number=True),  # type: ignore[arg-type]
    ],
)
def test_an_unusable_event_is_exit_two_material_and_makes_no_api_call(broken: Dict[str, Any]) -> None:
    gh = FakeGitHub()
    with pytest.raises(tool.EventError):
        tool.run(broken, REPO, gh)
    assert gh.calls == []


def test_main_returns_two_for_a_missing_environment_and_an_unreadable_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert tool.main() == 2
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "missing.json"))
    assert tool.main() == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(bad))
    assert tool.main() == 2


# --- nothing from the comment reaches the log ------------------------------------------------------------------


def test_the_comment_text_never_reaches_the_log(capsys: pytest.CaptureFixture[str]) -> None:
    hostile = "/assign ::error::pwned ::add-mask::secret `$(touch /tmp/x)` ${{ secrets.GITHUB_TOKEN }}"
    gh = FakeGitHub()
    assert run(gh, hostile) == 0
    out = capsys.readouterr()
    for text in (out.out, out.err):
        assert "::error::" not in text and "pwned" not in text and "secret" not in text and "$(" not in text
    assert out.out.strip() == "handled /assign on issue #7"
    assert not any("pwned" in c for c in gh.comments)


def test_gh_error_text_is_flattened_and_defanged_before_it_is_printed() -> None:
    assert tool._clean("::error::boom\nsecond line  with   gaps") == ": :error: :boom second line with gaps"
    assert len(tool._clean("x" * 1000)) == 200


# --- the gh wrapper -------------------------------------------------------------------------------------------


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_gh_api_runs_a_list_command_without_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Dict[str, Any] = {}

    def fake_run(command: Any, **kwargs: Any) -> _Completed:
        seen["command"], seen["kwargs"] = command, kwargs
        return _Completed(stdout='{"ok": true}')

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    assert tool.gh_api("POST", "repos/o/r/issues/1/assignees", {"assignees": ["a"]}) == {"ok": True}
    assert seen["command"] == ["gh", "api", "--method", "POST", "repos/o/r/issues/1/assignees", "--input", "-"]
    assert seen["kwargs"]["input"] == '{"assignees": ["a"]}'
    assert not seen["kwargs"].get("shell") and seen["kwargs"]["timeout"] == tool._GH_TIMEOUT_SECONDS


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


# --- the workflow file ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> Dict[Any, Any]:
    loaded = yaml.safe_load((ROOT / ".github/workflows/assign-on-comment.yml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_workflow_triggers_only_on_a_created_comment(workflow: Dict[Any, Any]) -> None:
    triggers = workflow[True]  # YAML 1.1 reads the key `on` as a boolean
    assert triggers == {"issue_comment": {"types": ["created"]}}


def test_workflow_token_is_least_privilege(workflow: Dict[Any, Any]) -> None:
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["assign"]
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    assert "timeout-minutes" in job


def test_workflow_has_no_concurrency_group_because_pending_runs_would_be_cancelled(workflow: Dict[Any, Any]) -> None:
    assert "concurrency" not in workflow and "concurrency" not in workflow["jobs"]["assign"]


def test_workflow_filters_pull_requests_bots_and_non_commands_before_a_runner_starts(
    workflow: Dict[Any, Any],
) -> None:
    condition = workflow["jobs"]["assign"]["if"]
    for needle in ("github.event.issue.pull_request", "'Bot'", "'/assign'", "'/unassign'"):
        assert needle in condition


def test_workflow_never_puts_an_expression_into_a_shell_line(workflow: Dict[Any, Any]) -> None:
    for step in workflow["jobs"]["assign"]["steps"]:
        assert "${{" not in step.get("run", "")
    runs = [s["run"] for s in workflow["jobs"]["assign"]["steps"] if "run" in s]
    assert runs == ["python3 tools/assign_on_comment.py"]


def test_workflow_uses_one_action_pinned_to_a_full_commit_sha(workflow: Dict[Any, Any]) -> None:
    uses = [s["uses"] for s in workflow["jobs"]["assign"]["steps"] if "uses" in s]
    assert len(uses) == 1 and uses[0].startswith("actions/checkout@")
    sha = uses[0].split("@", 1)[1]
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


def test_workflow_checkout_takes_one_file_and_keeps_no_credentials(workflow: Dict[Any, Any]) -> None:
    checkout = next(s for s in workflow["jobs"]["assign"]["steps"] if "uses" in s)
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["sparse-checkout"] == "tools/assign_on_comment.py"
    assert "ref" not in checkout["with"]  # the default branch, never an event-supplied ref


def test_workflow_only_secret_is_the_github_token(workflow: Dict[Any, Any]) -> None:
    step = next(s for s in workflow["jobs"]["assign"]["steps"] if "run" in s)
    assert step["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    text = (ROOT / ".github/workflows/assign-on-comment.yml").read_text(encoding="utf-8")
    assert text.count("secrets.") == 1 and "pull_request_target" not in text and "workflow_run" not in text


# --- the documentation promises what the tool does -------------------------------------------------------------


def test_contributing_documents_the_commands_the_limit_and_who_can_reassign() -> None:
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    section = text.split("### Working on an issue", 1)[1].split("**Grouped issues.**", 1)[0]
    assert "`/assign`" in section and "`/unassign`" in section
    assert f"at most {tool.MAX_OPEN_ASSIGNMENTS} open" in section
    assert "never changes an assignment" in section and "reassign" in section
    for label in (*tool.EXCLUDED_LABELS, "help wanted"):
        assert f"`{label}`" in section  # every label the tool keeps for a maintainer is named for the contributor


# --- review round: repeats, pages, the second decision, settling after the write --------------------------------


def test_the_maintainer_is_mentioned_once_per_issue_not_on_every_repeat() -> None:
    gh = FakeGitHub(labels=["bug"])
    assert run(gh) == 0
    assert tool.MAINTAINER_MENTION in gh.comments[0]
    assert run(gh, login="another") == 0
    assert gh.comments[1] == tool.MSG_NOT_OPEN_AGAIN
    assert tool.MAINTAINER_MENTION not in gh.comments[1]


def test_a_person_cannot_silence_the_maintainer_notice_by_pasting_the_marker() -> None:
    gh = FakeGitHub(labels=["bug"])
    gh.human_comments = [tool.NOT_OPEN_MARKER]  # a User, not a Bot: it must not count
    assert run(gh) == 0
    assert tool.MAINTAINER_MENTION in gh.comments[0]


def test_a_failing_notice_check_still_replies_and_mentions(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub(labels=["bug"])
    gh.fail[("GET", f"repos/{REPO}/issues/7/comments")] = "boom"
    assert run(gh) == 0
    assert tool.MAINTAINER_MENTION in gh.comments[0]
    assert "could not check for an earlier notice" in capsys.readouterr().err


def test_the_count_follows_pages_so_pull_requests_cannot_hide_issues() -> None:
    first_page = [{"number": n, "pull_request": {}} for n in range(100)]  # a full page with no issue on it
    gh = FakeGitHub()
    gh.assigned_pages = [first_page, [{"number": 500}, {"number": 501}]]
    assert tool.count_open_assignments(gh, REPO, "newcomer") == 2
    assert run(gh) == 0
    assert gh.assignees == [] and "limit at a time" in gh.comments[0]


def test_the_count_stops_as_soon_as_it_knows() -> None:
    gh = FakeGitHub()
    gh.assigned_pages = [[{"number": n} for n in range(100)], [{"number": 1000}]]
    assert tool.count_open_assignments(gh, REPO, "newcomer", stop_at=2) == 100
    assert len([c for c in gh.calls if "?assignee=" in c[1]]) == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda gh: setattr(gh, "state", "closed"),
        lambda gh: gh.labels.append("security"),
        lambda gh: gh.labels.remove("good first issue"),
    ],
    ids=["closed", "became-security", "lost-the-label"],
)
def test_an_issue_that_stops_qualifying_between_the_two_reads_is_not_assigned(
    change: Callable[[FakeGitHub], None],
) -> None:
    gh = FakeGitHub()
    gh.after_first_read = lambda: change(gh)
    assert run(gh) == 0
    assert gh.assignees == [] and gh.writes() == []
    assert "did not complete" not in gh.comments[0]


def test_a_claim_steps_back_when_a_maintainer_was_assigned_at_the_same_moment() -> None:
    gh = FakeGitHub()
    gh.after_assign = lambda: gh.assignees.insert(0, "cemililik")
    assert run(gh) == 0
    assert gh.assignees == ["cemililik"]
    assert gh.comments == [tool.MSG_LOST_RACE.format(front_door=tool.FRONT_DOOR_URL)]
    assert gh.writes()[-1] == ("DELETE", f"repos/{REPO}/issues/7/assignees", {"assignees": ["newcomer"]})


def test_a_maintainer_wins_a_simultaneous_claim_even_when_the_contributors_login_sorts_earlier() -> None:
    gh = FakeGitHub()
    gh.after_assign = lambda: gh.assignees.append(
        "cemililik"
    )  # "aaron" < "cemililik": only the maintainer rule releases
    assert run(gh, login="aaron") == 0
    assert gh.assignees == ["cemililik"]
    assert gh.comments == [tool.MSG_LOST_RACE.format(front_door=tool.FRONT_DOOR_URL)]


def test_between_two_contributors_the_earlier_login_stays_and_the_other_steps_back() -> None:
    later = FakeGitHub()
    later.after_assign = lambda: later.assignees.append("amy")  # the rival "amy" landed too; we are "zed"
    assert run(later, login="zed") == 0
    assert later.assignees == ["amy"] and later.comments == [tool.MSG_LOST_RACE.format(front_door=tool.FRONT_DOOR_URL)]

    earlier = FakeGitHub()
    earlier.after_assign = lambda: earlier.assignees.append("zed")  # we are "amy": we stay, the maintainer is told
    assert run(earlier, login="amy") == 0
    assert sorted(earlier.assignees) == ["amy", "zed"] and "More than one person" in earlier.comments[0]


def test_two_racing_runs_that_both_wrote_end_with_exactly_one_assignee() -> None:
    gh = FakeGitHub(assignees=["amy", "zed"])  # both POSTs have landed; now each run settles, in either order
    for order in (("zed", "amy"), ("amy", "zed")):
        gh.assignees = ["amy", "zed"]
        for login in order:
            ctx = tool.Context(REPO, 7, login, False, "/assign", False)
            tool._settle_after_write(ctx, gh, tuple(gh.assignees), "assigned")
        assert gh.assignees == ["amy"]


def test_a_maintainers_own_claim_never_steps_back() -> None:
    gh = FakeGitHub()
    gh.after_assign = lambda: gh.assignees.insert(0, "aaa-contributor")
    assert run(gh, login="cemililik") == 0
    assert "cemililik" in gh.assignees and "More than one person" in gh.comments[0]
    assert not [w for w in gh.writes() if w[0] == "DELETE"]


def test_a_claim_that_pushed_its_author_over_the_limit_steps_back() -> None:
    gh = FakeGitHub(held={"newcomer": tool.MAX_OPEN_ASSIGNMENTS - 1})
    gh.after_assign = lambda: gh.held.__setitem__("newcomer", tool.MAX_OPEN_ASSIGNMENTS + 1)  # other claims landed too
    assert run(gh) == 0
    assert gh.assignees == []
    assert gh.comments == [tool.MSG_LIMIT_RACE.format(limit=tool.MAX_OPEN_ASSIGNMENTS)]


def test_stepping_back_that_github_does_not_confirm_is_a_reported_failure() -> None:
    gh = FakeGitHub()
    gh.after_assign = lambda: gh.assignees.insert(0, "cemililik")
    gh.refuse_delete = True
    assert run(gh) == 1
    assert gh.comments == [tool.MSG_FAILED.format(mention=tool.MAINTAINER_MENTION)]


def test_a_reply_is_retried_once_before_giving_up() -> None:
    gh = FakeGitHub()
    attempts = {"posts": 0}

    def flaky(method: str, endpoint: str, payload: Any = None) -> Any:
        if method == "POST" and endpoint.endswith("/comments"):
            attempts["posts"] += 1
            if attempts["posts"] == 1:
                raise tool.GhError("transient")
        return gh(method, endpoint, payload)

    assert tool.run(event("/assign"), REPO, flaky) == 0
    assert attempts["posts"] == 2 and len(gh.comments) == 1


def test_a_failed_release_promises_a_release_not_an_assignment() -> None:
    gh = FakeGitHub(assignees=["newcomer"])
    gh.refuse_delete = True
    assert run(gh, "/unassign") == 1
    assert gh.comments == [tool.MSG_RELEASE_FAILED.format(mention=tool.MAINTAINER_MENTION)]
    assert "assign this issue" not in gh.comments[0]


@pytest.mark.parametrize(
    "method, endpoint",
    [
        ("PUT", "repos/o/r/issues/1"),
        ("GET", "-X"),
        ("GET", "user"),
        ("GET", "/repos/o/r"),
        ("GET", "repos/o/r/issues/1 --hostname evil"),
        ("POST", "repos/o/r/issues/1/comments\n--input"),
    ],
)
def test_gh_api_refuses_anything_but_the_fixed_repos_endpoints(
    monkeypatch: pytest.MonkeyPatch, method: str, endpoint: str
) -> None:
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: pytest.fail("the command must not run"))
    with pytest.raises(tool.GhError):
        tool.gh_api(method, endpoint)


@pytest.mark.parametrize(
    "body",
    ["/assign", "/ASSIGN", "/Unassign", "/assign please", " /assign", "\n/assign", "\t/unassign", "x /assign", ""],
)
def test_the_workflow_gate_lets_through_everything_the_script_accepts(body: str) -> None:
    gate = body.lower().startswith(("/assign", "/unassign"))  # startsWith ignores case and trims nothing
    if tool.parse_command(body) is not None:
        assert gate
