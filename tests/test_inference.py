"""Unit tests for forgelm.inference module.

All torch/transformers/peft calls are mocked so the test suite runs without
a GPU or the ML dependencies installed.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs for heavy dependencies
# ---------------------------------------------------------------------------


class _Scalar:
    """Module-scope scalar wrapper — `.item()` mimics torch's 0-d tensor."""

    def __init__(self, v):
        self._v = v

    def item(self):
        return self._v


class _Tensor:
    """Module-scope torch.Tensor stub.

    Hoisted out of `_make_torch_stub` so call sites don't have to bind it as
    a local variable (which would trigger snake-case naming warnings) and so
    `_make_torch_stub`'s cognitive complexity stays low.
    """

    def __init__(self, data=None):
        self._data = data or [0.1, 0.9]

    def float(self):
        return self

    def max(self):
        return _Scalar(max(self._data))

    def argmax(self):
        return _Scalar(self._data.index(max(self._data)))

    def log(self):
        import math

        return _Tensor([math.log(x + 1e-10) for x in self._data])

    def sum(self):
        return _Scalar(sum(self._data))

    def __mul__(self, other):
        if isinstance(other, _Tensor):
            return _Tensor([a * b for a, b in zip(self._data, other._data)])
        return _Tensor([x * other for x in self._data])

    def __add__(self, other):
        if isinstance(other, _Tensor):
            return _Tensor([a + b for a, b in zip(self._data, other._data)])
        return _Tensor([x + other for x in self._data])

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        if isinstance(other, _Tensor):
            return _Tensor([a - b for a, b in zip(self._data, other._data)])
        return _Tensor([x - other for x in self._data])

    def __truediv__(self, other):
        return _Tensor([x / other for x in self._data])

    def size(self, dim=None):
        if dim is not None:
            return len(self._data)
        return (len(self._data),)

    def __neg__(self):
        return _Tensor([-x for x in self._data])

    def __gt__(self, threshold):
        if isinstance(threshold, _Tensor):
            t = threshold._data[0] if len(threshold._data) == 1 else threshold._data
            return _Tensor([1 if x > (t if not isinstance(t, list) else t[i]) else 0 for i, x in enumerate(self._data)])
        return _Tensor([1 if x > threshold else 0 for x in self._data])

    def __lt__(self, other):
        if isinstance(other, _Tensor):
            t = other._data[0] if len(other._data) == 1 else None
            if t is not None:
                return _Tensor([1 if x < t else 0 for x in self._data])
            return _Tensor([1 if x < y else 0 for x, y in zip(self._data, other._data)])
        return _Tensor([1 if x < other else 0 for x in self._data])

    def item(self):
        return self._data[0]

    def to(self, device):
        return self

    @property
    def device(self):
        return "cpu"

    @property
    def shape(self):
        return (1, len(self._data))

    def __getitem__(self, idx):
        return self._data[idx]

    def __setitem__(self, idx, value):
        if isinstance(idx, _Tensor):
            # Boolean mask assignment
            for i, mask_val in enumerate(idx._data):
                if mask_val:
                    self._data[i] = value
        else:
            self._data[idx] = value

    def unsqueeze(self, dim):
        return self


def _stub_softmax(tensor, dim=-1):
    import math

    data = tensor._data if hasattr(tensor, "_data") else [0.1, 0.9]
    exp_vals = [math.exp(x) for x in data]
    s = sum(exp_vals)
    return _Tensor([x / s for x in exp_vals])


def _stub_log(tensor):
    import math

    data = tensor._data if hasattr(tensor, "_data") else [0.1, 0.9]
    return _Tensor([math.log(max(x, 1e-10)) for x in data])


def _stub_sum(tensor, dim=None):
    return _Scalar(sum(tensor._data))


def _make_torch_stub():
    """Build a lightweight torch stub covering the symbols inference.py uses.

    Tensor / Scalar / op implementations live at module scope; this function
    is just module-assembly so its cognitive complexity stays well below the
    SonarCloud threshold.
    """
    t = types.ModuleType("torch")
    t.inference_mode = lambda: MagicMock(__enter__=lambda s: None, __exit__=lambda *a: None)
    t.Tensor = _Tensor
    t.softmax = _stub_softmax
    t.log = _stub_log
    t.sum = _stub_sum
    t.cuda = MagicMock()
    t.cuda.is_available = MagicMock(return_value=False)
    t.cuda.is_bf16_supported = MagicMock(return_value=False)
    t.bfloat16 = "bfloat16"
    t.float16 = "float16"
    return t


