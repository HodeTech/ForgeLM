# Programatik ForgeLM Kullanımı

> **Hedef kitle:** ForgeLM'i pipeline orkestratörlerine (Airflow, Prefect, Dagster, Argo, Kubeflow) gömen veya CLI'nin process sınırının yolunda olduğu notebook'lardan çalışan ML mühendisleri.
>
> **Eşlik eden referans:** [`../reference/library_api_reference-tr.md`](../reference/library_api_reference-tr.md) — her public sembol, imza ve kararlılık katmanı.
>
> **Tasarım kaynağı:** [`../design/library_api.md`](../design/library_api.md) (Faz 18).

ForgeLM **eşit derecede first-class iki giriş noktası** yayınlar: shell pipeline'ları için `forgelm` console script'i (ve `python -m forgelm.cli`), programatik kullanım için burada belgelenen Python kütüphane API'si. CLI sarmalayıcıdır; kütüphane motordur. CLI'nin yaptığı her şeyi kütüphane yapabilir — exit-code eşlemesi ve yapılandırılmış logging kurulumu hariç.

## Library API ile CLI ne zaman kullanılır

**CLI**'yı seçin:

- Bir Bash / GitHub Actions / GitLab CI pipeline yayınlıyorsanız. Exit-code kontratı (0/1/2/3/4/5/6) entegrasyon yüzeyidir.
- ForgeLM'in stdout'a kutudan çıkar çıkmaz yaydığı yapılandırılmış logging + JSON envelope'larını istiyorsanız.
- Aşama başına bir process'in operasyonel birim olduğu altyapıda (çoğu CI runner, çoğu Argo pipeline) çalışıyorsanız.

**Library API**'yı seçin:

- Python'dan orkestre ediyorsanız: Airflow operatörleri, Prefect task'ları, Dagster op'ları, özel orkestratörler.
- Tek bir Python process'inde birden fazla ForgeLM operasyonunu (audit → train → verify → notify) compose etmeniz gerekiyorsa ve aşama başına subprocess overhead'i önemliyse.
- Konfigürasyonları programatik olarak parametrelendiriyorsanız (in-memory grid search, Bayesian sweep) ve YAML üzerinden round-trip sürtünme yaratıyorsa.
- Exit code yerine typed exception (`ConfigError`, `RuntimeError`, `OSError`) istiyorsanız.
- İnteraktif keşif için Jupyter notebook'tan çağırıyorsanız.

Yaygın bir hibrit: veri üretimi + denetimi için library API, GPU eğitim adımı için CLI — çünkü CLI'nin exit-code + auto-revert yolu CI deployment gate'lerinizin karşı koştuğu şeydir.

## Hızlı başlangıç

Her iki giriş noktasının kullandığı aynı wheel'i kurun:

```bash
pip install 'forgelm[ingestion]'
# veya security tooling'i için:
pip install 'forgelm[ingestion,security]'
```

Lazy-import kontratının geçerli olduğunu smoke test edin:

```python
import sys
import forgelm

# Paketi import etmek torch'u çekmez — kontrat gereği.
assert "torch" not in sys.modules

# Ama public yüzey autocomplete için tam keşfedilebilir.
print("ForgeTrainer" in dir(forgelm))   # True
print(forgelm.__version__)              # ör. "0.7.0"
print(forgelm.__api_version__)          # ör. "1.0.0"
```

Wheel ile birlikte yayınlanan çalıştırılabilir bir notebook (`notebooks/library_api_example.ipynb`) bu sayfanın kapsadığı aynı üç deseni yürür — provenance için tasarım dokümanı Faz 19 görev #13'e bakın.

## Yaşam döngüsü: config yükle → eğit → değerlendir → audit olayları yay

Kanonik library-modu pipeline'ı CLI'nin aşama sıralamasını yansıtır. Her aşama tek bir `forgelm.<symbol>` çağrısıdır.

