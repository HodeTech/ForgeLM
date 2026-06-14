# Hizalama & Eğitim-Sonrası Rehberi

ForgeLM modern eğitim-sonrası stack'in tamamını destekler: SFT →
Preference Optimization → Reasoning RL. Bu rehber her yöntemi ne zaman
ve nasıl kullanacağınızı açıklar.

---

## Yöntem genel bakışı

| Yöntem | `trainer_type` | Veri seti formatı | Ne zaman kullanılır |
|--------|---------------|-------------------|---------------------|
| **SFT** | `"sft"` | System/User/Assistant ya da `messages` | Talimat ayarı — modele *ne* söyleyeceğini öğret |
| **DPO** | `"dpo"` | `chosen` / `rejected` çiftleri | Preference hizalama — *nasıl* daha iyi söyleyeceğini öğret |
| **SimPO** | `"simpo"` | `chosen` / `rejected` çiftleri | DPO gibi ama referans modeli yok (daha düşük bellek) |
| **KTO** | `"kto"` | `completion` + `label` (bool) | Binary feedback — yalnızca thumbs up/down var |
| **ORPO** | `"orpo"` | `chosen` / `rejected` çiftleri | SFT + hizalama tek aşamada |
| **GRPO** | `"grpo"` | yalnız `prompt` | Reasoning RL — model üretip kendini iyileştirir |

---

## Modern eğitim-sonrası stack

2026'da production LLM'lerin çoğu şu pipeline'ı izler:

```
Base Model
    ↓
[Aşama 1] SFT — küratör veri üzerinde talimat ayarı
    ↓
[Aşama 2] DPO/SimPO/KTO — preference hizalama
    ↓
[Aşama 3] GRPO — reasoning RL (opsiyonel; matematik/kod için)
    ↓
Production Model
```

ForgeLM her aşamayı farklı config'lerle ayrı bir `forgelm` koşumu olarak
ele alır.

---

## Aşama 1: Supervised Fine-Tuning (SFT)

**Amaç:** Modele alanınızdaki talimatları izlemeyi öğret.

### Veri seti formatı

```json
{"System": "Hukuk asistanısın.", "User": "Tort nedir?", "Assistant": "Tort medeni bir haksız fiildir..."}
```

Ya da modern `messages` formatı:

```json
{"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
```

### Config

```yaml
model:
  name_or_path: "meta-llama/Llama-3.1-8B-Instruct"
  max_length: 4096
  backend: "transformers"

lora:
  r: 16
  alpha: 32
  target_modules: ["q_proj", "k_proj", "v_proj", "o_proj"]

training:
  trainer_type: "sft"
  num_train_epochs: 3
  learning_rate: 2.0e-5
  per_device_train_batch_size: 4

data:
  dataset_name_or_path: "./data/sft_data.jsonl"
```

```bash
forgelm --config sft_config.yaml
```

---

## Aşama 2: Preference hizalama

SFT sonrası modelin yanıtlarını insan tercihleriyle hizala. Verinize
göre seçin:

### DPO — Direct Preference Optimization

**En uygun:** Eşli tercih veriniz var (chosen vs rejected yanıtlar).

```json
{"prompt": "Recursion'ı açıkla", "chosen": "Recursion bir tekniktir...", "rejected": "Recursion bir şeyi tekrar yapmaktır..."}
```

```yaml
training:
  trainer_type: "dpo"
  dpo_beta: 0.1          # Sıcaklık — düşük = daha güçlü preference sinyali
  learning_rate: 5.0e-6   # SFT'den daha düşük LR
  num_train_epochs: 1     # 1-2 epoch genellikle yeterli

data:
  dataset_name_or_path: "./data/preferences.jsonl"
```

### SimPO — Simple Preference Optimization

**En uygun:** DPO ile aynı veri ama daha az bellek istiyorsunuz
(referans modeli gerekmez).

SimPO 7B+ ölçekte DPO'dan daha iyi performans gösterir (AlpacaEval
2'de +6.4 puan).

```yaml
training:
  trainer_type: "simpo"
  simpo_beta: 2.0         # Ölçekleme parametresi
  simpo_gamma: 0.5        # Margin terim
  learning_rate: 5.0e-6
```

### KTO — Kahneman-Tversky Optimization

**En uygun:** Yalnızca binary feedback (thumbs up/down) var, eşli
tercih yok. Production veri toplama için daha pratiktir.

```json
{"prompt": "Python nedir?", "completion": "Python bir programlama dilidir...", "label": true}
{"prompt": "Python nedir?", "completion": "Python bir yılandır.", "label": false}
```

```yaml
training:
  trainer_type: "kto"
  kto_beta: 0.1
  learning_rate: 5.0e-6

data:
  dataset_name_or_path: "./data/kto_feedback.jsonl"
```

### ORPO — Tek aşamalı SFT + hizalama

**En uygun:** SFT ve hizalamayı tek eğitim koşusunda birleştirmek
istiyorsunuz. chosen/rejected veriyi kullanır ama talimat formatını
da öğrenir.

```yaml
training:
  trainer_type: "orpo"
  orpo_beta: 0.1
```

---

## Aşama 3: Reasoning RL (GRPO)

**En uygun:** Çıktıların doğrulanabildiği matematik, kod, akıl
yürütme görevleri. DeepSeek-R1'in arkasındaki yöntem.

GRPO prompt başına çoklu yanıt üretir, skorlar ve daha iyileri
güçlendirir — insan tercih verisi gerekmez.

```json
{"prompt": "Çöz: 240'ın %15'i nedir?", "gold_answer": "36"}
```

