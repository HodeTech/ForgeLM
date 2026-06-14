"""Tests for forgelm.chat.ChatSession (F-P8-C-15 / F-P8-OPUS-12).

The REPL's slash-command dispatch, history assembly, and transcript
save are deterministic, no-model, no-GPU logic — yet no test ever
constructed ``ChatSession`` before this module. We stub model/tokenizer
(neither is touched by the covered paths) and capture output through the
``output_fn`` hook the class exposes precisely for re-entrant testing.
"""

from __future__ import annotations

import json

import pytest

import forgelm.chat as chat_mod
from forgelm.chat import ChatSession

# ``rich`` is an optional extra; the True-path assertion needs the real escaper.
try:
    import rich.markup  # noqa: F401

    _RICH_INSTALLED = True
except ImportError:
    _RICH_INSTALLED = False


def _make_session(**kwargs):
    """Build a ChatSession with a captured output sink.

    Model/tokenizer are inert sentinels: none of the command-dispatch /
    history / save paths invoke them (only ``_generate_and_print``, which
    we do not exercise here, would).
    """
    out: list[str] = []

    def _sink(text, end="\n", flush=False):  # noqa: ARG001 — mirror _default_output signature
        out.append(text)

    session = ChatSession(
        model=object(),
        tokenizer=object(),
        output_fn=_sink,
        **kwargs,
    )
    return session, out


class TestHandleCommand:
    def test_unknown_command_does_not_exit(self):
        session, out = _make_session()
        keep_running = session._handle_command("/bogus")
        assert keep_running is True
        assert any("Unknown command" in line for line in out)

    @pytest.mark.parametrize("directive", ["/exit", "/quit"])
    def test_exit_aliases_return_false(self, directive):
        session, _ = _make_session()
        assert session._handle_command(directive) is False

    def test_help_lists_commands_and_keeps_running(self):
        session, out = _make_session()
        assert session._handle_command("/help") is True
        blob = "\n".join(out)
        assert "/reset" in blob and "/temperature" in blob

    def test_reset_clears_history(self):
        session, out = _make_session()
        session.history.extend([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}])
        assert session._handle_command("/reset") is True
        assert session.history == []
        assert any("cleared" in line for line in out)


class TestCmdTemperature:
    def test_valid_value_updates_temperature(self):
        session, _ = _make_session(temperature=0.7)
        session._handle_command("/temperature 1.25")
        assert session.temperature == 1.25

    @pytest.mark.parametrize("bad", ["", "abc", "0", "0.0", "2.5", "-1"])
    def test_bad_arg_hint_leaves_temperature_unchanged(self, bad):
        session, out = _make_session(temperature=0.7)
        session._handle_command(f"/temperature {bad}".strip())
        assert session.temperature == 0.7
        assert any("Usage:" in line for line in out)


class TestCmdSystem:
    def test_set_and_view_system_prompt(self):
        session, out = _make_session()
        session._handle_command("/system You are a helpful bot.")
        assert session.system_prompt == "You are a helpful bot."
        out.clear()
        session._handle_command("/system")
        assert any("You are a helpful bot." in line for line in out)


class TestCmdSave:
    def test_transcript_replayable(self, tmp_path):
        session, _ = _make_session(system_prompt="SYS")
        session.history.extend(
            [
                {"role": "user", "content": "ping"},
                {"role": "assistant", "content": "pong"},
            ]
        )
        target = tmp_path / "sub" / "transcript.jsonl"
        session._handle_command(f"/save {target}")
        assert target.is_file()
        rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
        # System prompt persisted first so the transcript is replayable as-is.
        assert rows[0] == {"role": "system", "content": "SYS"}
        assert rows[1]["role"] == "user" and rows[1]["content"] == "ping"
        assert rows[2]["role"] == "assistant" and rows[2]["content"] == "pong"


class TestBuildMessages:
    def test_multi_turn_ordering_with_system_prompt(self):
        session, _ = _make_session(system_prompt="SYS")
        session.history.extend(
            [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply1"},
            ]
        )
        messages = session._build_messages("second")
        assert messages[0] == {"role": "system", "content": "SYS"}
        assert messages[1]["content"] == "first"
        assert messages[2]["content"] == "reply1"
        assert messages[-1] == {"role": "user", "content": "second"}

    def test_history_trimmed_to_max_pairs(self):
        from forgelm.chat import _MAX_HISTORY_PAIRS

        session, _ = _make_session()
        # Build more than the retained window of turns.
        for i in range(_MAX_HISTORY_PAIRS + 10):
            session.history.append({"role": "user", "content": f"u{i}"})
            session.history.append({"role": "assistant", "content": f"a{i}"})
        messages = session._build_messages("now")
        # No system prompt → all entries are trimmed history + the new turn.
        assert len(messages) == 2 * _MAX_HISTORY_PAIRS + 1
        assert messages[-1] == {"role": "user", "content": "now"}


class TestRichDualPath:
    """F-P7-OPUS-44: architecture.md §3 carve-out condition 4 requires the
    ``_HAS_RICH`` boolean be monkeypatched to exercise BOTH the extras-installed
    and extras-missing paths. The plain ``_HAS_RICH=False`` fallback is the
    install-minimal default (users who skip the ``[chat]`` extra) and was
    previously untested, so a regression in the rich path could silently break
    the fallback every such user depends on."""

    def test_inline_raw_plain_path_passes_markup_through_unescaped(self, monkeypatch):
        # _HAS_RICH=False (extras-missing): no rich engine to interpret markup,
        # so the token is written verbatim with no escaping.
        monkeypatch.setattr(chat_mod, "_HAS_RICH", False)
        session, out = _make_session()
        session._print_inline_raw("[red]ALERT[/red]")
        assert out == ["[red]ALERT[/red]"]

    @pytest.mark.skipif(not _RICH_INSTALLED, reason="rich extra not installed")
    def test_inline_raw_rich_path_escapes_markup(self, monkeypatch):
        # _HAS_RICH=True (extras-installed): markup-looking model tokens are
        # rich-escaped so they render literally (prompt-injection hardening).
        monkeypatch.setattr(chat_mod, "_HAS_RICH", True)
        session, out = _make_session()
        session._print_inline_raw("[red]ALERT[/red]")
        assert len(out) == 1
        assert out[0] != "[red]ALERT[/red]"
        assert "\\[red]" in out[0]

    def test_welcome_renders_in_both_rich_modes(self, monkeypatch):
        # The welcome banner must produce output without raising under both
        # modes, so a future edit to the rich path cannot silently break the
        # plain fallback.
        for has_rich in (False, _RICH_INSTALLED):
            monkeypatch.setattr(chat_mod, "_HAS_RICH", has_rich)
            session, out = _make_session()
            session._print_welcome()
            assert out, f"_print_welcome produced no output with _HAS_RICH={has_rich}"