```python
import logging
import os
from forgelm import (
    AuditLogger,
    ForgeTrainer,
    audit_dataset,
    load_config,
    verify_audit_log,
)

# Library hygiene: tüketici logger'ını açıkça yapılandırın.
# import forgelm, logging.basicConfig() ÇAĞIRMAZ.
logging.basicConfig(level=logging.INFO)
logging.getLogger("forgelm").setLevel(logging.INFO)

# Operatör kimliği zorunludur (veya yalnızca kısa-ömürlü test koşuları için
# FORGELM_ALLOW_ANONYMOUS_OPERATOR=1 ayarlayın — bkz. Yaygın tuzaklar).
os.environ.setdefault("FORGELM_OPERATOR", "airflow:dag-train:run-${RUN_ID}")

# 1. Config'i yükle + doğrula (geçersiz YAML'da ConfigError fırlatır).
config = load_config("configs/run.yaml")

# 2. Eğitim öncesi corpus'u denetle. CLI'nin `forgelm audit`
#    subcommand'inin yürüdüğü aynı gate.
report = audit_dataset(
    config.data.dataset_name_or_path,
    output_dir=config.training.output_dir,
    enable_pii_ml=True,
)
if report.near_duplicate_summary["pairs"] > 50 or report.pii_summary:
    raise SystemExit("data quality gate failed; fix before training")

# 3. Eğit. Ağır bağımlılıklar (torch, trl, transformers) yalnızca
#    .train() çağrıldığında yüklenir.
trainer = ForgeTrainer(config)
result = trainer.train()

# 4. Run bittikten sonra audit zincirini doğrula — başarı/başarısızlıktan
#    bağımsız olarak. Reverted bir run hâlâ geçerli bir zincir bırakır.
output_dir = config.training.output_dir          # kanonik: `training.output_dir`
verification = verify_audit_log(
    f"{output_dir}/audit_log.jsonl",             # audit_log.jsonl output_dir kökünde, compliance/ altında değil
    require_hmac=bool(os.environ.get("FORGELM_AUDIT_SECRET")),
)
if not verification.valid:
    raise SystemExit(f"audit chain broken: {verification.reason}")

# 5. Denetçinin ForgeLM run → orkestratör run korelasyonu kurabilmesi
#    için kendi pipeline-orkestratör-spesifik olayınızı aynı audit zincirine yayın.
#    `AuditLogger.run_id` parametre verilmediğinde otomatik üretilir; aynı
#    output_dir'i tekrar kullanın ve yeni event mevcut zincire eklenir.
logger = AuditLogger(output_dir=output_dir)
logger.log_event(
    "training.completed",
    orchestrator="airflow",
    dag_id="train",
    run_id=os.environ["RUN_ID"],
    outcome="success" if result.success else "reverted",
)
```

## Yaygın desenler

### Bir dict'ten config kurma (YAML yok)

`ForgeConfig` bir Pydantic modelidir. Parametrik sweep'lere ihtiyacınız olduğunda doğrudan kurun:

```python
from forgelm import ForgeConfig, ConfigError

base = {
    "model": {"name": "TinyLlama/TinyLlama-1.1B-Chat-v1.0"},
    "dataset": {"path": "data/train.jsonl", "format": "alpaca"},
    "training": {"trainer_type": "sft", "num_epochs": 1},
}

for lr in (1e-5, 2e-5, 5e-5):
    payload = {**base, "training": {**base["training"], "learning_rate": lr}}
    try:
        config = ForgeConfig(**payload)
    except ConfigError as exc:
        print(f"sweep cell rejected: {exc}")
        continue
    # ... ForgeTrainer'a besle ...
```

### Özel bir transform içinde bağımsız PII / secret temizleme

PII / secret yardımcıları trainer'dan bağımsızdır. Sade Python helper'ları olarak kullanın:

```python
from forgelm import detect_pii, detect_secrets, mask_pii, mask_secrets

def scrub(record: dict) -> dict:
    text = record["text"]
    if detect_secrets(text):
        text = mask_secrets(text)
    if detect_pii(text, language="en"):
        text = mask_pii(text, language="en")
    return {**record, "text": text}

scrubbed = [scrub(r) for r in raw_records]
```

### Audit gate'ini bir Airflow PythonOperator olarak çalıştırma

```python
from airflow.operators.python import PythonOperator

def audit_corpus(**ctx):
    from forgelm import audit_dataset
    report = audit_dataset(
        "/data/customer-support-v3.jsonl",
        output_dir=f"/audit/{ctx['run_id']}",
        emit_croissant=True,
        workers=4,
    )
    if report.near_duplicate_summary["pairs"] > 100:
        raise ValueError(f"too many duplicates: {report.near_duplicate_summary['pairs']}")
    return {"samples": report.total_samples, "duplicates": report.near_duplicate_summary["pairs"]}

audit_task = PythonOperator(
    task_id="audit_corpus",
    python_callable=audit_corpus,
    provide_context=True,
)
```

### CI'da bir release artefact bundle'ını doğrulama

```python
from forgelm import verify_annex_iv_artifact, verify_audit_log, verify_gguf

bundle_path = "outputs/v0.5.5/compliance/annex_iv_metadata.json"   # JSON manifest, ZIP değil
gguf_path = "outputs/v0.5.5/model.q4_K_M.gguf"
log_path = "outputs/v0.5.5/audit_log.jsonl"

bundle_check = verify_annex_iv_artifact(bundle_path)
gguf_check = verify_gguf(gguf_path)
log_check = verify_audit_log(log_path, require_hmac=True)

failures = [
    (name, check.reason)
    for name, check in (("bundle", bundle_check), ("gguf", gguf_check), ("audit", log_check))
    if not check.valid
]
if failures:
    raise SystemExit(f"release verification failed: {failures}")
```

### Trainer olmadan bir webhook bildirimi sürmek

