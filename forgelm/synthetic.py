"""Synthetic data pipeline for teacher→student distillation.

Generates training data by prompting a teacher model (API or local) and
saving structured outputs as JSONL for downstream fine-tuning.

Usage:
    forgelm --config job.yaml --generate-data

Or programmatically:
    from forgelm.synthetic import SyntheticDataGenerator
    gen = SyntheticDataGenerator(config)
    gen.generate("output.jsonl")
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("forgelm.synthetic")


@dataclass
class SyntheticResult:
    """Result of a synthetic data generation run."""

    total_prompts: int = 0
    successful: int = 0
    failed: int = 0
    output_file: str = ""
    duration_seconds: float = 0.0
    errors: List[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.successful / self.total_prompts if self.total_prompts > 0 else 0.0


class SyntheticDataGenerator:
    """Generate synthetic training data using a teacher model.

    Supports three teacher backends:
    - "api": OpenAI-compatible API (GPT-4, Claude, local vLLM, etc.)
    - "local": Load a HuggingFace model locally for generation
    - "file": Read responses from a pre-existing file (for offline/reproducible pipelines)
    """

    def __init__(self, config):
        self.config = config
        self.synth_cfg = config.synthetic
        if not self.synth_cfg or not self.synth_cfg.enabled:
            raise ValueError("Synthetic data generation is not enabled in config.")
        self._teacher = None

    def _generate_one(self, prompt: str, idx: int, result: SyntheticResult) -> Optional[dict]:
        """Generate a single entry; updates *result* counters in-place.

        Returns the formatted entry on success, ``None`` on failure (already
        recorded in ``result.errors``). The caller handles rate limiting.
        """
        try:
            response = self._call_teacher(prompt)
        except Exception as e:  # noqa: BLE001 — best-effort: per-prompt teacher invocation crosses three backends (api / local model / pre-recorded file) each with their own failure tail (network HTTPError, RuntimeError on CUDA OOM, KeyError on missing seed entry); per-prompt soft-fail with structured error capture is the documented contract so a single bad prompt cannot abort a corpus-scale run.  # NOSONAR
            result.failed += 1
            result.errors.append(f"Prompt {idx}: {e}")
            logger.warning("Generation failed for prompt %d: %s", idx, e)
            return None

        if not response:
            result.failed += 1
            result.errors.append(f"Prompt {idx}: empty response")
            logger.warning(
                "Empty response from teacher for prompt %d: %.80s",
                idx,
                prompt,
            )
            return None

        result.successful += 1
        return self._format_entry(prompt, response)

    def _write_output(self, output_file: str, generated: List[dict], result: SyntheticResult) -> None:
        """Write the JSONL output and log summary stats."""
        if not generated:
            logger.warning("No data generated. Output file not created.")
            return
        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            for entry in generated:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info(
            "Synthetic data saved: %s (%d entries, %.1f%% success rate)",
            output_file,
            len(generated),
            result.success_rate * 100,
        )

    def generate(self, output_path: Optional[str] = None) -> SyntheticResult:
        """Generate synthetic data and save to JSONL.

        Args:
            output_path: Override output file path. If None, uses config value.

        Returns:
            SyntheticResult with generation statistics.
        """
        output_file = output_path or self.synth_cfg.output_file
        prompts = self._load_seed_prompts()
        result = SyntheticResult(total_prompts=len(prompts), output_file=output_file)

        if not prompts:
            logger.warning("No seed prompts found. Nothing to generate.")
            return result

        logger.info(
            "Starting synthetic data generation: %d prompts, teacher=%s, backend=%s",
            len(prompts),
            self.synth_cfg.teacher_model,
            self.synth_cfg.teacher_backend,
        )

        rate_limit = self.synth_cfg.teacher_backend == "api" and self.synth_cfg.api_delay > 0
        start_time = time.time()
        generated: List[dict] = []

        last_idx = len(prompts) - 1
        for i, prompt in enumerate(prompts):
            entry = self._generate_one(prompt, i, result)
            if entry is not None:
                generated.append(entry)
            # Skip the trailing sleep — there's no next request to throttle
            if rate_limit and i < last_idx:
                time.sleep(self.synth_cfg.api_delay)

        result.duration_seconds = time.time() - start_time
        self._write_output(output_file, generated, result)
        return result

    def _load_seed_prompts(self) -> List[str]:
        """Load seed prompts from file or inline config."""
        # From file
        if self.synth_cfg.seed_file and os.path.isfile(self.synth_cfg.seed_file):
            prompts = []
            with open(self.synth_cfg.seed_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # Support JSONL ({"prompt": "..."}) or plain text
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        prompts.append(line)
                        continue
                    # A line that is valid JSON but not an object (a bare
                    # array/number/string/bool/null) has no ``.get`` — treat
                    # the raw line as a plain-text prompt instead of crashing
                    # the whole load with AttributeError (F-P6-OPUS-08).
                    if isinstance(data, dict):
                        prompts.append(data.get("prompt", data.get("text", line)))
                    else:
                        prompts.append(line)
            logger.info("Loaded %d seed prompts from %s", len(prompts), self.synth_cfg.seed_file)
            return prompts

        # From inline list
        if self.synth_cfg.seed_prompts:
            return list(self.synth_cfg.seed_prompts)

        logger.warning("No seed_file or seed_prompts configured.")
        return []

    def _call_teacher(self, prompt: str) -> str:
        """Route to the appropriate teacher backend."""
        backend = self.synth_cfg.teacher_backend

        if backend == "api":
            return self._call_api_teacher(prompt)
        elif backend == "local":
            return self._call_local_teacher(prompt)
        elif backend == "file":
            # File-based teacher — responses pre-loaded, keyed by exact prompt
            # string (byte-exact lookup; no hashing or whitespace normalisation).
            return self._call_file_teacher(prompt)
        else:
            raise ValueError(f"Unknown teacher_backend: {backend}")

    def _call_api_teacher(self, prompt: str) -> str:
        """Call an OpenAI-compatible API teacher.

        Routes through :func:`forgelm._http.safe_post` so the same SSRF /
        scheme / redirect / TLS / timeout policy that protects the webhook
        and judge call sites also covers synthetic-data generation. The
        bearer token in ``Authorization`` is masked from the failure log.
        """
        from ._http import safe_post

        api_base = self.synth_cfg.api_base.rstrip("/")
        api_key = self.synth_cfg.api_key or os.environ.get(self.synth_cfg.api_key_env or "", "")

        if not api_base:
            raise ValueError("synthetic.api_base is required for API teacher backend.")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        system_prompt = self.synth_cfg.system_prompt or "You are a helpful assistant."

        payload = {
            "model": self.synth_cfg.teacher_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": self.synth_cfg.max_new_tokens,
            "temperature": self.synth_cfg.temperature,
        }

        response = safe_post(
            f"{api_base}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.synth_cfg.api_timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def _call_local_teacher(self, prompt: str) -> str:
        """Generate with a locally loaded HuggingFace model."""
        if self._teacher is None:
            self._load_local_teacher()

        model, tokenizer = self._teacher
        system_prompt = self.synth_cfg.system_prompt or "You are a helpful assistant."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        if hasattr(tokenizer, "apply_chat_template"):
            formatted = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        else:
            formatted = f"{system_prompt}\n\nUser: {prompt}\nAssistant:"

        inputs = tokenizer(formatted, return_tensors="pt")
        if hasattr(model, "device"):
            inputs = {k: v.to(model.device) for k, v in inputs.items()}

        outputs = model.generate(
            **inputs,
            max_new_tokens=self.synth_cfg.max_new_tokens,
            temperature=self.synth_cfg.temperature,
            do_sample=self.synth_cfg.temperature > 0,
        )
        response = tokenizer.decode(outputs[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True)
        return response.strip()

    def _load_local_teacher(self):
        """Lazy-load the local teacher model."""
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        logger.info("Loading local teacher model: %s", self.synth_cfg.teacher_model)
        # ``trust_remote_code=False`` is the secure default (Phase 7 acceptance):
        # synthetic-data generation must never execute repo-bundled code from
        # an arbitrary teacher checkpoint.  Operators that genuinely need a
        # custom architecture should fork and pre-convert.
        tokenizer = AutoTokenizer.from_pretrained(self.synth_cfg.teacher_model, trust_remote_code=False)
        model = AutoModelForCausalLM.from_pretrained(
            self.synth_cfg.teacher_model,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map="auto" if torch.cuda.is_available() else None,
            trust_remote_code=False,
        )
        self._teacher = (model, tokenizer)

    def _load_file_responses(self) -> dict:
        """Read the pre-generated prompt→response map from seed_file (one-shot).

        Loud about failures: logs a warning when seed_file is unset or missing
        (which silently produces empty responses for every prompt) and
        debug-logs each malformed line with line number so users can fix the
        underlying file.
        """
        responses: dict = {}
        seed_file = self.synth_cfg.seed_file
        if not seed_file:
            logger.warning("teacher_backend='file' but synthetic.seed_file is unset; all responses will be empty.")
            return responses
        if not os.path.isfile(seed_file):
            logger.warning(
                "teacher_backend='file' but synthetic.seed_file does not exist: %s — all responses will be empty.",
                seed_file,
            )
            return responses
        malformed = 0
        with open(seed_file, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    malformed += 1
                    logger.debug(
                        "Skipping malformed JSON in %s line %d: %s — %s",
                        seed_file,
                        lineno,
                        e,
                        line[:120].rstrip(),
                    )
                    continue
                # Valid JSON but not an object (bare array/number/string/etc.)
                # has no ``.get`` — count it as malformed and skip rather than
                # crashing the whole load with AttributeError (F-P6-OPUS-08).
                if not isinstance(data, dict):
                    malformed += 1
                    logger.debug(
                        "Skipping non-object JSON in %s line %d (%s) — %s",
                        seed_file,
                        lineno,
                        type(data).__name__,
                        line[:120].rstrip(),
                    )
                    continue
                p = data.get("prompt", "")
                r = data.get("response") or data.get("completion") or data.get("output", "")
                if p and r:
                    responses[p] = r
        if malformed:
            logger.warning(
                "Skipped %d malformed line(s) while loading %s; enable DEBUG logging for line-by-line detail.",
                malformed,
                seed_file,
            )
        if not responses:
            logger.warning(
                "synthetic.seed_file %s loaded zero usable prompt→response pairs; all teacher calls will return empty.",
                seed_file,
            )
        return responses

    def _call_file_teacher(self, prompt: str) -> str:
        """Read pre-generated responses from a file."""
        if self._teacher is None:
            self._teacher = self._load_file_responses()
        return self._teacher.get(prompt, "")

    def _format_entry(self, prompt: str, response: str) -> dict:
        """Format a prompt-response pair into the configured output format."""
        fmt = self.synth_cfg.output_format

        if fmt == "messages":
            entry = {
                "messages": [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": response},
                ]
            }
            if self.synth_cfg.system_prompt:
                entry["messages"].insert(0, {"role": "system", "content": self.synth_cfg.system_prompt})
            return entry
        elif fmt == "instruction":
            return {"instruction": prompt, "output": response}
        elif fmt == "chatml":
            # NOTE (F-P3-FABLE-62): this is ForgeLM's legacy {User, Assistant}
            # key layout (data.py detects + trains on it natively), NOT OpenAI
            # <|im_start|> ChatML wire markup. The config value name is kept for
            # backward compatibility; the schema/doc description spells out the
            # discrepancy so users feeding external ChatML tools pick `messages`.
            return {"User": prompt, "Assistant": response}
        else:
            # Default: simple prompt/response
            return {"prompt": prompt, "response": response}
