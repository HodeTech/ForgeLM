# ForgeLM Library API Referansı

> **Hedef kitle:** ForgeLM'i pipeline orkestratörlerine (Airflow, Prefect, Dagster, Argo, Kubeflow) gömen veya Jupyter notebook'larından çağıran ML platform mühendisleri ve entegratörler. Bu sayfa `forgelm` paketinden re-export edilen her public sembolü sıralar, kararlılık katmanına göre sınıflandırır ve downstream tüketicilerin sabitleyebileceği imzaları listeler.
>
> **Mirror:** [library_api_reference.md](library_api_reference.md)
>
> **Eşlik eden rehber:** [`../guides/library_api-tr.md`](../guides/library_api-tr.md) — uçtan uca üç işlenmiş örnek.
>
> **Tasarım kaynağı:** [`../design/library_api.md`](../design/library_api.md) (Faz 18).

ForgeLM, `forgelm` console script'inin yanında bir Python kütüphane API'si de yayınlar. Kütüphane yüzeyi `forgelm/__init__.py` içinde `__all__` ile beyan edilir, PEP 562 `__getattr__` üzerinden lazy-resolve edilir ve downstream `mypy --strict` tüketicilerin gerçek imzaları görmesi için `TYPE_CHECKING` altında tip ipucu ile işaretlenir. `forgelm/py.typed` PEP 561 işaretçisi olarak wheel ile birlikte yayınlanır.

## Kararlılık katmanları

Üç katman, her public sembolün semver ağırlığını yönetir. Belirli bir katmana sabitleyen tüketici, bir `forgelm` yükseltmesinden ne bekleyeceğini bilir.

### Stable

