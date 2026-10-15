"""Phase 12.5 — data curation polish backlog.

Covers the four follow-up items from
:mod:`docs/roadmap/completed-phases.md`:

1. ``forgelm ingest --all-mask`` composite shorthand (this file).
2. Wizard "audit first" entry point (this file).
3. ``forgelm audit --croissant`` Croissant 1.0 metadata (this file).
4. Presidio ML-NER PII adapter via the ``[ingestion-pii-ml]`` extra
   (this file).

Each test class targets exactly one of those four items so a future
re-shuffle can split this file along section boundaries cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from forgelm.cli import main

# ---------------------------------------------------------------------------
# Item 3 — `forgelm ingest --all-mask` composite shorthand
# ---------------------------------------------------------------------------


class TestIngestAllMask:
    """Phase 12.5 #3: ``--all-mask`` is shorthand for ``--secrets-mask --pii-mask``."""

    def _write_text(self, path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")

    def test_all_mask_redacts_both_pii_and_secrets(self, tmp_path):
        # Inert fragments at runtime so repo-wide secret scanners stay silent
        # while the regex still has to match the canonical shape.
        aws_key = "AKIA" + "IOSFODNN7" + "EXAMPLE"
        email = "alice@example.com"
        src = tmp_path / "input.txt"
        self._write_text(src, f"contact: {email}\nkey={aws_key}\n")
        out = tmp_path / "out.jsonl"

        with patch(
            "sys.argv",
            ["forgelm", "ingest", str(src), "--output", str(out), "--all-mask"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        written = out.read_text(encoding="utf-8")
        # Both kinds of redaction tokens land in the JSONL — proves the flag
        # composes additively into both subsystems, not just one of them.
        assert email not in written
        assert aws_key not in written
        assert "[REDACTED-SECRET]" in written
        assert "[REDACTED]" in written

    def test_all_mask_combines_with_individual_flags_no_error(self, tmp_path):
        # Set-union semantics: passing ``--all-mask`` alongside ``--pii-mask``
        # must not raise; both stay True.
        src = tmp_path / "input.txt"
        self._write_text(src, "alice@example.com plain text")
        out = tmp_path / "out.jsonl"

        with patch(
            "sys.argv",
            [
                "forgelm",
                "ingest",
                str(src),
                "--output",
                str(out),
                "--all-mask",
                "--pii-mask",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        assert "[REDACTED]" in out.read_text(encoding="utf-8")

    def test_all_mask_does_not_redact_when_off(self, tmp_path):
        # Sanity: without the flag, neither masking subsystem fires.
        email = "alice@example.com"
        src = tmp_path / "input.txt"
        self._write_text(src, f"contact: {email}")
        out = tmp_path / "out.jsonl"

        with patch(
            "sys.argv",
            ["forgelm", "ingest", str(src), "--output", str(out)],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        assert email in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Item 4 — Wizard "audit first" entry point
# ---------------------------------------------------------------------------


class TestWizardAuditFirstOffer:
    """Phase 12.5 #4: when a JSONL is provided to the wizard, offer to audit it.

    Mirrors the Phase 11.5 ``_offer_ingest_for_directory`` pattern: the
    wizard surfaces the verdicts before continuing so the user can decide
    whether to swap datasets at config-time, not at training-time.
    """

    def _write_jsonl(self, path: Path, rows) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def test_audit_offer_runs_audit_when_user_accepts(self, tmp_path, capsys):
        from forgelm.wizard import _offer_audit_for_jsonl

        ds = tmp_path / "data.jsonl"
        self._write_jsonl(ds, [{"text": "alpha"}, {"text": "beta"}])

        # Simulate "yes" to the offer.
        with patch("forgelm.wizard._byod._prompt_yes_no", return_value=True):
            outcome = _offer_audit_for_jsonl(ds)

        assert outcome is True
        rendered = capsys.readouterr().out
        assert "Audit complete" in rendered or "Audit results" in rendered

    def test_audit_offer_skipped_when_user_declines(self, tmp_path, capsys):
        from forgelm.wizard import _offer_audit_for_jsonl

        ds = tmp_path / "data.jsonl"
        self._write_jsonl(ds, [{"text": "alpha"}])

        # Simulate "no" — must short-circuit before invoking audit_dataset.
        with patch("forgelm.wizard._byod._prompt_yes_no", return_value=False):
            with patch("forgelm.data_audit.audit_dataset") as mock_audit:
                outcome = _offer_audit_for_jsonl(ds)
                mock_audit.assert_not_called()

        assert outcome is False

    def test_audit_offer_recovers_when_audit_raises(self, tmp_path, capsys):
        # The wizard runs in interactive context; an unexpected audit failure
        # must NOT crash the wizard. The function returns False so the caller
        # falls through to the "continue without audit" path.
        from forgelm.wizard import _offer_audit_for_jsonl

        ds = tmp_path / "data.jsonl"
        self._write_jsonl(ds, [{"text": "alpha"}])

        with patch("forgelm.wizard._byod._prompt_yes_no", return_value=True):
            with patch(
                "forgelm.data_audit.audit_dataset",
                side_effect=RuntimeError("simulated audit failure"),
            ):
                outcome = _offer_audit_for_jsonl(ds)

        assert outcome is False
        assert "could not run" in capsys.readouterr().out.lower()


# ---------------------------------------------------------------------------
# Item 2 — Croissant 1.0 metadata
# ---------------------------------------------------------------------------


class TestCroissantMetadataExport:
    """Phase 12.5 #2: ``--croissant`` adds a Google Croissant subset to the report."""

    def _write_jsonl(self, path: Path, rows) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def test_croissant_section_empty_by_default(self, tmp_path):
        # Phase 12 precedent (``secrets_summary`` / ``quality_summary``):
        # additive keys appear as empty dicts on default audits so consumers
        # using ``report.get("...", {})`` keep working byte-equivalently.
        # The Croissant block follows the same convention.
        ds = tmp_path / "data.jsonl"
        self._write_jsonl(ds, [{"text": "alpha"}])
        out_dir = tmp_path / "audit"

        with patch(
            "sys.argv",
            ["forgelm", "audit", str(ds), "--output", str(out_dir)],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        report = json.loads((out_dir / "data_audit_report.json").read_text(encoding="utf-8"))
        # Pin the contract: ``croissant`` is *always* present in the
        # serialised report (dataclass ``field(default_factory=dict)``)
        # and is the empty dict when ``--croissant`` is off. This
        # matches the precedent set by ``secrets_summary`` /
        # ``quality_summary`` and prevents a future serializer change
        # from silently dropping the key for empty dicts.
        assert "croissant" in report
        assert report["croissant"] == {}

    def test_croissant_flag_emits_minimal_card(self, tmp_path):
        ds = tmp_path / "data.jsonl"
        self._write_jsonl(ds, [{"text": "alpha"}, {"text": "beta"}])
        out_dir = tmp_path / "audit"

        with patch(
            "sys.argv",
            [
                "forgelm",
                "audit",
                str(ds),
                "--output",
                str(out_dir),
                "--croissant",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        report = json.loads((out_dir / "data_audit_report.json").read_text(encoding="utf-8"))
        assert "croissant" in report
        crois = report["croissant"]
        # Pin the canonical Croissant 1.0 envelope so consumers / CI checks can
        # rely on it. ``@type`` and ``@context`` are the minimum-viable subset
        # that lets a Croissant-aware consumer parse the file without
        # extending the audit JSON's other top-level keys.
        assert crois.get("@type") == "sc:Dataset"
        assert "@context" in crois
        assert crois["@context"].get("sc") == "https://schema.org/"
        # ForgeLM-specific fields the report already has should be carried
        # across into the card so it stays a single self-contained document.
        assert "name" in crois
        assert "distribution" in crois  # describes the underlying JSONL


# ---------------------------------------------------------------------------
# Item 1 — Presidio adapter (``[ingestion-pii-ml]`` extra)
# ---------------------------------------------------------------------------


class TestPresidioPIIAdapter:
    """Phase 12.5 #1: optional ML-NER PII signal layered onto the regex path."""

    def _write_jsonl(self, path: Path, rows) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def test_presidio_disabled_by_default(self, tmp_path):
        # No --pii-ml flag → no Presidio counts; the regex path is unchanged.
        from forgelm.data_audit import audit_dataset

        ds = tmp_path / "data.jsonl"
        self._write_jsonl(ds, [{"text": "Visit Acme Corp in Berlin next Monday."}])

        report = audit_dataset(str(ds), output_dir=str(tmp_path / "audit"))
        # No new ML-tier categories — the regex-only path didn't see any PII
        # in this prose either, but we're really asserting the schema doesn't
        # add the Presidio-only buckets when the feature is off.
        for ml_only in ("person", "organization", "location"):
            assert ml_only not in report.pii_summary

    def test_presidio_missing_extra_raises_with_install_hint(self):
        from forgelm.data_audit import _require_presidio

        # When the extra isn't installed, the require-helper raises a typed
        # ImportError pointing at the install command — same pattern as
        # ``_require_datasketch`` / ``_require_detect_secrets``.
        with patch("forgelm.data_audit._optional._HAS_PRESIDIO", False):
            with pytest.raises(ImportError) as exc_info:
                _require_presidio()
        msg = str(exc_info.value)
        assert "ingestion-pii-ml" in msg
        assert "pip install" in msg

    def test_presidio_severity_table_exposes_ml_tiers(self):
        # The severity table grows three new rows so the existing pii_severity
        # block can grade ML-NER findings the same way it grades regex hits.
        from forgelm.data_audit import PII_ML_SEVERITY

        for kind in ("person", "organization", "location"):
            assert kind in PII_ML_SEVERITY, f"missing severity tier for ML kind {kind!r}"
        # All three categories are below the regex 'critical' tier — names /
        # orgs / locations leak less identity than a credit-card number.
        # Pin medium / low to keep audit verdicts stable across releases.
        assert PII_ML_SEVERITY["person"] == "medium"
        assert PII_ML_SEVERITY["organization"] == "low"
        assert PII_ML_SEVERITY["location"] == "low"

    def test_presidio_entity_map_only_canonical_keys(self):
        # Presidio canonicalises spaCy NER labels (PER/PERSON → PERSON,
        # LOC/GPE → LOCATION, ORG → ORGANIZATION, NORP → NRP) before
        # they reach ``analyzer.analyze().entity_type``. Pin the map to
        # the canonical names only so a future maintainer doesn't read
        # raw spaCy keys (``ORG`` / ``GPE`` / ``NORP`` / ``FAC``) as
        # live coverage. NRP is deliberately excluded — it's a distinct
        # privacy signal from ``location``.
        from forgelm.data_audit import _PRESIDIO_ENTITY_MAP

        assert set(_PRESIDIO_ENTITY_MAP.keys()) == {"PERSON", "ORGANIZATION", "LOCATION"}
        assert _PRESIDIO_ENTITY_MAP["PERSON"] == "person"
        assert _PRESIDIO_ENTITY_MAP["ORGANIZATION"] == "organization"
        assert _PRESIDIO_ENTITY_MAP["LOCATION"] == "location"

    def test_presidio_missing_spacy_model_surfaces_install_hint(self):
        # Even when ``presidio-analyzer`` is importable, the spaCy model
        # is a separate ``python -m spacy download …`` step. spaCy
        # raises ``OSError("Can't find model …")`` when the model
        # package isn't on the import path; ``_require_presidio`` must
        # catch that and re-raise as ``ImportError`` with the install
        # recipe so the operator never gets a deep spaCy traceback.
        from forgelm.data_audit import _get_presidio_analyzer, _require_presidio

        # Reset the cache so our patched class is the one that gets
        # constructed; otherwise a previous successful build could
        # return a stale instance.
        _get_presidio_analyzer.cache_clear()
        try:

            class _BoomAnalyzer:
                def __init__(self):
                    raise OSError("Can't find model 'en_core_web_lg'")

            with patch("forgelm.data_audit._optional._HAS_PRESIDIO", True):
                with patch("forgelm.data_audit._optional._PresidioAnalyzer", _BoomAnalyzer):
                    with pytest.raises(ImportError) as exc_info:
                        _require_presidio()
        finally:
            # Always restore the cache so subsequent tests in the same
            # session don't see ``_BoomAnalyzer``.
            _get_presidio_analyzer.cache_clear()
        msg = str(exc_info.value)
        assert "spacy" in msg.lower() or "spaCy" in msg
        assert "ingestion-pii-ml" in msg
        assert "en_core_web_lg" in msg

    def test_presidio_entity_map_collapses_findings_to_buckets(self):
        # Behavioural test for ``detect_pii_ml`` — a stub Presidio
        # analyzer that emits canonical entity_type strings should
        # produce the right counts; non-canonical ones (raw spaCy
        # labels) should be ignored. This test would have caught the
        # original review's M2 immediately.
        from forgelm.data_audit import _get_presidio_analyzer, detect_pii_ml

        class _Finding:
            def __init__(self, entity_type):
                self.entity_type = entity_type

        class _StubAnalyzer:
            def analyze(self, text, language):
                # Emit the full Presidio canonical set + a couple of raw
                # spaCy labels (which Presidio would never actually
                # emit) to confirm our map only honours canonicals.
                return [
                    _Finding("PERSON"),
                    _Finding("ORGANIZATION"),
                    _Finding("LOCATION"),
                    _Finding("LOCATION"),
                    _Finding("NRP"),  # Presidio canonical, not mapped
                    _Finding("ORG"),  # raw spaCy, must NOT be honoured
                    _Finding("GPE"),  # raw spaCy, must NOT be honoured
                ]

        _get_presidio_analyzer.cache_clear()
        try:
            with patch("forgelm.data_audit._optional._HAS_PRESIDIO", True):
                with patch("forgelm.data_audit._pii_ml._get_presidio_analyzer", return_value=_StubAnalyzer()):
                    counts = detect_pii_ml("Alice works at Acme Corp in Berlin.")
        finally:
            _get_presidio_analyzer.cache_clear()
        assert counts == {"person": 1, "organization": 1, "location": 2}


# ---------------------------------------------------------------------------
# Cross-cutting tests — coverage gaps surfaced by the Phase 12.5 review
# ---------------------------------------------------------------------------


class TestCroissantMultiSplit:
    """Multi-split layout produces one cr:FileObject + cr:RecordSet per split."""

    def _write_jsonl(self, path: Path, rows) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def test_multi_split_card_carries_one_record_per_split(self, tmp_path):
        # train.jsonl / validation.jsonl / test.jsonl is the canonical
        # multi-split layout the audit recognises. Each must produce
        # its own ``cr:FileObject`` (in ``distribution``) and its own
        # ``cr:RecordSet`` (with the right ``@id``).
        data_dir = tmp_path / "splits"
        data_dir.mkdir()
        for split, rows in [
            ("train", [{"text": "alpha"}, {"text": "beta"}]),
            ("validation", [{"text": "gamma"}]),
            ("test", [{"text": "delta"}]),
        ]:
            self._write_jsonl(data_dir / f"{split}.jsonl", rows)

        out_dir = tmp_path / "audit"
        with patch(
            "sys.argv",
            [
                "forgelm",
                "audit",
                str(data_dir),
                "--output",
                str(out_dir),
                "--croissant",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        report = json.loads((out_dir / "data_audit_report.json").read_text(encoding="utf-8"))
        crois = report["croissant"]
        ids = {entry["@id"] for entry in crois["distribution"]}
        assert ids == {"train_jsonl", "validation_jsonl", "test_jsonl"}
        record_ids = {entry["@id"] for entry in crois["recordSet"]}
        assert record_ids == {"train", "validation", "test"}
        # contentUrl must be the raw filename (not the slug), not an absolute
        # filesystem path — see the Phase 12.5 review m3 finding.
        for entry in crois["distribution"]:
            assert entry["contentUrl"] == entry["name"]
            assert "/" not in entry["contentUrl"]


class TestAllMaskSymmetric:
    """``--all-mask`` set-union covers the secrets-mask-already-true direction too."""

    def test_all_mask_with_secrets_mask_already_set(self, tmp_path):
        # Symmetric counterpart to test_all_mask_combines_with_individual_flags_no_error
        # so a future refactor that drops one branch of the boolean
        # union trips a test.
        aws_key = "AKIA" + "IOSFODNN7" + "EXAMPLE"
        src = tmp_path / "input.txt"
        src.write_text(
            f"alice@example.com mentioned key={aws_key}",
            encoding="utf-8",
        )
        out = tmp_path / "out.jsonl"
        with patch(
            "sys.argv",
            [
                "forgelm",
                "ingest",
                str(src),
                "--output",
                str(out),
                "--secrets-mask",
                "--all-mask",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        written = out.read_text(encoding="utf-8")
        assert "[REDACTED]" in written
        assert "[REDACTED-SECRET]" in written
        assert aws_key not in written


class TestPiiMlJsonEnvelope:
    """``forgelm audit --output-format json`` surfaces the Croissant card."""

    def _write_jsonl(self, path: Path, rows) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def test_envelope_carries_croissant_when_flag_on(self, tmp_path, capsys):
        ds = tmp_path / "data.jsonl"
        self._write_jsonl(ds, [{"text": "alpha"}])
        out_dir = tmp_path / "audit"
        with patch(
            "sys.argv",
            [
                "forgelm",
                "audit",
                str(ds),
                "--output",
                str(out_dir),
                "--croissant",
                "--output-format",
                "json",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        envelope = json.loads(capsys.readouterr().out)
        assert "croissant" in envelope
        assert envelope["croissant"]["@type"] == "sc:Dataset"

    def test_envelope_croissant_is_empty_when_flag_off(self, tmp_path, capsys):
        ds = tmp_path / "data.jsonl"
        self._write_jsonl(ds, [{"text": "alpha"}])
        out_dir = tmp_path / "audit"
        with patch(
            "sys.argv",
            [
                "forgelm",
                "audit",
                str(ds),
                "--output",
                str(out_dir),
                "--output-format",
                "json",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        envelope = json.loads(capsys.readouterr().out)
        # Phase 12 precedent: present-and-empty when off.
        assert "croissant" in envelope
        assert envelope["croissant"] == {}


# ---------------------------------------------------------------------------
# Phase 12.5 review fixes — verified-by-test follow-ups from PR #18
# ---------------------------------------------------------------------------


class TestCroissantFileIdReflectsRealFilename:
    """``file_id`` must come from the real source filename, not the split label.

    Single-file audits and alias layouts (``dev.jsonl`` → split
    ``validation``) used to fabricate ``file_id = f"{split_name}.jsonl"``
    which mismatched the file actually on disk and broke any consumer
    that tried to resolve the card's ``contentUrl`` back to the JSONL.
    """

    def _write_jsonl(self, path: Path, rows) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def test_single_file_audit_keeps_real_filename(self, tmp_path):
        # ``policies.jsonl`` must show up in the card as ``policies.jsonl``,
        # not as ``train.jsonl`` (the canonical split label _resolve_input
        # assigns to single-file inputs).
        ds = tmp_path / "policies.jsonl"
        self._write_jsonl(ds, [{"text": "alpha"}, {"text": "beta"}])
        out_dir = tmp_path / "audit"
        with patch(
            "sys.argv",
            [
                "forgelm",
                "audit",
                str(ds),
                "--output",
                str(out_dir),
                "--croissant",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        crois = json.loads((out_dir / "data_audit_report.json").read_text(encoding="utf-8"))["croissant"]
        ids = {entry["@id"] for entry in crois["distribution"]}
        assert ids == {"policies_jsonl"}
        for entry in crois["distribution"]:
            assert entry["contentUrl"] == "policies.jsonl"
            assert entry["name"] == "policies.jsonl"

    def test_alias_layout_keeps_real_filename(self, tmp_path):
        # ``dev.jsonl`` is folded onto canonical split name ``validation``
        # by _resolve_input. The card must still reference the real file
        # (``dev.jsonl``), otherwise the contentUrl points to a file
        # that doesn't exist on disk.
        data_dir = tmp_path / "splits"
        data_dir.mkdir()
        self._write_jsonl(data_dir / "train.jsonl", [{"text": "alpha"}])
        self._write_jsonl(data_dir / "dev.jsonl", [{"text": "beta"}])
        out_dir = tmp_path / "audit"
        with patch(
            "sys.argv",
            [
                "forgelm",
                "audit",
                str(data_dir),
                "--output",
                str(out_dir),
                "--croissant",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        crois = json.loads((out_dir / "data_audit_report.json").read_text(encoding="utf-8"))["croissant"]
        ids = {entry["@id"] for entry in crois["distribution"]}
        assert ids == {"train_jsonl", "dev_jsonl"}, (
            "alias layout must keep the real filename (slugged for JSON-LD), not the canonical split label"
        )
        # ``name`` and ``contentUrl`` are the operator-facing fields; they must
        # carry the *raw* filename (``dev.jsonl``) so a Croissant consumer can
        # actually fetch the file.  ``@id`` is the slugged JSON-LD identifier
        # (``dev_jsonl``) and is intentionally distinct.  Asserting both halves
        # protects against a regression that rewrites contentUrl to the
        # canonical split label or the slug form.
        by_name = {entry["name"]: entry for entry in crois["distribution"]}
        assert set(by_name) == {"train.jsonl", "dev.jsonl"}, (
            "distribution.name must reference the real on-disk filename"
        )
        for filename, entry in by_name.items():
            # Compare the URL's final path segment exactly so a leaked
            # filesystem path (``/abs/dir/dev.jsonl``) cannot pass a fuzzy
            # ``endswith(filename)`` check.  Reject backslashes too — they
            # would indicate a Windows-style path leak.
            url = entry["contentUrl"]
            assert "\\" not in url, f"contentUrl {url!r} contains a backslash (Windows path leak?)"
            assert url.split("/")[-1] == filename, (
                f"contentUrl {url!r} final path segment must equal {filename!r}, "
                "not the canonical split label, the slugged @id, or a leaked absolute path"
            )
            assert "validation" not in url, (
                "contentUrl must not leak the canonical split label (e.g. 'validation' for dev.jsonl)"
            )

    def test_url_does_not_leak_absolute_path(self, tmp_path):
        # When the operator passes an absolute path, the published card
        # must not carry ``/Users/...`` / ``/home/builder/...`` — those
        # leak the auditor's local layout to whoever reads the card.
        ds = tmp_path / "policies.jsonl"
        self._write_jsonl(ds, [{"text": "alpha"}])
        out_dir = tmp_path / "audit"
        absolute_input = str(ds.resolve())
        # ``Path.is_absolute()`` is OS-neutral: ``/Users/...`` on POSIX,
        # ``C:\Users\...`` on Windows.  ``startswith("/")`` would fail the
        # precondition on Windows even though the path is genuinely absolute.
        assert Path(absolute_input).is_absolute(), "test precondition: absolute path"
        with patch(
            "sys.argv",
            [
                "forgelm",
                "audit",
                absolute_input,
                "--output",
                str(out_dir),
                "--croissant",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        crois = json.loads((out_dir / "data_audit_report.json").read_text(encoding="utf-8"))["croissant"]
        assert "/" not in crois["url"], f"url field leaks absolute path: {crois['url']!r}"
        assert crois["url"] == "policies_jsonl"


_LANGUAGES_DOC_URL = "https://presidio.dataprivacystack.org/analyzer/languages/"


class TestPresidioLanguagePreflight:
    """Pre-flight rejects unsupported ``--pii-ml-language`` instead of swallowing.

    Without the pre-flight, ``analyzer.analyze(text, language='xx')``
    raises ``ValueError`` per row inside ``detect_pii_ml`` and the
    handler swallows it — so ``--pii-ml --pii-ml-language xx`` against
    a default Presidio install (which only registers English) returns
    zero ML findings without a peep. That's exactly the silent-failure
    anti-pattern Phase 12.5 set out to remove, so we pin the loud-fail
    behaviour with a regression test.
    """

    def test_unsupported_language_raises_value_error(self):
        from forgelm.data_audit import _get_presidio_analyzer, _require_presidio

        class _StubAnalyzer:
            supported_languages = ["en"]

        _get_presidio_analyzer.cache_clear()
        try:
            with patch("forgelm.data_audit._optional._HAS_PRESIDIO", True):
                with patch(
                    "forgelm.data_audit._pii_ml._get_presidio_analyzer",
                    return_value=_StubAnalyzer(),
                ):
                    with pytest.raises(ValueError) as exc_info:
                        _require_presidio(language="xx")
        finally:
            _get_presidio_analyzer.cache_clear()
        msg = str(exc_info.value)
        assert "xx" in msg
        assert "en" in msg  # registered list shows what is available
        assert _LANGUAGES_DOC_URL in msg  # points at the live Presidio docs
        assert "microsoft.github.io" not in msg  # stale URL must not resurface

    def test_default_english_language_passes_preflight(self):
        from forgelm.data_audit import _get_presidio_analyzer, _require_presidio

        class _StubAnalyzer:
            supported_languages = ["en"]

        _get_presidio_analyzer.cache_clear()
        try:
            with patch("forgelm.data_audit._optional._HAS_PRESIDIO", True):
                with patch(
                    "forgelm.data_audit._pii_ml._get_presidio_analyzer",
                    return_value=_StubAnalyzer(),
                ):
                    _require_presidio(language="en")  # must not raise
        finally:
            _get_presidio_analyzer.cache_clear()

    def test_unmapped_language_raises_value_error_at_analyzer_build(self):
        # Languages without a default spaCy model in
        # _SPACY_MODEL_FOR_LANGUAGE must fail fast at _get_presidio_analyzer
        # time with an actionable hint that points at the 'xx' multilingual
        # fallback or the Python-API custom-engine path. Exercises the
        # "language not in map" branch directly without needing Presidio
        # installed.
        from forgelm.data_audit import _get_presidio_analyzer

        _get_presidio_analyzer.cache_clear()
        try:
            with patch("forgelm.data_audit._optional._HAS_PRESIDIO", True):
                with pytest.raises(ValueError) as exc_info:
                    _get_presidio_analyzer(language="qq")
        finally:
            _get_presidio_analyzer.cache_clear()
        msg = str(exc_info.value)
        assert "qq" in msg
        assert "xx" in msg  # message points at the multilingual fallback
        assert _LANGUAGES_DOC_URL in msg  # points at the live Presidio docs
        assert "microsoft.github.io" not in msg  # stale URL must not resurface


class TestPresidioPerRowErrorLogging:
    """Per-row Presidio failures must surface at DEBUG level, not silently.

    The handler stays narrow ((ValueError, RuntimeError)) and still
    returns ``{}`` so a single bad row doesn't block the audit, but a
    deluge of failures (zero ML coverage) is now diagnosable via
    ``--log-level DEBUG`` instead of being invisible.
    """

    def test_per_row_value_error_emits_debug_log(self, caplog):
        import logging

        from forgelm.data_audit import _get_presidio_analyzer, detect_pii_ml

        class _AngryAnalyzer:
            def analyze(self, text, language):
                raise ValueError("simulated per-row failure")

        _get_presidio_analyzer.cache_clear()
        try:
            with patch("forgelm.data_audit._optional._HAS_PRESIDIO", True):
                with patch(
                    "forgelm.data_audit._pii_ml._get_presidio_analyzer",
                    return_value=_AngryAnalyzer(),
                ):
                    with caplog.at_level(logging.DEBUG, logger="forgelm.data_audit"):
                        result = detect_pii_ml("some text", language="en")
        finally:
            _get_presidio_analyzer.cache_clear()
        # Behaviour: per-row failure swallowed (returns empty dict)
        # but a DEBUG-level diagnostic record was emitted with the
        # language and exception so operators can trace why ML PII
        # coverage is zero in a noisy corpus.
        assert result == {}
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("per-row Presidio failure" in r.getMessage() for r in debug_records), (
            "expected DEBUG log for per-row Presidio failure"
        )
        assert any("language=en" in r.getMessage() for r in debug_records)
