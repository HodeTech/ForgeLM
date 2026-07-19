# `forgelm doctor` Referansı

> **Mirror:** [doctor_subcommand.md](doctor_subcommand.md)
>
> Operatörün kurulumdan sonra çalıştıracağı ilk komut. Python, torch + CUDA, GPU envanteri, opsiyonel ForgeLM extra'ları, HuggingFace Hub erişilebilirliği, çalışma alanı disk alanı ve `FORGELM_OPERATOR` audit-kimlik ipucunu probe eder; sonra tablo halinde metin raporu veya yapılandırılmış JSON envelope üretir.

## Synopsis

```shell
forgelm doctor [--offline] [--output-format {text,json}] [-q] [--log-level {DEBUG,INFO,WARNING,ERROR}]
```

Uygulama: [`forgelm/cli/subcommands/_doctor.py`](../../forgelm/cli/subcommands/_doctor.py).

## Flags

| Flag | Tip | Varsayılan | Açıklama |
|---|---|---|---|
| `--offline` | bool | `false` | HuggingFace Hub ağ probe'unu atlar; bunun yerine yerel cache'i inceler (öncelik: `HF_HUB_CACHE > HF_HOME/hub > ~/.cache/huggingface/hub`). `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` veya `HF_DATASETS_OFFLINE=1` ortamda set ise zımnen aktif olur. |
| `--output-format` | `text` \| `json` | `text` | Render. `json`, kilitli envelope `{"success": bool, "checks": [...], "summary": {pass, warn, fail, crashed}}` üretir. |
| `-q`, `--quiet` | bool | `false` | INFO loglarını bastırır. |
| `--log-level` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | `INFO` | Log seviyesi. |

## Probe'lar

| Probe `name` | Status politikası | Ne kontrol eder |
|---|---|---|
| `forgelm.install` | `pass` | Hangi ForgeLM'in gerçekten çalıştığı: çözümlenen paket dizini, sürüm ve paketin site-packages içinde olup olmadığı. İlk sırada çalışır; böylece yapıştırılan bir hata raporu, konuşulan kodu en başta belirtir. |
| `python.version` | `fail` <3.10, `warn` 3.10.x, `pass` >=3.11 | Desteklenen Python aralığı. |
| `torch.installed` / `torch.cuda` | torch eksikse `fail`; sadece CPU ise `warn`; CUDA varsa `pass` | torch + CUDA bulunabilirliği. |
| `numpy.torch_abi` | ABI uyuşmazlığında `fail`, aksi halde `pass` (torch yoksa kontrol atlanır ve `pass` döner) | Kurulu torch ile numpy'ın ABI uyumu. |
| `gpu.inventory` | Cihaz başına VRAM ile `pass`, CUDA yoksa `warn` | Görünen GPU'lar ve VRAM (GiB). |
| `extras.<ad>` | İçe aktarılabiliyorsa `pass`, yoksa kurulum ipucuyla `warn` | Her opsiyonel extra için bir satır: `qlora`, `unsloth`, `distributed`, `eval`, `tracking`, `export`, `ingestion`, `ingestion-pii-ml`, `ingestion-scale`. `merging` extra'sının satırı bilerek **yoktur** — model birleştirme çekirdek bağımlılıklarla native olarak uygulandığından o extra bir no-op'tur. |
| `pypdf_normalise.turkish` | Normalizasyon beklendiği gibi çalışıyorsa `pass`, aksi halde `warn` | PDF alımında Türkçe metin için font-fallback normalizasyonu; `extras.ingestion` satırının yanında durur, ikisi birlikte okunsun diye. |
| `hf_hub.reachable` (online) | 2xx/3xx `pass`, transport hatası `warn`, SSRF politika reddi `fail` | `forgelm._http.safe_get` ile 5 sn timeout HEAD `${HF_ENDPOINT}/api/models`. |
| `hf_hub.offline_cache` (`--offline`) | Dosya görünürse `pass`, boş/kısmen okunamıyorsa `warn`, hiç dosya yok ve walk hatası varsa `fail` | Resolved Hub cache'in sınırlandırılmış taraması (derinlik 4, 5000 dosya tavanı). |
| `disk.workspace` | <10 GiB `fail`, <50 GiB `warn`, aksi `pass` | `shutil.disk_usage(".")`. |
| `operator.identity` | `FORGELM_OPERATOR` set ise `pass`, `getpass` fallback ise `warn`, ikisi de yoksa `fail` (`FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` ise `warn`) | `AuditLogger`'ın kaydedeceği kimliği önceden tahmin eder. |

Opsiyonel extra'lar listesi: [`forgelm/cli/subcommands/_doctor.py::_OPTIONAL_EXTRAS`](../../forgelm/cli/subcommands/_doctor.py).

## Secret-mask disiplini

Adı secret listesine ([`_DOCTOR_SECRET_ENV_NAMES`](../../forgelm/cli/subcommands/_doctor.py): `FORGELM_AUDIT_SECRET`, `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `HUGGINGFACE_TOKEN`, `FORGELM_RESUME_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `WANDB_API_KEY`, `COHERE_API_KEY`) uyan ortam değişkeninin **değeri** hem `detail` hem `extras` içinde `<set, N chars>` olarak render edilir; böylece `--output-format json` çıktısı CI log'a aktarıldığında secret sızdırılmaz. `FORGELM_OPERATOR` kimlik bilgisidir, secret değildir; aynen gösterilir.