Semver korumalıdır. Aşağıdaki imzalardan herhangi birinde kırıcı değişiklik major sürüm artışı gerektirir (`__api_version__` MAJOR.MINOR.PATCH — bkz. [Sürümleme ve deprecation politikası](#sürümleme-ve-deprecation-politikası)). Varsayılan değerli yeni opsiyonel parametreler kırıcı değildir; yeniden adlandırılmış zorunlu parametreler veya kaldırılmış return-shape alanları kırıcıdır.

Stable semboller burada belgelenir, %100 tip ipuçludur, `tests/test_library_api.py` altında en az bir entegrasyon testi vardır ve deprecation cadence'ini izler (sürüm `N`'de deprecate, `N+1`'de çalışır halde tut, `N+2`'de kaldır).

### Experimental

Best-effort. Şekil, major artış olmadan minor sürümde değişebilir. Çağrı yerindeki operatör notları yaşam döngüsünü işaret eder. Mevcut şekle bağımlıysanız belirli bir minor sürüme sabitleyin.

### Internal

`forgelm.__all__` içinde olmayan ve [Public semboller](#public-semboller) tablolarında listelenmeyen her şey. Reach-in'ler (`from forgelm._http import ...`, `forgelm.cli._run_audit_cmd`) dil seviyesinde çalışır ama **sıfır** kararlılık garantisi taşır. Pipeline'ınız bir internal sembole bağımlıysa promotion talebi için issue açın.

## Public semboller

İlgi alanına göre gruplanmış tablolar. Her hücre, `import forgelm` sonrası canlı `forgelm` paketi üzerinde gerçek bir attribute'dur.

### Sürümleme

| Sembol | Katman | Tip | Açıklama |
|---|---|---|---|
| `forgelm.__version__` | Stable | `str` | PEP 396/8 sürüm dizesi, `importlib.metadata`'dan türetilir (tek doğru kaynak = `pyproject.toml`). |
| `forgelm.__api_version__` | Stable | `str` | Üç segmentli semver kütüphane-API sürümü (`"MAJOR.MINOR.PATCH"`, örn. `"1.0.0"`). Bump kuralları `forgelm/_version.py`'de: kaldırılan/imzası değişen stable sembol için MAJOR, yeni stable sembol için MINOR, public yüzeye dokunmayan implementasyon değişikliği için PATCH. Downstream kodda feature detection için kullanın. |

### Konfigürasyon

| Sembol | Katman | İmza | Açıklama |
|---|---|---|---|
| `forgelm.load_config` | Stable | `(path: str) -> ForgeConfig` | Bir YAML dosyasını doğrulanmış `ForgeConfig`'e parse eder. Doğrulama hatasında `ConfigError` fırlatır. |
| `forgelm.ForgeConfig` | Stable | Pydantic `BaseModel` | Kök config şeması. In-memory parametrik sweep için doğrudan `ForgeConfig(**dict_payload)` ile kurun. |
| `forgelm.ConfigError` | Stable | `Exception` alt sınıfı | `load_config` ve `ForgeConfig(**dict)` tarafından doğrulama hatasında fırlatılır. CLI dispatcher'lar yakalayıp exit code 1 ile çıkar. |

### Eğitim

| Sembol | Katman | İmza | Açıklama |
|---|---|---|---|
| `forgelm.ForgeTrainer` | Stable | `ForgeTrainer(config: ForgeConfig)` | Birincil eğitim giriş noktası. TRL `SFTTrainer` / `DPOTrainer` / `KTOTrainer` / `ORPOTrainer` / `GRPOTrainer` seçimini sarmalar. |
| `forgelm.ForgeTrainer.train` | Stable | `train() -> TrainResult` | Yapılandırılmış fine-tune'u çalıştırır. `TrainResult.success` / `metrics` / `final_model_path` döndürür. Ağır bağımlılıklar (`torch`, `transformers`, `trl`) yalnızca bu metot çağrılırken yüklenir. |
| `forgelm.TrainResult` | Stable | `dataclass` | `ForgeTrainer.train()` sonucu. Kanonik alanlar (`forgelm/results.py`): `success: bool`, `metrics: Dict[str, float]`, `final_model_path: Optional[str]`, `reverted: bool`, `error: Optional[str]`, `benchmark_scores`, `benchmark_average`, `benchmark_passed`, `safety_passed`, `safety_score`, `safety_categories`, `safety_severity`, `safety_low_confidence`, `judge_score`, `judge_details`, `estimated_cost_usd`, `staging_path`, `resource_usage`. |

### Veri hazırlama

| Sembol | Katman | İmza | Açıklama |
|---|---|---|---|
| `forgelm.prepare_dataset` | Experimental | `prepare_dataset(config: ForgeConfig, tokenizer: PreTrainedTokenizer) -> Dict[str, Any]` | Yapılandırılmış veri kümesini yükler + format-detect eder + tokenize eder. Bir splits dict'i döndürür (örn. `{"train": ..., "validation": ...}`). `datasets` minor yüzeyi periyodik olarak değişir, dolayısıyla Experimental. |
| `forgelm.get_model_and_tokenizer` | Experimental | `get_model_and_tokenizer(config: ForgeConfig) -> tuple[PreTrainedModel, PreTrainedTokenizerBase]` | Yapılandırılmış PEFT / quantization kurulumu ile HF model + tokenizer yükler. |

### Veri denetimi + PII / secret / dedup

| Sembol | Katman | İmza | Açıklama |
|---|---|---|---|
| `forgelm.audit_dataset` | Stable | `audit_dataset(source: str, *, output_dir: str \| None = None, near_dup_threshold: int = 3, dedup_method: str = "simhash", minhash_jaccard: float = 0.85, minhash_num_perm: int = 128, enable_quality_filter: bool = False, enable_pii_ml: bool = False, pii_ml_language: str = "en", emit_croissant: bool = False, workers: int = 1) -> AuditReport` | Tek-çağrı veri-denetim giriş noktası. Notebook'lar ve CI gate'leri için uygundur. |
| `forgelm.AuditReport` | Stable | `dataclass` | `audit_dataset` sonucu. Kanonik alanlar (`forgelm/data_audit/_types.py`): `generated_at`, `source_path`, `source_input`, `total_samples`, `splits`, `cross_split_overlap` (anahtar üzerinden erişilen dict-şekilli row-düzeyi alan), `pii_summary` (`Dict[str, int]`), `pii_severity`, `near_duplicate_summary` (anahtar `pairs_per_split`, `pairs` değil), `secrets_summary` (`Dict[str, int]`), `quality_summary`, `croissant`, `notes`. |
| `forgelm.detect_pii` | Stable | `detect_pii(text: str) -> Dict[str, int]` | Bağımsız PII detektörü. Kategori → sayım sözlüğü döner (`{"email": 3, "phone": 1, ...}`). `language` kwarg'ı yok; regex set dilden bağımsız. |
| `forgelm.mask_pii` | Stable | `mask_pii(text: str, replacement: str = "[REDACTED]", *, return_counts: bool = False) -> str \| Tuple[str, Dict[str, int]]` | Tespit edilen PII span'larını yerinde maskele. `return_counts=True` ile `(masked_text, counts_dict)` döner. |
| `forgelm.detect_secrets` | Stable | `detect_secrets(text: str) -> Dict[str, int]` | Bağımsız credential / API-key detektörü (AWS / GitHub / Slack / OpenAI / Google / JWT / private-key / Azure storage). Aile → sayım sözlüğü döner. |
| `forgelm.mask_secrets` | Stable | `mask_secrets(text: str) -> str` | Tespit edilen secret'ları yerinde maskele. |
| `forgelm.compute_simhash` | Experimental | `compute_simhash(text: str) -> int` | 64-bit SimHash imzası. Yüzey, gelecekte birleşik bir `compute_signature(method=...)` halinde toplanabilir. |
| `forgelm.compute_minhash` | Experimental | `compute_minhash(text: str, *, num_perm: int = 128) -> Optional[Any]` | MinHash LSH imza nesnesi (opsiyonel `datasketch` kütüphanesi yoksa `None` döner). `--dedup-method=minhash` audit yolunda dahili olarak kullanılır; dönen nesne kütüphane-iç bir tiptir — operatörler bunu doğrudan değil `audit_dataset(...)` üzerinden tüketir. `compute_simhash` ile aynı Experimental katman; ikisi de birleşik bir `compute_signature(method=...)` API'sinde toplanabilir. |

### Compliance + audit log

| Sembol | Katman | İmza | Açıklama |
|---|---|---|---|
| `forgelm.AuditLogger` | Stable | `AuditLogger(output_dir: str, run_id: str \| None = None)` | Append-only Article 12 audit logger. POSIX `fcntl.flock` kullanır; Windows'ta süreçler-arası kilit **yoktur** (advisory flock yardımcısı no-op'tur) — Windows'ta eşzamanlı süreçler arasında bir `output_dir`'i paylaşmayın. Her fork edilmiş alt süreç kendi instance'ını kurmalıdır. |
| `forgelm.AuditLogger.log_event` | Stable | `log_event(event: str, **fields) -> None` | Yapılandırılmış bir olay ekle. Olay kelime dağarcığı [`audit_event_catalog-tr.md`](audit_event_catalog-tr.md)'da belgelenir. |
| `forgelm.verify_audit_log` | Stable | `verify_audit_log(path: str, *, hmac_secret: str \| None = None, require_hmac: bool = False) -> VerifyResult` | SHA-256 hash zincirini yürür. Zincir hatalarında `VerifyResult(valid=False, reason=...)` döndürür (exception değil); yalnızca okunamaz dosyalar için `OSError` fırlatır. |
| `forgelm.VerifyResult` | Stable | `dataclass` | Kanonik alanlar (`forgelm/compliance.py:VerifyResult`): `valid: bool`, `entries_count: int`, `first_invalid_index: Optional[int]`, `reason: Optional[str]`. |

### Doğrulama toolbelt'i (Faz 36)

| Sembol | Katman | İmza | Açıklama |
|---|---|---|---|
| `forgelm.verify_annex_iv_artifact` | Stable | `verify_annex_iv_artifact(path: str) -> VerifyAnnexIVResult` | Bir Annex IV technical-documentation bundle'ını (manifest + model card + audit log + governance report) doğrular. |
| `forgelm.VerifyAnnexIVResult` | Stable | `dataclass-benzeri` | Kanonik attribute'lar: `valid: bool`, `reason: Optional[str]`, `missing_fields: List[str]`, `manifest_hash_actual: Optional[str]`, `manifest_hash_expected: Optional[str]`. (Not: `verify_annex_iv_artifact(path)` JSON manifest path'i kabul eder, ZIP değil — fonksiyon `path` üzerinde `json.load` çağırır.) |
| `forgelm.verify_gguf` | Stable | `verify_gguf(path: str) -> VerifyGgufResult` | Bir GGUF dışa aktarmasını (header + tensor catalogue + tokenizer block) doğrular. |
| `forgelm.VerifyGgufResult` | Stable | `dataclass-benzeri` | Kanonik attribute'lar: `valid: bool`, `reason: Optional[str]`, `checks: Dict[str, Any]` (her şey diğer — header / tensor catalogue / tokenizer block — `checks` içinde yaşar). |
| `forgelm.verify_integrity` | Stable | `verify_integrity(model_dir: str) -> VerifyIntegrityResult` | Bir model dizinini `model_integrity.json` SHA-256 manifestine karşı doğrular (Madde 15): değiştirilen / silinen / eklenen yapıtları raporlar. |
| `forgelm.VerifyIntegrityResult` | Stable | `dataclass-benzeri` | Kanonik attribute'lar: `valid: bool`, `reason: Optional[str]`, `changed: List[str]`, `removed: List[str]`, `added: List[str]`, `verified_count: int`. |

