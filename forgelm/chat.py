"""
Interactive terminal chat REPL for post-training model testing.

Provides streaming output and slash commands.  ``rich`` is used for pretty
rendering when available; falls back to plain print otherwise.

Usage (programmatic):
    from forgelm.chat import run_chat
    run_chat("./outputs/final_model", temperature=0.7)

Usage (CLI):
    forgelm chat ./outputs/final_model
    forgelm chat ./outputs/final_model --adapter ./outputs/adapter
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, List, Optional

logger = logging.getLogger("forgelm.chat")

try:
    from rich.console import Console
    from rich.panel import Panel

    _HAS_RICH = True
    _console = Console()
except ImportError:
    _HAS_RICH = False
    _console = None  # type: ignore[assignment]

# Slash-command prefix
_CMD = "/"

# Maximum history entries kept in memory (user + assistant pairs = 2 × N turns)
_MAX_HISTORY_PAIRS = 50


class ChatSession:
    """Stateful interactive chat session.

    Manages conversation history, slash-command dispatch, and streaming
    output.  Designed to be re-entrant for testing: pass ``input_fn`` and
    ``output_fn`` to override stdin/stdout.
    """

    def __init__(
        self,
        model: Any,
        tokenizer: Any,
        *,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_new_tokens: int = 512,
        stream: bool = True,
        input_fn=None,
        output_fn=None,
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_new_tokens = max_new_tokens
        self.stream = stream
        self.history: List[dict] = []
        self._input = input_fn or input
        self._output = output_fn or self._default_output

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _default_output(self, text: str, end: str = "\n", flush: bool = False) -> None:
        if _HAS_RICH:
            _console.print(text, end=end)
            if flush:
                # rich Console buffers on small writes; force a real flush so
                # token-by-token streaming doesn't appear frozen. Narrow the
                # catch to the actual failure modes — closed stdout (OSError)
                # or a console without a file attribute (AttributeError).
                try:
                    _console.file.flush()
                except (OSError, AttributeError):
                    pass
        else:
            print(text, end=end, flush=flush)

    def _print(self, text: str) -> None:
        self._output(text)

    def _print_inline(self, text: str) -> None:
        self._output(text, end="", flush=True)

    def _print_inline_raw(self, text: str) -> None:
        """Inline output that must be treated as plain text, not rich markup.

        Used for streaming model tokens — training data routinely contains
        bracketed strings (`[red]ALERT[/red]`, `[/]`, `[link=...]`) that rich
        would interpret as markup, scrambling the rendered output and creating
        a low-grade prompt-injection vector.
        """
        if _HAS_RICH:
            from rich.markup import escape as _rich_escape

            self._output(_rich_escape(text), end="", flush=True)
        else:
            self._output(text, end="", flush=True)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the REPL loop.  Blocks until the user types /exit."""
        self._print_welcome()

        while True:
            try:
                user_input = self._input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                self._print("\n[Goodbye]")
                break

            if not user_input:
                continue

            if user_input.startswith(_CMD):
                keep_running = self._handle_command(user_input)
                if not keep_running:
                    break
                continue

            self._generate_and_print(user_input)

    # ------------------------------------------------------------------
    # Slash-command dispatch
    # ------------------------------------------------------------------

    def _handle_command(self, raw: str) -> bool:
        """Dispatch a slash command.  Returns ``False`` to exit the loop."""
        parts = raw.strip().split(maxsplit=1)
        directive = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            "/exit": self._cmd_exit,
            "/quit": self._cmd_exit,
            "/reset": self._cmd_reset,
            "/save": self._cmd_save,
            "/temperature": self._cmd_temperature,
            "/system": self._cmd_system,
            "/help": self._cmd_help,
            "/?": self._cmd_help,
        }

        handler = handlers.get(directive)
        if handler is None:
            self._print(f"[Unknown command: {directive!r}. Type /help for available commands.]")
            return True

        return handler(arg)

    def _cmd_exit(self, _: str) -> bool:
        self._print("[Session ended]")
        return False

    def _cmd_reset(self, _: str) -> bool:
        self.history.clear()
        self._print("[Conversation history cleared]")
        return True

    def _cmd_save(self, path: str) -> bool:
        if not path:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = f"chat_history_{ts}.jsonl"
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                # Persist the system prompt as the first message so the saved
                # transcript is replayable as-is (reproducibility requirement).
                if self.system_prompt:
                    f.write(
                        json.dumps(
                            {"role": "system", "content": self.system_prompt},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                for msg in self.history:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            total = len(self.history) + (1 if self.system_prompt else 0)
            self._print(f"[History saved → {path} ({total} messages)]")
        except OSError as e:
            self._print(f"[Save failed: {e}]")
        return True

    def _cmd_temperature(self, arg: str) -> bool:
        try:
            val = float(arg)
            if not 0.0 < val <= 2.0:
                raise ValueError
            self.temperature = val
            self._print(f"[Temperature → {self.temperature}]")
        except ValueError:
            self._print("[Usage: /temperature 0.7  (range: 0.0 < t ≤ 2.0)]")
        return True

    def _cmd_system(self, arg: str) -> bool:
        if arg:
            self.system_prompt = arg
            self._print("[System prompt updated]")
        else:
            current = self.system_prompt or "(none)"
            self._print(f"[System prompt: {current}]")
        return True

    def _cmd_help(self, _: str) -> bool:
        self._print(
            "\n".join(
                [
                    "",
                    "  /reset              Clear conversation history",
                    "  /save [file]        Save history to JSONL file",
                    "  /temperature N      Set sampling temperature (0.0 < N ≤ 2.0)",
                    "  /system [prompt]    Set or view system prompt",
                    "  /exit, /quit        End the chat session",
                    "  /help, /?           Show this help",
                    "",
                ]
            )
        )
        return True

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def _build_messages(self, user_input: str) -> List[dict]:
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        # Trim history to avoid context overflow (keep last N pairs)
        trimmed = self.history[-(2 * _MAX_HISTORY_PAIRS) :]
        messages.extend(trimmed)
        messages.append({"role": "user", "content": user_input})
        return messages

    def _generate_and_print(self, user_input: str) -> None:
        from .inference import generate, generate_stream

        messages = self._build_messages(user_input)

        prefix = "[bold green]Assistant:[/bold green] " if _HAS_RICH else "Assistant: "
        self._print_inline(prefix)

        response = ""
        if self.stream:
            try:
                for token in generate_stream(
                    self.model,
                    self.tokenizer,
                    "",
                    messages=messages,
                    temperature=self.temperature,
                    max_new_tokens=self.max_new_tokens,
                ):
                    self._print_inline_raw(token)
                    response += token
            except Exception as e:  # noqa: BLE001 — best-effort: streaming generate crosses CUDA OOM (RuntimeError), tokenizer issues (ValueError), and stop-token race conditions; the chat REPL must keep running after a single failed turn so the operator can retry / change parameters.  # NOSONAR
                self._print(f"\n[Generation error: {e}]")
                return
            self._print("")  # trailing newline
        else:
            try:
                response = generate(
                    self.model,
                    self.tokenizer,
                    "",
                    messages=messages,
                    temperature=self.temperature,
                    max_new_tokens=self.max_new_tokens,
                )
            except Exception as e:  # noqa: BLE001 — best-effort: see streaming branch above; same broad torch / tokenizer surface, same per-turn soft-fail policy in the REPL loop.  # NOSONAR
                self._print(f"[Generation error: {e}]")
                return
            # Non-streaming path: full response is also untrusted model output
            if _HAS_RICH:
                from rich.markup import escape as _rich_escape

                self._print(_rich_escape(response))
            else:
                self._print(response)

        # Append to history
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": response})

    # ------------------------------------------------------------------
    # Welcome screen
    # ------------------------------------------------------------------

    def _print_welcome(self) -> None:
        lines = [
            "ForgeLM Chat  —  type your message and press Enter",
            f"Streaming : {'on' if self.stream else 'off'}  |  Temperature : {self.temperature}",
        ]
        if self.system_prompt:
            excerpt = self.system_prompt[:70] + "…" if len(self.system_prompt) > 70 else self.system_prompt
            lines.append(f"System    : {excerpt}")
        lines.append("Commands  : /help  /reset  /save  /temperature  /system  /exit")
        lines.append("─" * 56)

        if _HAS_RICH:
            self._print(Panel("\n".join(lines), title="[bold]ForgeLM[/bold]", border_style="blue"))
        else:
            for line in lines:
                self._print(line)


# ---------------------------------------------------------------------------
# Convenience entry point (called from CLI)
# ---------------------------------------------------------------------------


def run_chat(
    model_path: str,
    *,
    adapter: Optional[str] = None,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_new_tokens: int = 512,
    stream: bool = True,
    load_in_4bit: bool = False,
    load_in_8bit: bool = False,
    trust_remote_code: bool = False,
    backend: str = "transformers",
) -> None:
    """Load *model_path* and start an interactive chat session.

    This is the primary entry point called by the ``forgelm chat`` CLI subcommand.

    Args:
        model_path: Local directory or HF Hub model ID.
        adapter: Optional PEFT adapter directory (merged at load time).
        system_prompt: System-role message prepended to every conversation.
        temperature: Initial sampling temperature (changeable with /temperature).
        max_new_tokens: Maximum tokens to generate per response.
        stream: Stream tokens as they are produced (default True).
        load_in_4bit: 4-bit NF4 quantisation via bitsandbytes.
        load_in_8bit: 8-bit quantisation via bitsandbytes.
        trust_remote_code: Pass through to ``AutoModelForCausalLM``.
        backend: ``"transformers"`` or ``"unsloth"``.
    """
    from .inference import load_model

    logger.info("Loading model: %s (adapter=%s, 4bit=%s)", model_path, adapter, load_in_4bit)
    model, tokenizer = load_model(
        model_path,
        adapter=adapter,
        backend=backend,
        load_in_4bit=load_in_4bit,
        load_in_8bit=load_in_8bit,
        trust_remote_code=trust_remote_code,
    )

    session = ChatSession(
        model=model,
        tokenizer=tokenizer,
        system_prompt=system_prompt,
        temperature=temperature,
        max_new_tokens=max_new_tokens,
        stream=stream,
    )
    session.run()
