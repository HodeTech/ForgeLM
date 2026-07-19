# Air-Gap Deployment Pişirme Kitabı

> **Hedef kitle:** ForgeLM'i kısıtlı-egress host'a deploy eden operatörler — savunma, sağlık, bazı finans sektörleri, sınıflandırılmış-ağ müşteri ortamları. Eğitim sırasında outbound HTTPS'i reddeden güvenlik politikası olan herkes.
>
> Bu derinlemesine bir deployer pişirme kitabıdır. [GDPR erasure rehberi](gdpr_erasure-tr.md) ve [ISO/SOC 2 deployer rehberi](iso_soc2_deployer_guide-tr.md) derinliğini yansıtır — gösterilen her komut [`forgelm/cli/subcommands/_cache.py`](../../forgelm/cli/subcommands/_cache.py) ve [`forgelm/cli/subcommands/_doctor.py`](../../forgelm/cli/subcommands/_doctor.py) implementasyonuna karşı doğrulanmıştır.

## Bu rehber neyi çözer

Air-gap iş akışının ele aldığı üç operatör ağrısı:

1. **HuggingFace Hub egress'i bloklu.** Trainer, model weight'leri, tokenizer'lar, config'ler ve (safety eval etkin olduğunda) Llama Guard ister. Bunların hiçbiri air-gap host'unda eğitim sırasında indirilemez.
2. **lm-evaluation-harness dataset'leri bloklu.** `lm-eval`, dataset indirmelerini ilk invocation'a kadar erteler; air-gap host'unda bu, config hatası değil runtime çökmesi olur.
3. **Bundle'ın transferi sağlam atlattığını doğrulamanın yolu yok.** Operatörler 30 GiB cache'i USB / scp / çıkarılabilir medya ile kopyalar, sonra ilk eğitim çalıştırmasının 90. dakikasında bir shard'ın eksik olduğunu keşfeder.

`forgelm cache-models`, `forgelm cache-tasks` ve `forgelm doctor --offline`, bu iş akışını uçtan uca denetlenebilir kılan üç parçadır.

## İki-host iş akışı genel bakış

```text
┌─────────────────────────────────┐       ┌────────────────────────────────┐
│ Bağlı staging host              │       │ Air-gap target host            │
│                                 │       │                                │
│ 1. forgelm cache-models ...     │       │ 4. forgelm doctor --offline    │
│ 2. forgelm cache-tasks ...      │       │ 5. forgelm --offline           │
│                                 │       │      --config configs/run.yaml │
│ 3. tar / rsync the bundle ─────────────────►                             │
└─────────────────────────────────┘       └────────────────────────────────┘
```

Her adım yapılandırılmış audit event'leri üretir (`cache.populate_*`); bundle artefakttır, audit zinciri kanıttır.

## Adım adım

### Adım 1 — Bağlı host'ta: modelleri cache'leyin

```shell
$ export HF_HUB_CACHE="$PWD/airgap-bundle/hub"
$ export FORGELM_OPERATOR="staging:bundle-202605"

$ forgelm cache-models \
    --model "Qwen/Qwen2.5-7B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B"
Cached 2 model(s); 28415.32 MiB total under .../airgap-bundle/hub.
  - Qwen/Qwen2.5-7B-Instruct: 14207.66 MiB (412.8s)
  - meta-llama/Llama-Guard-3-8B: 14207.66 MiB (398.2s)
```

Ne nerede yaşar:

