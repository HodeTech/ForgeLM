"""Tests for tools/issue_dependencies.py and its two workflows.

The tool derives the ``blocked`` label from GitHub's native "Blocked by" links and fails a pull request that works
on a blocked issue (CONTRIBUTING.md, "Dependencies between issues"). These tests pin the rules, the parsing of
pull-request descriptions written by anyone (including its linear running time), the write paths of ``sync`` and the
pull-request check reconciliation against a fake API, the fixed-endpoint discipline of the GitHub calls, and the
security posture of both workflow files, since no other guard reads them.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import assign_on_comment  # noqa: E402
import issue_dependencies as tool  # noqa: E402

REPO = "HodeTech/ForgeLM"
SHA = "a" * 40


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
    assert tool.plan_issue(issue(5, blocker(1)), REPO) == tool.Plan(add_label=True)


def test_an_issue_already_labelled_and_still_waiting_changes_nothing() -> None:
    assert not tool.plan_issue(issue(5, blocker(1), labels=("blocked",)), REPO).changes


def test_one_open_blocker_among_closed_ones_keeps_the_issue_blocked() -> None:
    plan = tool.plan_issue(issue(5, blocker(1, "CLOSED", "COMPLETED"), blocker(2)), REPO)
    assert plan.add_label and not plan.remove_label


def test_when_every_blocker_is_completed_the_label_goes_and_one_comment_names_them() -> None:
    done = (blocker(1, "CLOSED", "COMPLETED"), blocker(9, "CLOSED", "COMPLETED", repo="HodeTech/Other"))
    plan = tool.plan_issue(issue(5, *done, labels=("blocked", "wave: 3")), REPO)
    assert plan.remove_label and not plan.add_label
    assert "(#1, HodeTech/Other#9)" in plan.comment and plan.comment.startswith("This issue is no longer blocked")
    assert plan.marker == "<!-- issue-dependencies:unblocked:hodetech/forgelm#1,hodetech/other#9 -->"
    assert plan.comment.endswith(plan.marker)


def test_a_hand_set_label_without_links_is_removed_with_the_explanation() -> None:
    plan = tool.plan_issue(issue(5, labels=("blocked",)), REPO)
    assert plan.remove_label and "set and removed automatically" in plan.comment and plan.marker.endswith(":none -->")


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
    notice = tool.not_completed_notice(gone, REPO)
    assert ("a duplicate" if reason == "DUPLICATE" else "not planned") in notice and notice.startswith("#1, which")
    assert notice.endswith("<!-- issue-dependencies:not-completed:hodetech/forgelm#1 -->")
    assert tool.MAINTAINER_MENTION in notice


def test_blockers_in_two_repositories_with_one_number_never_share_a_notice() -> None:
    here, there = blocker(7, "CLOSED", "NOT_PLANNED"), blocker(7, "CLOSED", "NOT_PLANNED", repo="HodeTech/Other")
    a, b = tool.not_completed_notice(here, REPO), tool.not_completed_notice(there, REPO)
    assert a.startswith("#7, which") and b.startswith("HodeTech/Other#7, which")
    assert a.splitlines()[-1] != b.splitlines()[-1]


# --- reading pull-request descriptions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, closing, every",
    [
        ("Fixes #12", {12}, {12}),
        ("fixed #12, closes #13 and resolves #14", {12, 13, 14}, {12, 13, 14}),
        ("FIXES #12", {12}, {12}),
        ("Fixes HodeTech/ForgeLM#12", {12}, {12}),
        # GitHub closes one issue per keyword: in a list only the first is closed, the rest are linked
        ("Fixes #12, #13 and #14", {12}, {12, 13, 14}),
        # forms GitHub does not close: linked (the check may fail on them) but never an exemption
        ("Fixes#12", set(), {12}),
        ("Resolves: #12", set(), {12}),
        ("Fixes https://github.com/HodeTech/ForgeLM/issues/12", set(), {12}),
        ("Refs #12", set(), {12}),
        ("references #12 & #13", set(), {12, 13}),
        ("Fixes other/repo#12 and https://github.com/other/repo/issues/13", set(), set()),
        ("See #12 for context; prefixes #13 are not keywords; refsfoo/bar#14", set(), set()),
        ("Fixes\n#12", set(), set()),  # a keyword never reaches across a line
        ("<!-- Fixes #12 -->\n`Refs #13`\n```\nFixes #14\n```\nRefs #15", set(), {15}),
    ],
)
def test_linked_issues(body: str, closing: set, every: set) -> None:
    assert tool.linked_issues(body, REPO) == (frozenset(closing), frozenset(every))


def test_the_pull_request_template_itself_links_nothing() -> None:
    template = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    assert tool.linked_issues(template, REPO) == (frozenset(), frozenset())


@pytest.mark.parametrize(
    "body, every",
    [
        ("Refs #1\n```\nFixes #2\nRefs #3", {1}),  # an unclosed fence runs to the end
        ("~~~~\nFixes #2\n~~~\nstill code\n~~~~\nRefs #3", {3}),  # a closing fence is at least as long
        ("```\nFixes #2\n~~~\nRefs #3", set()),  # and of the same character
        ("```py `x`\nRefs #3\n```", {3}),  # a backtick fence's info string has no backtick: not a fence
        ("    ```\nFixes #2\nRefs #3", {2, 3}),  # four spaces: not a fence opener
        ("Refs #1 <!-- Fixes #2 --> Refs #3", {1, 3}),
        ("Refs #1 <!-- Fixes #2\nRefs #3", {1}),  # an unclosed comment runs to the end
    ],
)
def test_fences_and_comments_follow_commonmark(body: str, every: set) -> None:
    assert tool.linked_issues(body, REPO)[1] == frozenset(every)


@pytest.mark.parametrize(
    "body",
    [
        "<!--" * 15_000,  # unclosed comments: the earlier `<!--.*?-->` took about 2 s here
        "```x\n" * 15_000,  # openers that never close: the earlier fence regex took about 5 s here
        "```\n" * 15_000,
        "~~~~\n```\n" * 7_500,
        "Fixes " * 10_000 + "#1",
        "Fixes" + " " * 60_000 + "x",
        "Fixes #1, " * 6_000,
        "`" * 60_000,
        "<!-- -->```\n" * 5_000,
    ],
)
def test_a_hostile_description_is_read_in_linear_time(body: str) -> None:
    # docs/standards/regex.md: a generous absolute floor; a quadratic reader blows far past it at this size.
    start = time.perf_counter()
    tool.linked_issues(body, REPO)
    assert time.perf_counter() - start < 1.0


# --- the pull-request rule -------------------------------------------------------------------------------------


def verdict(
    body: str, links: Dict[int, Optional[Tuple[tool.Blocker, ...]]], override: Optional[bool] = None
) -> tool.Verdict:
    closing, linked = tool.linked_issues(body, REPO)
    return tool.check_pull_request(override, closing, linked, lambda n: links.get(n, ()), REPO)


def test_a_pull_request_on_a_blocked_issue_fails_and_names_the_blocker() -> None:
    v = verdict("Fixes #5", {5: (blocker(1),)})
    assert not v.ok and v.lines[0] == "#5 is blocked by #1, which is still open."


def test_refs_counts_as_working_on_the_issue() -> None:
    assert not verdict("Refs #5", {5: (blocker(1),)}).ok


def test_closing_the_blocker_in_the_same_pull_request_passes() -> None:
    assert verdict("Fixes #1\nFixes #5", {5: (blocker(1),), 1: ()}).ok


@pytest.mark.parametrize("body", ["Fixes #5, refs #1", "Fixes #5\nFixes#1", "Fixes #5\nFixes: #1", "Fixes #5, #1"])
def test_a_link_github_would_not_close_never_exempts_the_blocker(body: str) -> None:
    assert not verdict(body, {5: (blocker(1),), 1: ()}).ok


def test_a_blocker_closed_without_being_completed_fails_even_if_named() -> None:
    v = verdict("Fixes #5\nFixes #1", {5: (blocker(1, "CLOSED", "NOT_PLANNED"),)})
    assert not v.ok and "closed without being completed" in v.lines[0]


def test_completed_blockers_pull_requests_and_closed_issues_pass() -> None:
    v = verdict("Fixes #5, #6", {5: (blocker(1, "CLOSED", "COMPLETED"),), 6: None})
    assert v.ok and v.lines == ("No linked issue is blocked (1 issue(s) checked).",)


def test_a_maintainers_override_passes_and_says_so() -> None:
    v = verdict("Fixes #5", {5: (blocker(1),)}, override=True)
    assert v.ok and "set by a maintainer" in v.lines[0]


def test_an_override_set_by_anyone_else_is_ignored_and_says_so() -> None:
    v = verdict("Fixes #5", {5: (blocker(1),)}, override=False)
    assert not v.ok and any("is ignored" in line for line in v.lines)


def label_event(kind: str, login: str, label: str = "dependency-override") -> Dict[str, Any]:
    return {"event": kind, "actor": {"login": login}, "label": {"name": label}}


@pytest.mark.parametrize(
    "events, authorized",
    [
        ([label_event("labeled", "cemililik")], True),
        ([label_event("labeled", "CemilIlik")], True),
        ([label_event("labeled", "contributor")], False),
        ([label_event("labeled", "cemililik"), label_event("unlabeled", "x"), label_event("labeled", "x")], False),
        ([label_event("labeled", "cemililik"), label_event("unlabeled", "cemililik")], False),
        ([label_event("labeled", "contributor", label="bug"), label_event("labeled", "cemililik")], True),
        ([], False),
    ],
)
def test_the_override_counts_only_when_a_maintainer_set_it_last(events: List[Dict[str, Any]], authorized: bool) -> None:
    gh = FakeGitHub({})
    gh.events[30] = events
    assert tool.override_authorized(gh, REPO, 30) is authorized


# --- a fake GitHub API -----------------------------------------------------------------------------------------


class FakeGitHub:
    """Plays the endpoints the tool calls: GraphQL for issues and their links, REST for the rest."""

    def __init__(self, issues: Dict[int, Dict[str, Any]], closed_labelled: Tuple[int, ...] = ()) -> None:
        # number -> {"labels": [...], "blockers": [(n, state, reason[, repo]), ...], "state": "OPEN", "type": "Issue"}
        self.issues = issues
        self.fresh: Dict[int, Dict[str, Any]] = {}  # what a re-read of one issue sees, when it differs
        self.closed_labelled = list(closed_labelled)
        self.calls: List[Tuple[str, str, Any]] = []
        self.comments: Dict[int, List[Dict[str, Any]]] = {}
        self.pulls: List[Dict[str, Any]] = []
        self.check_runs: Dict[str, List[Dict[str, Any]]] = {}
        self.events: Dict[int, List[Dict[str, Any]]] = {}
        self.fail: List[Tuple[str, str]] = []  # (method, endpoint prefix)

    @staticmethod
    def _node(number: int, data: Dict[str, Any]) -> Dict[str, Any]:
        nodes = [
            {"number": b[0], "state": b[1], "stateReason": b[2], "repository": {"nameWithOwner": (b[3:] or (REPO,))[0]}}
            for b in data["blockers"]
        ]
        return {
            "__typename": data.get("type", "Issue"),
            "number": number,
            "state": data.get("state", "OPEN"),
            "labels": {"totalCount": len(data["labels"]), "nodes": [{"name": n} for n in data["labels"]]},
            "blockedBy": {"totalCount": len(nodes), "nodes": nodes},
        }

    def pull(self, number: int, body: str, labels: Tuple[str, ...] = (), sha: str = SHA) -> None:
        self.pulls.append(
            {"number": number, "body": body, "labels": [{"name": n} for n in labels], "head": {"sha": sha}}
        )

    def __call__(self, method: str, endpoint: str, payload: Any = None) -> Any:
        self.calls.append((method, endpoint, payload))
        if any(m == method and endpoint.startswith(prefix) for m, prefix in self.fail):
            raise tool.GhError(f"{method} {endpoint} failed")
        if endpoint == "graphql":
            assert payload["query"] in tool.QUERIES.values()
            v = payload["variables"]
            if "number" in v:
                data = self.fresh.get(v["number"], self.issues.get(v["number"]))
                return {"data": {"repository": {"issueOrPullRequest": self._node(v["number"], data) if data else None}}}
            nodes = [
                self._node(n, i)
                for n, i in sorted(self.issues.items())
                if i.get("state", "OPEN") == "OPEN" and i.get("type", "Issue") == "Issue"
            ]
            return {"data": {"repository": {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}}}
        number = int(endpoint.split("/")[4]) if re.match(rf"repos/{REPO}/(issues|pulls)/\d+", endpoint) else None
        if method == "GET" and endpoint.startswith(f"repos/{REPO}/issues?state=closed&labels=blocked"):
            return [{"number": n} for n in self.closed_labelled]
        if method == "GET" and "/comments?" in endpoint:
            return self.comments.get(number, [])
        if method == "GET" and "/events?" in endpoint:
            return self.events.get(number, [])
        if method == "POST" and endpoint.endswith("/labels"):
            self.issues.setdefault(number, {"labels": [], "blockers": []})["labels"] += payload["labels"]
            return []
        if method == "DELETE" and "/labels/" in endpoint:
            if number in self.issues:
                self.issues[number]["labels"].remove("blocked")
            return None
        if method == "POST" and endpoint.endswith("/comments"):
            self.comments.setdefault(number, []).append({"user": {"type": "Bot"}, "body": payload["body"]})
            return {}
        if method == "GET" and endpoint.startswith(f"repos/{REPO}/pulls?state=open"):
            page = int(endpoint.rsplit("page=", 1)[1])
            return self.pulls[(page - 1) * 100 : page * 100]
        if method == "GET" and re.fullmatch(rf"repos/{REPO}/pulls/\d+", endpoint):
            return next(p for p in self.pulls if p["number"] == number)
        if method == "GET" and "/check-runs?check_name=Issue%20dependencies" in endpoint:
            return {"check_runs": self.check_runs.get(endpoint.split("/")[4], [])}
        if method == "POST" and re.fullmatch(rf"repos/{REPO}/actions/jobs/\d+/rerun", endpoint):
            return None
        raise AssertionError(f"unexpected call {method} {endpoint}")

    def writes(self) -> List[Tuple[str, str]]:
        return [(m, e) for m, e, _ in self.calls if m in ("POST", "DELETE") and e != "graphql"]

    def reruns(self) -> List[str]:
        return [e for _, e in self.writes() if e.endswith("/rerun")]


def run(
    job_id: int, conclusion: Optional[str], status: str = "completed", app: str = "github-actions"
) -> Dict[str, Any]:
    return {"id": job_id, "status": status, "conclusion": conclusion, "app": {"slug": app}}


# --- sync ------------------------------------------------------------------------------------------------------


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
    assert ("DELETE", f"repos/{REPO}/issues/40/labels/blocked") in gh.writes()  # a closed issue waits for nothing
    assert 40 not in gh.comments


def test_a_dry_run_reads_but_never_writes(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({5: {"labels": [], "blockers": [(1, "OPEN", None)]}, 6: {"labels": ["blocked"], "blockers": []}})
    gh.pull(30, "Fixes #5")
    gh.check_runs[SHA] = [run(77, "success")]
    assert tool.run_sync(REPO, gh, dry_run=True) == 0
    out = capsys.readouterr().out
    assert gh.writes() == [] and "would label #5 blocked" in out and "would re-run" in out


def test_a_blocker_reopened_since_the_snapshot_does_not_unblock_the_issue() -> None:
    gh = FakeGitHub({6: {"labels": ["blocked"], "blockers": [(2, "CLOSED", "COMPLETED")]}})
    gh.fresh[6] = {"labels": ["blocked"], "blockers": [(2, "OPEN", "REOPENED")]}
    assert tool.run_sync(REPO, gh) == 0
    assert gh.writes() == [] and gh.issues[6]["labels"] == ["blocked"]


def test_the_notice_goes_first_so_a_failed_label_removal_is_finished_without_a_second_notice() -> None:
    gh = FakeGitHub({6: {"labels": ["blocked"], "blockers": [(2, "CLOSED", "COMPLETED")]}})
    gh.fail = [("DELETE", f"repos/{REPO}/issues/6/labels")]
    with pytest.raises(tool.GhError):
        tool.run_sync(REPO, gh)
    assert len(gh.comments[6]) == 1 and gh.issues[6]["labels"] == ["blocked"]
    gh.fail = []
    assert tool.run_sync(REPO, gh) == 0
    assert len(gh.comments[6]) == 1 and gh.issues[6]["labels"] == []


def test_a_cancelled_blocker_is_flagged_once_and_a_persons_copy_of_the_marker_does_not_silence_it() -> None:
    gh = FakeGitHub({5: {"labels": ["blocked"], "blockers": [(1, "CLOSED", "NOT_PLANNED")]}})
    gh.comments[5] = [
        {"user": {"type": "User"}, "body": tool.NOT_COMPLETED_MARKER.format(blocker="hodetech/forgelm#1")}
    ]
    assert tool.run_sync(REPO, gh) == 0
    assert gh.issues[5]["labels"] == ["blocked", "needs-triage"] and len(gh.comments[5]) == 2
    assert tool.run_sync(REPO, gh) == 0  # the next run finds the bot's notice and stays quiet
    assert len(gh.comments[5]) == 2 and gh.issues[5]["labels"].count("needs-triage") == 1


def test_cancelled_blockers_with_one_number_in_two_repositories_each_get_a_notice() -> None:
    gh = FakeGitHub(
        {
            5: {
                "labels": ["blocked"],
                "blockers": [(7, "CLOSED", "NOT_PLANNED"), (7, "CLOSED", "DUPLICATE", "HodeTech/Other")],
            }
        }
    )
    assert tool.run_sync(REPO, gh) == 0
    bodies = [c["body"] for c in gh.comments[5]]
    assert len(bodies) == 2 and bodies[0].startswith("#7,") and bodies[1].startswith("HodeTech/Other#7,")


# --- re-running the pull-request check when it no longer matches -----------------------------------------------


@pytest.mark.parametrize(
    "blockers, last, rerun",
    [
        ([(2, "OPEN", None)], run(77, "success"), True),  # green, but the linked issue became blocked
        ([(2, "CLOSED", "COMPLETED")], run(77, "failure"), True),  # red, but its blocker is completed now
        ([(2, "OPEN", None)], run(77, "failure"), False),  # red and still blocked
        ([(2, "CLOSED", "COMPLETED")], run(77, "success"), False),  # green and free
        ([(2, "CLOSED", "COMPLETED")], run(77, None, status="in_progress"), False),  # still running: next sync looks
    ],
)
def test_the_sync_reruns_a_check_whose_result_no_longer_matches(blockers: list, last: dict, rerun: bool) -> None:
    gh = FakeGitHub({6: {"labels": ["blocked"] if blockers[0][1] == "OPEN" else [], "blockers": blockers}})
    gh.pull(30, "Fixes #6")
    gh.check_runs[SHA] = [run(70, "failure"), last]  # the latest run is the one with the highest id
    assert tool.run_sync(REPO, gh) == 0
    assert gh.reruns() == ([f"repos/{REPO}/actions/jobs/77/rerun"] if rerun else [])


def test_checks_from_another_app_and_pull_requests_that_link_nothing_are_left_alone() -> None:
    gh = FakeGitHub({6: {"labels": ["blocked"], "blockers": [(2, "OPEN", None)]}})
    gh.pull(30, "Fixes #6")
    gh.pull(31, "Docs only", sha="b" * 40)
    gh.check_runs[SHA] = [run(77, "success", app="someone-else")]
    gh.check_runs["b" * 40] = [run(78, "success")]
    assert tool.run_sync(REPO, gh) == 0
    assert gh.reruns() == []


def test_every_page_of_open_pull_requests_is_reconciled() -> None:
    gh = FakeGitHub({6: {"labels": ["blocked"], "blockers": [(2, "OPEN", None)]}})
    for n in range(100):
        gh.pull(1000 + n, "Docs only", sha=f"{n:040x}")
    gh.pull(30, "Fixes #6")
    gh.check_runs[SHA] = [run(77, "success")]
    assert tool.run_sync(REPO, gh) == 0
    assert gh.reruns() == [f"repos/{REPO}/actions/jobs/77/rerun"]


def test_a_failing_rerun_is_a_warning_not_a_failed_sync(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({6: {"labels": ["blocked"], "blockers": [(2, "CLOSED", "COMPLETED")]}})
    gh.pull(30, "Fixes #6")
    gh.check_runs[SHA] = [run(77, "failure")]
    gh.fail = [("POST", f"repos/{REPO}/actions/jobs/77/rerun")]  # the listing works; only the re-run fails
    assert tool.run_sync(REPO, gh) == 0
    assert gh.issues[6]["labels"] == [] and "warning: could not reconcile" in capsys.readouterr().err
    assert ("POST", f"repos/{REPO}/actions/jobs/77/rerun") in [(m, e) for m, e, _ in gh.calls]


def test_a_failing_read_fails_the_sync() -> None:
    gh = FakeGitHub({})
    gh.fail = [("POST", "graphql")]
    with pytest.raises(tool.GhError):
        tool.run_sync(REPO, gh)


@pytest.mark.parametrize("field, limit", [("blockedBy", "more than 50 blockers"), ("labels", "more than 100 labels")])
def test_more_links_or_labels_than_one_page_is_an_error_not_a_silent_truncation(field: str, limit: str) -> None:
    node = FakeGitHub._node(5, {"labels": ["blocked"], "blockers": [(1, "OPEN", None)]})
    node[field]["totalCount"] = 101
    with pytest.raises(tool.GhError, match=limit):
        tool._issue_state(node, True)


# --- check-pr --------------------------------------------------------------------------------------------------


def test_check_pr_reads_the_pull_request_again_instead_of_trusting_the_event() -> None:
    gh = FakeGitHub({5: {"labels": [], "blockers": [(1, "OPEN", None)]}, 1: {"labels": [], "blockers": []}})
    gh.pull(30, "Fixes #5")
    stale = {"pull_request": {"number": 30, "body": "Docs only", "labels": [{"name": "dependency-override"}]}}
    assert tool.run_check_pr(stale, REPO, gh) == 3
    gh.pulls[0]["labels"] = [{"name": "dependency-override"}]
    gh.events[30] = [label_event("labeled", "cemililik")]
    assert tool.run_check_pr(stale, REPO, gh) == 0


def test_check_pr_ignores_an_override_label_a_contributor_set(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({5: {"labels": [], "blockers": [(1, "OPEN", None)]}})
    gh.pull(30, "Fixes #5", labels=("dependency-override",))
    gh.events[30] = [label_event("labeled", "contributor")]
    assert tool.run_check_pr({"pull_request": {"number": 30}}, REPO, gh) == 3
    assert "is ignored" in capsys.readouterr().out


def test_check_pr_skips_linked_pull_requests_and_closed_issues() -> None:
    gh = FakeGitHub(
        {
            5: {"labels": [], "blockers": [(1, "OPEN", None)], "type": "PullRequest"},
            6: {"labels": [], "blockers": [(1, "OPEN", None)], "state": "CLOSED"},
        }
    )
    gh.pull(30, "Refs #5, #6 and #404")
    assert tool.run_check_pr({"pull_request": {"number": 30}}, REPO, gh) == 0


def test_check_pr_prints_errors_as_annotations_with_numbers_only(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({5: {"labels": [], "blockers": [(1, "OPEN", None)]}})
    gh.pull(30, "Fixes #5 ::set-output name=x::y")
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
    gh.fail = [("POST", "graphql")]
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
    assert "ref" not in checkout["with"]  # the default branch: never a pull request's code


def test_the_sync_workflow_triggers_permissions_and_concurrency() -> None:
    wf = load("issue-dependencies.yml")
    assert wf[True] == {
        "issues": {"types": ["closed", "reopened"]},
        "schedule": [{"cron": "17 * * * *"}],
        "workflow_dispatch": None,
    }  # YAML 1.1 reads the key `on` as a boolean
    assert wf["permissions"] == {}
    job = wf["jobs"]["sync"]
    assert job["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
        "checks": "read",
        "actions": "write",
    }
    assert wf["concurrency"] == {"group": "issue-dependencies", "cancel-in-progress": False}
    assert "github.repository == 'HodeTech/ForgeLM'" in job["if"]
    assert steps_of(wf)[1]["run"] == "python3 tools/issue_dependencies.py sync"


def test_the_pull_request_check_takes_its_definition_from_the_default_branch_and_never_runs_pr_code() -> None:
    wf = load("pr-dependencies.yml")
    text = (ROOT / ".github/workflows/pr-dependencies.yml").read_text(encoding="utf-8")
    # `pull_request` would take this file from the pull request, which could then rewrite the check that judges it.
    assert list(wf[True]) == ["pull_request_target"]
    assert set(wf[True]["pull_request_target"]) == {"types"}  # no paths/branches filter: a required check must report
    assert {"edited", "labeled", "unlabeled", "synchronize"} <= set(wf[True]["pull_request_target"]["types"])
    assert wf["permissions"] == {}
    job = wf["jobs"]["issue-dependencies"]
    assert job["name"] == tool.CHECK_NAME == "Issue dependencies"  # the required check's context
    assert job["permissions"] == {"contents": "read", "issues": "read", "pull-requests": "read"}
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())  # the YAML without its comments
    assert "head" not in code and "github.event.pull_request" not in code
    assert code.count("secrets.") == 1  # GITHUB_TOKEN only
    assert steps_of(wf)[1]["run"] == "python3 tools/issue_dependencies.py check-pr"
