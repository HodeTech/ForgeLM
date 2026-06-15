"""Tests for the BYOD (bring-your-own-data) input loop in ``wizard.py``.

Covers the path-validation block in ``_maybe_run_quickstart_template``:
nonexistent paths, directories, malformed JSONL, valid JSONL, HF Hub dataset
IDs, and ``~`` expansion. These are unit tests — no subprocess, no GPU,
no network. ``input()`` is stubbed via ``builtins.input`` patching, and
``run_quickstart`` is patched to keep the tests hermetic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from forgelm import wizard


def _make_input(answers):
    """Return a side_effect callable that returns each answer in order."""
    answers = list(answers)

    def _impl(_prompt_text=""):
        if not answers:
            raise AssertionError("input() called more times than answers provided")
        return answers.pop(0)

    return _impl


# Inputs we feed to the wizard before the BYOD prompt:
#   1. "y" — accept the quickstart-template offer
#   2. "domain-expert" — pick the BYOD template
# After that the BYOD loop consumes its own answers.
_PRELUDE = ["y", "domain-expert"]


def test_byod_rejects_nonexistent_path(capsys):
    answers = _PRELUDE + ["/no/such/file.jsonl", "cancel"]
    with patch("builtins.input", side_effect=_make_input(answers)):
        result = wizard._maybe_run_quickstart_template()
    assert result is None
    captured = capsys.readouterr().out
    assert "Path not found or not a regular file: /no/such/file.jsonl" in captured


def test_byod_rejects_empty_directory(tmp_path, capsys):
    # An empty directory has no ingestible documents — the wizard's
    # Phase 11.5 ingest-first hook surfaces a clear "no supported files"
    # message and re-prompts. Pre-Phase-11.5 wording ("not a JSONL file")
    # was deliberately replaced once the wizard learned to ingest inline.
    answers = _PRELUDE + [str(tmp_path), "cancel"]
    with patch("builtins.input", side_effect=_make_input(answers)):
        result = wizard._maybe_run_quickstart_template()
    assert result is None
    captured = capsys.readouterr().out
    assert "doesn't contain any" in captured


def test_byod_directory_with_docs_offers_ingest_then_cancels(tmp_path, capsys):
    # Phase 11.5: when the directory has ingestible files, the wizard offers
    # to run ingestion inline. We answer "no" to the offer; the decline path
    # surfaces the manual-ingest hint with the resolved directory.
    (tmp_path / "doc.txt").write_text("alpha\n\nbeta", encoding="utf-8")
    # Inputs after prelude: directory path → "n" (decline ingest offer) → "cancel"
    answers = _PRELUDE + [str(tmp_path), "n", "cancel"]
    with patch("builtins.input", side_effect=_make_input(answers)):
        result = wizard._maybe_run_quickstart_template()
    assert result is None
    captured = capsys.readouterr().out
    assert "Skipped" in captured
    assert "forgelm ingest" in captured


def test_byod_rejects_malformed_jsonl(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{bad json\n", encoding="utf-8")
    answers = _PRELUDE + [str(bad), "cancel"]
    with patch("builtins.input", side_effect=_make_input(answers)):
        result = wizard._maybe_run_quickstart_template()
    assert result is None
    captured = capsys.readouterr().out
    assert "not valid JSONL" in captured


def test_byod_accepts_valid_jsonl(tmp_path):
    good = tmp_path / "good.jsonl"
    good.write_text('{"messages":[]}\n', encoding="utf-8")
    # Phase 12.5: after a JSONL is validated, the wizard offers to run an
    # audit on it. Decline ("n") so this test remains scoped to the BYOD
    # validation path; the audit-offer behaviour has its own coverage in
    # test_phase12_5.py::TestWizardAuditFirstOffer.
    answers = _PRELUDE + [str(good), "n"]

    fake_result = type(
        "R",
        (),
        {
            "config_path": tmp_path / "generated.yaml",
            "chosen_model": "test-model",
            "selection_reason": "test",
            "dataset_path": str(good),
        },
    )()

    with (
        patch("builtins.input", side_effect=_make_input(answers)),
        patch("forgelm.quickstart.run_quickstart", return_value=fake_result) as mock_run,
    ):
        result = wizard._maybe_run_quickstart_template()

    assert result == str(tmp_path / "generated.yaml")
    mock_run.assert_called_once()
    # The dataset_override should be the resolved absolute path.
    _args, kwargs = mock_run.call_args
    assert kwargs["dataset_override"] == str(good)


def test_byod_accepts_hf_hub_id(tmp_path, capsys):
    hub_id = "tatsu-lab/alpaca"
    answers = _PRELUDE + [hub_id]

    fake_result = type(
        "R",
        (),
        {
            "config_path": tmp_path / "generated.yaml",
            "chosen_model": "test-model",
            "selection_reason": "test",
            "dataset_path": hub_id,
        },
    )()

    with (
        patch("builtins.input", side_effect=_make_input(answers)),
        patch("forgelm.quickstart.run_quickstart", return_value=fake_result) as mock_run,
    ):
        result = wizard._maybe_run_quickstart_template()

    assert result == str(tmp_path / "generated.yaml")
    captured = capsys.readouterr().out
    assert f"Treating '{hub_id}' as an HF Hub dataset ID" in captured
    # The HF ID must be passed through unchanged — not turned into a Path.
    _args, kwargs = mock_run.call_args
    assert kwargs["dataset_override"] == hub_id


def test_byod_expands_user_home(tmp_path, monkeypatch):
    good = tmp_path / "data.jsonl"
    good.write_text('{"messages":[]}\n', encoding="utf-8")

    # Pretend the user's home is tmp_path so "~/data.jsonl" expands to our file.
    # ``Path.expanduser()`` reads ``$HOME`` on POSIX and ``%USERPROFILE%`` on
    # Windows (with ``%HOMEDRIVE%%HOMEPATH%`` as a secondary fallback) —
    # set all four so the test passes on every cross-OS publish-matrix combo.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", "")
    monkeypatch.setenv("HOMEPATH", str(tmp_path))

    typed = "~/data.jsonl"
    # Phase 12.5: extra "n" declines the post-validation audit offer.
    answers = _PRELUDE + [typed, "n"]

    fake_result = type(
        "R",
        (),
        {
            "config_path": tmp_path / "generated.yaml",
            "chosen_model": "test-model",
            "selection_reason": "test",
            "dataset_path": str(good),
        },
    )()

    with (
        patch("builtins.input", side_effect=_make_input(answers)),
        patch("forgelm.quickstart.run_quickstart", return_value=fake_result) as mock_run,
    ):
        result = wizard._maybe_run_quickstart_template()

    assert result == str(tmp_path / "generated.yaml")
    _args, kwargs = mock_run.call_args
    # The override must be the expanded absolute path, not the literal "~/...".
    # _validate_local_jsonl applies .resolve() after .expanduser(), so on
    # macOS where /tmp is a symlink to /private/tmp the override may carry
    # the canonical form — compare via Path equality instead of string match.
    expanded = Path(typed).expanduser().resolve()
    assert Path(kwargs["dataset_override"]) == expanded
    # And it should resolve to our actual tmp file.
    assert Path(kwargs["dataset_override"]).is_file()


def test_byod_relative_local_path_not_misclassified_as_hub_id(tmp_path, monkeypatch, capsys):
    """``data/train.jsonl`` matches the HF Hub regex shape but is a local file.

    Regression: an earlier version of the loop tested the HF Hub regex
    *before* the local-file probe, so a relative path that happened to look
    like ``<org>/<name>`` (single slash, safe-name char class) silently
    became a Hub ID and bypassed JSONL validation. The fixed loop tries
    local first, falls back to Hub semantics only when the path is missing.
    """
    nested = tmp_path / "data"
    nested.mkdir()
    real_file = nested / "train.jsonl"
    real_file.write_text('{"messages":[]}\n', encoding="utf-8")

    monkeypatch.chdir(tmp_path)  # so "data/train.jsonl" resolves under tmp_path

    typed = "data/train.jsonl"
    # Phase 12.5: decline the post-validation audit offer.
    answers = _PRELUDE + [typed, "n"]

    fake_result = type(
        "R",
        (),
        {
            "config_path": tmp_path / "generated.yaml",
            "chosen_model": "test-model",
            "selection_reason": "test",
            "dataset_path": str(real_file),
        },
    )()

    with (
        patch("builtins.input", side_effect=_make_input(answers)),
        patch("forgelm.quickstart.run_quickstart", return_value=fake_result) as mock_run,
    ):
        result = wizard._maybe_run_quickstart_template()

    assert result == str(tmp_path / "generated.yaml")
    captured = capsys.readouterr().out
    # Must NOT have been treated as an HF Hub ID.
    assert "Treating" not in captured, "relative local path was misclassified as a Hub ID"

    _args, kwargs = mock_run.call_args
    # The override must point to the actual on-disk file, expanded to absolute.
    assert Path(kwargs["dataset_override"]) == real_file.resolve()


def test_byod_cancel_at_nested_audit_prompt_falls_back(tmp_path, capsys):
    """F-H-11: cancel typed at the audit-offer prompt inside _validate_local_jsonl
    must be caught by _resolve_byod_dataset_path and return None (fall back to
    the full wizard), NOT propagate WizardCancel up through the call stack.

    Regression: before the fix the try/except WizardCancel in
    _resolve_byod_dataset_path only wrapped the initial _prompt() call, so any
    WizardCancel raised from the four secondary prompts inside
    _offer_audit_for_jsonl / _offer_ingest_for_directory escaped uncaught and
    terminated the wizard entirely instead of falling back.
    """
    from forgelm.wizard._byod import _resolve_byod_dataset_path

    good = tmp_path / "good.jsonl"
    good.write_text('{"messages": []}\n', encoding="utf-8")

    # Inputs: JSONL path (valid) → "cancel" at the audit-offer prompt.
    # "cancel" triggers WizardCancel inside _offer_audit_for_jsonl's
    # _prompt_yes_no, which must be caught inside _resolve_byod_dataset_path.
    answers = [str(good), "cancel"]
    with patch("builtins.input", side_effect=_make_input(answers)):
        result = _resolve_byod_dataset_path()

    # Must return None (fall back) — not raise WizardCancel.
    assert result is None
    captured = capsys.readouterr().out
    assert "Cancelled" in captured


def test_byod_cancel_at_nested_audit_prompt_via_full_path(tmp_path, capsys):
    """F-H-11 (via full wizard path): cancel at the nested audit prompt inside
    _validate_local_jsonl returns None from _maybe_run_quickstart_template,
    not a clean wizard exit.  Verifies the fix applies end-to-end.
    """
    good = tmp_path / "good.jsonl"
    good.write_text('{"messages": []}\n', encoding="utf-8")

    # After the prelude, answer with the JSONL path, then "cancel" at the
    # audit-offer prompt.  The wizard should fall back gracefully.
    answers = _PRELUDE + [str(good), "cancel"]
    with patch("builtins.input", side_effect=_make_input(answers)):
        result = wizard._maybe_run_quickstart_template()

    assert result is None
    captured = capsys.readouterr().out
    assert "Cancelled" in captured


def test_audit_failure_logs_warning(tmp_path, caplog):
    """F-P7-OPUS-41: the BYOD audit-offer BLE001 handler previously surfaced a
    failure only on stdout via ``_print``; a CI/log pipeline tailing logs saw
    nothing. The wide-tail handler now logs at WARNING (per error-handling.md's
    mandatory BLE001 hygiene) while keeping the offer advisory — the helper
    still returns ``False`` so a failed audit does not abort BYOD selection."""
    import logging

    good = tmp_path / "good.jsonl"
    good.write_text('{"messages": []}\n', encoding="utf-8")

    with (
        patch("builtins.input", side_effect=_make_input(["y"])),  # accept the audit offer
        patch("forgelm.data_audit.audit_dataset", side_effect=RuntimeError("boom")),
        caplog.at_level(logging.WARNING, logger="forgelm.wizard"),
    ):
        result = wizard._offer_audit_for_jsonl(good)

    assert result is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(str(good) in r.getMessage() and "could not run" in r.getMessage() for r in warnings), (
        f"expected a WARNING naming the dataset path; got {[r.getMessage() for r in warnings]}"
    )