| Bundle alt-ağacı | Dolduran | Eğitim sırasında okur |
|---|---|---|
| `airgap-bundle/hub/` | `cache-models` (HF Hub snapshot'ları) | `huggingface_hub` üzerinden `transformers.AutoModel.from_pretrained` |
| `airgap-bundle/datasets/` | `cache-tasks` (parquet shard'ları) | `datasets.load_dataset` |

İkisi **ayrı cache**'tir, **ayrı env değişkenleri** kullanır (`HF_HUB_CACHE` vs `HF_DATASETS_CACHE`). `HF_HUB_CACHE` set etmek dataset indirmelerini yönlendirmez ve tersi de doğrudur. `HF_HOME` set etmek `hub/` ve `datasets/` alt-dizinleri üzerinden ikisini birden yönlendirir.

> **`--output` notu.** `HF_HUB_CACHE` env değişkeni set etmek yerine `--output ./airgap-bundle/hub` geçirebilirsiniz. ForgeLM, bunun `forgelm doctor --offline` ve trainer'ın okuyacağı env-resolved konumdan farklı olduğu uyarısını yapacaktır — transferden önce env değişkenini eşleşecek şekilde set edin (uyarı metni tam `HF_HUB_CACHE=...` veya `HF_DATASETS_CACHE=...` satırını verir). Env-değişkeni-öncelikli yaklaşım önerilir; çünkü staging'de kullanılan *aynı* yapılandırma air-gap host'unda da kullanılır.

### Adım 2 — Bağlı host'ta: lm-eval task'ları cache'leyin

```shell
$ export HF_DATASETS_CACHE="$PWD/airgap-bundle/datasets"

$ forgelm cache-tasks --tasks "hellaswag,arc_easy,truthfulqa,mmlu"
Cached 4 of 4 task(s) under .../airgap-bundle/datasets.
  - hellaswag: ok
  - arc_easy: ok
  - truthfulqa: ok
  - mmlu: ok
```

`[eval]` extra'sını gerektirir (`pip install 'forgelm[eval]'`). Eksik import açık kurulum ipucuyla `EXIT_CONFIG_ERROR` (operatör eylem alabilir) olarak raporlanır.

`cache-tasks`, çözülen cache dizinini bir `try/finally` içinde `HF_DATASETS_CACHE` olarak stamp'ler; böylece alttaki `datasets` kütüphanesi parquet shard'larını doğru alt-ağaca yazar — bu olmadan, runtime'ın `HF_DATASETS_CACHE`'i ile operatörün `--output`'u arasındaki bir uyumsuzluk shard'ları sessizce `~/.cache/huggingface/datasets`'e düşürür.

### Adım 3 — Bundle'layın ve aktarın

```shell
$ ls -la airgap-bundle/
hub/         # cache-models tarafından dolduruldu
datasets/    # cache-tasks tarafından dolduruldu

$ tar --create --gzip --file airgap-bundle.tar.gz airgap-bundle/
$ sha256sum airgap-bundle.tar.gz > airgap-bundle.tar.gz.sha256
```

`airgap-bundle.tar.gz`'i ve `airgap-bundle.tar.gz.sha256`'yı birlikte aktarın. Hash, staging host ile target host arasında tamper-evidence'ınızdır. Target'ta doğrulayın:

```shell
$ sha256sum -c airgap-bundle.tar.gz.sha256
airgap-bundle.tar.gz: OK
$ tar --extract --gzip --file airgap-bundle.tar.gz
```

### Adım 4 — Air-gap host'ta: `forgelm doctor --offline` ile doğrulayın

```shell
$ export HF_HUB_CACHE="$PWD/airgap-bundle/hub"
$ export HF_DATASETS_CACHE="$PWD/airgap-bundle/datasets"
$ export HF_HUB_OFFLINE=1
$ export TRANSFORMERS_OFFLINE=1
$ export HF_DATASETS_OFFLINE=1

$ forgelm doctor --offline
forgelm doctor - environment check

  [+ pass] python.version          Python 3.11.4 (CPython).
  [+ pass] torch.cuda              torch 2.4.0 with CUDA 12.4.
  [+ pass] gpu.inventory           1 GPU(s) - GPU0: NVIDIA A100 (80.0 GiB).
  [+ pass] extras.qlora            Installed (module bitsandbytes, ...).
  [+ pass] extras.eval             Installed (module lm_eval, ...).
  [+ pass] hf_hub.offline_cache    HF cache at .../airgap-bundle/hub: 27.7 GiB across 142 file(s). HF_HUB_OFFLINE=1.
  [+ pass] disk.workspace          Workspace /opt/airgap - 412.0 GiB free of 500.0 GiB.
  [+ pass] operator.identity       FORGELM_OPERATOR set to 'airgap-prod'; audit events will carry this identity.

Summary: 8 pass, 0 warn, 0 fail.
```

Anahtar kontroller:

- `hf_hub.offline_cache` probe'u, `--offline` geçirildiğinde VEYA `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`/`HF_DATASETS_OFFLINE` ortamda set edildiğinde `hf_hub.reachable`'in yerine geçer. ForgeLM üçüne de saygı duyar.
- Cache taraması sınırlandırılmıştır: derinlik 4, dosya sayısı 5000. Kesilmiş tarama, `extras` içinde `walk_truncated=true` raporlar; böylece kısmi-tarama sonuçları açıkça belirtilir (sessiz değil).
- Okunamaz cache root (chmod kırık, mount kopuk) `warn` yerine `fail` raporlar; böylece yanlış yapılandırılmış target host eğitim başlamadan yakalanır.

Doctor `fail` raporlarsa, eğitimi başlatmadan önce düzeltin — air-gap operatörünün duvar saati üzerinde debug yapmak için fazla pahalı.

### Adım 5 — `--offline` ile eğitin

```shell
$ forgelm --config configs/run.yaml --offline
```

`--offline` bayrağı uçtan uca `local_files_only=True` zorlar. Önceden cache'lenmemiş herhangi bir model referansı, eksik snapshot'ı gösteren açık bir hata ile fail-fast olur — 90. dakikada karışık `HTTPError` değil.

## CI iş akışı entegrasyonu

Hem staging hem target host kendi adımlarını CI'dan çalıştırabilir:

```yaml
# Staging-tarafı GitHub Actions iş akışı
- name: HF Hub modelleri ön-cache'le
  run: |
    forgelm cache-models \
      --model "${{ env.BASE_MODEL }}" \
      --safety "meta-llama/Llama-Guard-3-8B" \
      --output-format json -q | tee cache-models.json
    jq -e '.success' cache-models.json

- name: lm-eval task'ları ön-cache'le
  run: |
    forgelm cache-tasks --tasks "${{ env.EVAL_TASKS }}" \
      --output-format json -q | tee cache-tasks.json
    jq -e '.success' cache-tasks.json

- name: Bundle
  run: tar --create --gzip --file airgap-bundle.tar.gz airgap-bundle/
```

```yaml
# Air-gap target-tarafı iş akışı (internet'siz runner'da çalışır)
- name: Ortamı doğrula
  run: forgelm doctor --offline --output-format json -q | jq -e '.success'

- name: Eğit
  run: forgelm --config configs/run.yaml --offline
```

CI'ı belgelenen çıkış kodlarına göre dallandırın — bkz. [`docs/reference/cache_subcommands-tr.md#çıkış-kodları`](../reference/cache_subcommands-tr.md#çıkış-kodları) ve [`docs/reference/doctor_subcommand-tr.md#çıkış-kodları`](../reference/doctor_subcommand-tr.md#çıkış-kodları). Kod `2` (runtime hatası) yeniden denenebilir; kod `1` (config hatası) düzelt-ve-başarısız ol.

## Audit izi

Her cache adımı, çözülen cache dizinindeki `audit_log.jsonl`'a yazar (`--audit-dir` ile override edin). Tam event vocabulary'si:

| Event | Üreten | Madde | Tetikleyici |
|---|---|---|---|
| `cache.populate_models_requested` | `cache-models` | 12 | Invocation başlar. |
| `cache.populate_models_completed` | `cache-models` | 12 | Her model başarıyla indirildi. |
| `cache.populate_models_failed` | `cache-models` | 12 | Bir veya daha fazla indirme batch ortasında başarısız (operatörün neyin *yine de* indiğini bilmesi için `models_completed=[...]` taşır). |
| `cache.populate_tasks_requested` | `cache-tasks` | 12 | Invocation başlar. |
| `cache.populate_tasks_completed` | `cache-tasks` | 12 | Her task dataset'i başarıyla hazırlandı. |
| `cache.populate_tasks_failed` | `cache-tasks` | 12 | Bilinmeyen task adı VEYA batch ortasında dataset indirme hatası. |

Audit-logger inşası best-effort'tur: `FORGELM_OPERATOR` set edilmemiş bağlı staging makinesi debug-seviyesinde bir not görür ve çalıştırma audit olmadan devam eder — cache subcommand'larının değeri disk üzerindeki artefaktlardadır. Compliance programınız bundle'ı *kimin* stage ettiğine dair kanıt gerektiriyorsa, staging host'ta `FORGELM_OPERATOR`'ı sabitleyin.

## Sık karşılaşılan tuzaklar

### "`--output` set ettim ama `forgelm doctor --offline` boş cache raporluyor"

`--output` bir defalık indirme hedefidir; `forgelm doctor --offline` ve trainer her ikisi de env-var zincirini (`HF_HUB_CACHE > HF_HOME/hub > ~/.cache/huggingface/hub`) okur. Cache subcommand'ı ikisi farklılaştığında uyarı üretir — okuyun. Ya `--output`'u kaldırın (env-var zincirinin kazanmasına izin verin) ya da eşleşmek için `HF_HUB_CACHE=$(realpath your-output-dir)` set edin.

### "Doctor geçiyor ama trainer `local_files_only=True` ile model bulamıyor diye çöküyor"

YAML'ınızdaki model adı cache'teki snapshot'la tam eşleşmiyor. HF Hub ID'leri büyük-küçük harfe duyarlıdır (`meta-llama/Llama-Guard-3-8B` ≠ `meta-llama/llama-guard-3-8b`). Ayrıca: gated modeller, `cache-models` sırasında set edilen aynı `HF_TOKEN`'ı gerektirir; çünkü `forgelm doctor --offline` snapshot'ın **varlığını** doğrular, **lisansını** değil.

### "FORGELM_CACHE_DIR bir şey yapmıyor gibi"

Çünkü mevcut değil. ForgeLM `FORGELM_CACHE_DIR` env değişkeni tanımlamaz (bu, ghost-feature drift item GH-025 olarak bilinçli reddedildi). Kanonik env değişkenleri standart HuggingFace olanlardır: `HF_HUB_CACHE`, `HF_DATASETS_CACHE`, `HF_HOME`. Onları kullanın.

### "`HF_DATASETS_CACHE` set olmasına rağmen dataset'ler bulunamıyor"

`HF_DATASETS_OFFLINE=1`'i de set ettiniz mi? Onsuz, `datasets` sessizce eve telefon etmeyi deneyebilir ve yerel cache'e geri düşebilir — katı-egress ağında *deneme*nin kendisi compliance ihlalidir, veri yerel olarak alınmış olsa bile.

### "Bir org'un tüm modellerini tek seferde cache'lemek istiyorum"

Bu desteklenen bir bayrak değil (ve footgun — ForgeLM iş akışlarının çoğu yalnızca base + Llama Guard + belki sentetik veri için bir teacher gerektirir). Bunun yerine `--model`'i tekrarlayın, bayrak başına bir Hub ID:

```shell
$ forgelm cache-models \
    --model "meta-llama/Llama-3.2-3B" \
    --model "meta-llama/Llama-3.2-3B-Instruct" \
    --safety "meta-llama/Llama-Guard-3-8B"
```

### "CI runner'ımda GPU yok; yine de cache'leyebilir miyim?"

Evet. `cache-models` ve `cache-tasks` yalnızca ağ + disk + Python ister. `forgelm doctor` GPU olmadan çalışır (`gpu.inventory` probe'u yalnızca-CPU host'larda `warn` raporlar; bu verdict'i bloklamaz). Air-gap target host GPU ister; staging host istemez.

### "Python paketlerini de bundle'lamam gerekir mi?"

Genellikle, evet. Cache subcommand'ları HF artefaktlarını ele alır; Python wheel'leri (`forgelm` + extra'lar) ayrı bir problemdir. Staging host'ta `pip download 'forgelm[eval]' -d ./airgap-bundle/wheels` (veya `pip download 'forgelm[distributed]' -d ./airgap-bundle/wheels`) ve target'ta `pip install --no-index --find-links ./airgap-bundle/wheels 'forgelm[eval]'` (veya `'forgelm[distributed]'`) kullanın. Köşeli parantezleri zsh / bash glob expansion'ından korumak için extra-spec'i tırnak içine alın.

## Ayrıca

- [`docs/reference/cache_subcommands-tr.md`](../reference/cache_subcommands-tr.md) — `cache-models` ve `cache-tasks` için tam flag + çıkış-kodu + audit-event referansı.
- [`docs/reference/doctor_subcommand-tr.md`](../reference/doctor_subcommand-tr.md) — `forgelm doctor --offline` için tam referans.
- [Air-gap kullanıcı kılavuzu](../usermanuals/tr/operations/air-gap.md) — buraya bağlanan operatör özet sayfası.
- [Başlangıç](getting-started-tr.md) — air-gap varyantı bu rehber olan onboarding walkthrough.
- [`docs/reference/audit_event_catalog-tr.md`](../reference/audit_event_catalog-tr.md) §Air-gap ön-cache — tam event vocabulary'si.
- [`docs/qms/access_control-tr.md`](../qms/access_control-tr.md) — staging host'lar için önerilen `FORGELM_OPERATOR` namespace şeması.
- [Enterprise deployment (İngilizce)](enterprise_deployment.md) — sertleştirilmiş deployment'lar için komşu operatör playbook.