```python
from forgelm import WebhookNotifier, load_config

config = load_config("configs/notification-only.yaml")
notifier = WebhookNotifier(config)
notifier.notify_start(run_name="manual-smoke-2026-05-06")
```

## Yaygın tuzaklar

Bunlar yinelenen "kütüphane lokalde çalıştı ama pipeline'ım bozuldu" destek bileti şekilleridir.

### `FORGELM_OPERATOR`'ı pinlemeyi unutmak

Audit zinciri her olayı `$FORGELM_OPERATOR`'a atfeder. CI'da, run başına namespaced bir tanımlayıcı ayarlayın:

```python
import os
os.environ["FORGELM_OPERATOR"] = (
    f"airflow:{os.environ['AIRFLOW_DAG_ID']}:{os.environ['AIRFLOW_RUN_ID']}"
)
```

Değişken ayarlı değilse ve `FORGELM_ALLOW_ANONYMOUS_OPERATOR` `1` **değilse**, run gürültülü şekilde abort olur. Bu tasarımdır — anonim olaylar bir ISO 27001 A.6.4 + A.6.5 audit bulgusudur.

### `logging.basicConfig`'i iki kez çağırmak

Kütüphane `logging.basicConfig()` **çağırmaz**. Uygulamanız çağırır. Bir CLI run'ı kütüphane kodu içine sarmalarsanız, CLI'nin `_setup_logging`'i çakışmayacaktır çünkü kütüphane root logger yapılandırmasına asla dokunmaz.

### `verify_audit_log` hatalarını exception olarak ele almak

`verify_audit_log`, zincir hatalarında `VerifyResult(valid=False, reason=...)` döndürür. Yalnızca `OSError` yayılır (okunamaz dosya). `result.valid` üzerinde branch'leyin, `try / except` ile sarmalamayın.

### `AuditLogger`'ı fork'lar arasında paylaşmak

`AuditLogger` POSIX `fcntl.flock` kullanır; Windows'ta süreçler-arası kilit **yoktur** (advisory flock yardımcısı no-op'tur). File handle'ı `os.fork()` çocukları arasında paylaşmak desteklenmez — her child aynı `output_dir`'i işaret eden kendi logger'ını kurmalıdır. POSIX'te tüm yazımlar lock'u edindiği için zincir tutarlı kalır; Windows'ta aynı `output_dir`'e karşı eşzamanlı süreç çalıştırmayın (her koşu için ayrı bir `output_dir` kullanın).

### Hot path'te yeniden import

Lazy-import resolver, resolve edilmiş attribute'ları `globals()`'e geri yazar (PEP 562 idiomatic). Sonraki erişimler resolver'ı atlar. Hot loop içinde `importlib.reload(forgelm)` çağırmayın — cache'i yıkar ve her ağır bağımlılığı yeniden import etmeye zorlar.

### CLI subprocess + kütüphane çağrılarını karıştırmak

Library API ve CLI aynı wheel'i, aynı audit log'u ve aynı lock semantik'ini paylaşır. Bunları bir pipeline'da karıştırabilirsiniz (kütüphane veriyi denetler, CLI eğitimi koşar, kütüphane zinciri doğrular), ancak zincirin coherent olması için **aynı output dizinini** işaret etmelidirler. İki output dizini = iki zincir, çapraz korelasyon yok.

### Okumadığınız bir `__api_version__`'a pinlemek

`__api_version__` yalnızca bir stable katman imzası değiştiğinde artar. CLI bug'larını düzelten patch sürümleri `__api_version__`'ı değiştirmez. Feature detection için `__api_version__`'a pinleyin; ortam tekrarlanabilirliği için `__version__`'a pinleyin.

### Bir experimental sembolü kontratınıza dahil etmek

`forgelm.WebhookNotifier`, `forgelm.SyntheticDataGenerator`, `forgelm.run_benchmark`, `forgelm.compute_simhash` açıkça Experimental'dir. Operatör runbook'unuz mevcut şekle bağımlıysa, `forgelm`'in mevcut minor sürümüne pinleyin ve her sürüm döngüsünde [referans dokümanı](../reference/library_api_reference-tr.md) okuyun.

## Ayrıca bakınız

- [`../reference/library_api_reference-tr.md`](../reference/library_api_reference-tr.md) — tam sembol referansı.
- [`../reference/audit_event_catalog-tr.md`](../reference/audit_event_catalog-tr.md) — `AuditLogger.log_event`'in kabul ettiği her olay.
- [`../reference/configuration-tr.md`](../reference/configuration-tr.md) — `ForgeConfig` alan-by-alan referans.
- [`cicd_pipeline-tr.md`](cicd_pipeline-tr.md) — bu rehberin CLI karşılığı.
- [`iso_soc2_deployer_guide-tr.md`](iso_soc2_deployer_guide-tr.md) — audit katı cookbook'u (kütüphane çağıranları aynı artefaktları görür).
- [`../design/library_api.md`](../design/library_api.md) — Faz 18 tasarım.