### Benchmark + sentetik veri

| Sembol | Katman | İmza | Açıklama |
|---|---|---|---|
| `forgelm.run_benchmark` | Experimental | `run_benchmark(model, tokenizer, tasks: List[str], num_fewshot: Optional[int] = None, batch_size: str = "auto", limit: Optional[int] = None, output_dir: Optional[str] = None, min_score: Optional[float] = None) -> BenchmarkResult` | `lm-eval-harness`'ı sarmalar. `[eval]` extra'sını gerektirir. |
| `forgelm.BenchmarkResult` | Experimental | `dataclass-benzeri` | Kanonik alanlar: `scores: Dict[str, float]`, `average_score: float`, `passed: bool`, `failure_reason: Optional[str]`, `raw_results: Dict[str, Any]`. |
| `forgelm.SyntheticDataGenerator` | Experimental | `SyntheticDataGenerator(config: ForgeConfig)` | Teacher-distillation jeneratörü. `teacher_backend in {"api", "local", "file"}` switch'i muhtemelen yeni modlar büyütecek. |

### Yardımcı

| Sembol | Katman | İmza | Açıklama |
|---|---|---|---|
| `forgelm.WebhookNotifier` | Experimental | `WebhookNotifier(config: ForgeConfig)` | Slack / Teams / generic-HTTP yaşam-döngüsü bildirimleri. Constructor şeması gelecek bir sürümde ISO/SOC 2 alanları büyütebilir. |
| `forgelm.setup_authentication` | Experimental | `setup_authentication(token: Optional[str] = None) -> None` | `huggingface_hub.login` etrafında sarmalayıcı. `token` `None` olduğunda `HUGGINGFACE_TOKEN` env var'ını okur. |
| `forgelm.manage_checkpoints` | Experimental | `manage_checkpoints(checkpoint_dir: str, action: str = "keep") -> None` | Bir output dizinine karşı checkpoint-retention davranışını uygular. `action` retain/prune semantiklerini kontrol eder. |

