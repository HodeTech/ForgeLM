"""Tests for tools/issue_dependencies.py and its two workflows.

The tool derives the ``blocked`` label from GitHub's native "Blocked by" links and fails a pull request that works
on a blocked issue (CONTRIBUTING.md, "Dependencies between issues"). These tests pin the rules; the reading of
pull-request texts written by anyone, against GitHub's real rendering of hostile Markdown (tests/fixtures/
github_markdown/renders.json) and in linear time; the use of GitHub's own closing list for exemptions; the
maintainer override and its record; the write paths of ``sync`` and the pull-request check reconciliation against a
fake API (with GitHub's real error shapes); the GitHub call wrapper (fixed endpoints, retries, paging); and the
security posture of both workflow files, since no other guard reads them.
"""

from __future__ import annotations

import datetime
import html
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import assign_on_comment  # noqa: E402
import issue_dependencies as tool  # noqa: E402

REPO = "HodeTech/ForgeLM"
OTHER = "HodeTech/Other"
SHA = "a" * 40
NEW_SHA = "b" * 40
BOT = {"login": tool.BOT_LOGIN, "type": "Bot"}
NOT_FOUND = {
    "type": "NOT_FOUND",
    "path": ["repository", "issueOrPullRequest"],
    "message": "Could not resolve to an issue or pull request with the number of 404.",
}
RENDERS: Dict[str, str] = json.loads(
    (ROOT / "tests/fixtures/github_markdown/renders.json").read_text(encoding="utf-8")
)["renders"]


def plain_render(text: str) -> str:
    """A stand-in for GitHub's renderer where the Markdown does not matter: the text, escaped, in one paragraph."""
    return f"<p>{html.escape(text)}</p>" if text.strip() else ""


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
    links: Dict[int, Optional[Tuple[tool.Blocker, ...]]], override: Optional[tool.Override] = None, **kwargs: Any
) -> tool.Verdict:
    pull = facts(**kwargs)
    linked = tool.pull_links(pull, REPO, plain_render)
    return tool.check_pull_request(override, pull, linked, lambda n: links.get(n, ()), REPO)


COUNTS = tool.Override(True)
IGNORED = tool.Override(False, tool.MSG_OVERRIDE_NOT_MAINTAINER)


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
    plan = tool.plan_issue(issue(5, *done, labels=("blocked", "wave: 3")), REPO, "2026-10-20T08:00:00Z")
    assert plan.remove_label and not plan.add_label
    assert "(#1, HodeTech/Other#9)" in plan.comment and plan.comment.startswith("This issue is no longer blocked")
    assert "can be picked up now" in plan.comment
    assert plan.marker == (
        "<!-- issue-dependencies:unblocked:hodetech/forgelm#1,hodetech/other#9@2026-10-20T08:00:00Z -->"
    )
    assert plan.comment.endswith(plan.marker)


def test_two_unblockings_of_one_issue_have_different_markers() -> None:
    done = issue(5, blocker(1, "CLOSED", "COMPLETED"), labels=("blocked",))
    first, second = (tool.plan_issue(done, REPO, at) for at in ("2026-10-20T08:00:00Z", "2026-10-21T08:00:00Z"))
    assert first.marker != second.marker


def test_an_unblocked_issue_that_still_needs_triage_does_not_say_it_can_be_picked_up() -> None:
    plan = tool.plan_issue(issue(5, blocker(1, "CLOSED", "COMPLETED"), labels=("blocked", "needs-triage")), REPO)
    assert plan.remove_label and "still carries `needs-triage`" in plan.comment
    assert "can be picked up now" not in plan.comment


def test_a_hand_set_label_without_links_is_removed_with_the_explanation() -> None:
    plan = tool.plan_issue(issue(5, labels=("blocked",)), REPO)
    assert plan.remove_label and "set and removed automatically" in plan.comment
    assert plan.marker.endswith(":none@unknown -->")


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
        ("docs: `Fixes #12` in a commit message is no code span", {12}),  # GitHub closes from it all the same
    ],
)
def test_linked_issues_in_plain_text(text: str, linked: set) -> None:
    assert tool.linked_issues(text, REPO) == frozenset(linked)


# What a reader sees in GitHub's rendering of each sample (tests/fixtures/github_markdown/renders.json). The first
# block follows CommonMark's fences, comments and code spans; the second holds the constructs a line-by-line reader
# could not model (a code span holding `<!--`, escaped backticks, abrupt comments, fences inside a quote or a list
# item, raw `<pre>`); the third shows what rendering does to blocks and references.
EXPECTED: Dict[str, set] = {
    "<!-- Fixes #12 -->\n`Refs #13`\n```\nFixes #14\n```\nRefs #15": {15},
    "Refs #1\n```\nFixes #2\nRefs #3": {1},  # an unclosed fence runs to the end
    "~~~~\nFixes #2\n~~~\nstill code\n~~~~\nRefs #3": {3},  # a closing fence is at least as long
    "```\nFixes #2\n~~~\nRefs #3": set(),  # and of the same character
    "```py `x`\nRefs #3\n```": {3},  # a backtick fence's info string has no backtick: not a fence
    "    ```\nFixes #2\nRefs #3": {2, 3},  # four spaces: indented code, not a fence
    "Refs #1 <!-- Fixes #2 --> Refs #3": {1, 3},
    "Refs #1 <!-- Fixes #2\nRefs #3": {1, 2, 3},  # an inline comment that never closes is text a reader sees
    "Refs #1 ``Refs #2`` Refs #3": {1, 3},  # a code span closes on a run of the same length
    "Refs #1 `` Refs #2 ` still code `` Refs #3": {1, 3},
    "Refs #1 ``Refs #2` Refs #3": {1, 2, 3},  # unmatched runs are literal text
    "A stray ` here\n\nRefs #3\n\nand ` there": {3},  # a span never crosses a blank line
    "A span `across\nRefs #2` lines\nRefs #3": {3},  # but it does cross a line break in a paragraph
    "A `span\n```\nx\n```\nRefs #3 ` and": {3},  # nor a fence
    "```\n<!--\n```\nRefs #3": {3},  # a comment opener inside a fence is code, not a comment
    "A `<!--` span, then Refs #11 and a later --> here.": {11},
    "\\`Refs #12\\` escaped backticks.": {12},
    "<!--> Refs #13 after an abrupt comment.": {13},
    "<!---> Refs #14 after another.": {14},
    "> ```\n> Refs #15 in a quoted fence\n> ```\n\nRefs #9": {9},
    "- item\n  ```\n  Refs #16 in a list fence\n  ```\n\nRefs #9": {9},
    "<pre>`</pre>\nRefs #17 after a pre block\n`": {17},
    "<pre>\nRefs #18 in a raw pre block\n</pre>": set(),
    "Refs **#20** bold, Refs &#35;21 an entity, Refs \\#22 an escape": {20, 21, 22},
    "Fixes\n#12": set(),  # a line break ends the keyword's reach
    "| Refs | #5 |\n|---|---|\n| a | b |": set(),  # so does a table cell
    "<details><summary>Refs #7</summary>\n\nFixes #8\n</details>": {7, 8},
    "Refs [#5](https://github.com/HodeTech/ForgeLM/issues/6)": {5},  # what the reader sees, not the link target
    "Fixes https://github.com/HodeTech/ForgeLM/issues/12": {12},  # GitHub shows it as #12
    "Fixes other/repo#12 and https://github.com/other/repo/issues/13": set(),
    "# Refs #5\n\nFixes #6, #7 and #8": {5, 6, 7, 8},
    "Refs #5abc and refs #７": set(),
}


