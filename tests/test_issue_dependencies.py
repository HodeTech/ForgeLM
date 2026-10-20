"""Tests for tools/issue_dependencies.py and its two workflows.

The tool derives the ``blocked`` label from GitHub's native "Blocked by" links and fails a pull request that works
on a blocked issue (CONTRIBUTING.md, "Dependencies between issues"). These tests pin the rules, the reading of
pull-request texts written by anyone (including its linear running time), the use of GitHub's own closing list for
exemptions, the write paths of ``sync`` and the pull-request check reconciliation against a fake API (with GitHub's
real error shapes), the fixed-endpoint discipline of the GitHub calls, and the security posture of both workflow
files, since no other guard reads them.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import assign_on_comment  # noqa: E402
import issue_dependencies as tool  # noqa: E402

REPO = "HodeTech/ForgeLM"
OTHER = "HodeTech/Other"
SHA = "a" * 40
NOT_FOUND = {
    "type": "NOT_FOUND",
    "path": ["repository", "issueOrPullRequest"],
    "message": "Could not resolve to an issue or pull request with the number of 404.",
}


def blocker(number: int, state: str = "OPEN", reason: Optional[str] = None, repo: str = REPO) -> tool.Blocker:
    return tool.Blocker(number, repo, state, reason)


def issue(number: int, *blockers: tool.Blocker, labels: Tuple[str, ...] = (), is_open: bool = True) -> tool.IssueState:
    return tool.IssueState(number, is_open, frozenset(labels), tuple(blockers))


def pull_node(
    number: int = 30,
    body: str = "",
    title: str = "",
    labels: Sequence[str] = (),
    sha: str = SHA,
    base: str = "development",
    closing: Sequence[Any] = (),
    commits: Sequence[str] = (),
    closing_total: Optional[int] = None,
    commit_total: Optional[int] = None,
) -> Dict[str, Any]:
    """A pull request as the GraphQL queries return it; a closing entry is `n` or `(n, "owner/repo")`."""
    refs = [c if isinstance(c, tuple) else (c, REPO) for c in closing]
    return {
        "number": number,
        "title": title,
        "body": body,
        "headRefOid": sha,
        "baseRefName": base,
        "labels": {"totalCount": len(labels), "nodes": [{"name": n} for n in labels]},
        "closingIssuesReferences": {
            "totalCount": len(refs) if closing_total is None else closing_total,
            "nodes": [{"number": n, "repository": {"nameWithOwner": r}} for n, r in refs],
        },
        "commits": {
            "totalCount": len(commits) if commit_total is None else commit_total,
            "nodes": [{"commit": {"message": m}} for m in commits],
        },
    }


def facts(**kwargs: Any) -> tool.PullFacts:
    return tool._pull_facts(pull_node(**kwargs), "development")


def verdict(
    links: Dict[int, Optional[Tuple[tool.Blocker, ...]]], override: Optional[bool] = None, **kwargs: Any
) -> tool.Verdict:
    return tool.check_pull_request(override, facts(**kwargs), lambda n: links.get(n, ()), REPO)


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
    done = (blocker(1, "CLOSED", "COMPLETED"), blocker(9, "CLOSED", "COMPLETED", repo=OTHER))
    plan = tool.plan_issue(issue(5, *done, labels=("blocked", "wave: 3")), REPO)
    assert plan.remove_label and not plan.add_label
    assert "(#1, HodeTech/Other#9)" in plan.comment and plan.comment.startswith("This issue is no longer blocked")
    assert "can be picked up now" in plan.comment
    assert plan.marker == "<!-- issue-dependencies:unblocked:hodetech/forgelm#1,hodetech/other#9 -->"
    assert plan.comment.endswith(plan.marker)


def test_an_unblocked_issue_that_still_needs_triage_does_not_say_it_can_be_picked_up() -> None:
    plan = tool.plan_issue(issue(5, blocker(1, "CLOSED", "COMPLETED"), labels=("blocked", "needs-triage")), REPO)
    assert plan.remove_label and "still carries `needs-triage`" in plan.comment
    assert "can be picked up now" not in plan.comment


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
    here, there = blocker(7, "CLOSED", "NOT_PLANNED"), blocker(7, "CLOSED", "NOT_PLANNED", repo=OTHER)
    a, b = tool.not_completed_notice(here, REPO), tool.not_completed_notice(there, REPO)
    assert a.startswith("#7, which") and b.startswith("HodeTech/Other#7, which")
    assert a.splitlines()[-1] != b.splitlines()[-1]


# --- reading the texts a pull request is made of ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, linked",
    [
        ("Fixes #12", {12}),
        ("fixed #12, closes #13 and resolves #14", {12, 13, 14}),
        ("FIXES #12", {12}),
        ("Fixes HodeTech/ForgeLM#12", {12}),
        ("Fixes #12, #13 and #14", {12, 13, 14}),  # every issue of a list counts as linked
        ("Fixes#12", {12}),  # read leniently: links only make the check stricter
        ("Resolves: #12", {12}),
        ("Fixes https://github.com/HodeTech/ForgeLM/issues/12", {12}),
        ("Refs http://github.com/HodeTech/ForgeLM/issues/12", {12}),
        ("Refs #12", {12}),
        ("references #12 & #13", {12, 13}),
        ("Fixes other/repo#12 and https://github.com/other/repo/issues/13", set()),
        ("See #12 for context; prefixes #13 are not keywords; refsfoo/bar#14", set()),
        ("Fixes\n#12", set()),  # a keyword never reaches across a line
        ("Fixes #5abc and refs #７", set()),  # a reference is ASCII digits and a whole token
        ("<!-- Fixes #12 -->\n`Refs #13`\n```\nFixes #14\n```\nRefs #15", {15}),
    ],
)
def test_linked_issues(text: str, linked: set) -> None:
    assert tool.linked_issues(text, REPO) == frozenset(linked)


def test_the_pull_request_template_itself_links_nothing() -> None:
    template = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    assert tool.linked_issues(template, REPO) == frozenset()


@pytest.mark.parametrize(
    "text, linked",
    [
        ("Refs #1\n```\nFixes #2\nRefs #3", {1}),  # an unclosed fence runs to the end
        ("~~~~\nFixes #2\n~~~\nstill code\n~~~~\nRefs #3", {3}),  # a closing fence is at least as long
        ("```\nFixes #2\n~~~\nRefs #3", set()),  # and of the same character
        ("```py `x`\nRefs #3\n```", {3}),  # a backtick fence's info string has no backtick: not a fence
        ("    ```\nFixes #2\nRefs #3", {2, 3}),  # four spaces: not a fence opener
        ("Refs #1 <!-- Fixes #2 --> Refs #3", {1, 3}),
        ("Refs #1 <!-- Fixes #2\nRefs #3", {1}),  # an unclosed comment runs to the end
        ("Refs #1 ``Refs #2`` Refs #3", {1, 3}),  # a code span closes on a run of the same length
        ("Refs #1 `` Refs #2 ` still code `` Refs #3", {1, 3}),
        ("Refs #1 ``Refs #2` Refs #3", {1, 2, 3}),  # unmatched runs are literal text
        ("A stray ` here\n\nRefs #3\n\nand ` there", {3}),  # a span never crosses a blank line
        ("A span `across\nRefs #2` lines\nRefs #3", {3}),  # but it does cross a line break in a paragraph
        ("A `span\n```\nx\n```\nRefs #3 ` and", {3}),  # nor a fence
        ("```\n<!--\n```\nRefs #3", {3}),  # a comment opener inside a fence is code, not a comment
    ],
)
def test_fences_comments_and_code_spans_follow_commonmark(text: str, linked: set) -> None:
    assert tool.linked_issues(text, REPO) == frozenset(linked)


HOSTILE = {
    "unclosed comments": "<!--" * 15_000,  # the earlier `<!--.*?-->` took about 2 s here
    "unclosed fence openers": "```x\n" * 15_000,  # the earlier fence regex took about 5 s here
    "alternating fences": "```\n" * 15_000,
    "mixed fences": "~~~~\n```\n" * 7_500,
    "keywords without references": "Fixes " * 10_000 + "#1",
    "a keyword and a long space run": "Fixes" + " " * 60_000 + "x",
    "a long reference list": "Fixes #1, " * 6_000,
    "one backtick run": "`" * 60_000,
    "many single backticks": "`a" * 30_000,
    "unequal backtick runs": "``a`" * 15_000,
    "digit runs that are not tokens": "#" + "1" * 60_000 + "a",
    "comments then fences": "<!-- -->```\n" * 5_000,
}


@pytest.mark.parametrize("text", HOSTILE.values(), ids=HOSTILE.keys())
def test_a_hostile_text_is_read_in_linear_time(text: str) -> None:
    # docs/standards/regex.md: a generous absolute floor; a quadratic reader blows far past it at this size.
    start = time.perf_counter()
    tool.linked_issues(text, REPO)
    assert time.perf_counter() - start < 1.0


# --- the pull-request rule -------------------------------------------------------------------------------------


def test_a_pull_request_on_a_blocked_issue_fails_and_names_the_blocker() -> None:
    v = verdict({5: (blocker(1),)}, body="Fixes #5", closing=[5])
    assert not v.ok and v.lines[0] == "#5 is blocked by #1, which is still open."
    assert any(line.startswith("Wait until the blocker is closed as completed") for line in v.lines)


def test_refs_counts_as_working_on_the_issue_and_the_message_says_how_to_drop_it() -> None:
    v = verdict({5: (blocker(1),)}, body="Refs #5")
    assert not v.ok and any("remove the reference" in line for line in v.lines)


@pytest.mark.parametrize(
    "where",
    [{"title": "Refs #5"}, {"commits": ["feat: x\n\nFixes #5"]}, {"closing": [5]}],  # a manual Development link
    ids=["title", "commit message", "GitHub's closing list"],
)
def test_every_place_a_pull_request_can_link_an_issue_counts(where: Dict[str, Any]) -> None:
    assert not verdict({5: (blocker(1),)}, body="Docs only", **where).ok


def test_a_blocker_github_will_close_in_the_same_pull_request_is_exempt() -> None:
    assert verdict({5: (blocker(1),), 1: ()}, body="Fixes #1\nFixes #5", closing=[1, 5]).ok


def test_a_blocker_in_another_repository_is_exempt_when_github_will_close_it() -> None:
    there = blocker(7, repo=OTHER)
    assert verdict({5: (there,)}, body="Fixes #5", closing=[5, (7, OTHER)]).ok
    assert not verdict({5: (there,)}, body="Fixes #5", closing=[5, 7]).ok  # #7 here is another issue


@pytest.mark.parametrize(
    "body", ["Fixes #5\nFixes #1", "Fixes #5\nFixes#1", "Fixes #5, #1", "Fixes #5\n> ~~~\n> Fixes #1\n> ~~~"]
)
def test_only_githubs_closing_list_exempts_a_blocker_never_the_texts(body: str) -> None:
    # GitHub lists only #5 as closed by this pull request, whatever the description seems to say about #1.
    assert not verdict({5: (blocker(1),), 1: ()}, body=body, closing=[5]).ok


def test_off_the_default_branch_nothing_is_exempt_because_github_closes_nothing_on_merge() -> None:
    assert not verdict({5: (blocker(1),), 1: ()}, body="Fixes #1\nFixes #5", closing=[1, 5], base="main").ok


def test_a_blocker_closed_without_being_completed_fails_and_asks_for_a_re_plan_not_a_wait() -> None:
    v = verdict({5: (blocker(1, "CLOSED", "NOT_PLANNED"),)}, body="Fixes #1\nFixes #5", closing=[1, 5])
    assert not v.ok and "re-plan that dependency" in v.lines[0]
    assert not any(line.startswith("Wait until") for line in v.lines)


def test_completed_blockers_pull_requests_closed_and_missing_issues_pass() -> None:
    v = verdict({5: (blocker(1, "CLOSED", "COMPLETED"),), 6: None}, body="Fixes #5, #6")
    assert v.ok and v.lines == ("No linked issue is blocked (1 issue(s) checked).",)


def test_more_links_than_the_bound_fail_without_a_single_lookup() -> None:
    asked: List[int] = []
    body = " ".join(f"Refs #{n}" for n in range(1, tool._MAX_LINKED + 2))
    v = tool.check_pull_request(None, facts(body=body), lambda n: asked.append(n) or (), REPO)
    assert not v.ok and "at most 50" in v.lines[0] and asked == []
    accepted = tool.check_pull_request(True, facts(body=body), lambda n: asked.append(n) or (), REPO)
    assert accepted.ok and asked == []


@pytest.mark.parametrize("kwargs", [{"commit_total": 101}, {"closing": [5], "closing_total": 51}])
def test_lists_longer_than_one_page_fail_instead_of_being_read_in_part(kwargs: Dict[str, Any]) -> None:
    assert not verdict({}, body="Docs only", **kwargs).ok
    assert verdict({}, override=True, body="Docs only", **kwargs).ok


def test_a_maintainers_override_passes_and_lists_what_it_accepts() -> None:
    v = verdict({5: (blocker(1),)}, override=True, body="Fixes #5")
    assert v.ok and "set by a maintainer" in v.lines[0] and v.lines[1] == "#5 is blocked by #1, which is still open."


def test_an_override_set_by_anyone_else_is_ignored_and_says_so() -> None:
    v = verdict({5: (blocker(1),)}, override=False, body="Fixes #5")
    assert not v.ok and any("is ignored" in line for line in v.lines)


def label_event(kind: str, login: str, at: str = "2026-10-20T10:00:00Z", label: str = "dependency-override") -> dict:
    return {"event": kind, "actor": {"login": login}, "label": {"name": label}, "created_at": at, "id": 1}


@pytest.mark.parametrize(
    "events, authorized",
    [
        ([label_event("labeled", "cemililik")], True),
        ([label_event("labeled", "CemilIlik")], True),
        ([label_event("labeled", "contributor")], False),
        (
            [
                label_event("labeled", "cemililik", "2026-10-20T10:00:00Z"),
                label_event("unlabeled", "x", "2026-10-20T11:00:00Z"),
                label_event("labeled", "x", "2026-10-20T12:00:00Z"),
            ],
            False,
        ),
        ([label_event("labeled", "cemililik"), label_event("unlabeled", "cemililik", "2026-10-20T11:00:00Z")], False),
        ([label_event("labeled", "contributor", label="bug"), label_event("labeled", "cemililik")], True),
        # out of order: the latest by time decides, not the position in the answer
        ([label_event("labeled", "contributor", "2026-10-21T09:00:00Z"), label_event("labeled", "cemililik")], False),
        ([], False),
    ],
)
def test_the_override_counts_only_when_a_maintainer_set_it_last(events: List[Dict[str, Any]], authorized: bool) -> None:
    gh = FakeGitHub({})
    gh.events[30] = events
    assert tool.override_authorized(gh, REPO, 30) is authorized


# --- a fake GitHub API -----------------------------------------------------------------------------------------


class FakeGitHub:
    """Plays the endpoints the tool calls: GraphQL for issues and pull requests, REST for the rest."""

    def __init__(self, issues: Dict[int, Dict[str, Any]], closed_labelled: Tuple[int, ...] = ()) -> None:
        # number -> {"labels": [...], "blockers": [(n, state, reason[, repo]), ...], "state": "OPEN", "type": "Issue"}
        self.issues = issues
        self.fresh: Dict[int, Dict[str, Any]] = {}  # what a re-read of one issue sees, when it differs
        self.closed_labelled = list(closed_labelled)
        self.calls: List[Tuple[str, str, Any]] = []
        self.comments: Dict[int, List[Dict[str, Any]]] = {}
        self.pulls: Dict[int, Dict[str, Any]] = {}
        self.check_runs: Dict[str, List[Dict[str, Any]]] = {}
        self.events: Dict[int, List[Dict[str, Any]]] = {}
        self.fail: List[Tuple[str, str]] = []  # (method, endpoint prefix); `query:<name>` for a GraphQL query
        self.issue_page = 100

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
            "labels": {
                "totalCount": data.get("label_total", len(data["labels"])),
                "nodes": [{"name": n} for n in data["labels"]],
            },
            "blockedBy": {"totalCount": len(nodes), "nodes": nodes},
        }

    def pull(self, number: int, body: str = "", **kwargs: Any) -> None:
        self.pulls[number] = pull_node(number, body, **kwargs)

    def _graphql(self, payload: Dict[str, Any]) -> Any:
        query = next(name for name, text in tool.QUERIES.items() if text == payload["query"])
        if ("POST", f"query:{query}") in self.fail:
            raise tool.GhError(f"GraphQL {query} failed")
        v = payload["variables"]
        if query == "issue":
            data = self.fresh.get(v["number"], self.issues.get(v["number"]))
            if data is None:
                return {"data": {"repository": {"issueOrPullRequest": None}}, "errors": [NOT_FOUND]}
            return {"data": {"repository": {"issueOrPullRequest": self._node(v["number"], data)}}}
        if query == "open_issues":
            numbers = [
                n
                for n, i in sorted(self.issues.items())
                if i.get("state", "OPEN") == "OPEN" and i.get("type", "Issue") == "Issue"
            ]
            start = int(v["after"] or 0)
            page = numbers[start : start + self.issue_page]
            more = start + self.issue_page < len(numbers)
            info = {"hasNextPage": more, "endCursor": str(start + self.issue_page)}
            return {
                "data": {
                    "repository": {"issues": {"nodes": [self._node(n, self.issues[n]) for n in page], "pageInfo": info}}
                }
            }
        branch = {"defaultBranchRef": {"name": "development"}}
        if query == "pull":
            return {"data": {"repository": {**branch, "pullRequest": self.pulls.get(v["number"])}}}
        if query == "open_pulls":
            nodes = [self.pulls[n] for n in sorted(self.pulls)]
            start = int(v["after"] or 0)
            info = {"hasNextPage": start + 25 < len(nodes), "endCursor": str(start + 25)}
            return {
                "data": {
                    "repository": {**branch, "pullRequests": {"nodes": nodes[start : start + 25], "pageInfo": info}}
                }
            }
        raise AssertionError(query)

    def __call__(self, method: str, endpoint: str, payload: Any = None) -> Any:
        self.calls.append((method, endpoint, payload))
        if endpoint == "graphql":
            return self._graphql(payload)
        if any(m == method and endpoint.startswith(prefix) for m, prefix in self.fail):
            raise tool.GhError(f"{method} {endpoint} failed")
        number = int(endpoint.split("/")[4]) if re.match(rf"repos/{REPO}/issues/\d+", endpoint) else None
        if method == "GET" and endpoint.startswith(f"repos/{REPO}/issues?state=closed&labels=blocked"):
            return [{"number": n} for n in self.closed_labelled]
        if method == "GET" and "/comments?" in endpoint:
            page = int(endpoint.rsplit("page=", 1)[1])
            return self.comments.get(number, [])[(page - 1) * tool._PAGE_SIZE : page * tool._PAGE_SIZE]
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
        if method == "GET" and "/check-runs?check_name=Issue%20dependencies" in endpoint:
            return {"check_runs": self.check_runs.get(endpoint.split("/")[4], [])}
        if method == "POST" and re.fullmatch(rf"repos/{REPO}/actions/jobs/\d+/rerun", endpoint):
            return None
        raise AssertionError(f"unexpected call {method} {endpoint}")

    def writes(self) -> List[Tuple[str, str]]:
        return [(m, e) for m, e, _ in self.calls if m in ("POST", "DELETE") and e != "graphql"]

    def reruns(self) -> List[str]:
        return [e for _, e in self.writes() if e.endswith("/rerun")]


RECENT = "2026-10-20T09:00:00Z"


def run(
    job_id: int,
    conclusion: Optional[str],
    status: str = "completed",
    app: str = "github-actions",
    started: str = RECENT,
) -> dict:
    return {"id": job_id, "status": status, "conclusion": conclusion, "app": {"slug": app}, "started_at": started}


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "_now", lambda: datetime.datetime(2026, 10, 20, 12, 0, tzinfo=datetime.timezone.utc))


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


def test_every_page_of_open_issues_is_read() -> None:
    gh = FakeGitHub({n: {"labels": [], "blockers": [(1, "OPEN", None)]} for n in range(10, 15)})
    gh.issue_page = 2
    assert tool.run_sync(REPO, gh) == 0
    assert all(gh.issues[n]["labels"] == ["blocked"] for n in range(10, 15))


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
    assert tool.run_sync(REPO, gh) == 1
    assert len(gh.comments[6]) == 1 and gh.issues[6]["labels"] == ["blocked"]
    gh.fail = []
    assert tool.run_sync(REPO, gh) == 0
    assert len(gh.comments[6]) == 1 and gh.issues[6]["labels"] == []


def test_one_issue_that_cannot_be_written_stops_neither_the_others_nor_the_reconciliation() -> None:
    gh = FakeGitHub(
        {
            5: {"labels": [], "blockers": [(1, "OPEN", None)]},  # its label write fails
            6: {"labels": [], "blockers": [(1, "OPEN", None)]},
        }
    )
    gh.fail = [("POST", f"repos/{REPO}/issues/5/labels")]
    gh.pull(30, "Fixes #6")
    gh.check_runs[SHA] = [run(77, "success")]
    assert tool.run_sync(REPO, gh) == 1  # reported at the end
    assert gh.issues[6]["labels"] == ["blocked"] and gh.reruns() == [f"repos/{REPO}/actions/jobs/77/rerun"]


def test_an_issue_whose_links_cannot_be_read_is_reported_and_never_treated_as_free(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGitHub({5: {"labels": ["blocked"], "blockers": [(1, "OPEN", None)], "label_total": 101}})
    gh.pull(30, "Fixes #5")
    gh.check_runs[SHA] = [run(77, "failure")]  # red: must not be re-run as if #5 were free
    assert tool.run_sync(REPO, gh) == 1
    assert gh.reruns() == [] and "more than 100 labels" in capsys.readouterr().err


def test_a_failing_list_of_closed_issues_is_reported_and_the_rest_still_runs() -> None:
    gh = FakeGitHub({5: {"labels": [], "blockers": [(1, "OPEN", None)]}})
    gh.fail = [("GET", f"repos/{REPO}/issues?state=closed")]
    assert tool.run_sync(REPO, gh) == 1 and gh.issues[5]["labels"] == ["blocked"]


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
        {5: {"labels": ["blocked"], "blockers": [(7, "CLOSED", "NOT_PLANNED"), (7, "CLOSED", "DUPLICATE", OTHER)]}}
    )
    assert tool.run_sync(REPO, gh) == 0
    bodies = [c["body"] for c in gh.comments[5]]
    assert len(bodies) == 2 and bodies[0].startswith("#7,") and bodies[1].startswith("HodeTech/Other#7,")


def test_an_endless_comment_thread_is_never_answered_with_a_repeated_notice(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({})
    gh.comments[5] = [{"user": {"type": "User"}, "body": "chat"}] * (tool._PAGE_SIZE * tool._MAX_PAGES)
    assert tool._already_noticed(gh, REPO, 5, "<!-- x -->") is True
    assert "too many comments" in capsys.readouterr().err


def test_an_earlier_notice_is_found_on_a_later_page_and_its_absence_is_reported_after_the_last() -> None:
    gh = FakeGitHub({})
    chat = [{"user": {"type": "User"}, "body": "chat"}] * (tool._PAGE_SIZE + 50)
    gh.comments[5] = chat + [{"user": {"type": "Bot"}, "body": "<!-- x -->"}]
    gh.comments[6] = chat
    assert tool._already_noticed(gh, REPO, 5, "<!-- x -->") is True
    assert tool._already_noticed(gh, REPO, 6, "<!-- x -->") is False


# --- re-running the pull-request check when it no longer matches -----------------------------------------------


@pytest.mark.parametrize(
    "blockers, last, rerun",
    [
        ([(2, "OPEN", None)], run(77, "success"), True),  # green, but the linked issue became blocked
        ([(2, "OPEN", None)], run(77, "neutral"), True),  # branch protection passes `neutral` too
        ([(2, "CLOSED", "COMPLETED")], run(77, "failure"), True),  # red, but its blocker is completed now
        ([(2, "OPEN", None)], run(77, "failure"), False),  # red and still blocked
        ([(2, "CLOSED", "COMPLETED")], run(77, "success"), False),  # green and free
        ([(2, "CLOSED", "COMPLETED")], run(77, "skipped"), False),  # `skipped` passes as well
        ([(2, "CLOSED", "COMPLETED")], run(77, None, status="in_progress"), False),  # still running: next sync looks
        ([(2, "CLOSED", "COMPLETED")], run(77, "failure", started="2026-09-01T00:00:00Z"), False),  # too old to re-run
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
    for n in range(40):
        gh.pull(100 + n, "Docs only", sha=f"{n:040x}")
    gh.pull(999, "Fixes #6")
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


def test_a_failing_read_of_the_open_issues_fails_the_sync() -> None:
    gh = FakeGitHub({})
    gh.fail = [("POST", "query:open_issues")]
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
    gh.pull(30, "Fixes #5", closing=[5])
    stale = {"pull_request": {"number": 30, "body": "Docs only", "labels": [{"name": "dependency-override"}]}}
    assert tool.run_check_pr(stale, REPO, gh) == 3
    gh.pull(30, "Fixes #5", closing=[5], labels=["dependency-override"])
    gh.events[30] = [label_event("labeled", "cemililik")]
    assert tool.run_check_pr(stale, REPO, gh) == 0


def test_check_pr_ignores_an_override_label_a_contributor_set(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({5: {"labels": [], "blockers": [(1, "OPEN", None)]}})
    gh.pull(30, "Fixes #5", labels=["dependency-override"])
    gh.events[30] = [label_event("labeled", "contributor")]
    assert tool.run_check_pr({"pull_request": {"number": 30}}, REPO, gh) == 3
    assert "is ignored" in capsys.readouterr().out


def test_check_pr_passes_over_pull_requests_closed_issues_and_numbers_that_name_no_issue() -> None:
    gh = FakeGitHub(
        {
            5: {"labels": [], "blockers": [(1, "OPEN", None)], "type": "PullRequest"},
            6: {"labels": [], "blockers": [(1, "OPEN", None)], "state": "CLOSED"},
        }
    )
    gh.pull(30, "Refs #5, #6 and #404")  # #404 names no issue: GitHub answers NOT_FOUND, not a failure
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
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": "30"}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert tool.main(["check-pr"], gh) == 2
    event.write_text("{not json", encoding="utf-8")
    assert tool.main(["check-pr"], gh) == 2
    assert tool.main(["frobnicate"], gh) == 2
    assert tool.main(["--help"], gh) == 0
    assert gh.calls == []


def test_main_turns_an_api_failure_into_exit_one(monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGitHub({})
    gh.fail = [("POST", "query:open_issues")]
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


class Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_gh_api_runs_a_list_command_without_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Dict[str, Any] = {}

    def fake_run(command: List[str], **kwargs: Any) -> Proc:
        seen.update(command=command, **kwargs)
        return Proc(0, "[]")

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    assert tool.gh_api("GET", f"repos/{REPO}/issues/1") == []
    assert seen["command"] == ["gh", "api", "--method", "GET", f"repos/{REPO}/issues/1"] and "shell" not in seen


def test_gh_api_failure_text_is_flattened_and_defanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: Proc(1, "", "bad\n::error::injected"))
    with pytest.raises(tool.GhError) as info:
        tool.gh_api("GET", f"repos/{REPO}/issues/1")
    assert "\n" not in str(info.value) and "::error::" not in str(info.value)


def test_gh_api_hands_a_graphql_answer_with_errors_to_the_caller_but_not_a_rest_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `gh` exits 1 for a GraphQL answer with errors and prints the answer itself; graphql() decides what it accepts.
    answer = {"data": {"repository": {"issueOrPullRequest": None}}, "errors": [NOT_FOUND]}
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: Proc(1, json.dumps(answer), "gh: Could not resolve"))
    assert tool.gh_api("POST", "graphql", {"query": tool.QUERIES["issue"], "variables": {}}) == answer
    with pytest.raises(tool.GhError):
        tool.gh_api("GET", f"repos/{REPO}/issues/1")


@pytest.mark.parametrize(
    "errors, accepted",
    [
        ([NOT_FOUND], True),
        ([NOT_FOUND, NOT_FOUND], False),
        ([{**NOT_FOUND, "path": ["repository"]}], False),  # the repository itself is missing
        ([{**NOT_FOUND, "type": "FORBIDDEN"}], False),
    ],
)
def test_graphql_accepts_only_the_one_error_a_missing_issue_number_produces(errors: list, accepted: bool) -> None:
    answer = {"data": {"repository": {"issueOrPullRequest": None}}, "errors": errors}
    if accepted:
        assert tool.graphql(lambda *a: answer, "issue", allow_missing=True, owner="o", name="n", number=1)
    else:
        with pytest.raises(tool.GhError):
            tool.graphql(lambda *a: answer, "issue", allow_missing=True, owner="o", name="n", number=1)
    with pytest.raises(tool.GhError, match="GraphQL issue failed"):  # never without allow_missing
        tool.graphql(lambda *a: answer, "issue", owner="o", name="n", number=1)


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
def test_two_steps_a_pinned_minimal_checkout_and_no_expression_in_a_shell(name: str) -> None:
    steps = steps_of(load(name))
    assert len(steps) == 2
    checkout, script = steps
    assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
    assert checkout["with"] == {
        "persist-credentials": False,
        "sparse-checkout": "tools/issue_dependencies.py",
        "sparse-checkout-cone-mode": False,
    }  # no `ref`: the default branch, never a pull request's code
    assert "uses" not in script and "${{" not in script["run"]
    assert script["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}


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
    assert job["if"] == "${{ github.repository == 'HodeTech/ForgeLM' && github.ref == 'refs/heads/development' }}"
    assert steps_of(wf)[1]["run"] == "python3 tools/issue_dependencies.py sync"


def test_the_pull_request_check_takes_its_definition_from_the_default_branch_and_never_runs_pr_code() -> None:
    wf = load("pr-dependencies.yml")
    text = (ROOT / ".github/workflows/pr-dependencies.yml").read_text(encoding="utf-8")
    # `pull_request` would take this file from the pull request, which could then rewrite the check that judges it.
    assert list(wf[True]) == ["pull_request_target"]
    assert wf[True]["pull_request_target"] == {
        "types": ["opened", "edited", "reopened", "synchronize", "ready_for_review", "labeled", "unlabeled"]
    }  # no paths/branches filter: a required check must always report
    assert wf["permissions"] == {}
    assert wf["concurrency"] == {
        "group": "pr-dependencies-${{ github.event.pull_request.number }}",
        "cancel-in-progress": True,
    }
    job = wf["jobs"]["issue-dependencies"]
    assert job["name"] == tool.CHECK_NAME == "Issue dependencies"  # the required check's context
    assert job["permissions"] == {"contents": "read", "issues": "read", "pull-requests": "read"}
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())  # the YAML without its comments
    assert "head" not in code and code.count("github.event") == 1  # only the pull request's number, for concurrency
    assert code.count("secrets.") == 1  # GITHUB_TOKEN only
    assert steps_of(wf)[1]["run"] == "python3 tools/issue_dependencies.py check-pr"


def test_no_other_workflow_has_a_job_named_like_the_check() -> None:
    # Branch protection cannot tell two Actions checks of one name apart; keep the name unique in this repository.
    owners = [
        path.name
        for path in sorted((ROOT / ".github/workflows").glob("*.yml"))
        for job in (yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs") or {}).values()
        if isinstance(job, dict) and job.get("name") == tool.CHECK_NAME
    ]
    assert owners == ["pr-dependencies.yml"]
