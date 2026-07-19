"""Tests for tools/check_deprecation_targets.py.

The guard reads ``forgelm.config.DEPRECATION_REMOVAL_VERSION`` as the single
source of truth for when the deprecated YAML fields (``lora.use_dora``,
``lora.use_rslora``, ``training.sample_packing``) disappear, then fails on any
file in the public tree that names a different removal version — and on a
target that has already been reached by the shipping ``pyproject.toml``
version (the promise would be retroactively false).

Detection logic is pinned against synthetic in-memory fixtures so it stays
independent of the real, evolving corpus: a matching claim, a stale claim, a
version with no removal language, removal language with no deprecated field
nearby, the Turkish ``kaldır*`` wording, prose that wraps the removal sentence
across two physical lines, and the ``deprecation-target-ok`` opt-out. A
separate live-repo class asserts the invariant CI actually relies on —
``main(["--strict", "--quiet"]) == 0`` — mirroring
``tests/test_check_usermanual_schema_drift.py``.
"""

from __future__ import annotations

import importlib.util
import statistics
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "check_deprecation_targets.py"

# Built by concatenation so this file never contains a literal that the guard
# would have to exempt if its own self-exclusion were ever removed.
_STALE = "v0" + ".9.0"
_CANON = "v1" + ".0.0"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_deprecation_targets", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_deprecation_targets"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


