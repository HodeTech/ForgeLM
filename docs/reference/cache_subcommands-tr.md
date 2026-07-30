# `forgelm cache-models` & `forgelm cache-tasks` Referansı

> **Mirror:** [cache_subcommands.md](cache_subcommands.md)
>
> Air-gap ön-getirme komut çifti. Bağlı bir makinede yerel HuggingFace Hub cache'ini (`cache-models`) ve lm-evaluation-harness datasets cache'ini (`cache-tasks`) doldurun; oluşan ağaçları offline host'a aktarın; orada [`forgelm doctor --offline`](doctor_subcommand-tr.md) doğrular ve trainer `local_files_only=True` ile çalışır.

## Synopsis

```shell
forgelm cache-models --model HUB_ID [--model HUB_ID ...] [--safety HUB_ID]
                     [--output DIR] [--audit-dir DIR]
                     [--output-format {text,json}] [-q] [--log-level LEVEL]

forgelm cache-tasks  --tasks CSV
                     [--output DIR] [--audit-dir DIR]
                     [--output-format {text,json}] [-q] [--log-level LEVEL]
```

Uygulama: [`forgelm/cli/subcommands/_cache.py`](../../forgelm/cli/subcommands/_cache.py).

## `cache-models` flags

| Flag | Tip | Varsayılan | Açıklama |
|---|---|---|---|
| `--model HUB_ID` | string (tekrarlanabilir) | — | HuggingFace Hub ID'si (örn. `meta-llama/Llama-3.2-3B`) veya yerel yol. Birden fazla model için tekrarlayın. |
| `--safety HUB_ID` | string | — | Önceden cache'lenecek opsiyonel safety classifier (örn. `meta-llama/Llama-Guard-3-8B`). Dahili olarak model listesine eklenir. |
| `--output DIR` | path | env-resolved | Cache dizini override. **Çözüm sırası:** `--output` > `HF_HUB_CACHE` > `HF_HOME/hub` > `~/.cache/huggingface/hub`. Birbirinden farklı `--output`, uyarı üretir; çünkü [`forgelm doctor --offline`](doctor_subcommand-tr.md) ve trainer her ikisi de env-var zincirini okur, `--output`'u **değil**. |
| `--audit-dir DIR` | path | `--output` | `cache.populate_models_*` event'lerinin yazılacağı dizin. Operatör artefaktları audit log'dan farklı bir dizinde stage ediyorsa kullanın. |
| `--output-format` | `text` \| `json` | `text` | Render. |
| `-q`, `--quiet` | bool | `false` | INFO loglarını bastırır. |
| `--log-level` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | `INFO` | Log seviyesi. |

`--model` veya `--safety`'den en az biri zorunludur; ikisi birlikte verilebilir, böylece base model + Llama Guard tek invocation'da stage edilir.

## `cache-tasks` flags

| Flag | Tip | Varsayılan | Açıklama |
|---|---|---|---|
| `--tasks CSV` | string (zorunlu) | — | Virgülle ayrılmış lm-eval task adları (örn. `hellaswag,arc_easy,truthfulqa,mmlu`). Virgül etrafındaki boşluk tolere edilir. |
| `--output DIR` | path | env-resolved | Cache dizini override. **Çözüm sırası:** `--output` > `HF_DATASETS_CACHE` > `HF_HOME/datasets` > `~/.cache/huggingface/datasets`. Runtime, çözülen yolu `try/finally` içinde `HF_DATASETS_CACHE` olarak stamp'ler; uzun ömürlü süreçte / sonraki testte stamp sızdırmaz. |
| `--audit-dir DIR` | path | `--output` | `cache.populate_tasks_*` event'lerinin yazılacağı dizin. |
| `--output-format` | `text` \| `json` | `text` | Render. |
| `-q`, `--quiet` | bool | `false` | INFO loglarını bastırır. |
| `--log-level` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | `INFO` | Log seviyesi. |

`cache-tasks`, `[eval]` extra'sını gerektirir (`pip install 'forgelm[eval]'`); eksik import `EXIT_CONFIG_ERROR` üretir (operatör eylem alabilir), `EXIT_TRAINING_ERROR` değil.

## Cache-ağacı yerleşimi

HF cache, amacına göre bölümlüdür; `HF_HUB_CACHE` set etmek dataset indirmelerini yönlendirmez ve tersi de doğrudur.

