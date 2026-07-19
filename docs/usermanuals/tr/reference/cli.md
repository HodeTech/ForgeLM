---
title: CLI Referansı
description: Her forgelm subcommand'ı ve bayrağı, auth kurulumu ve sık pattern'ler.
---

# CLI Referansı

ForgeLM, subcommand'larla tek bir `forgelm` binary'si yayınlar. Bu sayfa kanonik referanstır; eğitsel rehberlik için bkz. [İlk Koşunuz](#/getting-started/first-run).

## Üst seviye subcommand'lar

| Komut | Yaptığı |
|---|---|
| `forgelm` (subcommand'sız) | Eğit (`--config` ile). |
| `forgelm doctor` | Ortam kontrolü — Python, CUDA, GPU, bağımlılıklar, HF cache. |
| `forgelm quickstart` | Yerleşik şablonları listele veya örnekle. |
| `forgelm ingest` | PDF/DOCX/EPUB → JSONL dönüşümü. |
| `forgelm audit` | Eğitim öncesi veri denetimi (PII / secrets / dedup / leakage / quality). |
| `forgelm chat` | Etkileşimli REPL. |
| `forgelm export` | GGUF export ve quantization. |
| `forgelm deploy` | Deployment config üret (Ollama, vLLM, TGI, HF Endpoints). |
| `forgelm verify-audit` | Audit log zincirini doğrula (timestamp, prev_hash, HMAC). |
| `forgelm verify-annex-iv` | Export edilmiş Annex IV artefact'ını doğrula (§1-9 alanlar + manifest hash). |
| `forgelm verify-gguf` | GGUF model dosyası bütünlüğünü doğrula (magic header + metadata + SHA-256 sidecar). |
| `forgelm verify-integrity` | Model dizinini Madde 15 SHA-256 bütünlük manifest'ine karşı doğrula. |
| `forgelm approve` | İnsan onay isteğini imzala ve `final_model.staging/`'i promote et. |
| `forgelm reject` | İnsan onay isteğini reddet; staging dizini adli inceleme için korunur. |
| `forgelm approvals` | Bekleyen onayları listele (`--pending`) veya tek birini incele (`--show RUN_ID`). |
| `forgelm purge` | GDPR Madde 17 silme: row-id, run-id veya `--check-policy` retention raporu. |
| `forgelm reverse-pii` | GDPR Madde 15 erişim hakkı: maskelenmiş corpora'da subject kimlik bilgisini ara (plaintext veya hash-mask scan). |
| `forgelm cache-models` | Air-gap workflow: bir veya birden fazla model için HuggingFace Hub cache'ini önceden doldur. |
| `forgelm cache-tasks` | Air-gap workflow: lm-eval task dataset cache'ini önceden doldur (`[eval]` extra'sı gerekir). |
| `forgelm safety-eval` | Bir model checkpoint'ine karşı standalone safety evaluation (varsayılan Llama Guard). |

Bunlardan herhangi biri için `forgelm <subcommand> --help`.

## Üst seviye bayraklar (eğitim modu — `--config` ile kullanılır)

| Bayrak | Açıklama |
|---|---|
| `--config PATH` | YAML config dosya yolu. Eğitim için gerekli. |
| `--wizard` | `config.yaml` üretmek için etkileşimli yapılandırma sihirbazını başlat. |
| `--wizard-start-from PATH` | Sihirbazı mevcut bir YAML'dan pre-populate et: her adımın prompt'ları operatörün önceki cevaplarına default'lar (idempotent yeniden koşum). `--wizard` ile birlikte kullanın. |
| `--dry-run` | Config'i ve model/dataset erişimini doğrula; eğitim yok. |
| `--fit-check` | Eğitim VRAM tahmini; model yüklenmez. `--config` gerektirir. |
| `--resume [PATH]` | Eğitime kaldığı yerden devam. Çıplak `--resume` son checkpoint'i otomatik bulur; `--resume PATH` belirli bir yerden. |
| `--offline` | Air-gap modu: tüm HF Hub ağ çağrılarını kapat. Modeller ve dataset'ler yerel olarak mevcut olmalı. |
| `--benchmark-only MODEL_PATH` | Mevcut bir model üzerinde benchmark koştur, eğitim yok. `evaluation.benchmark` config'i gerektirir. |
| `--merge` | Config'in `merge:` bloğundan model birleştirmeyi koştur. Eğitim yok. |
| `--generate-data` | Teacher modelle sentetik eğitim verisi üret. Eğitim yok. |
| `--compliance-export OUTPUT_DIR` | EU AI Act uyum artifact'larını (audit trail, data provenance, Annex IV) OUTPUT_DIR'a export et. Manifest'in tamamlanması için eğitimden sonra koşturun. |
| `--output DIR` | `--compliance-export` için çıktı dizini (varsayılan: `./compliance/`). |
| `--output-format {text,json}` | Sonuçlar için çıktı formatı (varsayılan: `text`). CI için JSON. |
| `--quiet, -q` | INFO loglarını bastır. Sadece warning ve error göster. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | Log detay seviyesi (varsayılan: INFO). |
| `--version` | Sürümü yazdır. |
| `--help, -h` | Yardım göster. |

## Eğitim: `forgelm`

En sık kullanılan pattern'ler:

```shell
$ forgelm --config configs/run.yaml --dry-run        # doğrula
$ forgelm --config configs/run.yaml --fit-check      # VRAM kontrolü
$ forgelm --config configs/run.yaml                  # eğit
$ forgelm --config configs/run.yaml --resume         # otomatik son checkpoint'ten devam
$ forgelm --config configs/run.yaml --resume /path   # belirli bir checkpoint'ten devam
$ forgelm --config configs/run.yaml --merge          # birleştirme işi olarak koştur
$ forgelm --config configs/run.yaml --generate-data  # sadece sentetik veri
```

## Doctor: `forgelm doctor`

```shell
$ forgelm doctor                                     # tam ortam kontrolü
$ forgelm doctor --offline                           # air-gap varyantı: cache + offline-env probe'ları
$ forgelm doctor --output-format json | jq .         # CI dostu envelope
```

Python sürümünü, torch / CUDA / GPU'yu, opsiyonel extra'ları, HF Hub erişilebilirliğini (veya `--offline` ile HF cache'i), disk alanını ve operatör kimliğini probe eder. Exit kodları: `0` = hepsi geçti (warning OK), `1` = en az bir fail, `2` = bir probe'un kendisi crash etti.

## Audit: `forgelm audit`

```shell
$ forgelm audit INPUT_PATH \
    [--output DIR] \
    [--verbose] \
    [--near-dup-threshold N] \
    [--dedup-method {simhash,minhash}] \
    [--jaccard-threshold X] \
    [--quality-filter] \
    [--croissant] \
    [--pii-ml] [--pii-ml-language LANG] \
    [--workers N] \
    [--output-format {text,json}]
```

`--workers N` split düzeyinde paralellik sağlar; on-disk JSON worker sayısından bağımsız olarak byte-identical (sadece `generated_at` zamanı değişir). Tam per-flag tablosu — `forgelm/cli/_parser.py::_add_audit_subcommand`'a senkron yetkili kanonik liste — [Veri Denetimi](#/data/audit) sayfasındadır. Bu sayfanın eski sürümleri `--strict`, `--skip-pii`, `--skip-secrets`, `--skip-quality`, `--skip-leakage`, `--remove-duplicates`, `--remove-cross-split-overlap`, `--output-clean`, `--show-leakage`, `--minhash-jaccard`, `--minhash-num-perm`, `--dedup-algo`, `--dedup-threshold`, `--sample-rate` ve `--add-row-ids` flag'larını belgeliyordu — hiçbiri parser'da yok. Yukarıdaki kanonik adları kullanın.

## Ingest: `forgelm ingest`

```shell
$ forgelm ingest INPUT_PATH \
    --output PATH.jsonl \
    [--recursive] \
    [--strategy {sliding,paragraph,markdown}] \
    [--chunk-size N] [--overlap N] \
    [--chunk-tokens N] [--overlap-tokens N] [--tokenizer MODEL_NAME] \
    [--input-encoding CODEC] \
    [--pii-mask] [--secrets-mask] [--all-mask] \
    [--language-hint LANG] [--script-sanity-threshold X] \
    [--normalise-profile {turkish,none} | --no-normalise-unicode] \
    [--no-quality-presignal] \
    [--epub-no-skip-frontmatter] [--keep-md-frontmatter] \
    [--strip-pattern REGEX ...] [--strip-pattern-no-timeout] \
    [--page-range START-END] [--keep-frontmatter] \
    [--strip-urls {keep,mask,strip}] \
    [--output-format {text,json}]
```

`--output-format json` ile [JSON Output Contract](#/reference/json-output)
sayfasındaki makine-okunabilir envelope alınır — chunk count / files-
processed üzerinden branch eden CI gate'ler için kullanışlı, metin
özetini parse etmeye gerek yok. Faz 15 (v0.6.0), `--language-hint`,
`--script-sanity-threshold`, `--normalise-profile`, `--no-normalise-unicode`,
`--no-quality-presignal`, `--epub-no-skip-frontmatter`, `--keep-md-frontmatter`,
`--strip-pattern`, `--strip-pattern-no-timeout`, `--page-range`,
`--keep-frontmatter` ve `--strip-urls` bayraklarını ekledi. Bkz.
[Doküman Ingestion](#/data/ingestion).

`--input-encoding CODEC` yalnızca `.txt` / `.md` girdisi için kaynak
codec'i sabitler — PDF / DOCX / EPUB kendi encoding metadata'sını
taşır ve bu flag'i yok sayar. Varsayılan (set edilmediğinde) `utf-8-sig`
üzerinden BOM-strip + `errors="replace"` fallback'iyle otomatik
tespit eder — önceki davranıştan farksız. Eski Windows araçlarıyla
export edilmiş korpusları her ASCII-olmayan byte'ı `U+FFFD` ile
değiştirmek yerine doğru decode etmek için bir legacy codec adı geçirin
(örn. `cp1254`, `cp1252`, `latin-1`). Tanınmayan bir codec adı, hiçbir
dosya okunmadan önce config hatasıyla (`1`) reddedilir.

## Chat: `forgelm chat`

```shell
$ forgelm chat MODEL_PATH \
    [--adapter PATH] \
    [--system "system prompt"] \
    [--temperature 0.7] [--max-new-tokens 512] [--no-stream] \
    [--load-in-4bit | --load-in-8bit] \
    [--trust-remote-code] \
    [--backend {transformers,unsloth}]
```

REPL içindeki slash komutları: `/reset`, `/save [file]`, `/temperature N`, `/system [prompt]`, `/help` (alias `/?`), `/exit` (alias `/quit`). Bkz. [Etkileşimli Chat](#/deployment/chat).

## Export: `forgelm export`

```shell
$ forgelm export CHECKPOINT_DIR \
    --output PATH.gguf \
    --quant {q2_k,q3_k_m,q4_k_m,q5_k_m,q8_0,f16} \
    [--adapter PATH] \
    [--no-integrity-update]
```

`--quant` her çağrıda tek seviye alır; birden fazla GGUF çıktısı için `forgelm export`'u her seviye için bir kez çalıştırın. Bkz. [GGUF Export](#/deployment/gguf-export).

## Deploy: `forgelm deploy`

```shell
$ forgelm deploy MODEL_PATH \
    --target {ollama,vllm,tgi,hf-endpoints} \
    [--output PATH] \
    [--system "PROMPT"]                              # sadece Ollama
    [--max-length 4096] \
    [--gpu-memory-utilization 0.90]                  # vLLM
    [--port 8080]                                    # TGI
    [--trust-remote-code]                            # vLLM
    [--vendor aws]                                   # HF Endpoints
```

Bkz. [Deploy Hedefleri](#/deployment/deploy-targets).

## Onaylar: `forgelm approvals` / `forgelm approve` / `forgelm reject`

```shell
$ forgelm approvals --pending                        # bekleyen onay gate'lerini listele
$ forgelm approvals --show RUN_ID                    # belirli bir koşunun chain + staging'ini incele
$ forgelm approve  RUN_ID --comment "N. inceledi."   # final_model.staging/ → final_model/ promote
$ forgelm reject   RUN_ID --comment "Sebep ..."      # reddi kaydet (staging adli inceleme için korunur)
```

Bkz. [İnsan Gözetim Gate'i](#/compliance/human-oversight). Exit kodları: `0` = bekleyen liste / onay kaydedildi, `1` = bilinmeyen run_id / config hatası, `4` (sadece eğitim modu) = onay bekliyor.

## Audit log doğrula: `forgelm verify-audit`

```shell
$ forgelm verify-audit PATH/TO/audit_log.jsonl
$ forgelm verify-audit PATH/TO/audit_log.jsonl --hmac-secret-env FORGELM_AUDIT_SECRET
$ forgelm verify-audit PATH/TO/audit_log.jsonl --require-hmac
```

Monoton timestamp'leri, `prev_hash` zincir bütünlüğünü, `seq` boşluk tespitini ve (yapılandırıldığında) HMAC imzalarını doğrular. En az bir girdilik geçerli zincirde exit `0`; tahrif tespitinde (zincir kırılması, HMAC uyuşmazlığı, genesis-manifest uyuşmazlığı, ya da genesis manifest'i bir ilk girdi sabitleyen sıfır-girdili bir log) structured error envelope ile exit `6`; hiçbir şey karşılaştırılamadığında (eksik yol, secret olmadan `--require-hmac`, ya da genesis manifest'i olmayan sıfır-girdili bir log) exit `1`; gerçek bir runtime I/O hatasında exit `2`. Bkz. [Audit Log Doğrulama](#/compliance/verify-audit).

## Model bütünlüğü doğrula: `forgelm verify-integrity`

```shell
$ forgelm verify-integrity MODEL_DIR
$ forgelm verify-integrity MODEL_DIR --output-format json
```

`<MODEL_DIR>/model_integrity.json` dosyasını (eğitim sırasında compliance export tarafından yazılır) okur ve kayıtlı her artefaktın SHA-256'sını yeniden hesaplar. Manifest oluşturulduğundan beri **değişen**, **kaldırılan** veya **eklenen** dosyaları raporlar. Manifest dosyasının kendisi yürüyüşten hariç tutulur. Her kayıtlı artefakt mevcut ve değişmemişse ve fazladan dosya yoksa exit `0`; herhangi bir uyuşmazlıkta (değişen / kaldırılan / eklenen dosya — manifest ayrıştırıldı ve yürüyüş çalıştı) exit `6`; hiçbir şey hash'lenmeden önce dönen girdi hatalarında (eksik yol, manifest bulunamadı, bozuk JSON, dizin dışına çıkan manifest girdisi) exit `1`; gerçek bir runtime I/O hatasında exit `2`. Bkz. [Model Bütünlüğü Doğrulama](#/compliance/verify-integrity).

## Kimlik Doğrulama

ForgeLM credential'ları environment variable'lardan alır. Asla YAML'a koymayın.

| Sağlayıcı | Env var | Kullanım yeri |
|---|---|---|
| HuggingFace | `HF_TOKEN` (alias: `HUGGINGFACE_TOKEN`) | Gated modeller (Llama, Llama Guard) |
| OpenAI | `OPENAI_API_KEY` | LLM-as-judge, sentetik veri |
| Anthropic | `ANTHROPIC_API_KEY` | LLM-as-judge, sentetik veri |
| W&B | `WANDB_API_KEY` | Experiment tracking |
| Cohere | `COHERE_API_KEY` | (sentetik veri) |

ForgeLM'in YAML loader'ı düz `yaml.safe_load`'dur — `${VAR}` shell-tarzı interpolation yoktur. Yukarıdaki credential'lar için iki farklı desen geçerlidir:

- **HF token'ı:** `auth:` altına hiçbir şey koymayın — shell'de `HF_TOKEN`'ı (veya eski `HUGGINGFACE_TOKEN`'ı) export edin; hem `huggingface_hub`'ın kendi otomatik algılaması hem de ForgeLM'in login adımı onu bulur.
- **Sentetik veri teacher API key'i:** env var'ı `synthetic.api_key_env`'de adlandırın (`SyntheticConfig` üzerinde bir alan, nested bir `teacher:` objesi değil — teacher model'in kendisi `synthetic.teacher_model`'dir):

```yaml
synthetic:
  teacher_model: "gpt-4o"
  teacher_backend: "api"
  api_key_env: "OPENAI_API_KEY"      # env var'ı adlandırır; key'in kendisi hiç YAML'a değmez
```

Sentetik veri adımı çalıştığında adlandırılan env var set değilse, config-zamanı bir kontrol yoktur — `api_key_env` set değilken istek `Authorization` header'ı olmadan gönderilir ve teacher API bunu ilk çağrıda reddeder (tipik olarak HTTP 401). `--generate-data`'yı çalıştırmadan önce env var'ı export edin; böylece hata koşu ortasında değil hemen ortaya çıkar.

## Exit kodları

| Exit | Anlamı |
|---|---|
| 0 | Başarı |
| 1 | Config / semantik doğrulama hatası (hatalı YAML, eksik dosya, boş `--query`, vb.) |
| 2 | Argparse kullanım hatası (bilinmeyen flag/subcommand, eksik zorunlu argüman, hatalı choice, aralık dışı tip doğrulayıcı), eğitim çökmesi, probe crash (`forgelm doctor`) veya kıstırılmış Ctrl+C |
| 3 | Auto-revert / regression |
| 4 | İnsan onayı bekleniyor (eğitim pipeline) |
| 5 | Sihirbaz iptal (operatör kaydı reddetti / non-tty reddi) |
| 6 | Dört `verify-*` alt komutundan birinde bütünlük hatası — artefakt okundu ve hash / zincir / manifest karşılaştırması başarısız oldu |

`argparse` kullanım hataları (hatalı flag, eksik zorunlu argüman, hatalı `choices`
veya tip-doğrulayıcı sınırı) **2** ile çıkar — argparse'in kendi `error()` kuralı —
ayrıştırmadan *sonra* ulaşılan config / semantik doğrulama ise **1** ile çıkar. Bir
Ctrl+C sinyal kaynaklı 130'dur ancak süreç çıkmadan önce **2**'ye
(`EXIT_TRAINING_ERROR`) kıstırılır, böylece public `0–6` kümesi dışında bir exit
kodu asla döndürülmez.

Dört `verify-*` alt komutunda `1` ile `6` tek bir soruya göre ayrılır: doğrulayıcı
bir şeyi karşılaştıracak kadar ilerledi mi? Hiçbir şey karşılaştırılmadıysa (eksik
yol, bozuk manifest, hiç GGUF olmayan bir dosya) **1**; karşılaştırıldı ve
uyuşmadıysa **6**.

Tam kontrat için bkz. [Exit Kodları](#/reference/exit-codes).

## Environment variable'lar

| Değişken | Ne ayarlar |
|---|---|
| `HF_TOKEN` / `HUGGINGFACE_TOKEN` | HuggingFace authentication |
| `HF_HOME` | HuggingFace cache kökü (varsayılan `~/.cache/huggingface`) |
| `HF_HUB_CACHE` | HF Hub cache dizinini özel olarak override et (öncelik: `HF_HUB_CACHE` > `HF_HOME/hub` > varsayılan) |
| `HF_HUB_OFFLINE=1` | HF Hub ağ çağrılarını kapat |
| `HF_ENDPOINT` | HF Hub endpoint override (self-hosted mirror için); `forgelm doctor` tarafından honor edilir |
| `TRANSFORMERS_OFFLINE=1` | transformers kütüphanesi ağ çağrılarını kapat |
| `HF_DATASETS_OFFLINE=1` | datasets kütüphanesi ağ çağrılarını kapat |
| `FORGELM_OPERATOR` | Audit event'lerinde kaydedilen operatör kimliği (`getpass.getuser()@hostname`'i override eder) |
| `FORGELM_ALLOW_ANONYMOUS_OPERATOR` | `1` olduğunda audit log'un anonim operatör kaydetmesine izin verir (aksi halde çözülemeyen kimlik hatası) |
| `FORGELM_AUDIT_SECRET` | Audit log chain için HMAC imza anahtarı (tahrif tespitini açar) |
| `FORGELM_GGUF_CONVERTER` | Özel `convert-hf-to-gguf.py` script'inin yolu |

## Sık pattern'ler

### "Sadece eğit ve beni rahatsız etme"

```shell
$ forgelm --config configs/run.yaml --output-format json | tee run.log
```

### "Önce audit, temizse eğit"

```shell
$ forgelm audit data/
$ forgelm --config configs/run.yaml
```

İki komutu CI pipeline'ında sırayla çalıştırın; `forgelm audit` policy ihlalinde non-zero exit verir, dolayısıyla kirli corpus'ta ikinci komut tetiklenmez.

### "İnsan onay gate'iyle eğit; sonra promote et"

```shell
$ forgelm --config configs/run.yaml                  # onay gate ateşlerse exit 4
$ forgelm approvals --pending                        # bekleyen koşuyu keşfet
$ forgelm approve RUN_ID --comment "İnceledim."      # staging'i promote et
```

### "Eğit, GGUF export et, Ollama'ya deploy et"

Üst-seviye `output:` veya `deployment:` YAML anahtarı yoktur — `ForgeConfig` bilinmeyen anahtarları reddeder (`extra="forbid"`), dolayısıyla bunlardan birini taşıyan bir config anında `--dry-run`'da başarısız olur. Export ve deploy, eğitim tamamlandıktan *sonra* çalıştırılan ayrı CLI adımlarıdır, config-driven pipeline aşamaları değil:

```shell
$ forgelm --config configs/run.yaml                                             # 1. eğit (./checkpoints/final_model'a yazar)
$ forgelm export ./checkpoints/final_model --output model.gguf --quant q4_k_m   # 2. GGUF'a export et
$ forgelm deploy ./checkpoints/final_model --target ollama --output ./Modelfile # 3. Ollama Modelfile'ını üret
```

Yukarıdaki [Export: `forgelm export`](#export-forgelm-export) ve [Deploy: `forgelm deploy`](#deploy-forgelm-deploy) bölümlerine, ve YAML-driven bir deploy adımının neden olmadığının tam açıklaması için [Konfigürasyon Referansı `deployment:`](#/reference/configuration) bölümüne bakın.

## Ayrıca bakın

- [Configuration Referansı](#/reference/configuration) — YAML eşleşmesi.
- [Exit Kodları](#/reference/exit-codes) — CI için gate kontratı.
- [YAML Şablonları](#/reference/yaml-templates) — tam çalışan config'ler.