```yaml
training:
  trainer_type: "grpo"
  grpo_num_generations: 4              # Prompt başına 4 yanıt üret
  grpo_max_completion_length: 512      # Tamamlama başına maks token (legacy alias `grpo_max_new_tokens` hâlâ kabul ediliyor)
  grpo_reward_model: null              # Aşağıdaki "Reward seçimi"ne bakın.
  learning_rate: 1.0e-6      # RL stabilitesi için çok düşük LR
  num_train_epochs: 1

data:
  dataset_name_or_path: "./data/math_prompts.jsonl"
```

### Reward seçimi

GRPO bir reward sinyaline ihtiyaç duyar. ForgeLM reward callable'ları
toplamsal olarak bağlar (TRL birden fazla reward fonksiyonunu tek bir
skalere toplar):

1. **`grpo_reward_model` set** — O path'teki HF sequence-classification
   modelini yükler ve onun skalar çıktısını tek reward sinyali olarak
   kullanır. Aşağıdaki yerleşik reward'lar bypass edilir; operatör
   öğrenilmiş bir reward'ı opt-in seçti.
2. **`grpo_reward_model` yok** — bir baseline reward her zaman bağlanır:
   - **`combined_format_length_reward`** (`forgelm/grpo_rewards.py`) —
     `0.8 × format_match + 0.2 × length_shaping`. Format bileşeni,
     üretim `Answer: <değer>` ile bittiğinde 1.0 döner (case-insensitive,
     birim izinli); uzunluk bileşeni `min(len(completion) / 200, 1.0)`
     döner; bu sayede erken eğitimde format uyumu başlamadan da
     non-flat gradient olur.
   - **`_math_reward_fn`** (`forgelm/trainer.py`) — yalnız dataset'in
     `gold_answer` alanı varsa eklenir. **Son** `Answer:` işaretinden
     sonraki değeri yakalar (böylece kendini düzelten bir üretim,
     gerçekten sonuçlandırdığı yanıt üzerinden — sona sabitlenmiş
     format ödülüyle tutarlı olarak — puanlanır), yaygın birimleri
     (`$`, `%`, `km/h`, `m²`, `liters`, …) soyar ve `gold_answer` ile
     önce exact-string, sonra numerik tolerans (1e-6) ile
     karşılaştırır. Doğru yanıt için `1.0`, aksi halde `0.0`.

Bundled `forgelm quickstart grpo-math` şablonu `gold_answer` doldurulmuş
olarak ship olur, böylece model kutudan çıktığı gibi hem format
öğretimi hem doğruluk öğretimi alır. grpo-math üzerinde gerçek bir
reward modelini kullanmak için `grpo_reward_model`'i set edin —
yerleşik reward'lar bypass edilir.

Kendi datasetiniz için: format+length baseline'ı her durumda geçerlidir.
Doğruluk sinyalini de almak için satır başına `gold_answer` alanı
ekleyin — prompt'un beklenen çıktı formatı `Answer: <değer>` (soyulan
opsiyonel birimlerle).

> **Not:** GRPO bir reward fonksiyonuna ya da doğrulanabilir reward'a
> ihtiyaç duyar. Matematik için yanıtın doğruluğu reward'dır. Genel
> metin için bir reward modeline ihtiyaç duyabilirsiniz.

---

## Doğru yöntemi seçme

```
Eşli tercih (chosen/rejected) verin var mı?
├── Evet → Bellek endişe mi?
│   ├── Evet → SimPO
│   └── Hayır → DPO
├── Hayır → Binary feedback (iyi/kötü) verin var mı?
│   ├── Evet → KTO
│   └── Hayır → Doğrulanabilir reward'larınız (matematik/kod) var mı?
│       ├── Evet → GRPO
│       └── Hayır → Sadece SFT kullanın
```

ForgeLM'in `--wizard` modu seçim yapmanıza yardım eder:

```bash
forgelm --wizard
# Adım 4 sorar: "Eğitim hedefinizi seçin"
# Her yöntem için format gerekliliklerini gösterir
```

---

## Çok aşamalı pipeline örneği

```bash
# Aşama 1: SFT
forgelm --config configs/stage1_sft.yaml

# Aşama 2: DPO (SFT modelini base olarak kullanır)
# stage2_dpo.yaml içinde:
#   model.name_or_path: "./checkpoints_sft/final_model"
forgelm --config configs/stage2_dpo.yaml

# Aşama 3: GRPO (DPO modelini base olarak kullanır)
forgelm --config configs/stage3_grpo.yaml
```

> **Planlanan (Faz 14 — pipeline chains):** Tek bir YAML dosyasında
> çok-aşamalı eğitim zincirlerini tanımlayan bir `pipeline:` config
> anahtarı, aşamalar arası manuel config curating'i ortadan kaldıracak.
> Takip issue'su v0.6.0+ release penceresine bağlı.

---

## İpuçları

- **Learning rate**: SFT 1e-5 - 3e-5 kullanır. Hizalama yöntemleri
  5e-7 - 5e-6 kullanır. GRPO 1e-6 ya da daha düşük.
- **Epoch**: SFT genellikle 2-3 epoch ister. Hizalama yöntemleri
  genellikle 1-2 epoch. Daha fazlası daha iyi değildir.
- **Veri kalitesi > veri miktarı**: 1.000 yüksek-kaliteli preference
  çifti, 50.000 gürültülü çiften daha iyi performans gösterir.
- **Her zaman değerlendir**: Kalite regresyonlarını yakalamak için
  `max_acceptable_loss` ile `auto_revert: true` kullan.
- **Ölçek önemlidir**: Araştırma (arxiv 2603.19335), algoritma
  sıralamalarının ölçeğe-bağlı olduğunu gösterir — SimPO 7B'de en
  iyidir ama DPO 1.5B'de daha iyi olabilir.