# ---------------------------------------------------------------------------
# Tests for pure functions (logit_stats, adaptive_sample, helpers)
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_fallback_no_template(self):
        from forgelm.inference import _build_prompt

        tokenizer = MagicMock()
        tokenizer.chat_template = None
        tokenizer.apply_chat_template = None

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        result = _build_prompt(tokenizer, messages)
        assert "system: You are helpful." in result
        assert "user: Hello" in result

    def test_uses_chat_template_when_available(self):
        from forgelm.inference import _build_prompt

        tokenizer = MagicMock()
        tokenizer.chat_template = "<template>"
        tokenizer.apply_chat_template.return_value = "<formatted>"

        messages = [{"role": "user", "content": "Hi"}]
        result = _build_prompt(tokenizer, messages)
        assert result == "<formatted>"
        tokenizer.apply_chat_template.assert_called_once_with(messages, tokenize=False, add_generation_prompt=True)


class TestToMessages:
    def test_prompt_only(self):
        from forgelm.inference import _to_messages

        msgs = _to_messages("Hello")
        assert msgs == [{"role": "user", "content": "Hello"}]

    def test_with_system_prompt(self):
        from forgelm.inference import _to_messages

        msgs = _to_messages("Hello", system_prompt="Be concise.")
        assert msgs[0] == {"role": "system", "content": "Be concise."}
        assert msgs[-1] == {"role": "user", "content": "Hello"}

    def test_with_history(self):
        from forgelm.inference import _to_messages

        history = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Reply"},
        ]
        msgs = _to_messages("Second", history=history)
        assert len(msgs) == 3
        assert msgs[-1] == {"role": "user", "content": "Second"}

    def test_system_and_history_ordering(self):
        from forgelm.inference import _to_messages

        history = [{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "A1"}]
        msgs = _to_messages("Q2", system_prompt="sys", history=history)
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[-1]["content"] == "Q2"


class TestLogitStats:
    def test_returns_expected_keys(self):
        torch_stub = _make_torch_stub()
        with patch.dict(sys.modules, {"torch": torch_stub}):
            from forgelm.inference import logit_stats

            logits = _Tensor([0.1, 2.0, 0.5, -1.0])
            stats = logit_stats(logits)

        assert "entropy" in stats
        assert "top1_prob" in stats
        assert "effective_vocab" in stats

    def test_top1_prob_in_range(self):
        torch_stub = _make_torch_stub()
        with patch.dict(sys.modules, {"torch": torch_stub}):
            from forgelm.inference import logit_stats

            logits = _Tensor([0.1, 0.9])
            stats = logit_stats(logits)

        assert 0.0 < stats["top1_prob"] <= 1.0

    def test_entropy_non_negative(self):
        torch_stub = _make_torch_stub()
        with patch.dict(sys.modules, {"torch": torch_stub}):
            from forgelm.inference import logit_stats

            logits = _Tensor([1.0, 1.0])
            stats = logit_stats(logits)

        assert stats["entropy"] >= 0.0

    def test_effective_vocab_non_negative_int(self):
        torch_stub = _make_torch_stub()
        with patch.dict(sys.modules, {"torch": torch_stub}):
            from forgelm.inference import logit_stats

            logits = _Tensor([0.1, 0.9])
            stats = logit_stats(logits)

        assert isinstance(stats["effective_vocab"], int)
        assert stats["effective_vocab"] >= 0


