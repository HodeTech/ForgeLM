# Çok Aşamalı Eğitim Pipeline'ları

Faz 14 — `v0.7.0` ile geliyor (CHANGELOG kaydı 2026-05-14; PyPI tag bekleniyor).

ForgeLM'in `pipeline:` config bloğu, 2 veya daha fazla eğitim aşamasını
(genellikle SFT → DPO → GRPO) tek bir config-tabanlı, dry-run-doğrulanabilir,
Annex IV-izlenebilir koşuya zincirler.  Faz 14'ten önce aynı iş 3 ya da
daha fazla ayrı config dosyası, aşamalar arası elle `model.name_or_path`
düzenleme ve dışarıdan bir shell betiğiyle orkestrasyon gerektiriyordu.
Faz 14'ten sonra tek bir `forgelm --config pipeline.yaml` çağrısı tüm
zinciri uçtan uca yürütür.

---

## When to use a pipeline (and when not to)

`pipeline:` bloğunu **tüm** aşağıdaki koşullar geçerliyse kullanın:

- Sıralı olarak 2 veya daha fazla eğitim aşaması koşturuyorsunuz
  (örn. SFT sonrası DPO).
- Her aşamanın giriş modeli, önceki aşamanın çıkış modelidir.
- Tüm zinciri kapsayan tek bir Annex IV manifest istiyorsunuz.

`pipeline:` bloğunu kullanmayın eğer:

- Sadece tek bir eğitim paradigması koşturuyorsanız.  Tek aşamalı
  config'ler (v0.6.0 default'u) hâlâ kanonik kullanım örneğidir ve
  Faz-14 öncesi davranışlarını byte-byte korur.