## Idiomatic kullanım örnekleri

En yaygın kütüphane-modu giriş noktalarını kapsayan işlenmiş snippet'ler. Aşağıdaki tüm import'lar doğrudan `from forgelm import ...` ile resolve olur.

### 1. Notebook'tan bir corpus denetlemek

```python
from forgelm import audit_dataset

report = audit_dataset(
    "data/customer_support.jsonl",
    output_dir="audit_out",
    enable_pii_ml=True,
    pii_ml_language="en",
    emit_croissant=True,
)

print(f"samples: {report.total_samples}")
print(f"duplicates: {report.duplicate_count}")
print(f"pii findings: {len(report.pii_findings)}")
# cross_split_overlap dict[str, Any] - anahtar üzerinden erişin
print(f"split overlap pairs: {report.cross_split_overlap.get('pairs', {})}")
```

### 2. CI'da bir audit log doğrulamak

```python
from forgelm import verify_audit_log

result = verify_audit_log(
    "outputs/run-001/audit_log.jsonl",
    hmac_secret=None,
    require_hmac=True,
)

if not result.valid:
    raise SystemExit(f"audit chain broken: {result.reason}")

print(f"verified {result.entries_checked} entries; head={result.chain_head}")
```

### 3. Uçtan uca eğitim (saf Python, YAML yok)

