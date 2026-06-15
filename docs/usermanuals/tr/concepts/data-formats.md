---
title: Dataset Formatları
description: SFT, DPO, SimPO, KTO, ORPO ve GRPO için JSONL formatları — her trainer ne bekler.
---

# Dataset Formatları

Her ForgeLM trainer'ı belirli bir JSONL formatı bekler. ForgeLM dosyanın ilk satırından formatı otomatik algılar; explicit beyan etmenize gerek yok — ama doğru şekli üretmeniz gerekir.

## Hızlı referans

| Format | Kullanan | Gerekli alanlar |
|---|---|---|
| `instructions` | SFT | `prompt`, `completion` |
| `messages` | SFT (multi-turn) | `messages: [{role, content}, …]` |
| `preference` | DPO, SimPO, ORPO | `prompt`, `chosen`, `rejected` |
| `binary` | KTO | `prompt`, `completion`, `label` |
| `reward` | GRPO | `prompt` (yanıt eğitim sırasında üretilir) |

## Instructions (tek-tur SFT)

En basit format — satır başına bir prompt, bir completion.

```json
{"prompt": "Türkiye'nin başkenti neresi?", "completion": "Ankara."}
{"prompt": "'Hello' kelimesini Türkçeye çevir.", "completion": "Merhaba."}
```

Opsiyonel alanlar:
- `system` — konuşmaya prepend edilen system prompt'u.
- `metadata` — keyfi dict; audit log'da korunur, eğitimde kullanılmaz.

## Messages (multi-turn SFT)

HuggingFace'in yerli chat formatı. Konuşmalar birden fazla turdaysa kullanın.

```json
{"messages": [
  {"role": "system", "content": "Sen kibar bir müşteri destek temsilcisisin."},
  {"role": "user", "content": "Aboneliğimi nasıl iptal ederim?"},
  {"role": "assistant", "content": "Ayarlar → Faturalandırma → Aboneliği İptal Et adımlarıyla..."},
  {"role": "user", "content": "Yine ücretlendirilir miyim?"},
  {"role": "assistant", "content": "Hayır. Erişiminiz mevcut faturalama döneminin sonuna kadar devam eder."}
]}
```

Roller: `system`, `user`, `assistant`. Tool-call rolleri (`tool`, `function`) chat template tanımladığında desteklenir.

:::tip
Eğitimdeki chat template modelin tokenizer'ından gelir. ForgeLM `tokenizer.apply_chat_template()` kullanır; Llama 3 chat formatında eğitilen model, Llama 3 chat istemcileri tarafından özel bir şey yapmadan doğru servis edilir.
:::

## Preference (DPO / SimPO / ORPO)

Her satır üçlü: prompt, tercih edilen yanıt, reddedilen yanıt.

```json
{
  "prompt": "Aboneliği nasıl iptal ederim?",
  "chosen": "Ayarlar → Faturalandırma → Aboneliği İptal Et. Erişim mevcut dönemin sonuna kadar devam eder.",
  "rejected": "Sadece ödemeyi durdurmak yeterli."
}
```

Opsiyonel:
- `system` — her iki yanıt için ortak system prompt.
- `prompt_messages` — multi-turn prompt array (nadir; prompt kendisi bir konuşma ise).

Audit (`forgelm audit`) `chosen == rejected` olan satırları flagler — preference toplama pipeline'larında sık bug.

## Binary (KTO)

Tek yanıt + thumbs-up/down. Eşli tercihten daha kolay toplanır.

```json
{
  "prompt": "Aboneliği nasıl iptal ederim?",
  "completion": "Sadece ödemeyi durdur.",
  "label": false
}
{
  "prompt": "Aboneliği nasıl iptal ederim?",
  "completion": "Ayarlar → Faturalandırma → Aboneliği iptal et…",
  "label": true
}
```

Anlamlar:
- `label: true` → istenen yanıt
- `label: false` → istenmeyen yanıt

KTO her iki sınıftan da örnek bekler — minimum %5-10 azınlık sınıfı kararlı eğitim için.

## Reward (GRPO)

GRPO completion'ları toplamaz — eğitim sırasında üretir. Promptları siz verirsiniz, ForgeLM yanıt örnekler, reward fonksiyonunuzla skorlar, policy'i günceller.

```json
{"prompt": "Çöz: 17 × 23 = ?", "ground_truth": "391"}
{"prompt": "Çöz: 144 ÷ 12 = ?", "ground_truth": "12"}
```

`ground_truth` alanı ForgeLM için opaque — reward fonksiyonunuza geçirilir:

```python
# my_reward.py
def reward(prompt: str, response: str, ground_truth: str) -> float:
    answer = extract_number(response)
    if answer is None:
        return -0.5
    return 1.0 if answer == int(ground_truth) else -1.0
```

Yerleşik format/uzunluk reward'ları için bkz. [GRPO](#/training/grpo).

## Çoklu veri seti karışımı

```yaml
datasets:
  - path: "data/policies.jsonl"
    format: "messages"
    weight: 0.7
  - path: "data/general-qa.jsonl"
    format: "instructions"
    weight: 0.3
```

Ağırlıklar 1.0'a tamamlanır; her batch bu olasılıklara göre örneklenir.

## Otomatik algılama

`format:` belirtmezseniz ForgeLM ilk boş olmayan satırı inceler:

| Satırda olan | Algılanan |
|---|---|
| `messages` array | `messages` |
| `chosen` ve `rejected` | `preference` |
| `completion` ve `label` (bool) | `binary` |
| `prompt` ve `completion` | `instructions` |
| Sadece `prompt` | `reward` |

:::warn
Otomatik algılama dosya başına bir kez. JSONL'iniz formatları karıştırırsa loader yanlış yönlendirir. Ayrı dosyalar kullanın ve her ikisini `datasets:` ile referans verin.
:::

## Verinizi doğrulama

Eğitimden önce her zaman `forgelm audit` çalıştırın:

```shell
$ forgelm audit data/preferences.jsonl
✓ format: preference (12,400 satır, 3 split)
⚠ PII bulundu: 5 orta ciddiyet (rapora bakın)
⚠ 12 chosen-rejected aynı satır — muhtemelen toplama hatası
✓ split'ler arası sızıntı yok
```

Tam audit semantiği için bkz. [Veri Seti Denetimi](#/data/audit).

## Bkz.

- [Doküman Ingest'i](#/data/ingestion) — PDF/DOCX/EPUB/Markdown'ı bu formatlara dönüştür.
- [Veri Seti Denetimi](#/data/audit) — eğitimden önce çalıştır.
- [Trainer Seçimi](#/concepts/choosing-trainer) — verinizi doğru trainer'a eşleyin.