class TestReadCanonicalVersion:
    def test_reads_the_constant_from_a_synthetic_config(self, tool, tmp_path):
        config = tmp_path / "config.py"
        config.write_text(f'{tool.CANONICAL_CONSTANT} = "{_CANON}"\n', encoding="utf-8")
        assert tool.read_canonical_version(config) == _CANON

    def test_missing_constant_is_a_hard_error(self, tool, tmp_path):
        config = tmp_path / "config.py"
        config.write_text("SOMETHING_ELSE = 1\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            tool.read_canonical_version(config)

    def test_non_literal_constant_is_a_hard_error(self, tool, tmp_path):
        # A computed value cannot be read without importing, which would defeat
        # the no-runtime-dependency AST approach — fail loudly rather than skip.
        config = tmp_path / "config.py"
        config.write_text(f'{tool.CANONICAL_CONSTANT} = "v" + str(1)\n', encoding="utf-8")
        with pytest.raises(SystemExit):
            tool.read_canonical_version(config)

    def test_reads_an_annotated_assignment(self, tool, tmp_path):
        # forgelm/config.py already annotates sibling module constants
        # (`_STRICT_RISK_TIERS: frozenset[str] = ...`), so a routine style pass
        # adding `: str` here must not make the guard silently unable to find
        # its own source of truth.
        config = tmp_path / "config.py"
        config.write_text(f'{tool.CANONICAL_CONSTANT}: str = "{_CANON}"\n', encoding="utf-8")
        assert tool.read_canonical_version(config) == _CANON

    def test_bare_annotation_without_a_value_is_a_hard_error(self, tool, tmp_path):
        # `X: str` declares nothing readable — fail loudly rather than fall
        # through to the "not found" branch with a misleading message.
        config = tmp_path / "config.py"
        config.write_text(f"{tool.CANONICAL_CONSTANT}: str\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            tool.read_canonical_version(config)

    def test_reads_the_real_config_py(self, tool):
        canonical = tool.read_canonical_version()
        assert canonical.startswith("v")


class TestTargetIsStillInTheFuture:
    def test_target_ahead_of_shipping_version_passes(self, tool):
        assert tool.target_is_still_in_the_future(_CANON, "0.9.1rc1") is True

    def test_target_equal_to_shipping_version_fails(self, tool):
        assert tool.target_is_still_in_the_future(_CANON, "1.0.0") is False

    def test_target_behind_shipping_version_fails(self, tool):
        assert tool.target_is_still_in_the_future(_CANON, "1.2.0") is False

    def test_comparison_is_semantic_not_lexical(self, tool):
        # "v1.10.0" < "v1.2.0" as strings; Version() must order them correctly.
        assert tool.target_is_still_in_the_future("v1.10.0", "1.2.0") is True

    def test_prerelease_shipping_version_is_below_its_final(self, tool):
        assert tool.target_is_still_in_the_future(_CANON, "1.0.0rc1") is True

    def test_unparseable_version_is_a_hard_error(self, tool):
        with pytest.raises(SystemExit):
            tool.target_is_still_in_the_future("vNOPE", "0.9.1rc1")


class TestScanText:
    def _scan(self, tool, text):
        return tool.scan_text(text, Path("synthetic.md"))

    def test_same_line_claim_is_captured(self, tool):
        claims = self._scan(tool, f"`use_dora` is deprecated; it will be removed in {_STALE}.\n")
        assert [c.version for c in claims] == [_STALE]
        assert claims[0].line == 1

    def test_canonical_claim_is_captured_too(self, tool):
        # scan_text reports every claim; main() decides which diverge.
        claims = self._scan(tool, f"`sample_packing` will be removed in {_CANON}.\n")
        assert [c.version for c in claims] == [_CANON]

    def test_version_without_removal_language_is_ignored(self, tool):
        assert self._scan(tool, f"`use_dora` was added in {_STALE}.\n") == []

    def test_removal_language_without_a_deprecated_field_is_ignored(self, tool):
        assert self._scan(tool, f"The `--data-audit` flag is removed in {_STALE}.\n") == []

    def test_field_within_the_window_attaches(self, tool):
        text = f"The `use_rslora` boolean is a shortcut.\n\nIt will be removed in {_STALE}.\n"
        claims = self._scan(tool, text)
        assert [c.version for c in claims] == [_STALE]
        assert claims[0].line == 3

    def test_field_outside_the_window_does_not_attach(self, tool):
        text = "`use_dora` is a shortcut.\n" + "filler\n" * 5 + f"It will be removed in {_STALE}.\n"
        assert self._scan(tool, text) == []

    def test_turkish_removal_wording_is_detected(self, tool):
        claims = self._scan(tool, f"`sample_packing` {_STALE}'da kaldırılır.\n")
        assert [c.version for c in claims] == [_STALE]

    def test_ignore_marker_suppresses_the_line(self, tool):
        text = f"`use_dora` was removed in {_STALE}. <!-- {tool._IGNORE_MARKER} historical -->\n"
        assert self._scan(tool, text) == []

    def test_multiple_versions_on_one_line_are_all_reported(self, tool):
        claims = self._scan(tool, f"`use_dora` removal moved from {_STALE} to {_CANON}.\n")
        assert [c.version for c in claims] == [_STALE, _CANON]

    def test_excerpt_and_path_are_carried_for_the_report(self, tool):
        line = f"`use_dora` will be removed in {_STALE}."
        claims = tool.scan_text(line + "\n", Path("docs/x.md"))
        assert claims[0].excerpt == line
        assert claims[0].path == Path("docs/x.md")

    def test_wrapped_prose_version_then_removal_verb_on_the_next_line(self, tool):
        # THE regression case. docs/guides/troubleshooting-tr.md wraps exactly
        # here: version at the end of one physical line, removal verb at the
        # start of the next. The original same-line pairing found ZERO claims
        # in that whole file, so a future retarget could have left it stale
        # with --strict CI still green.
        text = f"Boolean flag'lar `lora.use_dora` ve `lora.use_rslora` deprecated — {_STALE}'da\nkaldırılacaklar.\n"
        claims = self._scan(tool, text)
        assert [c.version for c in claims] == [_STALE]
        # Reported at the line carrying the version, which is what a reader
        # has to edit to fix the drift.
        assert claims[0].line == 1

    def test_wrapped_prose_removal_verb_then_version_on_the_next_line(self, tool):
        # The mirror wrap: the sentence breaks before the version instead.
        text = f"`sample_packing` is deprecated and will be removed\nin {_STALE}.\n"
        claims = self._scan(tool, text)
        assert [c.version for c in claims] == [_STALE]
        assert claims[0].line == 2

    def test_version_outside_the_removal_window_does_not_pair(self, tool):
        # A version from a neighbouring paragraph must not be dragged into a
        # removal sentence — the window is tolerance for wrapping, not a
        # licence to pair anything in the file.
        text = "`use_dora` will be removed.\n" + "filler\n" * 5 + f"Unrelated: {_STALE}.\n"
        assert self._scan(tool, text) == []

    def test_ignore_marker_on_the_removal_line_suppresses_a_wrapped_claim(self, tool):
        # The opt-out has to survive the wrap too, or the marker becomes
        # unusable on exactly the prose that needed the window.
        text = f"`use_dora` deprecated — {_STALE}'da\nkaldırılacaklar. <!-- {tool._IGNORE_MARKER} historical -->\n"
        assert self._scan(tool, text) == []

    def test_f_string_interpolated_message_names_no_literal(self, tool):
        # This is the shape forgelm/config.py now uses — the version is
        # interpolated from the constant, so there is nothing to drift.
        source = 'raise ValueError(f"use_dora will be removed in {DEPRECATION_REMOVAL_VERSION}.")\n'
        assert self._scan(tool, source) == []


class TestVersionTokenShapes:
    """The token regex must accept every spelling that occurs in the corpus.

    A shape the regex misses is a silent false negative: the claim is simply
    never checked, and ``--strict`` stays green while the promise rots.
    """

    def _versions(self, tool, fragment: str) -> list[str]:
        text = f"`use_dora` will be removed in {fragment}.\n"
        return [c.version for c in tool.scan_text(text, Path("synthetic.md"))]

    @pytest.mark.parametrize(
        "fragment",
        [
            "v0" + ".9.0",  # lowercase prefix, three segments (the classic form)
            "V0" + ".9.0",  # uppercase prefix — case-insensitive
            "0" + ".9.0",  # unprefixed: the natural copy-paste from pyproject
            "v0" + ".9",  # prefixed two-segment MAJOR.MINOR
            "v0" + ".9.0rc1",  # release candidate
            "v0" + ".9.0RC1",  # release candidate, uppercased
        ],
    )
    def test_accepted_shapes(self, tool, fragment):
        assert self._versions(tool, fragment) == [fragment]

    def test_bare_two_segment_number_is_not_a_version(self, tool):
        # An unprefixed MAJOR.MINOR is indistinguishable from a decimal. Config
        # tables document `neftune_noise_alpha` as `5.0` two lines from a
        # sample_packing deprecation row; treating that as a version made the
        # guard report four bogus divergent claims on the real tree.
        assert self._versions(tool, "5.0") == []

    def test_version_embedded_in_a_word_is_not_matched(self, tool):
        assert self._versions(tool, "abc1.0.0") == []


class TestClaimMatchesCanonical:
    """Divergence is about the promised *release*, not its spelling."""

    @pytest.mark.parametrize("claimed", ["v1" + ".0.0", "V1" + ".0.0", "1" + ".0.0", "v1" + ".0"])
    def test_equivalent_spellings_agree(self, tool, claimed):
        assert tool.claim_matches_canonical(claimed, _CANON) is True

    @pytest.mark.parametrize("claimed", [_STALE, "v1" + ".0.1", "v2" + ".0.0", "v1" + ".0.0rc1"])
    def test_different_releases_diverge(self, tool, claimed):
        assert tool.claim_matches_canonical(claimed, _CANON) is False

    def test_unparseable_token_falls_back_to_a_normalised_string_compare(self, tool):
        assert tool.claim_matches_canonical("vNOPE", "vNOPE") is True
        assert tool.claim_matches_canonical("vNOPE", _CANON) is False


class TestVersionRegexLinearity:
    """ReDoS regression pin for ``_VERSION_RE`` (docs/standards/regex.md).

    Every quantifier in the pattern is bounded and no two of them can consume
    the same character, so a scan is O(n) by construction. Measured at 1K/5K/10K
    with a median of 5 runs per the standard's methodology; the assertion is a
    generous absolute safety floor, since a genuine ReDoS blows past it by
    orders of magnitude rather than by a few percent.
    """

    def test_pathological_input_scans_in_linear_time(self, tool):
        for size in (1_000, 5_000, 10_000):
            # Adversarial: endless near-miss version prefixes that force the
            # engine to start (and abandon) a match at every position.
            payload = "v1." * size
            timings = []
            for _ in range(5):
                start = time.perf_counter()
                tool._VERSION_RE.findall(payload)
                timings.append(time.perf_counter() - start)
            median = statistics.median(timings)
            assert median < 1.0, f"n={size}: {median * 1000:.1f} ms — possible ReDoS in _VERSION_RE"


class TestFileSelection:
    def test_changelog_is_excluded(self, tool):
        assert tool._is_excluded(Path("CHANGELOG.md")) is True

    def test_analysis_working_memory_is_excluded(self, tool):
        assert tool._is_excluded(Path("docs/analysis/review-notes.md")) is True

    def test_marketing_working_memory_is_excluded(self, tool):
        assert tool._is_excluded(Path("docs/marketing/strategy/x.md")) is True

    def test_guard_and_its_own_test_are_excluded(self, tool):
        assert tool._is_excluded(Path("tools/check_deprecation_targets.py")) is True
        assert tool._is_excluded(Path("tests/test_check_deprecation_targets.py")) is True

    def test_agent_guidance_mirrors_are_excluded(self, tool):
        # CLAUDE.md / AGENTS.md narrate the repo's own review history
        # ("post-v0.9.0 Opus review") right next to the deprecated field names,
        # which is the CHANGELOG.md rationale, not a removal promise to an
        # operator. Deliberate omission — pinned so it stays a decision rather
        # than drifting back in unnoticed.
        assert tool._is_excluded(Path("CLAUDE.md")) is True
        assert tool._is_excluded(Path("AGENTS.md")) is True

    def test_normal_doc_is_not_excluded(self, tool):
        assert tool._is_excluded(Path("docs/reference/configuration.md")) is False

    def test_public_root_markdown_is_not_excluded(self, tool):
        assert tool._is_excluded(Path("README.md")) is False
        assert tool._is_excluded(Path("CONTRIBUTING.md")) is False

    def test_target_files_cover_the_documented_scope(self, tool):
        rels = {p.relative_to(_REPO_ROOT) for p in tool.iter_target_files()}
        assert Path("config_template.yaml") in rels
        assert Path("forgelm/config.py") in rels
        assert Path("docs/reference/configuration.md") in rels
        assert Path("tests/test_config.py") in rels
        assert Path("CHANGELOG.md") not in rels
        assert Path("tests/test_check_deprecation_targets.py") not in rels

    def test_repo_root_markdown_is_scanned(self, tool):
        # README.md states the product's public claims; leaving repo-root docs
        # out of the scan set was a hole a stale removal promise could sit in.
        rels = {p.relative_to(_REPO_ROOT) for p in tool.iter_target_files()}
        assert Path("README.md") in rels
        assert Path("CONTRIBUTING.md") in rels
        assert Path("CLAUDE.md") not in rels
        assert Path("AGENTS.md") not in rels


class TestRealRepo:
    """Pins the invariant CI enforces: ci.yml's validate job runs this guard
    with ``--strict``, so a pytest run alone must also fail if a removal-version
    claim drifts from the canonical constant."""

    def test_strict_run_is_clean(self, tool):
        assert tool.main(["--strict", "--quiet"]) == 0

    def test_advisory_run_exits_zero(self, tool):
        assert tool.main(["--quiet"]) == 0

    def test_real_corpus_has_claims_to_check(self, tool):
        # A guard that silently matches nothing is dead enforcement — pin a
        # floor so a regex/scope regression cannot make it vacuously green.
        assert len(tool.collect_claims()) >= 10

    def test_canonical_constant_matches_the_config_module(self, tool):
        from forgelm.config import DEPRECATION_REMOVAL_VERSION

        assert tool.read_canonical_version() == DEPRECATION_REMOVAL_VERSION

    def test_wrapped_turkish_troubleshooting_claim_is_detected(self, tool):
        """The concrete false negative this guard's windowing was fixed for.

        ``docs/guides/troubleshooting-tr.md`` states the removal promise across
        a line break. Under the original same-line pairing the entire file
        yielded zero claims, so a retarget could silently leave it stale. This
        asserts the file is genuinely under enforcement — not merely that the
        overall claim count went up somewhere.
        """
        page = _REPO_ROOT / "docs" / "guides" / "troubleshooting-tr.md"
        claims = tool.scan_text(page.read_text(encoding="utf-8"), page)
        assert claims, "troubleshooting-tr.md states a removal version but yields no claim"
        canonical = tool.read_canonical_version()
        for claim in claims:
            assert tool.claim_matches_canonical(claim.version, canonical)

    def test_guard_wired_into_ci(self):
        # Assert the exact invocation, not just the filename: a step that runs
        # the guard without --strict reports drift and still exits 0, which is
        # the fake-green failure mode the guard exists to prevent.
        ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "python3 tools/check_deprecation_targets.py --strict" in ci