```python
from forgelm import ForgeConfig, ForgeTrainer

config = ForgeConfig(
    model={"name_or_path": "TinyLlama/TinyLlama-1.1B-Chat-v1.0"},
    lora={"r": 8, "alpha": 16, "target_modules": ["q_proj", "v_proj"]},
    data={"dataset_name_or_path": "data/train.jsonl"},
    training={
        "trainer_type": "sft",
        "num_train_epochs": 1,
        "per_device_train_batch_size": 1,
        "output_dir": "./checkpoints/quick",
    },
)

trainer = ForgeTrainer(config)
result = trainer.train()

print(f"success={result.success}  output={result.output_dir}")
if not result.success and result.revert_reason:
    print(f"reverted: {result.revert_reason}")
```

Yukarıdaki anahtarlar tek **gerekli** olanlardır; geri kalan her şey `forgelm/config.py` varsayılanlarına düşer. `model.name_or_path`, `lora:` bloğu, `data.dataset_name_or_path` ve `training.{trainer_type, output_dir}` Pydantic şeması tarafından zorunlu kılınır; `num_epochs` / `batch_size` kanonik adlar değildir ve `ValidationError` atar.

### 4. Kendi pipeline'ınızdan Article 12 audit olayları yaymak

```python
import os
from forgelm import AuditLogger

os.environ.setdefault("FORGELM_OPERATOR", "airflow:dag-1234:run-5678")

logger = AuditLogger(output_dir="outputs/dag-1234")
logger.log_event(
    "training.started",
    trainer_type="sft",
    model="meta-llama/Llama-3.1-8B-Instruct",
    dataset="acme/customer-support-v3",
)
# ... pipeline'ınız çalışır ...
logger.log_event(
    "pipeline.completed",
    exit_code=0,
    duration_seconds=4218.7,
    success=True,
    metrics_summary={"eval_loss": 0.43, "rouge_l": 0.61},
)
```

### 5. Serbest-form girdi üzerinde PII / secret tespiti

```python
from forgelm import detect_pii, detect_secrets, mask_pii, mask_secrets

text = "Contact alice@example.com or use AKIAIOSFODNN7EXAMPLE for the call."

pii = detect_pii(text)
secrets = detect_secrets(text)

print(f"pii: {[(f.kind, f.span) for f in pii]}")
print(f"secrets: {[(f.kind, f.span) for f in secrets]}")

masked = mask_secrets(mask_pii(text))
print(masked)
```

## Lazy-import disiplini

Paket facade'ını import etmek **kontrat gereği ucuzdur**: `import forgelm`, `torch`, `transformers`, `trl`, `datasets`, `peft` veya başka herhangi bir ağır ML bağımlılığını **yüklemez**. Yalnızca `importlib.metadata` ve küçük bir module-level state dict'e dokunulur.

Ağır attribute'lar PEP 562 `__getattr__` üzerinden ilk erişimde resolve olur:

```python
import sys
import forgelm

assert "torch" not in sys.modules           # kontrat — CI'da pinned

_ = forgelm.ForgeTrainer                    # forgelm.trainer'ı import eder ama
assert "torch" not in sys.modules           # forgelm.trainer da torch'u erteler

trainer = forgelm.ForgeTrainer(config)      # constructor hâlâ ucuz
result = trainer.train()                    # ŞİMDİ torch yüklenir
```

Bu invariant, hafif CI runner'larının, `forgelm doctor`'un ve `python -m forgelm.cli --help`'in anında yanıt vermesi gerektiği için var. `tests/test_library_api.py::test_lazy_import_no_torch` regression olarak sabitler.

## Eşzamanlılık

