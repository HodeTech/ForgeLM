"""Tests for tools/issue_dependencies.py and its two workflows.

The tool derives the ``blocked`` label from GitHub's native "Blocked by" links and fails a pull request that works
on a blocked issue (CONTRIBUTING.md, "Dependencies between issues"). These tests pin the rules, the parsing of
pull-request descriptions written by anyone, the write paths of ``sync`` against a fake API, the fixed-endpoint
discipline of the GitHub calls, and the security posture of both workflow files, since no other guard reads them.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import assign_on_comment  # noqa: E402
import issue_dependencies as tool  # noqa: E402

REPO = "HodeTech/ForgeLM"


def blocker(number: int, state: str = "OPEN", reason: Optional[str] = None, repo: str = REPO) -> tool.Blocker:
    return tool.Blocker(number, repo, state, reason)


def issue(number: int, *blockers: tool.Blocker, labels: Tuple[str, ...] = (), is_open: bool = True) -> tool.IssueState:
    return tool.IssueState(number, is_open, frozenset(labels), tuple(blockers))


# --- the rules ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state, reason, status",
    [
        ("OPEN", None, "waiting"),
        ("OPEN", "REOPENED", "waiting"),
        ("CLOSED", "COMPLETED", "resolved"),
        ("CLOSED", None, "resolved"),  # closed before GitHub recorded reasons: closing meant completed
        ("CLOSED", "NOT_PLANNED", "not-completed"),
        ("CLOSED", "DUPLICATE", "not-completed"),
    ],
)
def test_a_blocker_is_resolved_only_when_closed_as_completed(state: str, reason: Optional[str], status: str) -> None:
    assert blocker(1, state, reason).status == status


def test_an_open_blocker_labels_the_issue_without_a_comment() -> None:
    plan = tool.plan_issue(issue(5, blocker(1)), REPO)
    assert plan == tool.Plan(add_label=True)


def test_an_issue_already_labelled_and_still_waiting_changes_nothing() -> None:
    plan = tool.plan_issue(issue(5, blocker(1), labels=("blocked",)), REPO)
    assert not plan.changes


def test_one_open_blocker_among_closed_ones_keeps_the_issue_blocked() -> None:
    plan = tool.plan_issue(issue(5, blocker(1, "CLOSED", "COMPLETED"), blocker(2)), REPO)
    assert plan.add_label and not plan.remove_label


def test_when_every_blocker_is_completed_the_label_goes_and_one_comment_names_them() -> None:
    done = (blocker(1, "CLOSED", "COMPLETED"), blocker(9, "CLOSED", "COMPLETED", repo="HodeTech/Other"))
    plan = tool.plan_issue(issue(5, *done, labels=("blocked", "wave: 3")), REPO)
    assert plan.remove_label and not plan.add_label
    assert "(#1, HodeTech/Other#9)" in plan.comment and plan.comment.startswith("This issue is no longer blocked")


def test_a_hand_set_label_without_links_is_removed_with_the_explanation() -> None:
    plan = tool.plan_issue(issue(5, labels=("blocked",)), REPO)
    assert plan.remove_label and "set and removed automatically" in plan.comment


def test_an_issue_that_was_never_blocked_is_left_alone() -> None:
    assert not tool.plan_issue(issue(5), REPO).changes
    assert not tool.plan_issue(issue(5, blocker(1, "CLOSED", "COMPLETED")), REPO).changes


def test_a_closed_issue_loses_the_label_silently() -> None:
    plan = tool.plan_issue(issue(5, blocker(1), labels=("blocked",), is_open=False), REPO)
    assert plan == tool.Plan(remove_label=True)


@pytest.mark.parametrize("reason", ["NOT_PLANNED", "DUPLICATE"])
def test_a_blocker_closed_without_being_completed_keeps_the_issue_blocked_and_asks_for_triage(reason: str) -> None:
    gone = blocker(1, "CLOSED", reason)
    plan = tool.plan_issue(issue(5, gone, labels=("blocked",)), REPO)
    assert not plan.remove_label and plan.not_completed == (gone,)
    notice = tool.not_completed_notice(gone)
    assert ("a duplicate" if reason == "DUPLICATE" else "not planned") in notice
    assert tool.CANCELLED_MARKER.format(blocker=1) in notice and tool.MAINTAINER_MENTION in notice


# --- reading pull-request descriptions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, closing, every",
    [
        ("Fixes #12", {12}, {12}),
        ("fixed #12, closes #13 and resolves #14", {12, 13, 14}, {12, 13, 14}),
        ("Resolves: #12", {12}, {12}),
        ("Fixes #12, #13 and #14", {12, 13, 14}, {12, 13, 14}),
        ("Refs #12", set(), {12}),
        ("references #12 & #13", set(), {12, 13}),
        ("Fixes HodeTech/ForgeLM#12", {12}, {12}),
        ("Fixes https://github.com/HodeTech/ForgeLM/issues/12", {12}, {12}),
        ("Fixes other/repo#12 and https://github.com/other/repo/issues/13", set(), set()),
        ("FIXES #12", {12}, {12}),
        ("Fixes#12", {12}, {12}),  # GitHub would not close it; a blocking check errs on the safe side
        ("See #12 for context; prefixes #13 are not keywords", set(), set()),
        ("<!-- Fixes #12 -->\n`Refs #13`\n```\nFixes #14\n```\nRefs #15", set(), {15}),
    ],
)
def test_linked_issues(body: str, closing: set, every: set) -> None:
    assert tool.linked_issues(body, REPO) == (frozenset(closing), frozenset(every))


def test_the_pull_request_template_itself_links_nothing() -> None:
    template = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    assert tool.linked_issues(template, REPO) == (frozenset(), frozenset())


# --- the pull-request rule -------------------------------------------------------------------------------------


def verdict(
    body: str, links: Dict[int, Optional[Tuple[tool.Blocker, ...]]], labels: Tuple[str, ...] = ()
) -> tool.Verdict:
    closing, linked = tool.linked_issues(body, REPO)
    return tool.check_pull_request(frozenset(labels), closing, linked, lambda n: links.get(n, ()), REPO)


def test_a_pull_request_on_a_blocked_issue_fails_and_names_the_blocker() -> None:
    v = verdict("Fixes #5", {5: (blocker(1),)})
    assert not v.ok and v.lines[0] == "#5 is blocked by #1, which is still open."


def test_refs_counts_as_working_on_the_issue() -> None:
    assert not verdict("Refs #5", {5: (blocker(1),)}).ok


def test_closing_the_blocker_in_the_same_pull_request_passes() -> None:
    assert verdict("Fixes #1\nFixes #5", {5: (blocker(1),), 1: ()}).ok


def test_only_referring_to_the_blocker_does_not_count_as_closing_it() -> None:
    assert not verdict("Fixes #5, refs #1", {5: (blocker(1),), 1: ()}).ok


def test_a_blocker_closed_without_being_completed_fails_even_if_named() -> None:
    v = verdict("Fixes #5\nFixes #1", {5: (blocker(1, "CLOSED", "NOT_PLANNED"),)})
    assert not v.ok and "closed without being completed" in v.lines[0]


def test_completed_blockers_pull_requests_and_closed_issues_pass() -> None:
    v = verdict("Fixes #5, #6", {5: (blocker(1, "CLOSED", "COMPLETED"),), 6: None})
    assert v.ok and v.lines == ("No linked issue is blocked (1 issue(s) checked).",)


def test_the_override_label_passes_and_says_so() -> None:
    v = verdict("Fixes #5", {5: (blocker(1),)}, labels=("dependency-override",))
    assert v.ok and "dependency-override" in v.lines[0]


# --- a fake GitHub API -----------------------------------------------------------------------------------------


class FakeGitHub:
    """Plays the endpoints the tool calls: GraphQL for issues and their links, REST for writes."""

    def __init__(self, issues: Dict[int, Dict[str, Any]], closed_labelled: Tuple[int, ...] = ()) -> None:
        self.issues = issues  # number -> {"labels": [...], "blockers": [(n, state, reason), ...], "state": "OPEN"}
        self.closed_labelled = list(closed_labelled)
        self.calls: List[Tuple[str, str, Any]] = []
        self.comments: Dict[int, List[Dict[str, Any]]] = {}
        self.pulls: List[Dict[str, Any]] = []
        self.runs: Dict[str, List[Dict[str, Any]]] = {}
        self.fail_prefix: Optional[str] = None

    def _node(self, number: int) -> Dict[str, Any]:
        i = self.issues[number]
        nodes = [
            {"number": n, "state": s, "stateReason": r, "repository": {"nameWithOwner": REPO}}
            for n, s, r in i["blockers"]
        ]
        return {
            "__typename": i.get("type", "Issue"),
            "number": number,
            "state": i.get("state", "OPEN"),
            "labels": {"nodes": [{"name": n} for n in i["labels"]]},
            "blockedBy": {"totalCount": len(nodes), "nodes": nodes},
        }

    def __call__(self, method: str, endpoint: str, payload: Any = None) -> Any:
        self.calls.append((method, endpoint, payload))
        if self.fail_prefix and endpoint.startswith(self.fail_prefix):
            raise tool.GhError(f"{method} {endpoint} failed")
        if endpoint == "graphql":
            assert payload["query"] in tool.QUERIES.values()
            v = payload["variables"]
            if "number" in v:
                found = v["number"] in self.issues
                return {"data": {"repository": {"issueOrPullRequest": self._node(v["number"]) if found else None}}}
            open_nodes = [self._node(n) for n, i in sorted(self.issues.items()) if i.get("state", "OPEN") == "OPEN"]
            return {"data": {"repository": {"issues": {"nodes": open_nodes, "pageInfo": {"hasNextPage": False}}}}}
        if method == "GET" and endpoint.startswith(f"repos/{REPO}/issues?state=closed&labels=blocked"):
            return [{"number": n} for n in self.closed_labelled]
        if method == "GET" and re.fullmatch(rf"repos/{REPO}/issues/\d+/comments\?.*", endpoint):
            return self.comments.get(int(endpoint.split("/")[4]), [])
        if method == "POST" and endpoint.endswith("/labels"):
            self.issues.setdefault(int(endpoint.split("/")[4]), {"labels": [], "blockers": []})["labels"] += payload[
                "labels"
            ]
            return []
        if method == "DELETE" and "/labels/" in endpoint:
            n = int(endpoint.split("/")[4])
            if n in self.issues:
                self.issues[n]["labels"].remove("blocked")
            return None
        if method == "POST" and endpoint.endswith("/comments"):
            self.comments.setdefault(int(endpoint.split("/")[4]), []).append(
                {"user": {"type": "Bot"}, "body": payload["body"]}
            )
            return {}
        if method == "GET" and endpoint.startswith(f"repos/{REPO}/pulls?state=open"):
            return self.pulls
        if method == "GET" and re.fullmatch(rf"repos/{REPO}/pulls/\d+", endpoint):
            return next(p for p in self.pulls if p["number"] == int(endpoint.rsplit("/", 1)[1]))
        if method == "GET" and "/actions/workflows/pr-dependencies.yml/runs?head_sha=" in endpoint:
            return {"workflow_runs": self.runs.get(endpoint.split("head_sha=")[1].split("&")[0], [])}
        if method == "POST" and endpoint.endswith("/rerun-failed-jobs"):
            return None
        raise AssertionError(f"unexpected call {method} {endpoint}")

    def writes(self) -> List[Tuple[str, str]]:
        return [(m, e) for m, e, _ in self.calls if m in ("POST", "DELETE") and e != "graphql"]


SHA = "a" * 40


def test_sync_labels_blocked_issues_and_unblocks_finished_ones() -> None:
    gh = FakeGitHub(
        {
            5: {"labels": [], "blockers": [(1, "OPEN", None)]},
            6: {"labels": ["blocked"], "blockers": [(2, "CLOSED", "COMPLETED")]},
            7: {"labels": ["blocked"], "blockers": [(1, "OPEN", None)]},
            8: {"labels": [], "blockers": []},
        },
        closed_labelled=(40,),
    )
    assert tool.run_sync(REPO, gh) == 0
    assert (
        gh.issues[5]["labels"] == ["blocked"] and gh.issues[6]["labels"] == [] and gh.issues[7]["labels"] == ["blocked"]
    )
    assert set(gh.comments) == {6} and gh.comments[6][0]["body"].startswith("This issue is no longer blocked")
    assert (("DELETE", f"repos/{REPO}/issues/40/labels/blocked")) in gh.writes()  # a closed issue waits for nothing
    assert 40 not in gh.comments


def test_a_dry_run_reads_but_never_writes(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({5: {"labels": [], "blockers": [(1, "OPEN", None)]}, 6: {"labels": ["blocked"], "blockers": []}})
    assert tool.run_sync(REPO, gh, dry_run=True) == 0
    assert gh.writes() == [] and "would label #5 blocked" in capsys.readouterr().out


def test_a_cancelled_blocker_is_flagged_once_and_a_persons_copy_of_the_marker_does_not_silence_it() -> None:
    gh = FakeGitHub({5: {"labels": ["blocked"], "blockers": [(1, "CLOSED", "NOT_PLANNED")]}})
    gh.comments[5] = [{"user": {"type": "User"}, "body": tool.CANCELLED_MARKER.format(blocker=1)}]
    assert tool.run_sync(REPO, gh) == 0
    assert gh.issues[5]["labels"] == ["blocked", "needs-triage"] and len(gh.comments[5]) == 2
    assert tool.run_sync(REPO, gh) == 0  # the next run finds the bot's notice and stays quiet
    assert len(gh.comments[5]) == 2 and gh.issues[5]["labels"].count("needs-triage") == 1


def test_unblocking_reruns_the_failed_check_of_a_pull_request_that_links_the_issue() -> None:
    gh = FakeGitHub({6: {"labels": ["blocked"], "blockers": [(2, "CLOSED", "COMPLETED")]}})
    gh.pulls = [
        {"number": 30, "body": "Fixes #6", "head": {"sha": SHA}},
        {"number": 31, "body": "Fixes #9", "head": {"sha": SHA}},
    ]
    gh.runs[SHA] = [{"id": 77, "conclusion": "failure"}]
    assert tool.run_sync(REPO, gh) == 0
    assert ("POST", f"repos/{REPO}/actions/runs/77/rerun-failed-jobs") in gh.writes()
    assert sum(1 for m, e in gh.writes() if e.endswith("/rerun-failed-jobs")) == 1


def test_a_failing_rerun_is_a_warning_not_a_failed_sync(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({6: {"labels": ["blocked"], "blockers": []}})
    gh.fail_prefix = f"repos/{REPO}/pulls"
    assert tool.run_sync(REPO, gh) == 0
    assert gh.issues[6]["labels"] == [] and "warning: could not re-run" in capsys.readouterr().err


def test_a_failing_read_fails_the_sync() -> None:
    gh = FakeGitHub({})
    gh.fail_prefix = "graphql"
    with pytest.raises(tool.GhError):
        tool.run_sync(REPO, gh)


def test_more_links_than_one_page_is_an_error_not_a_silent_truncation() -> None:
    node = {"number": 5, "blockedBy": {"totalCount": 51, "nodes": []}}
    with pytest.raises(tool.GhError, match="more than 50 blockers"):
        tool._blockers(node)


def test_check_pr_reads_the_pull_request_again_instead_of_trusting_the_event() -> None:
    gh = FakeGitHub({5: {"labels": [], "blockers": [(1, "OPEN", None)]}, 1: {"labels": [], "blockers": []}})
    gh.pulls = [{"number": 30, "body": "Fixes #5", "labels": []}]
    stale = {"pull_request": {"number": 30, "body": "Docs only", "labels": [{"name": "dependency-override"}]}}
    assert tool.run_check_pr(stale, REPO, gh) == 3
    gh.pulls[0]["labels"] = [{"name": "dependency-override"}]
    assert tool.run_check_pr(stale, REPO, gh) == 0


def test_check_pr_skips_linked_pull_requests_and_closed_issues() -> None:
    gh = FakeGitHub(
        {
            5: {"labels": [], "blockers": [(1, "OPEN", None)], "type": "PullRequest"},
            6: {"labels": [], "blockers": [(1, "OPEN", None)], "state": "CLOSED"},
        }
    )
    gh.pulls = [{"number": 30, "body": "Refs #5, #6 and #404", "labels": []}]
    assert tool.run_check_pr({"pull_request": {"number": 30}}, REPO, gh) == 0


def test_check_pr_prints_errors_as_annotations_with_numbers_only(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({5: {"labels": [], "blockers": [(1, "OPEN", None)]}})
    gh.pulls = [{"number": 30, "body": "Fixes #5 ::set-output name=x::y", "labels": []}]
    assert tool.run_check_pr({"pull_request": {"number": 30}}, REPO, gh) == 3
    out = capsys.readouterr().out
    assert out.startswith("::error::#5 is blocked by #1") and "set-output" not in out


# --- entry point and the GitHub call wrapper -------------------------------------------------------------------


def test_main_rejects_a_missing_repository_a_missing_event_and_a_bad_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gh = FakeGitHub({})
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert tool.main(["sync"], gh) == 2
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert tool.main(["check-pr"], gh) == 2
    broken = tmp_path / "event.json"
    broken.write_text(json.dumps({"pull_request": {"number": "30"}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(broken))
    assert tool.main(["check-pr"], gh) == 2
    assert tool.main(["frobnicate"], gh) == 2
    assert gh.calls == []


def test_main_turns_an_api_failure_into_exit_one(monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGitHub({})
    gh.fail_prefix = "graphql"
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    assert tool.main(["sync"], gh) == 1


@pytest.mark.parametrize(
    "method, endpoint, payload",
    [
        ("GET", "user", None),
        ("PATCH", f"repos/{REPO}/issues/1", {}),
        ("GET", f"repos/{REPO}/issues/1 --jq .", None),
        ("POST", "graphql", {"query": "mutation { deleteIssue }"}),
        ("GET", "graphql", {"query": tool.QUERIES["issue"]}),
    ],
)
def test_gh_api_refuses_anything_outside_the_fixed_endpoints(method: str, endpoint: str, payload: Any) -> None:
    with pytest.raises(tool.GhError, match="refusing"):
        tool.gh_api(method, endpoint, payload)


def test_gh_api_runs_a_list_command_without_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Dict[str, Any] = {}

    class Done:
        returncode, stdout, stderr = 0, "[]", ""

    def fake_run(command: List[str], **kwargs: Any) -> Done:
        seen.update(command=command, **kwargs)
        return Done()

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    assert tool.gh_api("GET", f"repos/{REPO}/issues/1") == []
    assert seen["command"] == ["gh", "api", "--method", "GET", f"repos/{REPO}/issues/1"] and "shell" not in seen


def test_gh_api_failure_text_is_flattened_and_defanged(monkeypatch: pytest.MonkeyPatch) -> None:
    class Failed:
        returncode, stdout, stderr = 1, "", "bad\n::error::injected"

    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(tool.GhError) as info:
        tool.gh_api("GET", f"repos/{REPO}/issues/1")
    assert "\n" not in str(info.value) and "::error::" not in str(info.value)


def test_a_graphql_error_is_a_gherror() -> None:
    with pytest.raises(tool.GhError, match="GraphQL issue failed"):
        tool.graphql(lambda *a: {"errors": [{"message": "nope"}]}, "issue", owner="o", name="n", number=1)


def test_the_maintainer_list_matches_the_assign_tool() -> None:
    assert tool.MAINTAINER_LOGINS == assign_on_comment.MAINTAINER_LOGINS  # that one is checked against MAINTAINERS.md


def test_the_links_in_the_notices_resolve_to_contributing_headings() -> None:
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    headings = {
        re.sub(r"[^\w\- ]", "", h).strip().lower().replace(" ", "-") for h in re.findall(r"^#+ (.+)$", text, re.M)
    }
    for anchor in ("dependencies-between-issues", "working-on-an-issue"):
        assert anchor in headings


# --- the workflow files ----------------------------------------------------------------------------------------


def load(name: str) -> Dict[Any, Any]:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def steps_of(workflow: Dict[Any, Any]) -> List[Dict[str, Any]]:
    (job,) = workflow["jobs"].values()
    return job["steps"]


@pytest.mark.parametrize("name", ["issue-dependencies.yml", "pr-dependencies.yml"])
def test_no_expression_reaches_a_shell_and_the_checkout_is_pinned_and_minimal(name: str) -> None:
    steps = steps_of(load(name))
    assert all("${{" not in str(step.get("run", "")) for step in steps)
    checkout = steps[0]
    assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["sparse-checkout"] == "tools/issue_dependencies.py"


def test_the_sync_workflow_triggers_permissions_and_concurrency() -> None:
    wf = load("issue-dependencies.yml")
    assert wf[True] == {
        "issues": {"types": ["closed", "reopened"]},
        "schedule": [{"cron": "17 * * * *"}],
        "workflow_dispatch": None,
    }  # YAML 1.1 reads the key `on` as a boolean
    assert wf["permissions"] == {}
    job = wf["jobs"]["sync"]
    assert job["permissions"] == {"contents": "read", "issues": "write", "pull-requests": "read", "actions": "write"}
    assert wf["concurrency"] == {"group": "issue-dependencies", "cancel-in-progress": False}
    assert "ref" not in steps_of(wf)[0]["with"]  # the default branch: what `issues` and `schedule` run on
    assert "github.repository == 'HodeTech/ForgeLM'" in job["if"]
    assert steps_of(wf)[1]["run"] == "python3 tools/issue_dependencies.py sync"


def test_the_pull_request_check_never_runs_the_pull_requests_own_code() -> None:
    wf = load("pr-dependencies.yml")
    assert list(wf[True]) == ["pull_request"]  # never pull_request_target
    assert set(wf[True]["pull_request"]) == {"types"}  # no paths/branches filter: a required check must report
    assert wf["permissions"] == {}
    job = wf["jobs"]["issue-dependencies"]
    assert job["name"] == "Issue dependencies"  # the required check's context
    assert job["permissions"] == {"contents": "read", "issues": "read", "pull-requests": "read"}
    assert steps_of(wf)[0]["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert steps_of(wf)[1]["run"] == "python3 tools/issue_dependencies.py check-pr"
    assert {"edited", "labeled", "unlabeled", "synchronize"} <= set(wf[True]["pull_request"]["types"])