- Doğrusal olmayan aşama bağımlılıkları (DAG-şekilli pipeline'lar)
  istiyorsanız.  Faz 14 sadece sıralı pipeline'lar gönderir; şema
  ilerideki bir DAG genişlemesi için yüzeyi koruyor.
- Paralel aşama yürütmesi (eşzamanlı bağımsız dallar) istiyorsanız.
  DAG desteğiyle aynı zaman ufkunda — Wave 2 veya sonrası.

---

## Anatomy of a pipeline config

```yaml
# Root config — her aşamanın aşmadığı sürece miras aldığı varsayılanları
# sağlar.
model:
  name_or_path: "meta-llama/Llama-3-8B"      # Aşama 0'ın başlangıç modeli
lora:
  r: 8
  alpha: 16
training:
  trainer_type: "sft"                         # Training bloğunu miras alan
                                              # aşamalar bunu kullanır
data:
  dataset_name_or_path: "./placeholder.jsonl" # Root'ta zorunlu; her
                                              # aşama kendi data'sını verir

# Pipeline-level blok — zinciri tanımlar.
pipeline:
  output_dir: "./pipeline_run"                # pipeline_state.json,
                                              # compliance/pipeline_manifest.json
                                              # ve pipeline-level audit_log.jsonl
                                              # buraya yazılır
  stages:
    - name: sft_stage                         # ^[a-z0-9_]{1,32}$
      training:
        trainer_type: "sft"                   # Her aşama için zorunlu
        output_dir: "./pipeline_run/stage1_sft"
        num_train_epochs: 3
      data:
        dataset_name_or_path: "./data/sft.jsonl"

    - name: dpo_stage
      training:
        trainer_type: "dpo"
        output_dir: "./pipeline_run/stage2_dpo"
        num_train_epochs: 1
        dpo_beta: 0.1
      data:
        dataset_name_or_path: "./data/preferences.jsonl"

    - name: grpo_stage
      training:
        trainer_type: "grpo"
        output_dir: "./pipeline_run/stage3_grpo"
        grpo_num_generations: 4
      data:
        dataset_name_or_path: "./data/math_prompts.jsonl"
```

**Otomatik zincirleme:** her aşamanın `model.name_or_path` değeri,
önceki aşamanın `training.output_dir/final_model` yoluna otomatik
olarak ayarlanır — bu yolu elinizle yazmazsınız ve aşama N'in DPO
eğiticisini yanlışlıkla base modele (aşama N-1'in SFT çıktısı yerine)
yönlendiremezsiniz.

---

## Inheritance matrix

Section-wholesale override semantiği — bir aşama üst seviye blok (`model`,
`lora`, `training`, `data`, `evaluation`) tanımlarsa **tüm** blok root'unkini
değiştirir; aşama bloğu atlarsa root'un bloğu birebir miras alınır.  Field
seviyesinde deep-merge yok: "miras almak istiyorsan bloğu yazma; override
etmek istiyorsan tam bloğu yaz."

| Bölüm | Miras davranışı | Notlar |
|---|---|---|
| `model.name_or_path` | **Otomatik zincirlenir** (aşama 1'den itibaren root'u geçer) | Önceki aşamanın `training.output_dir/final_model` değerine ayarlanır.  Aşama 0 root'un `model.name_or_path`'ini okur.  Aşamada explicit `model:` bloğu otomatik zincirlemeyi o aşama için devre dışı bırakır (kaçış kapısı). |
| `model.*` (diğer alanlar) | `model:` bloğu override edilmediyse miras alınır | `backend`, `load_in_4bit`, `trust_remote_code`, `max_length`, `chat_template` root'u izler. |
| `lora` | `lora:` bloğu override edilmediyse miras alınır | **Kritik edge case:** aşama 2 DPO, aşama 1 SFT'den *farklı* bir `lora.r` ile gelirse aşama 2 **birleştirilmiş SFT modelinin üstüne taze bir LoRA**dır, SFT'nin LoRA'sının devamı değil.  Operatör-görünür davranış. |
| `data` | `data:` bloğu override edilmediyse miras alınır (önerilmez) | Şema aşama bazında override'ı **zorunlu kılmıyor** — operatörler farklı bir trainer'a karşı aynı veri setini ablation için yeniden koşturabilir — ama production'da aşamalar arası aynı dataset'i kullanan pipeline'lar son derece nadirdir. Operator kılavuzu inheritance'ı bir kötü-koku olarak işaretler; gerçek pipeline'ların neredeyse tümünde SFT küratör SFT dataset'i kullanır, DPO preference-pairs dataset'i, GRPO matematik/ödül dataset'i. |
| `training` | `training:` bloğu override edilmediyse miras alınır | `trainer_type` her aşamada explicit set EDİLMEK ZORUNDADIR (audit-clarity doğrulayıcısı).  Diğer alanlar blok override edildiğinde wholesale yer değiştirir. |
| `evaluation` | `evaluation:` bloğu override edilmediyse miras alınır | Aşama bazında gate'ler (loss eşikleri, auto_revert, safety, judge, human-approval) burada yaşar. |
| `distributed` | Sadece root — **aşamada reddedilir** | Distributed strateji koşu boyunca tutarlı olmak zorundadır. |
| `webhook` | Sadece root — **aşamada reddedilir** | Aşama bazında olaylar payload'da aşama adını taşır. |
| `compliance` | Sadece root — **aşamada reddedilir** | Provider/system/risk metadata pipeline seviyesindedir. |
| `risk_assessment`, `monitoring`, `retention`, `synthetic`, `merge`, `auth` | Sadece root — **aşamada reddedilir** | Pipeline seviyesi sorumlulukları. |

Bir aşama root-only bölümlerden birini tanımlarsa, config yükleme
zamanında `EXIT_CONFIG_ERROR (1)` ile reddedilir ve hata mesajı
ihlal eden bölüm adını taşır.

---

## CLI

```bash
# Uçtan uca pipeline koşusu.
forgelm --config pipeline.yaml

# Dry-run her aşamayı (Pydantic + chain integrity) hiçbir GPU
# tahsisi olmadan doğrular.  `pytest --collectonly` gibi tüm
# hataları çıkmadan önce toplar.
forgelm --config pipeline.yaml --dry-run

# Tek bir adlı aşamayı yalıtılmış olarak koştur (audit / yeniden
# koşturma senaryoları).  İlk olmayan aşamalar önceki aşamanın
# disk üzerindeki çıktısını veya explicit --input-model override
# gerektirir.
forgelm --config pipeline.yaml --stage dpo_stage

# Başarısız / kesintiye uğramış bir koşudan adlı aşamadan itibaren
# devam et.  output_model yolu disk üzerinde var olan
# already-completed aşamalar atlanır (INFO log'lanır).
forgelm --config pipeline.yaml --resume-from dpo_stage

# Tek aşama için otomatik zincirlenen input modeli override et
# (kaçış kapısı).  Audit log `input_source: cli_override` kaydeder.
forgelm --config pipeline.yaml --stage dpo_stage --input-model ./other/checkpoint
```

### `--stage <name>` partial-run rules

| Senaryo | Davranış |
|---|---|
| `--stage <name>` ve `<name>` ilk aşama | Root `model.name_or_path` okunur; normal koşar. |
| `--stage <name>` ve önceki aşamanın `output_dir/final_model` diskte var | Önceki aşama yeni bitmiş gibi otomatik zincirlenir. |
| `--stage <name>` ve önceki aşamanın çıktısı eksik | `EXIT_CONFIG_ERROR (1)` ile hard-fail: `Stage <name> requires <prev_stage> output at <path>; pass --input-model <path> to override or run the full pipeline first.`  Root `model.name_or_path`'e sessiz geri dönüş yok. |
| `--stage <name> --input-model <path>` | Operatör kaçış kapısı: otomatik zincirlemeyi atlar, `<path>`'i kullanır.  Audit log `input_source: cli_override` kaydeder. |
| `--stage <name>` ve `<name>` config'te yok | Parse zamanında valid aşama adları listesiyle hard-fail. |

### `--resume-from <name>` semantics

- State dosyası: `<pipeline.output_dir>/pipeline_state.json` (atomic-write).
- `status: completed` ve `output_model` yolu hâlâ diskte olan aşamalar
  **atlanır** ve aşama bazında training manifest'leri korunur.  Adlı
  aşama ve sonrasındaki her aşama yeniden koşturulur.
- **Stale-state koruması:** disk üzerindeki state dosyasının
  `pipeline_config_hash`'i mevcut YAML byte'larından farklıysa, resume
  `EXIT_CONFIG_ERROR (1)` ile başarısız olur — bu "koşu sırasında
  config'i düzenledim ve resume ettim" sessiz divergenc'yi engeller.
  `--force-resume` ile override edilebilir (WARNING log'lanır, audit
  event kaydedilir).
- Faz 14 sadece **aşama sınırlarında** resume eder.  Aşama içi HF
  `Trainer.train(resume_from_checkpoint=...)` entegrasyonu bir Faz 14.x
  takipçisine ertelendi — Sınırlamalar bölümüne bakın.

---

## Human-approval gate within a pipeline

Bir aşama `evaluation.require_human_approval: true` taşıyorsa, ForgeLM'in
mevcut Faz 9 akışı aynen koşar: model `final_model.staging.<run_id>/`'ye
iner, orkestratör staging yolunu pipeline state'inde
`status: gated_pending_approval` ile yakalar ve koşu
`EXIT_AWAITING_APPROVAL (4)` ile çıkar.

Sonraki aşamalar `pending` kalır (`skipped_due_to_prior_revert` değil)
böylece onaydan sonra resume onları alır:

```bash
# Aşama 1 SFT onay bekliyor — pipeline 4 ile çıkar.
$ forgelm --config pipeline.yaml
# ... → exit 4

# Operatör staged modeli inceler, sonra onaylar.
$ forgelm approve <run_id> --output-dir ./pipeline_run/stage1_sft
# ... → final_model/ promote edilir, exit 0

# Pipeline'ı DPO'dan resume et; SFT atlanır (status=completed on disk).
# DPO'nun input_model'i staging değil *promote edilen* final_model/
# yolunu işaret eder.
$ forgelm --config pipeline.yaml --resume-from dpo_stage
# ... → zincirin geri kalanı başarılıysa exit 0
```

---

## Audit events

Orkestratör bu olayları pipeline-level `audit_log.jsonl`'a (pipeline.output_dir
altında) emit eder:

- `pipeline.started` — run id, config hash, stage count, stage names
- `pipeline.stage_started` — stage name, index, input model, input source
- `pipeline.stage_completed` — stage name, gate decision (`passed` / `failed`), metrics summary
- `pipeline.stage_gated` — stage name, gate decision `approval_pending`, staging path (bir aşama `EXIT_AWAITING_APPROVAL` ile çıkış yaptığında emit edilir)
- `pipeline.stage_reverted` — stage name, auto-revert reason
- `pipeline.force_resume` — operatör onaylı stale-hash override, `old_config_hash` + `new_config_hash` taşır
- `pipeline.completed` — final status, stopped-at stage (varsa)

Bu olaylar mevcut her aşamanın `ForgeTrainer`'inin emit etmeye devam
ettiği `training.*` olaylarının yanında yaşar; `training.failure`
üzerinde filtreleyen Slack / Teams dashboard'ları değişmeden çalışır.

İlgili webhook metotları `notify_pipeline_started`,
`notify_pipeline_completed` ve `notify_pipeline_reverted` —
`forgelm/webhook.py`'a bakın.

---

## Annex IV manifest

Her aşama geçişi `<pipeline.output_dir>/compliance/pipeline_manifest.json`
dosyasını (atomic-write) yeniden yazar.  Pipeline manifest, aşama bazında
`training_manifest.json` dosyalarını doğrulanabilir bir zincire bağlayan
**indekstir**; aşama bazında manifest'ler tek aşamalı Annex IV şemasına
karşı bireysel olarak geçerli kalmaya devam eder.

Tam bir koşuyu doğrulayıcı ile geçerleyin:

```bash
forgelm verify-annex-iv --pipeline ./pipeline_run
```

Doğrulayıcı şu koşullar sağlandığında exit 0 döner:

1. Her zorunlu üst seviye anahtar mevcut ve iyi biçimli.
2. Aşama indeksleri sırayla `0..N-1`.
3. Her chain aşamasının `input_model`'i önceki yürütülen aşamanın
   `output_model`'iyle eşit (operatör `--input-model` override'ları
   `input_source: cli_override` ile kaydedilir ve bu kontrolden
   tasarımla muaftır — auditor'lar legitimate override'ı bozuk
   manifest'ten ayırmak için audit log'a bakar).
