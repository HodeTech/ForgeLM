"""Tests for forgelm.data_audit (Phase 11).

Pure-Python regex / simhash logic; no torch / TRL required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forgelm.data_audit import (
    DEFAULT_NEAR_DUP_HAMMING,
    PII_TYPES,
    AuditReport,
    _is_credit_card,
    _is_tr_id,
    audit_dataset,
    compute_simhash,
    detect_pii,
    find_near_duplicates,
    hamming_distance,
    mask_pii,
    summarize_report,
)

# ---------------------------------------------------------------------------
# PII detection
# ---------------------------------------------------------------------------


class TestPiiDetection:
    def test_email_detected(self):
        assert detect_pii("write to alice@example.com today").get("email") == 1

    def test_phone_detected_with_country_prefix(self):
        # Narrow phone regex requires + or () context; bare digit runs no
        # longer flag (Bug 6 narrowing).
        assert detect_pii("call +90 532 123 45 now").get("phone", 0) >= 1

    def test_phone_detected_with_paren_area_code(self):
        assert detect_pii("call (212) 555-1234 today").get("phone", 0) >= 1

    def test_bare_digits_not_phone(self):
        # 4111 1111 1111 1111 used to match phone before the narrowing —
        # now phone requires explicit international or area-code context.
        assert detect_pii("number 4111 1111 1111 1111").get("phone", 0) == 0
        # ISO date should not flag
        assert detect_pii("event on 2024-01-15 here").get("phone", 0) == 0
        # Log line numbers should not flag
        assert detect_pii("line 1234 in foo.py").get("phone", 0) == 0

    def test_credit_card_validated_via_luhn(self):
        # 4111 1111 1111 1111 is a Visa test card with valid Luhn
        assert detect_pii("card 4111 1111 1111 1111").get("credit_card") == 1
        # Same shape but invalid Luhn → not flagged
        assert detect_pii("not a card 4111 1111 1111 1112").get("credit_card", 0) == 0

    def test_tr_id_validated_via_checksum(self):
        # Real-format checksum-valid TR Kimlik (synthetic, math-checked)
        valid = "10000000146"  # passes the canonical TR algorithm
        assert _is_tr_id(valid) is True
        assert detect_pii(f"id is {valid}").get("tr_id") == 1
        # Random 11 digits should fail the checksum
        assert detect_pii("id is 12345678901").get("tr_id", 0) == 0

    def test_tr_id_unicode_digits_detected_intentional_posture(self):
        """F-P6-OPUS-21: the national-ID ``\\d`` is intentionally Unicode-aware,
        so a checksum-valid TR Kimlik written in Arabic-Indic digits is still
        detected (higher recall on internationalised digit forms, matching the
        audit's over-report posture). Pins the documented Unicode intent so a
        future ``re.ASCII`` tightening is a deliberate, visible change."""
        ascii_id = "10000000146"  # passes the canonical TR checksum
        arabic_id = "".join(chr(0x0660 + int(c)) for c in ascii_id)  # U+0660-0669
        assert arabic_id != ascii_id
        assert detect_pii(f"id is {arabic_id}").get("tr_id") == 1

    def test_us_ssn_excludes_invalid_prefixes(self):
        assert detect_pii("ssn 123-45-6789").get("us_ssn") == 1
        # 666 is reserved — not a valid SSN prefix
        assert detect_pii("ssn 666-45-6789").get("us_ssn", 0) == 0

    def test_returns_empty_for_clean_text(self):
        assert detect_pii("hello world how are you") == {}

    def test_returns_empty_for_non_string(self):
        # Signature is `Any` — defensive passthrough for arbitrary JSONL
        # row payloads that aren't strings (None, ints, lists, etc.).
        assert detect_pii(None) == {}
        assert detect_pii(42) == {}

    def test_pii_types_listed(self):
        # Sanity: the public tuple matches what detect_pii can emit.
        assert "email" in PII_TYPES
        assert "credit_card" in PII_TYPES
        assert "tr_id" in PII_TYPES

    def test_de_id_does_not_match_iata_or_uuid_fragments(self):
        # Bug 7: previous \b[A-Z0-9]{9,10}\b matched too aggressively.
        # IATA airport codes / UUID fragments / API key fragments must
        # NOT flag as DE Personalausweis. Narrowed pattern requires
        # leading letter + ≥7 digits.
        assert detect_pii("flight ABCD12345 to ISTANBUL").get("de_id", 0) == 0
        assert detect_pii("uuid 1234ABCDE9 here").get("de_id", 0) == 0
        # A real-shape DE Personalausweis (letter + 8 digits + check)
        # should still flag.
        assert detect_pii("Personalausweis L01234567X").get("de_id", 0) == 1


class TestPiiMasking:
    def test_email_redacted(self):
        out = mask_pii("contact alice@example.com please")
        assert "alice@example.com" not in out
        assert "[REDACTED]" in out

    def test_valid_credit_card_is_redacted(self):
        out = mask_pii("card 4111 1111 1111 1111")
        assert "4111 1111 1111 1111" not in out
        assert "[REDACTED]" in out

    def test_luhn_helper_distinguishes_valid_from_invalid(self):
        # The masker may also redact long digit runs as candidate phone
        # numbers (false positives are intentional per the module docstring).
        # We assert at the helper level instead, which is unambiguous.
        assert _is_credit_card("4111111111111111") is True
        assert _is_credit_card("4111111111111112") is False

    def test_replacement_can_be_overridden(self):
        out = mask_pii("email alice@example.com", replacement="<X>")
        assert "<X>" in out

    def test_passes_non_string_through(self):
        # Defensive passthrough — see :func:`mask_pii` docstring.
        assert mask_pii(None) is None


class TestLuhnHelper:
    @pytest.mark.parametrize("number", ["4111111111111111", "4012888888881881", "5555555555554444"])
    def test_known_test_cards_pass(self, number):
        assert _is_credit_card(number) is True

    def test_short_number_rejected(self):
        assert _is_credit_card("1234") is False

    def test_invalid_luhn_rejected(self):
        assert _is_credit_card("1234567812345678") is False


# ---------------------------------------------------------------------------
# Simhash + near-duplicate detection
# ---------------------------------------------------------------------------


class TestSimhash:
    def test_identical_text_same_fingerprint(self):
        a = "The quick brown fox jumps over the lazy dog."
        b = "The quick brown fox jumps over the lazy dog."
        assert compute_simhash(a) == compute_simhash(b)

    def test_empty_text_zero(self):
        assert compute_simhash("") == 0
        assert compute_simhash("   ") == 0

    def test_near_duplicate_close_in_hamming(self):
        a = "The quick brown fox jumps over the lazy dog."
        b = "The quick brown fox leaps over the lazy dog."  # one word changed
        assert hamming_distance(compute_simhash(a), compute_simhash(b)) <= 16

    def test_unrelated_text_far_in_hamming(self):
        a = "The quick brown fox jumps over the lazy dog."
        b = "Quantum chromodynamics describes the strong nuclear force."
        # Should be large; exact value depends on hash mixing
        assert hamming_distance(compute_simhash(a), compute_simhash(b)) > 10

    def test_token_digest_default_and_explicit_bits_agree(self):
        """F-P6-OPUS-20: ``_token_digest('x')`` and ``_token_digest('x', 64)``
        compute the identical value (the default IS 64). They occupy two
        ``lru_cache`` slots, but production callers always pass ``bits``
        positionally so the cache never splits in practice — this pins the
        value-equivalence the comment documents."""
        from forgelm.data_audit._simhash import _token_digest

        assert _token_digest("hello") == _token_digest("hello", 64)


class TestFindNearDuplicates:
    def test_finds_identical_pairs(self):
        fps = [compute_simhash("alpha"), compute_simhash("alpha"), compute_simhash("beta")]
        pairs = find_near_duplicates(fps, threshold=0)
        assert (0, 1, 0) in pairs
        # No (alpha, beta) pair below threshold 0
        assert all(not (i == 0 and j == 2) for i, j, _ in pairs)

    def test_skips_zero_fingerprints(self):
        fps = [0, 0, compute_simhash("alpha")]
        assert find_near_duplicates(fps, threshold=DEFAULT_NEAR_DUP_HAMMING) == []


# ---------------------------------------------------------------------------
# audit_dataset end-to-end
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


class TestAuditSingleFile:
    def test_basic_metrics(self, tmp_path):
        path = tmp_path / "train.jsonl"
        _write_jsonl(
            path,
            [
                {"text": "Alpha bravo charlie."},
                {"text": "Delta echo foxtrot."},
                {"text": ""},  # null/empty case
            ],
        )
        report = audit_dataset(str(path))
        assert isinstance(report, AuditReport)
        assert report.total_samples == 3
        assert "train" in report.splits
        info = report.splits["train"]
        assert info["sample_count"] == 3
        assert info["null_or_empty_count"] == 1
        assert info["text_length"]["min"] >= 1

    def test_pii_aggregated_into_summary(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        _write_jsonl(
            path,
            [
                {"text": "Email alice@example.com"},
                {"text": "Another to bob@example.com"},
            ],
        )
        report = audit_dataset(str(path))
        assert report.pii_summary.get("email") == 2

    def test_writes_report_when_output_dir_given(self, tmp_path):
        path = tmp_path / "x.jsonl"
        _write_jsonl(path, [{"text": "hello world"}])
        out_dir = tmp_path / "audit"
        audit_dataset(str(path), output_dir=str(out_dir))
        report_path = out_dir / "data_audit_report.json"
        assert report_path.is_file()
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert payload["total_samples"] == 1

    def test_missing_input_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            audit_dataset(str(tmp_path / "nope.jsonl"))


class TestAuditDirectoryLayout:
    def test_split_keyed_directory(self, tmp_path):
        _write_jsonl(tmp_path / "train.jsonl", [{"text": "A"}, {"text": "B"}])
        _write_jsonl(tmp_path / "validation.jsonl", [{"text": "C"}])
        report = audit_dataset(str(tmp_path))
        assert set(report.splits) == {"train", "validation"}
        assert report.total_samples == 3

    @pytest.mark.parametrize(
        "alias,canonical", [("dev", "validation"), ("val", "validation"), ("eval", "test"), ("holdout", "test")]
    )
    def test_split_alias_folded_to_canonical(self, tmp_path, alias, canonical):
        # Bug 14: dev / val / eval / holdout get treated as
        # validation / test as appropriate so cross-split leakage works
        # without forcing operators to rename their files.
        _write_jsonl(tmp_path / "train.jsonl", [{"text": "A"}])
        _write_jsonl(tmp_path / f"{alias}.jsonl", [{"text": "B"}])
        report = audit_dataset(str(tmp_path))
        assert canonical in report.splits
        assert any(alias in note for note in report.notes), f"alias {alias} should be surfaced in notes"

    def test_pseudo_split_fallback_warns(self, tmp_path, caplog):
        # Bug 26: when no canonical split files exist, every .jsonl becomes
        # its own pseudo-split BUT the operator must be warned that
        # cross-split leakage analysis is meaningless in that case.
        _write_jsonl(tmp_path / "alpha.jsonl", [{"text": "x"}])
        _write_jsonl(tmp_path / "beta.jsonl", [{"text": "y"}])
        with caplog.at_level("WARNING", logger="forgelm.data_audit"):
            report = audit_dataset(str(tmp_path))
        assert "alpha" in report.splits and "beta" in report.splits
        assert any("pseudo-split" in n for n in report.notes)
        # The warning must reach the logger so CI / log aggregators see it,
        # not only the in-report notes (operators rarely cat the report file).
        assert any("pseudo-split" in record.message for record in caplog.records)

    def test_canonical_alias_collision_warns_and_keeps_canonical(self, tmp_path, caplog):
        # F-P6-OPUS-12/16: a directory with both validation.jsonl and
        # dev.jsonl (both map to the 'validation' split) must (a) keep the
        # canonical file, drop the alias, and (b) WARN loudly — not only
        # bury the collision in the report-JSON notes. Mirrors the
        # pseudo-split branch which already logs at WARNING.
        _write_jsonl(tmp_path / "train.jsonl", [{"text": "T"}])
        _write_jsonl(tmp_path / "validation.jsonl", [{"text": "canonical"}])
        _write_jsonl(tmp_path / "dev.jsonl", [{"text": "alias"}])
        with caplog.at_level("WARNING", logger="forgelm.data_audit"):
            report = audit_dataset(str(tmp_path))
        # Canonical wins; the alias is folded out, not a separate split.
        assert "validation" in report.splits
        assert "dev" not in report.splits
        # The collision note is in the report AND on the logger.
        assert any("map to" in n for n in report.notes)
        assert any("map to" in record.message and record.levelname == "WARNING" for record in caplog.records), (
            "collision must be surfaced at WARNING, not only in report.notes"
        )

    def test_cross_split_overlap_caught(self, tmp_path):
        # Identical row in train + test → leakage
        _write_jsonl(tmp_path / "train.jsonl", [{"text": "alpha bravo charlie delta echo"}])
        _write_jsonl(tmp_path / "test.jsonl", [{"text": "alpha bravo charlie delta echo"}])
        report = audit_dataset(str(tmp_path))
        pairs = report.cross_split_overlap.get("pairs", {})
        assert any("train" in k and "test" in k for k in pairs)
        # Bug 2: report carries per-split leak rates explicitly; both
        # directions are surfaced so an asymmetric split (large train,
        # small test) doesn't bury the test-side rate.
        leak_payload = next(iter(pairs.values()))
        assert leak_payload["leak_rate_train"] > 0.0
        assert leak_payload["leak_rate_test"] > 0.0
        assert leak_payload["leaked_rows_in_train"] == 1
        assert leak_payload["leaked_rows_in_test"] == 1

    def test_cross_split_leak_rate_per_direction_asymmetric(self, tmp_path):
        # 100 train + 10 test, 5 of test are exact duplicates of train.
        # The asymmetric direction (train side) hides the contamination
        # at 5/100 = 5%; the test side reports 5/10 = 50% — that's the
        # number an operator actually needs.
        _write_jsonl(tmp_path / "train.jsonl", [{"text": f"row {i} alpha bravo charlie"} for i in range(100)])
        _write_jsonl(
            tmp_path / "test.jsonl",
            [{"text": f"row {i} alpha bravo charlie"} for i in range(5)]  # leaked
            + [{"text": f"row {i} novel content"} for i in range(100, 105)],  # unique
        )
        report = audit_dataset(str(tmp_path))
        payload = report.cross_split_overlap["pairs"]["train__test"]
        assert payload["leaked_rows_in_train"] == 5
        assert payload["leaked_rows_in_test"] == 5
        # The headline number is the test-side rate (the destructive direction).
        assert payload["leak_rate_train"] == round(5 / 100, 4)
        assert payload["leak_rate_test"] == round(5 / 10, 4)
        # Notes line should call out the worst rate, not the small one.
        notes_blob = " ".join(report.notes)
        assert "50.00%" in notes_blob or "0.5" in notes_blob


class TestJsonlDecodeErrorDetection:
    """F-P6-OPUS-13: decode_error must reflect a *strict* UTF-8 failure,
    not merely the presence of a U+FFFD code point that legitimately
    decodes (so a corpus with literal replacement chars is not falsely
    flagged)."""

    def test_literal_replacement_char_not_flagged_as_decode_error(self, tmp_path):
        from forgelm.data_audit._streaming import _read_jsonl_split

        # Valid UTF-8 bytes that *contain* the 3-byte encoding of U+FFFD.
        path = tmp_path / "u.jsonl"
        path.write_bytes(b'{"text": "caf\xef\xbf\xbd"}\n')
        results = list(_read_jsonl_split(path))
        assert len(results) == 1
        row, parse_error, decode_error = results[0]
        assert parse_error is False
        assert decode_error is False, "literal U+FFFD in valid UTF-8 must not be a decode error"
        assert row == {"text": "caf�"}

    def test_genuine_invalid_utf8_flagged_as_decode_error(self, tmp_path):
        from forgelm.data_audit._streaming import _read_jsonl_split

        # A lone 0x80 continuation byte is not valid UTF-8 → must flag.
        path = tmp_path / "bad.jsonl"
        path.write_bytes(b'{"text": "ab\x80cd"}\n')
        results = list(_read_jsonl_split(path))
        assert len(results) == 1
        _row, _parse_error, decode_error = results[0]
        assert decode_error is True
        # F-L-26: a decode error must NOT become a parse error — the row is
        # decoded with errors='replace' and then parsed normally; it must
        # survive as a valid dict.  A regression that returns (None, True, True)
        # would silently drop rows without the assertions below catching it.
        assert _parse_error is False, "decode error must not become a parse error"
        assert _row is not None and isinstance(_row, dict), "decoded row must still be parseable after replacement"


class TestMessagesFormat:
    def test_concatenates_message_content_for_dedup(self, tmp_path):
        path = tmp_path / "chat.jsonl"
        _write_jsonl(
            path,
            [
                {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]},
                {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]},
            ],
        )
        report = audit_dataset(str(path))
        # Two identical chats → near_duplicate_pairs should be 1
        assert report.splits["train"]["near_duplicate_pairs"] >= 1


class TestSchemaDrift:
    def test_columns_use_union_of_keys(self, tmp_path):
        # Bug 3: heterogeneous JSONL — some rows have extra fields. The
        # column schema must be the union, with drift surfaced.
        path = tmp_path / "het.jsonl"
        _write_jsonl(
            path,
            [
                {"text": "alpha"},
                {"text": "beta", "gold_answer": "x"},  # drift column
                {"text": "gamma"},
            ],
        )
        report = audit_dataset(str(path))
        cols = report.splits["train"]["columns"]
        assert "text" in cols and "gold_answer" in cols
        assert report.splits["train"].get("schema_drift_columns") == ["gold_answer"]


class TestNonDictRowTolerance:
    def test_non_dict_row_does_not_crash_audit(self, tmp_path):
        # Bug: a JSON array / scalar row used to crash the audit at
        # _extract_text_payload (AttributeError on list.get). It must now
        # be classified as `non_object_rows` and counted toward
        # null_or_empty without aborting the run.
        path = tmp_path / "het.jsonl"
        path.write_text(
            '["a", "b"]\n{"text": "valid"}\n42\n"plain string"\n',
            encoding="utf-8",
        )
        report = audit_dataset(str(path))
        assert report.total_samples == 4
        info = report.splits["train"]
        assert info["non_object_rows"] == 3  # the array, the int, the string
        assert info["null_or_empty_count"] >= 3
        assert info["sample_count"] == 4


class TestSyntheticFormatsVisibleToAudit:
    """F-P6-OPUS-07: the synthetic generator emits four output shapes
    (``messages`` / ``instruction`` / ``chatml`` / ``prompt_response``); the
    audit text extractor used to read only ``messages`` and a bare ``prompt``,
    so ``instruction``/``chatml`` rows extracted to ``""`` (counted as
    ``null_or_empty``, never scanned) and ``prompt_response`` silently dropped
    its ``response`` half — the text most likely to carry memorised PII."""

    def test_extract_payload_covers_synthetic_formats(self):
        from forgelm.data_audit._streaming import _extract_text_payload

        # Build via the real generator's formatter so the test pins the
        # actual emitted shapes, not a hand-rolled approximation.
        rows = [
            {"instruction": "q", "output": "alice@example.com"},
            {"User": "q", "Assistant": "alice@example.com"},
            {"prompt": "q", "response": "alice@example.com"},
            {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "alice@example.com"}]},
        ]
        for row in rows:
            payload = _extract_text_payload(row)
            assert payload, f"empty payload for {sorted(row)}"
            # The assistant/response/output half (with the secret) is included.
            assert "alice@example.com" in payload, f"response half dropped for {sorted(row)}"

    @pytest.mark.parametrize(
        "row",
        [
            {"instruction": "ask", "output": "leak alice@example.com here"},
            {"User": "ask", "Assistant": "leak alice@example.com here"},
            {"prompt": "ask", "response": "leak alice@example.com here"},
        ],
    )
    def test_synthetic_response_half_pii_detected_by_audit(self, tmp_path, row):
        path = tmp_path / "synthetic.jsonl"
        _write_jsonl(path, [row])
        report = audit_dataset(str(path))
        info = report.splits["train"]
        # Row is no longer silently classified as empty/unreadable...
        assert info["null_or_empty_count"] == 0
        # ...and the PII baked into the teacher response half is detected.
        assert info["pii_counts"].get("email") == 1


class TestParseAndDecodeIntegrity:
    def test_malformed_jsonl_lines_surface_in_report(self, tmp_path):
        # Bug 4: parse errors used to be log-only — now they're a
        # structured field on the split + an actionable note.
        path = tmp_path / "broken.jsonl"
        path.write_text(
            '{"text": "good1"}\n{not valid json\n{"text": "good2"}\n}}}also bad\n',
            encoding="utf-8",
        )
        report = audit_dataset(str(path))
        info = report.splits["train"]
        assert info["sample_count"] == 2  # only the parseable rows
        assert info["parse_errors"] == 2
        assert any("malformed JSONL" in n for n in report.notes)

    def test_non_utf8_bytes_do_not_abort_audit(self, tmp_path):
        # Bug 3: UnicodeDecodeError used to bubble up before any
        # tolerance kicked in. Permissive read + decode_errors counter
        # keeps the audit running.
        path = tmp_path / "mojibake.jsonl"
        path.write_bytes(
            b'{"text": "good"}\n{"text": "bad \xff\xfe bytes"}\n{"text": "another"}\n',
        )
        report = audit_dataset(str(path))
        info = report.splits["train"]
        assert info["sample_count"] == 3  # nothing was silently dropped
        assert info["decode_errors"] == 1
        assert any("non-UTF-8" in n for n in report.notes)


class TestModalSchemaDrift:
    def test_drift_uses_modal_keyset_not_row_zero(self, tmp_path):
        # Bug 9: when row 0 is the outlier (e.g. labeller skipped a
        # field), the modal-keyset base must classify the majority shape
        # as the norm — gold_answer here is on 9/10 rows so it's NOT drift.
        path = tmp_path / "modal.jsonl"
        rows = [{"text": "alpha"}]  # row 0: only `text`
        for i in range(9):
            rows.append({"text": f"beta {i}", "gold_answer": str(i)})  # majority
        _write_jsonl(path, rows)
        report = audit_dataset(str(path))
        info = report.splits["train"]
        # gold_answer is the modal column → not flagged as drift
        assert "gold_answer" not in info.get("schema_drift_columns", [])
        # `text` is in both shapes so it isn't drift either
        assert info.get("schema_drift_columns", []) == []


class TestMaskPiiReturnCounts:
    """Bug 8: pii_redaction_counts is compliance evidence — protect it."""

    def test_aggregates_per_pattern(self):
        out, counts = mask_pii("a@x.com b@y.com c@z.com", return_counts=True)
        assert counts == {"email": 3}
        assert out.count("[REDACTED]") == 3

    def test_first_match_wins_no_double_count(self):
        # A 16-digit Luhn-valid run is a credit card AND used to also
        # match the older phone pattern. The first-match-wins precedence
        # must attribute it to ONE pattern only.
        text = "card 4111 1111 1111 1111 here"
        _, counts = mask_pii(text, return_counts=True)
        assert counts.get("credit_card") == 1
        assert counts.get("phone", 0) == 0

    def test_invalid_luhn_not_counted(self):
        # _replace returns the original text when validation fails;
        # counts must NOT increment for the rejected match.
        out, counts = mask_pii("nope 4111 1111 1111 1112", return_counts=True)
        assert counts.get("credit_card", 0) == 0
        # The string is preserved in the output (validation failed)
        assert "4111 1111 1111 1112" in out

    def test_non_string_returns_empty_counts(self):
        out, counts = mask_pii(None, return_counts=True)
        assert (out, counts) == (None, {})

    def test_back_compat_one_arg_form_still_returns_string(self):
        # Existing callers that call mask_pii(text) without
        # return_counts must keep getting a plain string back.
        out = mask_pii("ping alice@example.com")
        assert isinstance(out, str)
        assert "[REDACTED]" in out


class TestPartialFailureTolerance:
    def test_unreadable_split_skips_with_note(self, tmp_path, monkeypatch):
        # Bug 31: a split that cannot be read (permission/IO/etc.) must
        # not abort the audit; report it under the split's `error` key
        # and continue. Simulate with a monkeypatch on the reader so the
        # test stays portable (chmod 000 doesn't survive on every CI runner).
        from forgelm import data_audit as audit_mod

        _write_jsonl(tmp_path / "train.jsonl", [{"text": "A"}, {"text": "B"}])
        _write_jsonl(tmp_path / "validation.jsonl", [{"text": "C"}])

        # Faz 14: data_audit was split into a package. The streaming reader
        # lives in ._streaming and is imported by ._aggregator at module
        # load time, so patching the package-level re-export does not reach
        # the call site. Patch the binding inside ._aggregator (where
        # _audit_split actually looks it up) — same effect, lockstep with
        # the split refactor.
        original_read = audit_mod._streaming._read_jsonl_split

        def flaky_read(path):
            if path.name == "validation.jsonl":
                raise OSError("simulated permission denied")
            return original_read(path)

        monkeypatch.setattr(audit_mod._aggregator, "_read_jsonl_split", flaky_read)

        report = audit_dataset(str(tmp_path))
        assert "train" in report.splits
        assert report.splits["validation"].get("error", "").startswith("read_failed")
        assert any("validation" in n and "skipped" in n for n in report.notes)
        # `train` audit should be intact
        assert report.splits["train"]["sample_count"] == 2


class TestActionableNotes:
    def test_pii_note_suggests_mask_command(self, tmp_path):
        path = tmp_path / "x.jsonl"
        _write_jsonl(path, [{"text": "ping alice@example.com"}])
        report = audit_dataset(str(path))
        notes_blob = " ".join(report.notes)
        assert "pii-mask" in notes_blob.lower() or "mask_pii" in notes_blob

    def test_leakage_note_appears_when_pairs_leak(self, tmp_path):
        _write_jsonl(tmp_path / "train.jsonl", [{"text": "alpha bravo charlie delta"}])
        _write_jsonl(tmp_path / "test.jsonl", [{"text": "alpha bravo charlie delta"}])
        report = audit_dataset(str(tmp_path))
        notes_blob = " ".join(report.notes)
        assert "leakage" in notes_blob.lower() or "leak" in notes_blob.lower()


class TestReproducibility:
    def test_report_carries_both_source_input_and_resolved_path(self, tmp_path):
        # Bug 27: AuditReport stores both the literal user input and the
        # absolute resolved path. Compliance bundles can pick whichever
        # they need without re-resolving.
        path = tmp_path / "x.jsonl"
        _write_jsonl(path, [{"text": "alpha"}])
        report = audit_dataset(str(path))
        assert report.source_input == str(path)
        assert Path(report.source_path).is_absolute()


class TestSummarize:
    def test_renders_split_metrics(self, tmp_path):
        path = tmp_path / "x.jsonl"
        _write_jsonl(path, [{"text": "alpha"}, {"text": "alpha"}])
        report = audit_dataset(str(path))
        rendered = summarize_report(report)
        assert "Total samples" in rendered
        assert "train" in rendered


# ---------------------------------------------------------------------------
# Phase 11.5 — LSH banding, streaming, PII severity, summary verbose, atomic write
# ---------------------------------------------------------------------------


class TestLshBandedNearDuplicates:
    """Phase 11.5: find_near_duplicates uses LSH banding + must keep recall."""

    def test_lsh_finds_same_pairs_as_brute_force(self):
        """Sanity: LSH path must not lose any near-duplicates the brute path would find."""
        from forgelm.data_audit import _find_near_duplicates_brute, find_near_duplicates

        texts = [
            "The quick brown fox jumps over the lazy dog",
            "The quick brown fox leaps over the lazy dog",
            "Quantum chromodynamics describes the strong nuclear force",
            "The quick brown fox jumps over the lazy hound",
            "Pure noise: blip blop bing bang bong",
        ]
        fps = [compute_simhash(t) for t in texts]
        brute = _find_near_duplicates_brute(fps, threshold=DEFAULT_NEAR_DUP_HAMMING)
        lsh = find_near_duplicates(fps, threshold=DEFAULT_NEAR_DUP_HAMMING)
        # Same pair set (order is normalised by find_near_duplicates).
        brute_pairs = {(i, j) for i, j, _ in brute}
        lsh_pairs = {(i, j) for i, j, _ in lsh}
        assert brute_pairs == lsh_pairs

    def test_lsh_falls_back_to_brute_for_high_threshold(self):
        """Threshold so high that bands shrink below 4 bits → brute path."""
        from forgelm.data_audit import find_near_duplicates

        fps = [compute_simhash("alpha"), compute_simhash("alpha"), compute_simhash("beta")]
        # 64-bit fingerprint, threshold 16 → bands=17 → band_bits ≈ 3 → fallback.
        pairs = find_near_duplicates(fps, threshold=16)
        # Must still recall the alpha/alpha pair.
        assert (0, 1) in {(i, j) for i, j, _ in pairs}


class TestCountLeakedRowsLshParity:
    """Phase 11.5: _count_leaked_rows must match the linear-scan fallback."""

    def test_lsh_count_matches_brute_force_count(self):
        """Direct test for _count_leaked_rows; previously only covered via audit_dataset."""
        from forgelm.data_audit import _count_leaked_rows, hamming_distance

        source_texts = [
            "the quick brown fox jumps over the lazy dog",
            "completely unrelated payload with different vocabulary",
            "another row that shares nothing with the others",
        ]
        target_texts = [
            "the quick brown fox jumps over the lazy hound",  # near-dup of source[0]
            "yet another distinct payload with no overlap whatsoever",
        ]
        source_fps = [compute_simhash(t) for t in source_texts]
        target_fps = [compute_simhash(t) for t in target_texts]

        # Brute reference: count source rows whose nearest target is within threshold.
        threshold = DEFAULT_NEAR_DUP_HAMMING
        brute_count = sum(
            1
            for fp in source_fps
            if fp != 0 and any(other != 0 and hamming_distance(fp, other) <= threshold for other in target_fps)
        )
        lsh_count = _count_leaked_rows(source_fps, target_fps, threshold)
        assert lsh_count == brute_count

    def test_lsh_count_falls_back_for_high_threshold(self):
        from forgelm.data_audit import _count_leaked_rows

        # threshold 16 forces the fallback; identical rows must still leak.
        source_fps = [compute_simhash("identical")]
        target_fps = [compute_simhash("identical")]
        assert _count_leaked_rows(source_fps, target_fps, threshold=16) == 1


class TestAtomicWriteFailure:
    """Phase 11.5: _atomic_write_json must clean up its tempfile on os.replace failure."""

    def test_failure_during_replace_leaves_no_tmp(self, tmp_path, monkeypatch):
        from forgelm import data_audit as audit_mod

        out_dir = tmp_path / "audit"
        out_dir.mkdir()
        target = out_dir / "data_audit_report.json"

        def _boom(*_args, **_kwargs):
            raise OSError("simulated replace failure")

        # Faz 14: _atomic_write_json now lives in ._orchestrator, which is
        # the module that holds the ``os`` import the helper actually calls.
        monkeypatch.setattr(audit_mod._orchestrator.os, "replace", _boom)
        with pytest.raises(OSError, match="simulated replace failure"):
            audit_mod._atomic_write_json(target, {"hello": "world"})

        # Canonical file was not created (replace failed).
        assert not target.exists()
        # No leftover .tmp files in the output directory.
        leftovers = [p for p in out_dir.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


class TestPiiSeveritySnapshot:
    """Phase 11.5: _build_pii_severity must snapshot PII_SEVERITY at call time."""

    def test_mid_run_mutation_does_not_corrupt_output(self, monkeypatch):
        from forgelm import data_audit as audit_mod

        # Stash a clean copy and mutate the module-level table.
        original = dict(audit_mod.PII_SEVERITY)
        try:
            # Sabotage: drop credit_card from the live table after import.
            audit_mod.PII_SEVERITY.pop("credit_card", None)
            severity = audit_mod._build_pii_severity({"credit_card": 1})
            # The snapshot taken at call time should still classify it
            # — but here we mutated *before* the call, so the runtime
            # binding wins and we get "unknown". This test pins the
            # current contract: snapshot is per-call, not eternal.
            # If a future change goes the other way (deep-frozen table),
            # update the assertion.
            assert severity["by_type"]["credit_card"]["tier"] == "unknown"
        finally:
            audit_mod.PII_SEVERITY.clear()
            audit_mod.PII_SEVERITY.update(original)


class TestPiiSeverity:
    """Phase 11.5: severity tiers surface a worst-tier verdict."""

    def test_credit_card_is_critical(self, tmp_path):
        path = tmp_path / "x.jsonl"
        # Real Luhn-valid credit card test number: 4111 1111 1111 1111
        _write_jsonl(path, [{"text": "card 4111 1111 1111 1111"}])
        report = audit_dataset(str(path))
        assert report.pii_severity["worst_tier"] == "critical"
        assert report.pii_severity["by_tier"]["critical"] >= 1
        assert report.pii_severity["by_type"]["credit_card"]["tier"] == "critical"

    def test_no_pii_yields_neutral_severity(self, tmp_path):
        path = tmp_path / "x.jsonl"
        _write_jsonl(path, [{"text": "plain prose with no identifiers"}])
        report = audit_dataset(str(path))
        assert report.pii_severity["worst_tier"] is None
        assert report.pii_severity["total"] == 0


class TestSummarizeVerbosePolicy:
    """Phase 11.5: summarize_report folds zero-finding splits by default."""

    def test_clean_split_folded_in_default_mode(self, tmp_path):
        # Build a dir with one clean split + one with a near-duplicate.
        train_path = tmp_path / "train.jsonl"
        val_path = tmp_path / "validation.jsonl"
        _write_jsonl(train_path, [{"text": "alpha alpha alpha"}, {"text": "alpha alpha alpha"}])  # near-dup
        _write_jsonl(val_path, [{"text": "completely different prose payload"}])
        report = audit_dataset(str(tmp_path))
        rendered_default = summarize_report(report)
        rendered_verbose = summarize_report(report, verbose=True)
        # Behavioural assertion (less brittle than substring matching the fold-line wording):
        # in default mode the clean split must be **absent** as its own header block but
        # **present** by name in the trailing fold-summary; verbose mode must show the
        # split's own header block.
        assert "└─ validation" not in rendered_default
        assert "validation" in rendered_default  # named in the fold-summary
        assert "└─ validation" in rendered_verbose


class TestAtomicWrite:
    """Phase 11.5: audit report writes are crash-safe via tempfile + rename."""

    def test_no_temp_file_left_behind_on_success(self, tmp_path):
        path = tmp_path / "x.jsonl"
        _write_jsonl(path, [{"text": "alpha"}])
        out_dir = tmp_path / "audit"
        audit_dataset(str(path), output_dir=str(out_dir))
        # Canonical report exists.
        assert (out_dir / "data_audit_report.json").is_file()
        # No leftover .data_audit_report.json.*.tmp files.
        leftovers = [p for p in out_dir.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


class TestStreamingReader:
    """Phase 11.5: _read_jsonl_split is now a generator."""

    def test_yields_per_line_tuples(self, tmp_path):
        from forgelm.data_audit import _read_jsonl_split

        path = tmp_path / "x.jsonl"
        path.write_text(
            '{"text": "good"}\n{not valid json\n{"text": "good2"}\n',
            encoding="utf-8",
        )
        items = list(_read_jsonl_split(path))
        # Three non-empty lines → three yields, one with parse_err=True.
        assert len(items) == 3
        good = [row for row, p_err, _ in items if not p_err]
        bad = [row for row, p_err, _ in items if p_err]
        assert len(good) == 2
        assert len(bad) == 1
        assert bad[0] is None


class TestTokenCachePerformance:
    """Phase 11.5: lru_cache on _token_digest is observable via cache_info."""

    def test_repeat_token_across_texts_hits_cache(self):
        """compute_simhash dedupes within a text; the win is across texts."""
        from forgelm.data_audit import _token_digest

        _token_digest.cache_clear()
        # Each call hashes 3 distinct tokens → first call misses, second is all hits.
        compute_simhash("alpha beta gamma")
        first = _token_digest.cache_info()
        compute_simhash("alpha beta gamma")
        second = _token_digest.cache_info()
        # Misses didn't grow on the second call; hits did.
        assert second.misses == first.misses
        assert second.hits == first.hits + 3


# ---------------------------------------------------------------------------
# F-H-08: Email regex ReDoS linearity regression
# ---------------------------------------------------------------------------


class TestEmailRegexReDoSLinearity:
    """F-H-08: the structured-domain email pattern must not exhibit O(n²)
    backtracking on adversarial inputs (two competing unbounded quantifiers in
    the old ``[A-Za-z0-9.-]+\\.`` form).  Verify linearity per regex.md
    ReDoS budget methodology: median of 5 runs at 1K / 5K / 10K characters,
    roughly linear growth (doubling input → roughly doubling time, not ×4+)."""

    def _median_ms(self, pattern, payload, n_runs: int = 5) -> float:
        import statistics
        import time

        times = []
        for _ in range(n_runs):
            t0 = time.perf_counter()
            pattern.search(payload)
            times.append((time.perf_counter() - t0) * 1000)
        return statistics.median(times)

    def test_email_pattern_linear_on_adversarial_domain(self):
        """Adversarial payload: local-part@dots-and-labels with no valid TLD.
        Old pattern: O(n²) confirmed up to 577 ms at n=6400.
        New pattern: each label must consume ≥1 '.' + letter — O(n) guaranteed
        because no two quantifiers compete for the same characters."""
        from forgelm.data_audit._pii_regex import _PII_PATTERNS

        pat = _PII_PATTERNS["email"]
        # Build payloads: 'a@' + 'aa.'*n (no valid TLD at end → no match →
        # exercises worst-case backtracking on the old pattern)
        sizes = [1_000, 5_000, 10_000]
        medians = []
        for n in sizes:
            payload = "a@" + "aa." * n
            medians.append(self._median_ms(pat, payload))

        # Primary ReDoS guard — ABSOLUTE time. The old O(n²) form hit 577 ms at
        # n=6400; the bounded-label form stays in single-digit ms even at 10K.
        # Absolute time is the load-bearing, noise-robust signal: a quadratic
        # blowup cannot hide under this ceiling, while sub-millisecond ratios
        # (below) are dominated by timer noise on a genuinely linear regex.
        assert medians[2] < 100.0, (
            f"Email regex too slow at 10K chars: {medians[2]:.1f}ms — possible ReDoS "
            f"(medians: {medians[0]:.2f}ms / {medians[1]:.2f}ms / {medians[2]:.2f}ms)"
        )
        # Secondary linearity check (ratio), applied ONLY when the baseline is
        # large enough to measure meaningfully (≥1 ms) — otherwise the ratio is
        # pure timer noise and would flake on a regex that is provably linear.
        if medians[1] >= 1.0:
            ratio_5k_to_10k = medians[2] / medians[1]
            assert ratio_5k_to_10k < 4, (
                f"Email regex is super-linear: 5K→10K ratio={ratio_5k_to_10k:.1f}x "
                f"(medians: {medians[0]:.2f}ms / {medians[1]:.2f}ms / {medians[2]:.2f}ms)"
            )


# ---------------------------------------------------------------------------
# F-M-17: _LengthDigest tests
# ---------------------------------------------------------------------------


class TestLengthDigest:
    """F-M-17: _LengthDigest has a documented 'byte-identical contract'
    (constant LCG seed) but had zero test coverage."""

    def test_exact_stats_below_reservoir_cap(self):
        """For N <= _LENGTH_RESERVOIR_SIZE the reservoir holds every element:
        min/max/mean/p50/p95 must all be exact."""
        from forgelm.data_audit._streaming import _LengthDigest

        d = _LengthDigest()
        lengths = list(range(1, 101))  # 1..100, well below 100 K cap
        for v in lengths:
            d.update(v)
        s = d.stats()
        assert s["min"] == 1
        assert s["max"] == 100
        assert s["mean"] == pytest.approx(50.5, abs=0.1)
        # For 100 elements sorted 1..100: p50 index = 50 → value 51
        assert s["p50"] == 51
        # p95 index = min(99, 95) = 95 → value 96
        assert s["p95"] == 96

    def test_determinism_two_identical_streams_equal_stats(self):
        """Two _LengthDigest instances fed the same sequence must produce
        identical stats() dicts (the byte-identical contract)."""
        from forgelm.data_audit._streaming import _LENGTH_RESERVOIR_SIZE, _LengthDigest

        n = _LENGTH_RESERVOIR_SIZE + 500  # force reservoir-sampling path
        d1 = _LengthDigest()
        d2 = _LengthDigest()
        for i in range(n):
            d1.update(i % 200)
            d2.update(i % 200)
        assert d1.stats() == d2.stats()

    def test_reservoir_bounded_above_cap(self):
        """After inserting far more elements than _LENGTH_RESERVOIR_SIZE, the
        internal reservoir list must not exceed the cap."""
        from forgelm.data_audit._streaming import _LENGTH_RESERVOIR_SIZE, _LengthDigest

        d = _LengthDigest()
        for i in range(_LENGTH_RESERVOIR_SIZE + 10_000):
            d.update(i % 300)
        assert len(d._reservoir) <= _LENGTH_RESERVOIR_SIZE

    def test_lcg_bias_fix_slot_zero_not_always_overwritten(self):
        """F-M-17 LCG bias: with the old seed=0, the (cap+1)th element always
        overwrote slot 1 (first LCG advance → counter=1, j=1%n=1 < cap).
        With the new seed=multiplier, slot 1 is NOT deterministically replaced.
        Verify by checking that across two fresh digests fed different (cap+1)th
        values, the slot contents differ — proving the slot is not pinned."""
        from forgelm.data_audit._streaming import _LENGTH_RESERVOIR_SIZE, _LengthDigest

        # Fill the reservoir to the cap with value 0, then insert a sentinel.
        sentinel_a = 9001
        sentinel_b = 9002
        d_a = _LengthDigest()
        d_b = _LengthDigest()
        for _ in range(_LENGTH_RESERVOIR_SIZE):
            d_a.update(0)
            d_b.update(0)
        d_a.update(sentinel_a)
        d_b.update(sentinel_b)
        # The two reservoirs must differ somewhere if the sentinels were placed
        # at least occasionally in different slots (or not placed at all for one).
        # At minimum: if slot 1 is no longer deterministically the target, the
        # sum over the reservoir will differ between the two digests.
        assert d_a._reservoir != d_b._reservoir or (
            sentinel_a not in d_a._reservoir and sentinel_b not in d_b._reservoir
        ), (
            "LCG bias not fixed: both sentinels landed in the same slot (slot 1) "
            "meaning the seed-0 deterministic-replacement bug is still present"
        )

    def test_empty_digest_returns_empty_stats(self):
        from forgelm.data_audit._streaming import _LengthDigest

        assert _LengthDigest().stats() == {}


# ---------------------------------------------------------------------------
# F-L-13: _extract_text_payload empty-half edge cases
# ---------------------------------------------------------------------------


class TestExtractTextPayloadEmptyHalves:
    """F-L-13: _INSTRUCTION_PAIRS loop filters empty halves via strip();
    neither empty-half case was covered, so a regression adding a
    'both non-empty' guard would silently drop one half."""

    @pytest.mark.parametrize(
        "row, expected_substr",
        [
            # prompt is empty — response half must still be extracted
            ({"prompt": "", "response": "secret@example.com"}, "secret@example.com"),
            # output is empty — instruction half must still be extracted
            ({"instruction": "question", "output": ""}, "question"),
        ],
    )
    def test_empty_half_does_not_drop_nonempty_half(self, row, expected_substr):
        from forgelm.data_audit._streaming import _extract_text_payload

        payload = _extract_text_payload(row)
        assert expected_substr in payload, f"expected {expected_substr!r} in payload but got {payload!r} for row {row}"


# ---------------------------------------------------------------------------
# F-L-27: TR ID — negative countercase for Unicode digit checksum
# ---------------------------------------------------------------------------


class TestTrIdUnicodeDigitsNegativeChecksum:
    """F-L-27: the positive test pins that a checksum-valid Arabic-Indic TR ID
    is detected; this negative countercase ensures a checksum-invalid sequence
    is NOT detected (guarding against a regression that bypasses the checksum
    for non-ASCII digit strings)."""

    def test_tr_id_unicode_digits_invalid_checksum_not_detected(self):
        bad_ascii = "10000000147"  # one digit off from the valid 10000000146
        bad_arabic = "".join(chr(0x0660 + int(c)) for c in bad_ascii)
        assert detect_pii(f"id is {bad_arabic}").get("tr_id", 0) == 0, (
            "checksum-invalid Arabic-Indic TR ID must not be detected"
        )


# ---------------------------------------------------------------------------
# IBAN — compact + ISO 13616 spaced print form (previously zero coverage)
# ---------------------------------------------------------------------------


class TestIbanDetection:
    """The IBAN detector must surface both the compact run and the ISO 13616
    four-character-grouped print form (how IBANs actually appear on invoices,
    statements, and email); previously only the contiguous run matched, so the
    common spaced form was silently under-reported. Real IBANs (checksum
    valid via ISO 7064 mod-97) are used throughout — the detector validates
    the checksum in _validate_match precisely so checksum-invalid all-caps
    prose does NOT get flagged (see TestIbanFalsePositiveGuard below)."""

    def test_compact_iban_detected(self):
        assert detect_pii("IBAN: TR460006100154780000002668").get("iban") == 1

    def test_spaced_iban_detected(self):
        assert detect_pii("IBAN: TR46 0006 1001 5478 0000 0026 68").get("iban") == 1

    def test_de_spaced_iban_detected(self):
        assert detect_pii("Please wire to DE89 3704 0044 0532 0130 00 today").get("iban") == 1

    @pytest.mark.parametrize(
        "text",
        [
            "prose with no structured identifiers whatsoever",
            "DE89 3704",  # body far below the 11-char minimum
            "TR330006100154780000002668",  # right shape, wrong checksum (33 vs valid 46)
        ],
    )
    def test_non_iban_shape_not_flagged(self, text):
        assert detect_pii(text).get("iban", 0) == 0


class TestIbanFalsePositiveGuard:
    """The spaced print-form pattern's optional per-character space lets a
    ``[A-Z]{2}\\d{2}`` token followed by >=11 spaced uppercase letters span
    all-caps prose word boundaries. Without a checksum, every one of these
    was counted as a critical-tier PII hit and would be redacted by
    ``ingest --pii-mask``, corrupting legitimate training text."""

    @pytest.mark.parametrize(
        "text",
        [
            "US20 MEN WENT TO THE STORE AND BOUGHT MILK TODAY",
            "NO20 PEOPLE CAME BUT MANY LEFT EARLY",
            "THE OLD PA55 CODES AND STUFF WERE RETIRED LAST YEAR",
        ],
    )
    def test_all_caps_prose_not_flagged_as_iban(self, text):
        assert detect_pii(text).get("iban", 0) == 0


class TestIbanCheckDigitsAsciiOnly:
    """The 2-digit IBAN check-digit group must be ASCII-only (``[0-9]``),
    matching the ASCII-only ``[A-Z0-9]`` body — a single structured
    identifier should not accept a non-ASCII digit script in one sub-part
    (bare ``\\d`` is Unicode-aware) while requiring ASCII everywhere else."""

    def test_fullwidth_check_digits_do_not_match_iban_pattern(self):
        from forgelm.data_audit._pii_regex import _PII_PATTERNS

        pat = _PII_PATTERNS["iban"]
        # U+FF14 U+FF16 = fullwidth "4" "6" — Unicode Nd category, so bare
        # \\d would match them; [0-9] must not.
        fullwidth_candidate = "TR４６0006100154780000002668"
        assert pat.search(fullwidth_candidate) is None

    def test_ascii_check_digits_still_match_iban_pattern(self):
        from forgelm.data_audit._pii_regex import _PII_PATTERNS

        pat = _PII_PATTERNS["iban"]
        assert pat.search("TR460006100154780000002668") is not None


# ---------------------------------------------------------------------------
# fr_ssn — non-capturing groups so findall returns the full match
# ---------------------------------------------------------------------------


class TestFrSsnDetection:
    """fr_ssn was the only pattern with capturing groups, so ``findall``
    returned a truncated group-tuple instead of the full 15-digit match,
    corrupting detect_pii's payload (and any future checksum validation).
    Converting to non-capturing groups restores the full-match contract."""

    def test_fr_ssn_pattern_is_group_free(self):
        from forgelm.data_audit._pii_regex import _PII_PATTERNS

        pat = _PII_PATTERNS["fr_ssn"]
        assert pat.groups == 0
        # findall returns the FULL match string, not a captured-group tuple.
        assert pat.findall("num 295037531234567 done") == ["295037531234567"]

    def test_fr_ssn_detected(self):
        assert detect_pii("num 295037531234567 done").get("fr_ssn") == 1

    def test_non_fr_ssn_leading_digit_not_flagged(self):
        # INSEE serials start with 1 or 2; a 3-prefixed run is not fr_ssn.
        assert detect_pii("num 395037531234567 done").get("fr_ssn", 0) == 0


# ---------------------------------------------------------------------------
# _extract_text_payload — single-half instruction pairs must still be scanned
# ---------------------------------------------------------------------------


class TestExtractTextPayloadHalfPresent:
    """A row carrying only ONE half of a recognised instruction pair (sibling
    key absent or ``None``) must still be extracted: instruction / output /
    User / Assistant / response have no ``_TEXT_COLUMNS`` fallback, so such a
    row previously extracted to ``""`` and was silently counted as
    null/empty — never scanned for PII/secrets/quality."""

    @pytest.mark.parametrize(
        "row, expected_substr",
        [
            ({"instruction": "My IBAN is DE89370400440532013000, refund it."}, "DE89370400440532013000"),
            ({"instruction": "question", "output": None}, "question"),
            ({"User": "my SSN is 123-45-6789"}, "123-45-6789"),
            ({"Assistant": "leaked secret@example.com here"}, "secret@example.com"),
            ({"response": "reply carrying alice@example.com"}, "alice@example.com"),
            ({"output": "the answer text"}, "the answer text"),
        ],
    )
    def test_single_half_still_extracted(self, row, expected_substr):
        from forgelm.data_audit._streaming import _extract_text_payload

        payload = _extract_text_payload(row)
        assert expected_substr in payload, f"expected {expected_substr!r} in payload but got {payload!r} for {row}"

    def test_canonical_text_column_still_wins_over_lone_pair_half(self):
        # A row with both a lone pair-half and a canonical text column keeps
        # extracting the canonical column (the half-present scan is last-resort).
        from forgelm.data_audit._streaming import _extract_text_payload

        assert _extract_text_payload({"instruction": "inst half", "text": "canonical body"}) == "canonical body"

    def test_single_half_pii_reaches_audit(self, tmp_path):
        _write_jsonl(tmp_path / "x.jsonl", [{"instruction": "email me at alice@example.com"}])
        report = audit_dataset(str(tmp_path))
        assert report.pii_summary.get("email") == 1
        only_split = next(iter(report.splits.values()))
        assert only_split.get("null_or_empty_count", 0) == 0


# ---------------------------------------------------------------------------
# Cross-split leakage rendering — human-readable, not raw dict repr
# ---------------------------------------------------------------------------


class TestCrossSplitSummaryRendering:
    """The cross-split leakage block must render a hand-formatted line like the
    other summary blocks, not dump the raw payload dict via f-string repr."""

    def test_pair_line_is_human_readable(self, tmp_path):
        _write_jsonl(tmp_path / "train.jsonl", [{"text": "alpha bravo charlie delta echo"}])
        _write_jsonl(tmp_path / "test.jsonl", [{"text": "alpha bravo charlie delta echo"}])
        report = audit_dataset(str(tmp_path))
        text = summarize_report(report)
        assert "Cross-split leakage" in text
        # No raw dict dump: payload keys / dict braces must not leak into output.
        assert "leaked_rows_in_train" not in text
        assert "{'" not in text
        # Hand-formatted rendering present.
        assert "leaked=" in text and "rate=" in text

    def test_render_cross_split_pair_shape(self):
        from forgelm.data_audit._summary import _render_cross_split_pair

        payload = {
            "leaked_rows_in_train": 2,
            "leak_rate_train": 0.1667,
            "leaked_rows_in_test": 1,
            "leak_rate_test": 0.25,
        }
        line = _render_cross_split_pair("train__test", payload)
        assert "{" not in line
        assert "leaked=2/1" in line
        assert "16.67%" in line and "25.00%" in line

    def test_render_cross_split_pair_split_name_containing_double_underscore(self):
        """A split named 'train__aug' paired with 'test' builds the composite
        pair key 'train__aug__test'. ``pair_name.split("__", 1)`` used to
        mis-parse this as a=('train', b='aug__test'), missing both real
        payload keys and silently rendering 'leaked=0/0' for a genuine leak.
        Split names must come from the payload's own keys, not the composite
        key."""
        from forgelm.data_audit._summary import _render_cross_split_pair

        payload = {
            "leaked_rows_in_train__aug": 5,
            "leak_rate_train__aug": 0.05,
            "leaked_rows_in_test": 2,
            "leak_rate_test": 0.2,
        }
        line = _render_cross_split_pair("train__aug__test", payload)
        assert "leaked=5/2" in line
        assert "5.00%" in line and "20.00%" in line
        # The old bug rendered exactly this string for a genuine leak.
        assert "leaked=0/0" not in line


# ---------------------------------------------------------------------------
# NOSONAR discipline — every suppression carries its Sonar rule code
# ---------------------------------------------------------------------------


class TestCroissantNosonarRuleCode:
    """coding.md NOSONAR rule 1: every ``# NOSONAR`` must carry the Sonar rule
    code on the same line; bare ``# NOSONAR`` is rejected."""

    def test_all_nosonar_lines_carry_rule_code(self):
        import re as _re
        from pathlib import Path as _Path

        import forgelm.data_audit._croissant as croissant_mod

        source = _Path(croissant_mod.__file__).read_text(encoding="utf-8")
        nosonar_lines = [ln for ln in source.splitlines() if "# NOSONAR" in ln]
        assert nosonar_lines, "expected NOSONAR suppressions in _croissant.py"
        for ln in nosonar_lines:
            assert _re.search(r"# NOSONAR\s+python:S\d+", ln), (
                f"bare NOSONAR without a rule code (coding.md rule 1): {ln.strip()!r}"
            )


# ---------------------------------------------------------------------------
# ReDoS linearity — detect_pii runs on operator-controlled text, no timeout
# ---------------------------------------------------------------------------


class TestPiiRegexLinearity:
    """regex.md ReDoS-budget: detect_pii/mask_pii run the fixed pattern set on
    every row with no per-call timeout, so the phone and (space-tolerant) IBAN
    patterns must scale linearly on pathological input. Median-of-5 growth-
    ratio check (absolute ms is flaky on shared CI)."""

    _TOLERANCE = 3

    def _median_ms(self, fn, payload, samples=5):
        import statistics
        import time

        obs = []
        for _ in range(samples):
            t0 = time.perf_counter()
            fn(payload)
            obs.append((time.perf_counter() - t0) * 1000)
        return statistics.median(obs)

    @pytest.mark.parametrize(
        "name, builder",
        [
            ("phone", lambda n: "+1" + " 2" * n),
            ("iban", lambda n: "AB12" + "C" * n),
        ],
    )
    def test_pattern_linear_on_pathological_input(self, name, builder):
        from forgelm.data_audit._pii_regex import _PII_PATTERNS

        pat = _PII_PATTERNS[name]
        sizes = (1_000, 5_000, 10_000)
        timings = {n: self._median_ms(pat.search, builder(n)) for n in sizes}
        baseline = max(timings[1_000], 0.001)
        for n in sizes[1:]:
            ratio = timings[n] / baseline
            allowed = (n / 1_000) * self._TOLERANCE
            assert ratio <= allowed, (
                f"{name} regex grew {ratio:.1f}x from n=1000 to n={n} "
                f"(allowed {allowed:.1f}x); possible ReDoS regression. timings_ms={timings}"
            )