@pytest.mark.parametrize("markdown", EXPECTED, ids=[f"sample-{i}" for i in range(len(EXPECTED))])
def test_references_are_read_from_githubs_rendering_as_a_reader_sees_it(markdown: str) -> None:
    assert tool.linked_issues(tool.prose(RENDERS[markdown]), REPO) == frozenset(EXPECTED[markdown])


def test_every_captured_rendering_has_an_expectation() -> None:
    assert set(RENDERS) == set(EXPECTED)


@pytest.mark.parametrize(
    "markup, linked",
    [
        ("<p>Refs</p><p>#5</p>", set()),  # a block boundary ends the line
        ("<ul><li>Refs</li><li>#5</li></ul>", set()),
        ("<p>Refs<br>#5</p>", set()),
        ("<p>Refs <em>#5</em> and <a href='x'>Fixes</a> #6</p>", {5, 6}),  # inline elements run on
        ("<pre><code>Refs <span>#5</span></code></pre><p>Refs #6</p>", {6}),  # nested inside code is code
        ("<p>Refs <code>#5</code> Refs #6</p>", {6}),
        ("<p>Refs &#35;5 &amp; #6</p>", {5, 6}),
    ],
)
def test_prose_keeps_the_text_outside_code_and_ends_a_line_at_every_block(markup: str, linked: set) -> None:
    assert tool.linked_issues(tool.prose(markup), REPO) == frozenset(linked)


def test_the_pull_request_template_links_nothing_outside_its_html_comments() -> None:
    # GitHub drops HTML comments when it renders a description; the template keeps every example in one.
    template = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    outside, pos = [], 0
    while (start := template.find("<!--", pos)) >= 0:
        outside.append(template[pos:start])
        end = template.find("-->", start)
        pos = len(template) if end < 0 else end + 3
    outside.append(template[pos:])
    assert tool.linked_issues("".join(outside), REPO) == frozenset()


def test_the_description_is_read_through_the_renderer_and_the_title_and_commit_messages_as_written() -> None:
    seen: List[str] = []

    def render(text: str) -> str:
        seen.append(text)
        return plain_render(text)

    pull = facts(title="Refs #1", body="Refs #2", commits=["Refs #3"])
    assert tool.pull_links(pull, REPO, render) == {1, 2, 3} and seen == ["Refs #2"]


@pytest.mark.parametrize("title", ["```Fixes #5", "<!-- Fixes #5 -->", "Fix `Refs #5` docs"])
def test_a_title_hides_nothing_behind_markdown_it_does_not_render(title: str) -> None:
    # GitHub shows an HTML comment or a fence marker in a title as written, so the reference is visible there.
    assert 5 in tool.pull_links(facts(title=title), REPO, lambda text: "")


def test_an_empty_text_is_never_sent_to_the_renderer() -> None:
    gh = FakeGitHub({})
    assert tool.render_markdown(gh, REPO, " \n ") == "" and gh.calls == []