class TestAdaptiveSample:
    def _make_logits(self, values):
        torch_stub = _make_torch_stub()

        # Build a proper stub that supports topk, sort, cumsum, multinomial
        logit = _Tensor(values)

        # Patch torch with real topk, sort, cumsum, multinomial using simple impls
        def topk(tensor, k):
            data = tensor._data
            sorted_data = sorted(data, reverse=True)[:k]
            indices = [data.index(v) for v in sorted_data]

            class _TopK:
                def __init__(self, vals, idxs):
                    self._data = vals

                def __getitem__(self, idx):
                    # Handle numpy-style (Ellipsis, -1) → last element
                    if isinstance(idx, tuple):
                        idx = idx[-1]
                    return _Tensor([self._data[idx]])

            return _TopK(sorted_data, indices), None

        def sort(tensor, descending=False):
            data = tensor._data[:]
            indexed = sorted(enumerate(data), key=lambda x: x[1], reverse=descending)
            sorted_vals = [v for _, v in indexed]
            sorted_idxs = [i for i, _ in indexed]

            class _Idx(_Tensor):
                def __init__(self, idxs):
                    super().__init__(idxs)

            return _Tensor(sorted_vals), _Idx(sorted_idxs)

        def cumsum(tensor, dim=-1):
            s = 0.0
            result = []
            for v in tensor._data:
                s += v
                result.append(s)
            return _Tensor(result)

        def multinomial(tensor, num_samples):
            return _Tensor([tensor._data.index(max(tensor._data))])

        def masked_fill(tensor, mask, value):
            data = tensor._data[:]
            mask_data = mask._data if hasattr(mask, "_data") else [mask] * len(data)
            result = [value if m else v for m, v in zip(mask_data, data)]
            return _Tensor(result)

        def scatter(tensor, dim, index, src):
            return src  # simplified for testing

        class _LocalScalar:
            def __init__(self, v):
                self._v = v

            def item(self):
                return self._v

        def sum_fn(tensor, dim=None):
            return _LocalScalar(sum(tensor._data))

        _Tensor.masked_fill = lambda self, mask, val: masked_fill(self, mask, val)
        _Tensor.scatter = lambda self, dim, idx, src: scatter(self, dim, idx, src)

        torch_stub.topk = topk
        torch_stub.sort = sort
        torch_stub.cumsum = cumsum
        torch_stub.multinomial = multinomial
        torch_stub.sum = sum_fn

        return torch_stub, logit

    def test_greedy_on_low_entropy(self):
        """Low entropy (confident model) should trigger greedy decoding."""
        torch_stub, logits = self._make_logits([0.0, 10.0])  # very confident
        with patch.dict(sys.modules, {"torch": torch_stub}):
            from forgelm.inference import adaptive_sample

            idx = adaptive_sample(logits, entropy_threshold=100.0)  # always greedy

        assert isinstance(idx, int)

    def test_returns_valid_index(self):
        torch_stub, logits = self._make_logits([1.0, 2.0, 3.0])
        with patch.dict(sys.modules, {"torch": torch_stub}):
            from forgelm.inference import adaptive_sample

            idx = adaptive_sample(logits, entropy_threshold=0.0)  # always sample

        assert isinstance(idx, int)


# ---------------------------------------------------------------------------
# Tests for load_model (mocked)
# ---------------------------------------------------------------------------


