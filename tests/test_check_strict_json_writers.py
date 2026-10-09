"""Tests for the strict-JSON writer-discipline guard.

Phase 16 S2 converted four writers and declared the class closed. Its own
review then found `compliance_report.json` — an EU AI Act Art. 11 / Annex IV
artefact — still emitting bare `NaN`. The guard that followed named nine
modules by hand, and the next review found `ForgeConfig.model_dump_json`, the
`--dry-run` output, `model_integrity.json` and the safety-eval envelope outside
the list, and showed the check could be walked around with `import json as j`.
Converting instances does not close a class; a scope derived from the
filesystem, and a match on what an import binds rather than on a spelling, does.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def guard(monkeypatch):
    path = _REPO_ROOT / "tools" / "check_strict_json_writers.py"
    spec = importlib.util.spec_from_file_location("check_strict_json_writers", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "check_strict_json_writers", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_repo(guard, tmp_path, monkeypatch):
    """A scratch repo root with a ``forgelm/`` package, so the guard scans files we control."""
    (tmp_path / "forgelm").mkdir()
    monkeypatch.setattr(guard, "_REPO_ROOT", tmp_path)
    return tmp_path


def _write(root: Path, rel: str, body: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


class TestAgainstTheRepo:
    def test_the_whole_package_is_clean(self, guard):
        assert guard.main(["--quiet"]) == 0

    def test_the_guard_reports_what_it_examined(self, guard, capsys):
        assert guard.main([]) == 0
        out = capsys.readouterr().out
        assert "module(s) under forgelm/ scanned" in out and "exempt with reasons" in out, (
            "a guard that prints nothing on success cannot be distinguished from one that ran nothing"
        )

    def test_scope_is_the_package_not_a_hand_picked_list(self, guard):
        """The defect this replaced: the modules that mattered were simply not on the list."""
        scanned = set(guard._package_modules())
        for rel in (
            "forgelm/config.py",  # ForgeConfig.model_dump_json
            "forgelm/cli/_dry_run.py",  # the --dry-run JSON output
            "forgelm/export.py",  # model_integrity.json
            "forgelm/cli/subcommands/_safety_eval.py",  # the safety-eval envelope
            "forgelm/cli/subcommands/_cache.py",
            "forgelm/cli/_no_train_modes.py",
        ):
            assert rel in scanned, f"{rel} is outside the guard's scope"
        assert len(scanned) >= guard._MIN_MODULES_EXPECTED

    def test_a_module_created_tomorrow_is_covered_without_editing_the_guard(self, guard, fake_repo):
        _write(fake_repo, "forgelm/brand_new.py", "import json\n\n\ndef save(p, fh):\n    json.dump(p, fh)\n")
        # Pad the package so the vacuity floor is not what fails.
        for i in range(guard._MIN_MODULES_EXPECTED):
            _write(fake_repo, f"forgelm/pad_{i}.py", "X = 1\n")
        assert "forgelm/brand_new.py" in guard._package_modules()
        assert guard.main(["--quiet"]) == 1

    def test_an_empty_discovery_root_fails_rather_than_passing_vacuously(self, guard, fake_repo, capsys):
        assert guard.main([]) == 1
        assert "scans nothing passes" in capsys.readouterr().out


class TestExclusionsStayHonest:
    def test_each_exclusion_names_a_live_function(self, guard):
        """A stale exclusion reads as a reviewed decision while naming nothing."""
        for rel_path, func in guard._HASH_CANONICALISATION:
            assert guard._defines_function(rel_path, func), f"{rel_path} exclusion names {func}(), which is gone"

    def test_each_exclusion_carries_a_reason(self, guard):
        blank = [k for k, v in guard._HASH_CANONICALISATION.items() if not v.strip()]
        assert not blank, f"exclusions with no written reason: {blank}"

    def test_each_exclusion_suppresses_a_real_finding(self, guard):
        """An exclusion over a function that no longer calls an encoder is dead weight.

        ``_manifest_json_default`` sat in the table for a whole phase while containing no
        ``json.dumps`` at all; nothing noticed, because only existence was checked.
        """
        used: set[tuple[str, str]] = set()
        for rel in guard._package_modules():
            guard.scan_file(rel, used)
        assert used == set(guard._HASH_CANONICALISATION)

    def test_an_exclusion_that_suppresses_nothing_fails_the_run(self, guard, fake_repo, monkeypatch, capsys):
        for i in range(guard._MIN_MODULES_EXPECTED):
            _write(fake_repo, f"forgelm/pad_{i}.py", "X = 1\n")
        _write(fake_repo, "forgelm/hashing.py", "def digest(p):\n    return str(p)\n")
        monkeypatch.setattr(guard, "_HASH_CANONICALISATION", {("forgelm/hashing.py", "digest"): "reason"})
        assert guard.main([]) == 1
        assert "suppresses nothing" in capsys.readouterr().out


class TestDetection:
    """Every spelling of 'serialise JSON without the chokepoint' must be caught."""

    @pytest.mark.parametrize(
        ("label", "source"),
        [
            ("plain dump", "import json\n\n\ndef save(p, fh):\n    json.dump(p, fh)\n"),
            ("plain dumps", "import json\n\n\ndef save(p):\n    return json.dumps(p)\n"),
            ("module alias", "import json as j\n\n\ndef save(p):\n    return j.dumps(p)\n"),
            ("direct import", "from json import dumps\n\n\ndef save(p):\n    return dumps(p)\n"),
            ("direct import, renamed", "from json import dumps as d\n\n\ndef save(p):\n    return d(p)\n"),
            ("direct dump import", "from json import dump\n\n\ndef save(p, fh):\n    dump(p, fh)\n"),
            ("orjson", "import orjson\n\n\ndef save(p):\n    return orjson.dumps(p)\n"),
            ("ujson alias", "import ujson as u\n\n\ndef save(p):\n    return u.dumps(p)\n"),
            ("simplejson", "from simplejson import dumps\n\n\ndef save(p):\n    return dumps(p)\n"),
        ],
    )
    def test_the_spelling_is_caught(self, guard, fake_repo, label, source):
        _write(fake_repo, "forgelm/writer.py", source)
        findings = guard.scan_file("forgelm/writer.py")
        assert findings and "save()" in findings[0], f"{label!r} slipped past the guard"

    def test_dumps_strict_is_accepted(self, guard, fake_repo):
        _write(
            fake_repo,
            "forgelm/writer.py",
            "from ._strict_json import dumps_strict\n\n\ndef save(payload, fh):\n    fh.write(dumps_strict(payload))\n",
        )
        assert guard.scan_file("forgelm/writer.py") == []

    def test_loading_is_not_flagged(self, guard, fake_repo):
        """The guard is about what leaves the process, not what comes in."""
        _write(fake_repo, "forgelm/reader.py", "import json\n\n\ndef read(fh):\n    return json.load(fh)\n")
        assert guard.scan_file("forgelm/reader.py") == []

    def test_an_unrelated_dumps_method_is_not_flagged(self, guard, fake_repo):
        """``yaml.dump`` / ``pickle.dumps`` are not JSON encoders."""
        _write(
            fake_repo,
            "forgelm/other.py",
            "import yaml\nimport pickle\n\n\ndef f(p):\n    return yaml.dump(p), pickle.dumps(p)\n",
        )
        assert guard.scan_file("forgelm/other.py") == []

    def test_a_hash_canonicalisation_site_is_exempt(self, guard, fake_repo, monkeypatch):
        _write(fake_repo, "forgelm/h.py", "import json\n\n\ndef digest(p):\n    return json.dumps(p, sort_keys=True)\n")
        monkeypatch.setattr(guard, "_HASH_CANONICALISATION", {("forgelm/h.py", "digest"): "defines the digest"})
        used: set[tuple[str, str]] = set()
        assert guard.scan_file("forgelm/h.py", used) == []
        assert used == {("forgelm/h.py", "digest")}