HOSTILE_TEXT: Dict[str, Callable[[int], str]] = {
    "keywords without references": lambda n: "Fixes " * (n // 6) + "#1",
    "a keyword and a long space run": lambda n: "Fixes" + " " * n + "x",
    "a long reference list": lambda n: "Fixes #1, " * (n // 10),
    "digit runs that are not tokens": lambda n: "#" + "1" * n + "a",
    "keywords run together": lambda n: "refs" * (n // 4),
    "lists that never end in a reference": lambda n: "Fixes #1" + ", and &" * (n // 7),
}
HOSTILE_HTML: Dict[str, Callable[[int], str]] = {
    "inline elements": lambda n: "<p>" + "<strong>Refs</strong> " * (n // 23) + "</p>",
    "nested code": lambda n: "<pre>" + "<code>x</code>" * (n // 14) + "</pre>",
    "many blocks": lambda n: "<p>Refs</p>" * (n // 11),
    "entities": lambda n: "<p>" + "&#35;" * (n // 5) + "</p>",
}


def _read_text(text: str) -> Any:
    return tool.linked_issues(text, REPO)


def _read_html(markup: str) -> Any:
    return tool.linked_issues(tool.prose(markup), REPO)


HOSTILE = [(name, make, _read_text) for name, make in HOSTILE_TEXT.items()] + [
    (name, make, _read_html) for name, make in HOSTILE_HTML.items()
]


@pytest.mark.parametrize("name, make, read", HOSTILE, ids=[h[0] for h in HOSTILE])
def test_a_hostile_text_is_read_well_under_a_generous_floor(name: str, make: Callable, read: Callable) -> None:
    # docs/standards/regex.md: an absolute floor only as a safety net; a quadratic reader blows far past it here.
    payload = make(60_000)
    start = time.perf_counter()
    read(payload)
    assert time.perf_counter() - start < 1.0


def _seconds_per_call(read: Callable, payload: str, repeat: int) -> float:
    samples = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(repeat):
            read(payload)
        samples.append((time.perf_counter() - start) / repeat)
    return statistics.median(samples)


@pytest.mark.parametrize("name, make, read", HOSTILE, ids=[h[0] for h in HOSTILE])
def test_reading_a_hostile_text_grows_linearly_with_its_length(name: str, make: Callable, read: Callable) -> None:
    # docs/standards/regex.md: measure the shape of the curve, not the wall clock. The small input runs many times,
    # so its time per call sits far above the timer's resolution and a noisy host (#72) moves both sides alike. A
    # linear reader grows about 10x on a 10x input; 3x that is allowed; a quadratic one grows 100x.
    small, large = make(2_000), make(20_000)
    per_small = max(_seconds_per_call(read, small, repeat=20), 1e-7)
    per_large = _seconds_per_call(read, large, repeat=2)
    allowed = 3 * len(large) / len(small)
    assert per_large / per_small <= allowed, f"{name}: grew {per_large / per_small:.1f}x (allowed {allowed:.1f}x)"


# --- the pull-request rule -------------------------------------------------------------------------------------


def test_a_pull_request_on_a_blocked_issue_fails_and_names_the_blocker() -> None:
    v = verdict({5: (blocker(1),)}, body="Fixes #5", closing=[5])
    assert not v.ok and v.lines[0] == "#5 is blocked by #1, which is still open."
    assert any(line.startswith("Wait until the blocker is closed as completed") for line in v.lines)
    assert any("within 15 minutes" in line for line in v.lines)


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


@pytest.mark.parametrize("body", ["Fixes #5\nFixes #1", "Fixes #5\nFixes#1", "Fixes #5, #1"])
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
    pull = facts(body=body)
    linked = tool.pull_links(pull, REPO, plain_render)
    v = tool.check_pull_request(None, pull, linked, lambda n: asked.append(n) or (), REPO)
    assert not v.ok and "at most 50" in v.lines[0] and asked == []
    accepted = tool.check_pull_request(COUNTS, pull, linked, lambda n: asked.append(n) or (), REPO)
    assert accepted.ok and asked == []


@pytest.mark.parametrize("kwargs", [{"commit_total": 101}, {"closing": [5], "closing_total": 51}])
def test_lists_longer_than_one_page_fail_instead_of_being_read_in_part(kwargs: Dict[str, Any]) -> None:
    assert not verdict({}, body="Docs only", **kwargs).ok
    assert verdict({}, override=COUNTS, body="Docs only", **kwargs).ok
    ignored = verdict({}, override=IGNORED, body="Docs only", **kwargs)
    assert not ignored.ok and tool.MSG_OVERRIDE_NOT_MAINTAINER in ignored.lines


def test_a_maintainers_override_passes_and_lists_what_it_accepts() -> None:
    v = verdict({5: (blocker(1),)}, override=COUNTS, body="Fixes #5")
    assert v.ok and "set by a maintainer" in v.lines[0] and v.lines[1] == "#5 is blocked by #1, which is still open."


def test_an_override_that_does_not_count_is_ignored_and_says_why() -> None:
    v = verdict({5: (blocker(1),)}, override=IGNORED, body="Fixes #5")
    assert not v.ok and tool.MSG_OVERRIDE_NOT_MAINTAINER in v.lines
    free = verdict({5: ()}, override=IGNORED, body="Fixes #5")
    assert free.ok and tool.MSG_OVERRIDE_NOT_MAINTAINER in free.lines  # said even when nothing is blocked


# --- the override and its record -------------------------------------------------------------------------------


def change(kind: str, login: str, at: str = "2026-10-20T10:00:00Z", label: str = "dependency-override") -> dict:
    """A label change as the GraphQL timeline returns it."""
    typename = "LabeledEvent" if kind == "labeled" else "UnlabeledEvent"
    return {"__typename": typename, "createdAt": at, "actor": {"login": login}, "label": {"name": label}}


def record(
    head: str = SHA, issues: Sequence[int] = (5,), at: str = "2026-10-20T10:00:05Z", user: Optional[dict] = None
) -> dict:
    """A comment holding an override record, as the REST API returns it."""
    marker = tool.OVERRIDE_MARKER.format(head=head, issues=",".join(str(n) for n in issues))
    return {"user": user or BOT, "created_at": at, "body": f"recorded\n\n{marker}"}


def override_of(
    changes: List[dict], comments: List[dict], body: str = "Fixes #5", sha: str = SHA
) -> Optional[tool.Override]:
    gh = FakeGitHub({})
    gh.pull(30, body, labels=["dependency-override"], sha=sha)
    gh.label_changes[30] = changes
    gh.comments[30] = comments
    pull = tool.fetch_pull(gh, REPO, 30)
    return tool.override_status(gh, REPO, pull, tool.pull_links(pull, REPO, plain_render))


MAINTAINER_SET = [change("labeled", "cemililik")]


@pytest.mark.parametrize(
    "changes, why",
    [
        (MAINTAINER_SET, ""),
        ([change("labeled", "CemilIlik")], ""),
        ([change("labeled", "contributor")], tool.MSG_OVERRIDE_NOT_MAINTAINER),
        (
            [
                change("labeled", "cemililik", "2026-10-20T09:00:00Z"),
                change("unlabeled", "x", "2026-10-20T09:30:00Z"),
                change("labeled", "x", "2026-10-20T10:00:00Z"),
            ],
            tool.MSG_OVERRIDE_NOT_MAINTAINER,
        ),
        (
            [change("labeled", "cemililik"), change("unlabeled", "cemililik", "2026-10-20T11:00:00Z")],
            tool.MSG_OVERRIDE_NOT_MAINTAINER,
        ),
        ([change("labeled", "contributor", label="bug"), change("labeled", "cemililik")], ""),
        # out of order: the latest by time decides, not the position in the answer
        (
            [change("labeled", "contributor", "2026-10-21T09:00:00Z"), change("labeled", "cemililik")],
            tool.MSG_OVERRIDE_NOT_MAINTAINER,
        ),
        ([], tool.MSG_OVERRIDE_NOT_MAINTAINER),  # beyond the 100 most recent label changes
    ],
)
def test_the_override_counts_only_when_a_maintainer_set_it_last(changes: List[dict], why: str) -> None:
    status = override_of(changes, [record()])
    assert status is not None and status.counts == (why == "") and status.why == why


@pytest.mark.parametrize(
    "comments, body, counts, why",
    [
        ([record()], "Fixes #5", True, ""),
        ([record(issues=(5, 6))], "Fixes #5", True, ""),  # a link removed since keeps the override
        ([record(issues=(5,)), record(issues=(5, 6), at="2026-10-20T10:01:00Z")], "Fixes #5, #6", True, ""),
        ([record(issues=(5,))], "Fixes #5, #6", False, "also links #6"),
        ([record(head=NEW_SHA)], "Fixes #5", False, "set for commit `bbbbbbb`"),
        ([], "Fixes #5", False, "was not recorded"),
        ([record(at="2026-10-20T09:59:59Z")], "Fixes #5", False, "was not recorded"),  # a record from an earlier label
        ([record(user={"login": "cemililik", "type": "User"})], "Fixes #5", False, "was not recorded"),
        ([record(user={"login": "coderabbitai[bot]", "type": "Bot"})], "Fixes #5", False, "was not recorded"),
    ],
    ids=[
        "recorded",
        "a link removed",
        "the latest record",
        "a new link",
        "a new head",
        "no record",
        "an older record",
        "a person's copy",
        "another app's copy",
    ],
)
def test_the_override_covers_only_the_head_commit_and_the_links_it_recorded(
    comments: List[dict], body: str, counts: bool, why: str
) -> None:
    status = override_of(MAINTAINER_SET, comments, body=body)
    assert status is not None and status.counts == counts and why in status.why


def test_no_label_no_override_and_no_lookup() -> None:
    gh = FakeGitHub({})
    gh.pull(30, "Fixes #5")
    assert tool.override_status(gh, REPO, tool.fetch_pull(gh, REPO, 30), frozenset({5})) is None
    assert [e for _, e, _ in gh.calls] == ["graphql"]  # the pull request itself, nothing more


def event_of(
    number: int = 30,
    head: str = SHA,
    body: str = "Fixes #5",
    title: str = "",
    action: str = "labeled",
    label: str = "dependency-override",
) -> dict:
    """A `pull_request_target` payload for a label being added."""
    return {
        "action": action,
        "label": {"name": label},
        "pull_request": {"number": number, "head": {"sha": head}, "title": title, "body": body},
    }


def overridable(body: str = "Fixes #5", changes: Optional[List[dict]] = None) -> FakeGitHub:
    gh = FakeGitHub({5: {"labels": ["blocked"], "blockers": [(1, "OPEN", None)]}, 6: {"labels": [], "blockers": []}})
    gh.pull(30, body, labels=["dependency-override"])
    gh.label_changes[30] = MAINTAINER_SET if changes is None else changes
    gh.now = "2026-10-20T10:00:07Z"
    return gh


def test_recording_a_maintainers_override_posts_the_head_and_the_links_and_the_check_then_passes() -> None:
    gh = overridable()
    assert tool.run_check_pr({"pull_request": {"number": 30}}, REPO, gh) == 3  # not recorded yet
    assert tool.run_record_override(event_of(), REPO, gh) == 0
    (comment,) = gh.comments[30]
    assert comment["body"].endswith(f"<!-- issue-dependencies:override:{SHA}:5 -->")
    assert "commit `aaaaaaa`" in comment["body"] and "(#5)" in comment["body"]
    assert tool.run_check_pr({"pull_request": {"number": 30}}, REPO, gh) == 0
    assert tool.run_record_override(event_of(), REPO, gh) == 0 and len(gh.comments[30]) == 1  # recorded once


def test_the_record_holds_the_description_as_it_stood_when_the_label_was_set() -> None:
    gh = overridable(body="Fixes #5 and Refs #6")  # edited a moment after the label
    assert tool.run_record_override(event_of(body="Fixes #5"), REPO, gh) == 0
    assert gh.comments[30][0]["body"].endswith(":5 -->")
    gh.issues[6]["blockers"] = [(2, "OPEN", None)]
    assert tool.run_check_pr({"pull_request": {"number": 30}}, REPO, gh) == 3  # #6 was never accepted


@pytest.mark.parametrize(
    "event, changes, why",
    [
        (event_of(head=NEW_SHA), MAINTAINER_SET, "a new head"),
        (event_of(), [change("labeled", "contributor")], "not set by a maintainer"),
        (event_of(label="bug"), MAINTAINER_SET, "nothing to record"),
        (event_of(action="unlabeled"), MAINTAINER_SET, "nothing to record"),
    ],
    ids=["pushed since", "a contributor", "another label", "a removal"],
)
def test_nothing_is_recorded_for_an_override_that_must_not_count(
    event: dict, changes: List[dict], why: str, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = overridable(changes=changes)
    assert tool.run_record_override(event, REPO, gh) == 0
    assert 30 not in gh.comments and why in capsys.readouterr().out


@pytest.mark.parametrize(
    "event",
    [{}, {"pull_request": {"number": 30}}, {"pull_request": {"number": 30, "head": {"sha": "not-a-sha"}}}],
)
def test_record_override_rejects_an_unusable_payload(event: dict) -> None:
    with pytest.raises(tool.EventError):
        tool.run_record_override(event, REPO, FakeGitHub({}))


def test_a_new_commit_ends_the_override_and_the_check_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    gh = overridable()
    tool.run_record_override(event_of(), REPO, gh)
    gh.pull(30, "Fixes #5", labels=["dependency-override"], sha=NEW_SHA)
    assert tool.run_check_pr({"pull_request": {"number": 30}}, REPO, gh) == 3
    assert "no longer counts" in capsys.readouterr().out


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
        self.label_changes: Dict[int, List[Dict[str, Any]]] = {}
        self.rendered: Dict[str, str] = {}  # Markdown -> GitHub's rendering; plain_render otherwise
        self.fail: List[Tuple[str, str]] = []  # (method, endpoint prefix); `query:<name>` for a GraphQL query
        self.issue_page = 100
        self.now = "2026-10-20T12:00:00Z"  # the time of a comment the tool posts

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
        if query == "label_changes":
            kind = "PullRequest" if v["number"] in self.pulls else "Issue"
            nodes = self.label_changes.get(v["number"], [])
            return {
                "data": {"repository": {"issueOrPullRequest": {"__typename": kind, "timelineItems": {"nodes": nodes}}}}
            }
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
        if endpoint == "markdown":
            return self.rendered.get(payload["text"], plain_render(payload["text"]))
        number = int(endpoint.split("/")[4]) if re.match(rf"repos/{REPO}/issues/\d+", endpoint) else None
        if method == "GET" and endpoint.startswith(f"repos/{REPO}/issues?state=closed&labels=blocked"):
            page = int(endpoint.rsplit("page=", 1)[1])
            return [{"number": n} for n in self.closed_labelled][(page - 1) * tool._PAGE_SIZE : page * tool._PAGE_SIZE]
        if method == "GET" and "/comments?" in endpoint:
            page = int(endpoint.rsplit("page=", 1)[1])
            return self.comments.get(number, [])[(page - 1) * tool._PAGE_SIZE : page * tool._PAGE_SIZE]
        if method == "POST" and endpoint.endswith("/labels"):
            self.issues.setdefault(number, {"labels": [], "blockers": []})["labels"] += payload["labels"]
            return []
        if method == "DELETE" and "/labels/" in endpoint:
            if number in self.issues:
                self.issues[number]["labels"].remove("blocked")
            return None
        if method == "POST" and endpoint.endswith("/comments"):
            self.comments.setdefault(number, []).append({"user": BOT, "body": payload["body"], "created_at": self.now})
            return {}
        if method == "GET" and "/check-runs?check_name=Issue%20dependencies" in endpoint:
            return {"check_runs": self.check_runs.get(endpoint.split("/")[4], [])}
        if method == "POST" and re.fullmatch(rf"repos/{REPO}/actions/jobs/\d+/rerun", endpoint):
            return None
        raise AssertionError(f"unexpected call {method} {endpoint}")

    def writes(self) -> List[Tuple[str, str]]:
        return [(m, e) for m, e, _ in self.calls if m in ("POST", "DELETE") and e not in ("graphql", "markdown")]

    def reruns(self) -> List[str]:
        return [e for _, e in self.writes() if e.endswith("/rerun")]


RECENT = "2026-10-20T09:00:00Z"


def run(
    job_id: int,
    conclusion: Optional[str],
    status: str = "completed",
    app: str = "github-actions",
    started: str = RECENT,
    pulls: Sequence[int] = (),
) -> dict:
    return {
        "id": job_id,
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": app},
        "started_at": started,
        "pull_requests": [{"number": n} for n in pulls],
    }


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
    gh.label_changes[6] = [change("labeled", "github-actions", "2026-10-20T08:00:00Z", label="blocked")]
    assert tool.run_sync(REPO, gh) == 0
    assert (
        gh.issues[5]["labels"] == ["blocked"] and gh.issues[6]["labels"] == [] and gh.issues[7]["labels"] == ["blocked"]
    )
    assert set(gh.comments) == {6} and gh.comments[6][0]["body"].startswith("This issue is no longer blocked")
    assert gh.comments[6][0]["body"].endswith("hodetech/forgelm#2@2026-10-20T08:00:00Z -->")
    assert ("DELETE", f"repos/{REPO}/issues/40/labels/blocked") in gh.writes()  # a closed issue waits for nothing
    assert 40 not in gh.comments


def test_an_issue_unblocked_a_second_time_gets_a_second_notice() -> None:
    gh = FakeGitHub({6: {"labels": ["blocked"], "blockers": [(2, "CLOSED", "COMPLETED")]}})
    gh.label_changes[6] = [change("labeled", "github-actions", "2026-10-20T08:00:00Z", label="blocked")]
    assert tool.run_sync(REPO, gh) == 0 and len(gh.comments[6]) == 1
    # The blocker is reopened and closed again: sync labelled the issue anew in between.
    gh.issues[6]["labels"].append("blocked")
    gh.label_changes[6] += [
        change("unlabeled", "github-actions", "2026-10-20T08:15:00Z", label="blocked"),
        change("labeled", "github-actions", "2026-10-20T09:00:00Z", label="blocked"),
    ]
    assert tool.run_sync(REPO, gh) == 0 and len(gh.comments[6]) == 2
    assert tool.run_sync(REPO, gh) == 0 and len(gh.comments[6]) == 2


def test_every_page_of_open_issues_is_read() -> None:
    gh = FakeGitHub({n: {"labels": [], "blockers": [(1, "OPEN", None)]} for n in range(10, 15)})
    gh.issue_page = 2
    assert tool.run_sync(REPO, gh) == 0
    assert all(gh.issues[n]["labels"] == ["blocked"] for n in range(10, 15))


def test_a_dry_run_reads_but_never_writes(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({5: {"labels": [], "blockers": [(1, "OPEN", None)]}, 6: {"labels": ["blocked"], "blockers": []}})
    gh.pull(30, "Fixes #5", labels=["blocked"])
    gh.check_runs[SHA] = [run(77, "success")]
    assert tool.run_sync(REPO, gh, dry_run=True) == 0
    out = capsys.readouterr().out
    assert gh.writes() == [] and "would label #5 blocked" in out and "would re-run" in out
    assert "would unlabel pull request #30 blocked" in out


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


def test_a_cancelled_blocker_is_flagged_once_and_a_copy_of_the_marker_by_anyone_else_does_not_silence_it() -> None:
    gh = FakeGitHub({5: {"labels": ["blocked"], "blockers": [(1, "CLOSED", "NOT_PLANNED")]}})
    marker = tool.NOT_COMPLETED_MARKER.format(blocker="hodetech/forgelm#1")
    gh.comments[5] = [
        {"user": {"login": "someone", "type": "User"}, "body": marker},
        {"user": {"login": "coderabbitai[bot]", "type": "Bot"}, "body": marker},  # an app repeating someone's words
    ]
    assert tool.run_sync(REPO, gh) == 0
    assert gh.issues[5]["labels"] == ["blocked", "needs-triage"] and len(gh.comments[5]) == 3
    assert tool.run_sync(REPO, gh) == 0  # the next run finds the bot's notice and stays quiet
    assert len(gh.comments[5]) == 3 and gh.issues[5]["labels"].count("needs-triage") == 1


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
    gh.comments[5] = chat + [{"user": BOT, "body": "<!-- x -->"}]
    gh.comments[6] = chat
    assert tool._already_noticed(gh, REPO, 5, "<!-- x -->") is True
    assert tool._already_noticed(gh, REPO, 6, "<!-- x -->") is False


def test_a_blocked_label_on_a_pull_request_is_removed_open_or_closed() -> None:
    gh = FakeGitHub({}, closed_labelled=(41,))  # the closed list holds pull requests too
    gh.pull(30, "Docs only", labels=["blocked"])
    assert tool.run_sync(REPO, gh) == 0
    assert {e for _, e in gh.writes()} == {
        f"repos/{REPO}/issues/30/labels/blocked",
        f"repos/{REPO}/issues/41/labels/blocked",
    }
    assert gh.comments == {}


def test_a_list_that_fills_its_last_page_exactly_costs_no_extra_request() -> None:
    asked: List[str] = []

    def gh(method: str, endpoint: str, payload: Any = None) -> Any:
        asked.append(endpoint)
        page = tool.Page({"number": n} for n in range(tool._PAGE_SIZE))
        page.has_next = endpoint.endswith("page=1")  # GitHub's `Link: rel="next"`
        return page

    assert len(tool.fetch_closed_labelled(gh, REPO)) == 2 * tool._PAGE_SIZE and len(asked) == 2


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
    gh.label_changes[6] = [change("labeled", "github-actions", label="blocked")]
    gh.pull(30, "Fixes #6")
    gh.check_runs[SHA] = [run(70, "failure"), last]  # the latest run is the one with the highest id
    assert tool.run_sync(REPO, gh) == 0
    assert gh.reruns() == ([f"repos/{REPO}/actions/jobs/77/rerun"] if rerun else [])


def test_on_a_head_commit_two_pull_requests_share_the_run_github_ties_to_this_one_is_compared() -> None:
    gh = FakeGitHub({6: {"labels": [], "blockers": [(2, "CLOSED", "COMPLETED")]}})
    gh.pull(30, "Fixes #6")
    gh.check_runs[SHA] = [run(70, "failure", pulls=[30]), run(77, "success", pulls=[31])]
    assert tool.run_sync(REPO, gh) == 0 and gh.reruns() == [f"repos/{REPO}/actions/jobs/70/rerun"]
    gh.calls.clear()
    gh.check_runs[SHA] = [run(70, "failure"), run(77, "success")]  # a fork's runs name no pull request
    assert tool.run_sync(REPO, gh) == 0 and gh.reruns() == []


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


def test_the_reconciliation_reads_the_override_as_the_check_does() -> None:
    gh = overridable()
    tool.run_record_override(event_of(), REPO, gh)
    gh.check_runs[SHA] = [run(77, "failure")]  # red from before the record
    assert tool.run_sync(REPO, gh) == 0 and gh.reruns() == [f"repos/{REPO}/actions/jobs/77/rerun"]


def test_a_failing_rerun_is_a_warning_not_a_failed_sync(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({6: {"labels": ["blocked"], "blockers": [(2, "CLOSED", "COMPLETED")]}})
    gh.pull(30, "Fixes #6")
    gh.check_runs[SHA] = [run(77, "failure")]
    gh.fail = [("POST", f"repos/{REPO}/actions/jobs/77/rerun")]  # the listing works; only the re-run fails
    assert tool.run_sync(REPO, gh) == 0
    assert gh.issues[6]["labels"] == [] and "warning: could not reconcile" in capsys.readouterr().err
    assert ("POST", f"repos/{REPO}/actions/jobs/77/rerun") in [(m, e) for m, e, _ in gh.calls]


def test_a_rendering_that_fails_is_a_warning_and_the_label_cleanup_still_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGitHub({})
    gh.pull(30, "Fixes #6", labels=["blocked"])
    gh.fail = [("POST", "markdown")]
    assert tool.run_sync(REPO, gh) == 0
    assert "warning: could not reconcile" in capsys.readouterr().err
    assert ("DELETE", f"repos/{REPO}/issues/30/labels/blocked") in gh.writes()


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
    gh.pull(30, "Docs only")
    assert tool.run_check_pr({"pull_request": {"number": 30, "body": "Fixes #5"}}, REPO, gh) == 0


def test_check_pr_reads_the_description_through_githubs_rendering() -> None:
    gh = FakeGitHub({16: {"labels": [], "blockers": [(1, "OPEN", None)]}, 9: {"labels": [], "blockers": []}})
    body = "- item\n  ```\n  Refs #16 in a list fence\n  ```\n\nRefs #9"
    gh.rendered[body] = RENDERS[body]
    gh.pull(30, body)
    assert tool.run_check_pr({"pull_request": {"number": 30}}, REPO, gh) == 0  # #16 sits in code
    assert ("POST", "markdown", {"text": body, "mode": "gfm", "context": REPO}) in gh.calls


def test_check_pr_ignores_an_override_label_a_contributor_set(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub({5: {"labels": [], "blockers": [(1, "OPEN", None)]}})
    gh.pull(30, "Fixes #5", labels=["dependency-override"])
    gh.label_changes[30] = [change("labeled", "contributor")]
    gh.comments[30] = [record()]
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
    assert tool.main(["record-override"], gh) == 2
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": "30"}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert tool.main(["check-pr"], gh) == 2
    assert tool.main(["record-override"], gh) == 2
    event.write_text("[1, 2]", encoding="utf-8")
    assert tool.main(["check-pr"], gh) == 2
    event.write_text("{not json", encoding="utf-8")
    assert tool.main(["check-pr"], gh) == 2
    assert tool.main(["frobnicate"], gh) == 2
    assert tool.main(["--help"], gh) == 0
    assert gh.calls == []


def test_main_runs_record_override_from_the_event_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gh = overridable()
    event = tmp_path / "event.json"
    event.write_text(json.dumps(event_of()), encoding="utf-8")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert tool.main(["record-override"], gh) == 0 and len(gh.comments[30]) == 1
    assert tool.main(["check-pr"], gh) == 0


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
        ("GET", "markdown", {"text": "x", "mode": "gfm", "context": REPO}),
        ("POST", "markdown", {"text": "x", "mode": "markdown", "context": REPO}),
        ("POST", "markdown", {"text": "x", "mode": "gfm"}),
        ("POST", "markdown", {"text": "x", "mode": "gfm", "context": "not a repository"}),
        ("POST", "markdown", {"text": 1, "mode": "gfm", "context": REPO}),
    ],
)
def test_gh_api_refuses_anything_outside_the_fixed_endpoints(method: str, endpoint: str, payload: Any) -> None:
    with pytest.raises(tool.GhError, match="refusing"):
        tool.gh_api(method, endpoint, payload)


class Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def response(status: int = 200, body: str = "[]", headers: Sequence[Tuple[str, str]] = ()) -> str:
    """``gh api --include`` output: a status line, CRLF headers, a blank line and the body."""
    return f"HTTP/2.0 {status} X\n" + "".join(f"{k}: {v}\r\n" for k, v in headers) + "\r\n" + body


class Runs:
    """Stands in for ``subprocess.run``: answers in turn, and records each command and every wait."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *answers: Any) -> None:
        self.answers, self.commands, self.waits = list(answers), [], []
        monkeypatch.setattr(tool.subprocess, "run", self)
        monkeypatch.setattr(tool, "_sleep", self.waits.append)

    def __call__(self, command: List[str], **kwargs: Any) -> Proc:
        assert "shell" not in kwargs
        self.commands.append(command)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def test_gh_api_runs_a_list_command_without_a_shell_and_reads_the_link_header(monkeypatch: pytest.MonkeyPatch) -> None:
    link = ("Link", '<https://api.github.com/repositories/1/issues?page=2>; rel="next", <...>; rel="last"')
    runs = Runs(monkeypatch, Proc(0, response(body="[1]", headers=[link])), Proc(0, response(body="[2]")))
    first, last = tool.gh_api("GET", f"repos/{REPO}/issues?page=1"), tool.gh_api("GET", f"repos/{REPO}/issues?page=2")
    assert first == [1] and first.has_next and last == [2] and not last.has_next
    assert runs.commands[0] == ["gh", "api", "--include", "--method", "GET", f"repos/{REPO}/issues?page=1"]


def test_gh_api_returns_githubs_rendering_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    Runs(monkeypatch, Proc(0, response(body="<p>Refs #1</p>\n", headers=[("Content-Type", "text/html")])))
    assert tool.gh_api("POST", "markdown", {"text": "Refs #1", "mode": "gfm", "context": REPO}) == "<p>Refs #1</p>\n"


@pytest.mark.parametrize(
    "first, wait",
    [
        (Proc(1, response(502, "<html>"), "gh: HTTP 502"), 2.0),
        (Proc(1, "", "error connecting to api.github.com"), 2.0),  # no answer at all
        (Proc(1, response(429, "{}", [("Retry-After", "5")]), "gh: HTTP 429"), 5.0),
        (Proc(1, response(403, "{}", [("Retry-After", "7")]), "gh: secondary rate limit"), 7.0),
        (Proc(1, response(403, "{}", [("X-RateLimit-Remaining", "0"), ("X-RateLimit-Reset", "1792497610")]), ""), 11.0),
        (subprocess.TimeoutExpired("gh", 60), 2.0),
    ],
    ids=["a 5xx", "no answer", "Retry-After", "a secondary limit", "the rate-limit reset", "a timeout"],
)
def test_gh_api_tries_a_read_again_after_a_transient_failure(
    monkeypatch: pytest.MonkeyPatch, first: Any, wait: float
) -> None:
    runs = Runs(monkeypatch, first, Proc(0, response(body="{}")))
    assert tool.gh_api("GET", f"repos/{REPO}/issues/1") == {} and runs.waits == [wait]


def test_gh_api_backs_off_and_gives_up_after_the_last_try(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = Runs(monkeypatch, *[Proc(1, response(503, ""), "gh: HTTP 503")] * tool._ATTEMPTS)
    with pytest.raises(tool.GhError, match="HTTP 503"):
        tool.gh_api("GET", f"repos/{REPO}/issues/1")
    assert runs.waits == [2.0, 4.0]


@pytest.mark.parametrize(
    "method, endpoint, payload, answer",
    [
        ("POST", f"repos/{REPO}/issues/1/comments", {"body": "x"}, Proc(1, response(502, ""), "gh: HTTP 502")),
        ("GET", f"repos/{REPO}/issues/1", None, Proc(1, response(404, "{}"), "gh: Not Found (HTTP 404)")),
        ("GET", f"repos/{REPO}/issues/1", None, Proc(1, response(403, "{}"), "gh: Resource not accessible")),
        ("GET", f"repos/{REPO}/issues/1", None, Proc(1, response(429, "{}", [("Retry-After", "600")]), "")),
    ],
    ids=["a write", "a 404", "a plain 403", "a wait too long"],
)
def test_gh_api_never_repeats_a_write_nor_a_failure_that_is_not_transient(
    monkeypatch: pytest.MonkeyPatch, method: str, endpoint: str, payload: Any, answer: Proc
) -> None:
    runs = Runs(monkeypatch, answer)
    with pytest.raises(tool.GhError):
        tool.gh_api(method, endpoint, payload)
    assert len(runs.commands) == 1 and runs.waits == []


def test_gh_api_failure_text_is_flattened_and_defanged(monkeypatch: pytest.MonkeyPatch) -> None:
    Runs(monkeypatch, *[Proc(1, "", "bad\n::error::injected")] * tool._ATTEMPTS)
    with pytest.raises(tool.GhError) as info:
        tool.gh_api("GET", f"repos/{REPO}/issues/1")
    assert "\n" not in str(info.value) and "::error::" not in str(info.value)


def test_clean_strips_terminal_escapes_and_invisible_characters() -> None:
    cleaned = tool._clean("\x1b[31mred\x1b[0m \u202eevil\u0007\n::error::x")
    assert cleaned == "red evil : :error: :x"
    assert assign_on_comment._clean("\x1b[1mbold\x1b[0m\u202e") == "bold"


def test_gh_api_hands_a_graphql_answer_with_errors_to_the_caller_but_not_a_rest_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `gh` exits 1 for a GraphQL answer with errors and prints the answer itself; graphql() decides what it accepts.
    answer = {"data": {"repository": {"issueOrPullRequest": None}}, "errors": [NOT_FOUND]}
    Runs(monkeypatch, Proc(1, response(body=json.dumps(answer)), "gh: Could not resolve"))
    assert tool.gh_api("POST", "graphql", {"query": tool.QUERIES["issue"], "variables": {}}) == answer
    Runs(monkeypatch, Proc(1, response(404, json.dumps({"errors": []})), "gh: Not Found"))
    with pytest.raises(tool.GhError):
        tool.gh_api("GET", f"repos/{REPO}/issues/1")


@pytest.mark.parametrize(
    "stdout, status, headers, body",
    [
        (response(201, '{"a": 1}', [("Link", "x"), ("X-A", "b: c")]), 201, {"link": "x", "x-a": "b: c"}, '{"a": 1}'),
        ('{"a": 1}', None, {}, '{"a": 1}'),  # no status line: gh never got an answer
        ("HTTP/2.0 200 OK\nno blank line", None, {}, "HTTP/2.0 200 OK\nno blank line"),
        ("HTTP/2.0 abc\n\n{}", None, {}, "HTTP/2.0 abc\n\n{}"),
    ],
)
def test_split_response(stdout: str, status: Optional[int], headers: dict, body: str) -> None:
    assert tool._split_response(stdout) == (status, headers, body)


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


@pytest.mark.parametrize(
    "item",
    [
        {"__typename": "LabeledEvent", "createdAt": "yesterday", "label": {"name": "blocked"}},
        {"label": {"name": "blocked"}},
    ],
)
def test_a_label_change_without_a_well_formed_time_is_an_error(item: dict) -> None:
    gh = FakeGitHub({6: {"labels": [], "blockers": []}})
    gh.label_changes[6] = [item]
    with pytest.raises(tool.GhError):
        tool.fetch_label_changes(gh, REPO, 6, "blocked")


def test_the_maintainer_list_matches_the_assign_tool() -> None:
    assert tool.MAINTAINER_LOGINS == assign_on_comment.MAINTAINER_LOGINS  # that one is checked against MAINTAINERS.md


def test_the_links_in_the_notices_resolve_to_contributing_headings_on_the_default_branch() -> None:
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    headings = {
        re.sub(r"[^\w\- ]", "", h).strip().lower().replace(" ", "-") for h in re.findall(r"^#+ (.+)$", text, re.M)
    }
    for anchor in ("dependencies-between-issues", "working-on-an-issue"):
        assert anchor in headings
        assert tool.contributing_url(REPO, anchor) == f"https://github.com/{REPO}/blob/HEAD/CONTRIBUTING.md#{anchor}"


# --- the workflow files ----------------------------------------------------------------------------------------


def load(name: str) -> Dict[Any, Any]:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name, job",
    [
        ("issue-dependencies.yml", "sync"),
        ("pr-dependencies.yml", "record-override"),
        ("pr-dependencies.yml", "issue-dependencies"),
    ],
)
def test_two_steps_a_pinned_minimal_checkout_and_no_expression_in_a_shell(name: str, job: str) -> None:
    steps = load(name)["jobs"][job]["steps"]
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
        "schedule": [{"cron": "2,17,32,47 * * * *"}],  # every 15 minutes, off the hour
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
    assert job["steps"][1]["run"] == "python3 tools/issue_dependencies.py sync"


def test_the_pull_request_check_takes_its_definition_from_the_default_branch_and_never_runs_pr_code() -> None:
    wf = load("pr-dependencies.yml")
    text = (ROOT / ".github/workflows/pr-dependencies.yml").read_text(encoding="utf-8")
    # `pull_request` would take this file from the pull request, which could then rewrite the check that judges it.
    assert list(wf[True]) == ["pull_request_target"]
    assert wf[True]["pull_request_target"] == {
        "types": ["opened", "edited", "reopened", "synchronize", "ready_for_review", "labeled", "unlabeled"]
    }  # no paths/branches filter: a required check must always report
    assert wf["permissions"] == {} and "concurrency" not in wf  # the recorder must never be cancelled
    assert list(wf["jobs"]) == ["record-override", "issue-dependencies"]
    recorder, check = wf["jobs"]["record-override"], wf["jobs"]["issue-dependencies"]
    assert recorder["name"] == "Record dependency override"
    assert recorder["if"] == (
        f"${{{{ github.event.action == 'labeled' && github.event.label.name == '{tool.OVERRIDE_LABEL}' }}}}"
    )
    assert recorder["permissions"] == {"contents": "read", "pull-requests": "write"}  # the only write: a comment
    assert "concurrency" not in recorder
    assert recorder["steps"][1]["run"] == "python3 tools/issue_dependencies.py record-override"
    assert check["name"] == tool.CHECK_NAME == "Issue dependencies"  # the required check's context
    assert check["needs"] == "record-override" and check["if"] == "${{ !cancelled() }}"
    assert check["concurrency"] == {
        "group": "pr-dependencies-${{ github.event.pull_request.number }}",
        "cancel-in-progress": True,
    }
    assert check["permissions"] == {"contents": "read", "issues": "read", "pull-requests": "read"}
    assert check["steps"][1]["run"] == "python3 tools/issue_dependencies.py check-pr"
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())  # the YAML without its comments
    # Only the event's action and label (the recorder's `if:`) and the pull request's number (concurrency).
    assert "head" not in code and code.count("github.event") == 3
    assert code.count("secrets.") == 2  # GITHUB_TOKEN only


def test_no_other_workflow_has_a_job_named_like_the_check() -> None:
    # Branch protection cannot tell two Actions checks of one name apart; keep the name unique in this repository.
    owners = [
        path.name
        for path in sorted((ROOT / ".github/workflows").glob("*.yml"))
        for job in (yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs") or {}).values()
        if isinstance(job, dict) and job.get("name") == tool.CHECK_NAME
    ]
    assert owners == ["pr-dependencies.yml"]