4. Her aşama bazında `training_manifest` yolu gerçek bir dosyayı
   işaret eder.
5. `stopped_at` (set ise) gerçek bir aşamayı adlandırır ve o aşamanın
   status'u `failed` veya `gated_pending_approval`.

Sıfır olmayan exit kodu ihlalleri keşfedildikleri sırayla listeler.

---

## Limitations (Phase 14 Wave 1)

- **Aşama içi checkpoint resume yok.**  `--resume-from` sadece aşama
  sınırlarında devam alır.  Bir aşamanın `ForgeTrainer.train()`
  yarıda crash ederse, resume o aşamayı epoch 0'dan yeniden koşturur.
  Wave 2.
- **Sadece sıralı — DAG semantiği yok.**  Aşamalar `pipeline.stages`
  içinde görünme sıralarında yürütülür.  Branch / fan-out / merge
  ertelendi.
- **Paralel aşama yürütmesi yok.**  İki aşama mantıken bağımsız olsa
  bile orkestratör onları sıralı koşturur.
- **`pipeline.output_dir` üzerinde multi-process lock yok.**  Aynı
  `pipeline.output_dir`'a karşı eş zamanlı iki `forgelm --config
  pipeline.yaml` çağrısı `pipeline_state.json` + manifest üzerinde
  race oluşturur — eş zamanlı koşular için ayrı `pipeline.output_dir`
  seçin.  Atomic-write helper'ı writer başına benzersiz temp adı
  kullanır, dolayısıyla race son-yazan-kazanır şeklinde yüzeyleşir
  (FileNotFoundError traceback değil), ama kaybedenin per-stage
  ilerlemesi kendi process'inde görünmez kalır.
- **Stage ve pipeline `output_dir` çakışması mümkün ama audit log'u
  iç içe geçirir.**  Bir aşamanın çözülen `training.output_dir`'ı
  `pipeline.output_dir`'a eşit olduğunda, aşamanın `training.*`
  olayları ile pipeline'ın `pipeline.*` olayları aynı
  `audit_log.jsonl` dosyasına düşer (dizin başına bir dosya).
  Hash-chain + satır-bazında flock dosyayı iç tutarlı tutar ve
  `tests/fixtures/pipeline/inheritance_matrix.yaml` fixture'ı bu
  layout'u kasten icra eder; ama dosya başına tek `event_kind`
  bekleyen SIEM filtreleri ayrı dizinler kullanmalı.
- **`--force-resume` topology guard yalnızca pozisyonel kırılmaları
  yakalar.**  Stage ekleme, silme veya yeniden adlandırma
  `--force-resume` altında bile reddedilir (state.stages[i] başka
  bir aşamayı adresliyor olur).  *Value-level* düzenlemeler — aynı
  stage adı altında farklı `trainer_type`, `training.output_dir`,
  hyperparameter — tasarımı gereği kabul edilir; `--force-resume`
  operatörün ayrışmayı anladığını bildiren sinyaldir.
- **`forgelm wizard` entegrasyonu yok.**  Tek aşamalı config'ler
  wizard'la üretilebilir; pipeline'lar operatör-seviyesi ve manuel
  YAML yüzeyini kullanır.  Wizard'ın işi "çalışan bir tek aşama
  config'i üret, büyüdüğünde elle pipeline'a düzenle" olarak kalır.
- **Pakete dahil bir pipeline-demo notebook'u yok.**  `notebooks/`
  altındaki 11 demo notebook her eğitim paradigmasını ayrı ayrı zaten
  kapsar ve önerilen etkileşimli yüzey olmaya devam eder; uçtan uca
  bir pipeline demosu her notebook'un setup boilerplate'ini üç kez
  tekrarlardı.  `tests/fixtures/pipeline/` altındaki fixture suite,
  reviewer'lara bir notebook'la aynı yüzeyi verir — üstüne golden
  manifest'le byte-comparable olma avantajıyla.

---

## Cross-references

- Faz 14 yayınlanan kapsam: [docs/roadmap/completed-phases.md](../roadmap/completed-phases.md#phase-14--multi-stage-pipeline-chains-v070)
- Faz 14.5 takip planı: [docs/roadmap/phase-14-5-pipeline-hardening.md](../roadmap/phase-14-5-pipeline-hardening.md)
- Roadmap girişi: [docs/roadmap-tr.md](../roadmap-tr.md)
- Annex IV doğrulayıcısı: `forgelm verify-annex-iv --pipeline <run_dir>` (CLI help'e bakın)
- Audit log standardı: [docs/standards/logging-observability.md](../standards/logging-observability.md)
- Tek aşamalı eğitici kılavuzu (her şey bu yüzeyden miras alır):
  [docs/guides/alignment-tr.md](alignment-tr.md)