| Cache | Çözen | Dolduran | Ne yaşar |
|---|---|---|---|
| **Hub cache** | `HF_HUB_CACHE` > `HF_HOME/hub` > `~/.cache/huggingface/hub` | `cache-models` | Model snapshot'ları, tokenizer'lar, config'ler (`huggingface_hub.snapshot_download` blob store). |
| **Datasets cache** | `HF_DATASETS_CACHE` > `HF_HOME/datasets` > `~/.cache/huggingface/datasets` | `cache-tasks` | Parquet shard'ları, işlenmiş Arrow split'leri (`datasets` kütüphanesinin kendi cache'i). |

> `FORGELM_CACHE_DIR` bir ForgeLM env değişkeni **değildir**. Yukarıdaki kanonik HuggingFace değişkenlerini kullanın.

## Çıkış kodları

| Kod | Anlamı |
|---|---|
| `0` | İstenen her model / task başarıyla cache'lendi. |
| `1` | Config hatası — boş `--model`+`--safety`, bozuk model adı, boş `--tasks`, bilinmeyen lm-eval task adı, eksik `[eval]` extra'sı. |
| `2` | Runtime hatası — HF Hub transport hatası, disk dolu, `huggingface_hub` import bozuk, bir veya daha fazla task dataset'i hazırlanamadı. |

`cache-models`, kısmi-batch hatasını raporlar: audit zinciri `cache.populate_models_failed` event'ini `models_completed=[<şimdiye-kadar-yapılan-liste>]` payload'ı ile kaydeder; böylece operatör çökmeden önce neyin tamamlandığını bilir ve hatalı modeli atlayarak yeniden çalıştırabilir.

`cache-tasks` da aynı şekilde raporlar. `cached: false` ile biten herhangi bir task — indirme hatası ya da lm-eval'in indirilebilir dataset sunmadığı bir task — komutun tamamını `success: false` ile exit `2`'ye düşürür ve hata zarfı per-task satırları `tasks` altında korur. **0.11'de değişti:** 0.10'a kadar bu hatalar `tasks[].error`'a yazılıyor, komut yine 0 ile çıkıyor ve `cache.populate_tasks_completed` logluyordu; `jq -e '.success'` ile gate eden bir CI job'ı bu yüzden eksik bir cache'i paketleyip air-gap'li host'a gönderiyordu.

## Üretilen audit event'leri

| Event | Ne zaman üretilir | Payload (envelope'a ek) | Madde |
|---|---|---|---|
| `cache.populate_models_requested` | `cache-models` invocation başlar. | `models`, `cache_dir`, `safety_classifier` | 12 |
| `cache.populate_models_completed` | Her model başarıyla indirildi. | Tüm `requested` alanları + `total_size_bytes`, `count` | 12 |
| `cache.populate_models_failed` | Bir veya daha fazla model indirme hatası (transport, disk-full, HF auth). | Tüm `requested` alanları + `models_completed`, `error_class`, `error_message` | 12 |
| `cache.populate_tasks_requested` | `cache-tasks` invocation başlar. | `tasks`, `cache_dir` | 12 |
| `cache.populate_tasks_completed` | Her lm-eval task dataset'i başarıyla hazırlandı. | Tüm `requested` alanları + `count` | 12 |
| `cache.populate_tasks_failed` | Bilinmeyen task adı VEYA hiçbir task hazırlanamadı. | Tüm `requested` alanları + `tasks_completed`, `error_class`, `error_message` (enumeration hatası) veya `tasks_cached`, `tasks_failed`, `tasks_unavailable` (hiçbiri hazırlanamadı) | 12 |
| `cache.populate_tasks_partial` | Bazı task'lar hazırlandı, bazıları hazırlanmadı. | Tüm `requested` alanları + `tasks_cached`, `tasks_failed`, `tasks_unavailable` | 12 |

Audit-logger inşası **best-effort**'tur: bağlı stage makinesinde `FORGELM_OPERATOR` set edilmemiş bir operatör debug-seviyesinde bir not görür ve çalıştırma audit zinciri olmadan devam eder. Cache subcommand'larının değeri disk üzerindeki artefaktlardadır, audit zincirinde değil. Ayna girişler: [`audit_event_catalog-tr.md`](audit_event_catalog-tr.md) §Air-gap ön-cache.

## Örnekler

### Offline eğitim için base model + Llama Guard cache'leme

```shell
$ forgelm cache-models \
    --model "Qwen/Qwen2.5-7B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B"
Cached 2 model(s); 28415.32 MiB total under /home/me/.cache/huggingface/hub.
  - Qwen/Qwen2.5-7B-Instruct: 14207.66 MiB (412.8s)
  - meta-llama/Llama-Guard-3-8B: 14207.66 MiB (398.2s)
```

### Tek invocation'da birden fazla model cache'leme

```shell
$ forgelm cache-models \
    --model "meta-llama/Llama-3.2-3B" \
    --model "meta-llama/Llama-3.2-3B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B" \
    --output ./airgap-bundle/hub
```

`HF_HUB_CACHE=$PWD/airgap-bundle/hub` set edilmemişse `--output` farklılık uyarısı çıkar. Çözüm: ya `--output`'u kaldırın ya da env var'ı bundle yoluna sabitleyin.

### lm-eval task cache'leme

```shell
$ forgelm cache-tasks --tasks "hellaswag,arc_easy,truthfulqa,mmlu"
Cached 4 of 4 task(s) under /home/me/.cache/huggingface/datasets.
  - hellaswag: ok
  - arc_easy: ok
  - truthfulqa: ok
  - mmlu: ok
```

### CI bundle staging (JSON envelope)

```shell
$ forgelm cache-models \
    --model "Qwen/Qwen2.5-7B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B" \
    --output-format json -q \
  | jq '.success'
true
```

```json
{
  "success": true,
  "models": [
    {"name": "Qwen/Qwen2.5-7B-Instruct", "cached_path": "...", "size_bytes": 14897516032, "size_mb": 14207.66, "duration_s": 412.8},
    {"name": "meta-llama/Llama-Guard-3-8B", "cached_path": "...", "size_bytes": 14897516032, "size_mb": 14207.66, "duration_s": 398.2}
  ],
  "total_size_mb": 28415.32,
  "cache_dir": "/home/me/.cache/huggingface/hub"
}
```

## Ayrıca

- [Air-gap deployment rehberi](../guides/air_gap_deployment-tr.md) — bağlı-makine → bundle → air-gap-host iş akışı için tam operatör pişirme kitabı.
- [`doctor_subcommand-tr.md`](doctor_subcommand-tr.md) — `forgelm doctor --offline` doldurulmuş cache'i doğrular.
- [`audit_event_catalog-tr.md`](audit_event_catalog-tr.md) — tam audit-event kataloğu.
- [Air-gap kullanıcı kılavuzu](../usermanuals/tr/operations/air-gap.md) — operatör özet sayfası.
- [JSON çıktı şeması](../usermanuals/tr/reference/json-output.md) — kilitli envelope sözleşmesi.