class TestLoadModel:
    def test_basic_load_no_adapter(self):
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_tokenizer.pad_token = None
        mock_tokenizer.eos_token = "eos"

        torch_stub = MagicMock()
        torch_stub.cuda.is_available.return_value = False

        transformers_stub = MagicMock()
        transformers_stub.AutoModelForCausalLM.from_pretrained.return_value = mock_model
        transformers_stub.AutoTokenizer.from_pretrained.return_value = mock_tokenizer

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub}):
            from forgelm.inference import load_model

            model, tok = load_model("org/model")

        assert model is mock_model
        assert tok is mock_tokenizer
        assert mock_tokenizer.pad_token == "eos"
        mock_model.eval.assert_called_once()

    def test_adapter_is_merged(self):
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_tokenizer.pad_token = "pad"

        merged_model = MagicMock()
        peft_model = MagicMock()
        peft_model.merge_and_unload.return_value = merged_model

        torch_stub = MagicMock()
        torch_stub.cuda.is_available.return_value = False

        transformers_stub = MagicMock()
        transformers_stub.AutoModelForCausalLM.from_pretrained.return_value = mock_model
        transformers_stub.AutoTokenizer.from_pretrained.return_value = mock_tokenizer

        peft_stub = MagicMock()
        peft_stub.PeftModel.from_pretrained.return_value = peft_model

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub, "peft": peft_stub}):
            from forgelm.inference import load_model

            model, _ = load_model("org/model", adapter="./adapter")

        peft_stub.PeftModel.from_pretrained.assert_called_once_with(mock_model, "./adapter")
        peft_model.merge_and_unload.assert_called_once()
        assert model is merged_model

    def test_tokenizer_loaded_from_adapter_when_tokenizer_config_present(self, tmp_path):
        """P2 regression: fine-tuning may add special tokens or a custom
        chat template; if the adapter directory carries tokenizer_config.json
        the loader must prefer it over the base model's tokenizer."""
        adapter_dir = tmp_path / "adapter"
        adapter_dir.mkdir()
        (adapter_dir / "tokenizer_config.json").write_text("{}")

        mock_model = MagicMock()
        base_tokenizer = MagicMock()
        base_tokenizer.pad_token = "pad"
        adapter_tokenizer = MagicMock()
        adapter_tokenizer.pad_token = "pad"

        torch_stub = MagicMock()
        torch_stub.cuda.is_available.return_value = False

        transformers_stub = MagicMock()
        transformers_stub.AutoModelForCausalLM.from_pretrained.return_value = mock_model

        # AutoTokenizer.from_pretrained returns different objects for base vs adapter paths
        def _tok_loader(path, **_kwargs):
            return adapter_tokenizer if str(adapter_dir) in str(path) else base_tokenizer

        transformers_stub.AutoTokenizer.from_pretrained.side_effect = _tok_loader

        peft_stub = MagicMock()
        peft_model = MagicMock()
        peft_model.merge_and_unload.return_value = mock_model
        peft_stub.PeftModel.from_pretrained.return_value = peft_model

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub, "peft": peft_stub}):
            from forgelm.inference import load_model

            _model, tok = load_model("org/base-model", adapter=str(adapter_dir))

        assert tok is adapter_tokenizer, (
            "Adapter tokenizer must take precedence when tokenizer_config.json is present in the adapter dir"
        )
        # AutoTokenizer should be called with the adapter path, not the base path
        called_paths = [call.args[0] for call in transformers_stub.AutoTokenizer.from_pretrained.call_args_list]
        assert str(adapter_dir) in called_paths

    def test_tokenizer_falls_back_to_base_when_adapter_has_no_tokenizer_config(self, tmp_path):
        """If the adapter dir has no tokenizer_config.json (older trainer
        runs that only saved adapter weights), use the base path."""
        adapter_dir = tmp_path / "adapter"
        adapter_dir.mkdir()
        # NOTE: deliberately no tokenizer_config.json here

        mock_model = MagicMock()
        base_tokenizer = MagicMock()
        base_tokenizer.pad_token = "pad"

        torch_stub = MagicMock()
        torch_stub.cuda.is_available.return_value = False

        transformers_stub = MagicMock()
        transformers_stub.AutoModelForCausalLM.from_pretrained.return_value = mock_model
        transformers_stub.AutoTokenizer.from_pretrained.return_value = base_tokenizer

        peft_stub = MagicMock()
        peft_model = MagicMock()
        peft_model.merge_and_unload.return_value = mock_model
        peft_stub.PeftModel.from_pretrained.return_value = peft_model

        with patch.dict(sys.modules, {"torch": torch_stub, "transformers": transformers_stub, "peft": peft_stub}):
            from forgelm.inference import load_model

            _model, tok = load_model("org/base-model", adapter=str(adapter_dir))

        assert tok is base_tokenizer
        called_paths = [call.args[0] for call in transformers_stub.AutoTokenizer.from_pretrained.call_args_list]
        assert "org/base-model" in called_paths

    def test_unsloth_backend_raises_without_package(self, monkeypatch):
        """``_load_unsloth`` must surface a clear ImportError when the
        ``unsloth`` package is not installed.

        Calls ``_load_unsloth`` directly instead of going through
        :func:`load_model`: the dispatcher imports ``torch`` and
        ``transformers`` at function-entry, and chasing the entire
        transitive-dep MagicMock chain (``torch.__spec__``,
        ``torch.__version__`` for safetensors, etc.) makes the test
        order-sensitive against other TestLoadModel tests that populate
        ``sys.modules`` with stubs.  The dispatcher is exercised by
        ``test_basic_load_no_adapter`` and ``test_adapter_is_merged``;
        this test owns the "unsloth not installed" failure surface only.
        """
        # Make ``from unsloth import FastLanguageModel`` raise — None in
        # sys.modules is the standard sentinel that triggers ImportError.
        monkeypatch.setitem(sys.modules, "unsloth", None)

        from forgelm.inference import _load_unsloth

        # F-P3-FABLE-52: the message must carry the canonical extra-install hint.
        with pytest.raises(ImportError, match=r"pip install 'forgelm\[unsloth\]'"):
            _load_unsloth(
                "org/model",
                adapter=None,
                trust_remote_code=False,
                load_in_4bit=False,
                load_in_8bit=False,
            )


# ---------------------------------------------------------------------------
# Tests for generate (mocked)
# ---------------------------------------------------------------------------


class TestGenerate:
    def test_returns_decoded_response(self):

        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_tokenizer.chat_template = None

        # Tokenizer returns fake input_ids
        fake_input = MagicMock()
        fake_input.input_ids = MagicMock()
        fake_input.input_ids.to.return_value = fake_input.input_ids
        fake_input.input_ids.shape = [1, 3]
        mock_tokenizer.return_value = fake_input

        # Model generates [input_ids] + [new_token]
        output_ids = MagicMock()
        fake_new_tokens = [1, 2, 3]
        output_ids.__getitem__ = MagicMock(return_value=fake_new_tokens)
        mock_model.generate.return_value = output_ids
        mock_model.device = "cpu"

        mock_tokenizer.decode.return_value = "Hello world"
        mock_tokenizer.pad_token_id = 0
        mock_tokenizer.eos_token_id = 1

        torch_stub = MagicMock()
        torch_stub.inference_mode.return_value = MagicMock(__enter__=lambda s: None, __exit__=lambda *a: None)

        with patch.dict(sys.modules, {"torch": torch_stub}):
            from forgelm.inference import generate

            result = generate(mock_model, mock_tokenizer, "Hello")

        assert result == "Hello world"