## Çıkış kodları

| Kod | Anlamı |
|---|---|
| `0` | Tüm probe'lar geçti. `warn` satırları bunu çevirmez — operatör eylem alabilir ama akışı bloklamaz. |
| `1` | En az bir probe `fail` döndürdü (config-error sınıfı — operatör düzeltebilir). |
| `2` | Bir probe'un kendisi çöktü (runtime-error sınıfı — doctor bug'ı veya operatör-ortam sürprizi). |

[`forgelm/cli/_exit_codes.py`](../../forgelm/cli/_exit_codes.py)'de tanımlı: `EXIT_SUCCESS=0`, `EXIT_CONFIG_ERROR=1`, `EXIT_TRAINING_ERROR=2`. Geçici hatalarda yeniden deneyen pipeline'lar `2` (yeniden çalıştır) ile `1` (düzelt-ve-başarısız ol) arasında dallanır.

## Üretilen audit event'leri

`forgelm doctor` bir **salt-okunur tanılama** komutudur ve audit event üretmez. `audit_log.jsonl`'a hiç dokunmaz; çalışmak için `FORGELM_OPERATOR` zorunlu değildir. `operator.identity` probe'u `AuditLogger`'ın kaydedeceğini *tahmin eder*, gerçek bir yazma yapmaz.

## JSON envelope şekli

```json
{
  "success": true,
  "checks": [
    {
      "name": "python.version",
      "status": "pass",
      "detail": "Python 3.11.4 (CPython).",
      "extras": {"version": "3.11.4", "implementation": "CPython"}
    }
  ],
  "summary": {"pass": 9, "warn": 2, "fail": 0, "crashed": 0}
}
```

`success`, `summary.fail == 0` ise `true`'dur. `extras`, `ensure_ascii=False` ile JSON'a çevrilir (Unicode operatör adı veya cache yolu aynen render edilir) ve `default=str` ile (gelecekte `Path`/`datetime` değer üreten bir probe renderer'ı çökertmesin diye). Tam şema [`docs/usermanuals/en/reference/json-output.md`](../usermanuals/en/reference/json-output.md)'de kilitli.

## Örnekler

### İlk çalıştırma duman testi

```shell
$ forgelm doctor
forgelm doctor - environment check

  [+ pass] python.version          Python 3.11.4 (CPython).
  [+ pass] torch.cuda              torch 2.4.0 with CUDA 12.4.
  [+ pass] gpu.inventory           1 GPU(s) - GPU0: NVIDIA RTX 4090 (24.0 GiB).
  [+ pass] extras.qlora            Installed (module bitsandbytes, purpose: 4-bit / 8-bit QLoRA training).
  [! warn] extras.tracking         Optional extra missing - install with: pip install 'forgelm[tracking]' (purpose: Weights & Biases experiment tracking).
  [+ pass] hf_hub.reachable        HuggingFace Hub reachable at https://huggingface.co (HTTP 200).
  [+ pass] disk.workspace          Workspace /home/me/forgelm - 387.0 GiB free of 500.0 GiB.
  [! warn] operator.identity       FORGELM_OPERATOR not set; audit events will fall back to 'me@workstation'. Pin FORGELM_OPERATOR=<id> for CI / pipeline runs so the audit log identifies a stable identity.

Summary: 6 pass, 2 warn, 0 fail.
```

### Offline (air-gap) doğrulaması

```shell
$ HF_HUB_OFFLINE=1 forgelm doctor --offline
```

`hf_hub.reachable` yerine `hf_hub.offline_cache` görünür. Dolu cache; boyut, dosya sayısı ve `HF_HUB_OFFLINE` değerini raporlar; boş cache, [`cache_subcommands.md`](cache_subcommands-tr.md)'a yönlendiren `warn` üretir.

### CI gate (JSON)

```shell
$ forgelm doctor --output-format json -q | jq '.summary'
{
  "pass": 6,
  "warn": 2,
  "fail": 0,
  "crashed": 0
}
$ forgelm doctor --output-format json -q | jq '.success'
true
```

### Özel HuggingFace endpoint

```shell
$ HF_ENDPOINT=https://hub.internal.example.com forgelm doctor
```

`_resolve_hf_endpoint`, `huggingface_hub` kütüphanesini taklit ederek `HF_ENDPOINT`'e saygı duyar; corp-mirror operatörleri hatalı uyarı almaz.

## Ayrıca

- [Başlangıç rehberi](../guides/getting-started-tr.md) — `forgelm doctor` ile başlayan onboarding.
- [`cache_subcommands-tr.md`](cache_subcommands-tr.md) — `forgelm doctor --offline`'in doğruladığı air-gap ön-cache komut çifti.
- [Air-gap deployment rehberi](../guides/air_gap_deployment-tr.md) — tam air-gap operatör akışı.
- [`audit_event_catalog-tr.md`](audit_event_catalog-tr.md) — tam audit-event kataloğu (doctor hiçbir event üretmez).
- [Kurulum kullanıcı kılavuzu](../usermanuals/tr/getting-started/installation.md) — `pip install forgelm[<extras>]` referansı.
- [JSON çıktı şeması](../usermanuals/tr/reference/json-output.md) — kilitli envelope sözleşmesi.