| Sembol | Çoklu thread? | Construction sonrası fork-safe? |
|---|---|---|
| `ForgeTrainer.train()` | Hayır — TRL GPU state tutar | Hayır |
| `audit_dataset()` | Evet — her çağrı self-contained | Evet |
| `AuditLogger.log_event()` | POSIX'te evet — `flock(LOCK_EX)`; Windows'ta süreçler-arası kilit yoktur (no-op), bu yüzden bir `output_dir`'i süreçler arasında paylaşmayın | Her child için yeni logger kurun; handle'ları fork'lar arasında paylaşmak desteklenmez |
| `verify_audit_log()` | Evet — read-only | Evet |
| `WebhookNotifier.notify_*()` | Evet — her çağrı kendi `requests` session'ını açar | Evet |

## Kütüphane sınırında hata yönetimi

CLI dispatcher'lar exception'ları public exit code'lara (0/1/2/3/4) eşler. **Kütüphane çağıranları exit code görmez** — typed exception'lar yayılır.

| Sembol | Yayılan hata türü |
|---|---|
| `ForgeTrainer.train()` | `ConfigError` (validation), `RuntimeError` (CUDA / training-loop), `OSError` (I/O) |
| `audit_dataset()` | `ValueError` (geçersiz arg), `OSError` (I/O), `OptionalDependencyError` (eksik extra) |
| `verify_audit_log()` | Zincir hataları için `VerifyResult(valid=False, reason=...)` döndürür; yalnızca okunamaz dosyalar için `OSError` fırlatır |
| `AuditLogger.log_event()` | Yazma hatasında `OSError` (caller retry vs abort kararını verir) |

Kütüphane kodu asla `sys.exit` çağırmaz. Her exit-code eşlemesi CLI dispatcher'larında yaşar.

## Kütüphane modunda logging

`import forgelm`, `logging.basicConfig()` **çağırmaz**. Tüketici logger'ını açıkça yapılandırın:

```python
import logging
logging.getLogger("forgelm").setLevel(logging.WARNING)  # kütüphanelerde varsayılan: sessiz
```

CLI kendi kurulumunu `forgelm.cli._setup_logging` içinde yapar; kütüphane kararı caller'a bırakır (PEP 8 / `logging` HOWTO library hygiene).

## Sürümleme ve deprecation politikası

İki bağımsız sürüm dizesi:

| Değişken | Ne zaman artar... | Kim okur... |
|---|---|---|
| `forgelm.__version__` | Her sürüm (CLI fix, kütüphane fix, doc-only release) | Downstream pinning, audit manifest stamp |
| `forgelm.__api_version__` | Bir stable katman imzası değiştiğinde | Downstream feature detection |

`__api_version__` üç segmentli bir semver string'idir (`"1.0.0"` v0.5.5'te — formel Phase 19 kontratının ilk yayını). Bump kuralları `forgelm/_version.py`'de: kaldırılan / imzası değişen stable sembol için MAJOR, yeni stable sembol için MINOR, public yüzeye dokunmayan implementasyon değişikliği için PATCH.

**Deprecation cadence** (`docs/standards/release.md` başına):

1. Sürüm `N`'de eski sembolü `DeprecationWarning` ile işaretleyin. Uyarı, yerine geçen sembol adını ve planlanan-kaldırma sürümünü içermelidir.
2. Sürüm `N+1`'de çalışır halde tutun.
3. Sürüm `N+2`'de kaldırın.

Cadence'i izlemeden stable bir imzaya kırıcı değişiklik yapmak release-process bug'ıdır.

## Ayrıca bakınız

- [`../guides/library_api-tr.md`](../guides/library_api-tr.md) — uçtan uca üç işlenmiş örnek.
- [`audit_event_catalog-tr.md`](audit_event_catalog-tr.md) — `AuditLogger.log_event`'in kabul ettiği tam olay kelime dağarcığı.
- [`configuration-tr.md`](configuration-tr.md) — `ForgeConfig` alan referansı.
- [`../design/library_api.md`](../design/library_api.md) — Faz 18 tasarım + 16 satırlık Faz 19 görev planı.
- [`../standards/release.md`](../standards/release.md) — deprecation cadence ve sürüm süreci.
