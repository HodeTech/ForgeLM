# ForgeLM ile Başlarken

> **Hedef kitle:** Yeni ForgeLM operatörleri — yeni bir host'ta ilk fine-tune'unu çalıştıran mühendisler, MLOps ekipleri ve compliance bilinçli veri bilimcileri.
>
> Bu rehber sizi `pip install forgelm`'den yeşil bir `forgelm doctor`'a, sonra ilk eğitim çalıştırmasına götürür; yol boyunca regülasyonlu ortamların beklediği tanılama kontrol noktalarını içerir.

## Bu rehber neyi çözer

ForgeLM'i yeni kurdunuz. Şimdi ne?

İlk-kez operatörleri en sık tökezleten ağrı noktaları, sıklık sırasına göre:

1. **Sessiz CUDA / extras yanlış-yapılandırması.** 28. dakikada `bitsandbytes` yanlış sürümü yüzünden çöken 30 dakikalık eğitim, fine-tuning'in en kötü geri besleme döngüsüdür.
2. **CI'da `FORGELM_OPERATOR` sabitlenmemiş.** Audit log'a stable bir kimlik yerine `me@workstation` yazılır; operatörün makinesi yeniden başladıktan sonra EU AI Act Madde 12 record-keeping bozulur.
3. **Eğitim ortasında disk dolu.** 7B model + checkpoint'ler + Llama Guard ön-cache'i 50+ GiB yer; `/`'de 10 GiB boşken uzun fine-tune çalıştırmak garanti geç hata.
4. **HuggingFace gated-model erişimi.** Llama / Llama Guard HF token ister; "OSError: HuggingFace token not found" 28. dakikada klasiktir.
5. **Yanlış onboarding sırası.** Operatörler, hangi extra'ya sahip olduklarını bilmeden YAML yazmaya başlar; sonra exception traceback'lerini okuyarak feature varlığını debug eder.

`forgelm doctor`, bunların her birini 30 dakika yerine iki saniyede yüzeye çıkarmak için var.

## Adım adım

### Adım 1 — ForgeLM'i kurun

Gerçekten kullanacağınız extra'ları seçin. Base kurulum size trainer'ı, altı alignment paradigmasını, evaluation, safety scoring, compliance artefakt üretimini ve CLI'yı verir:

```shell
$ pip install forgelm
```

GPU eğitimi için ihtiyacınız olan extra'ları ekleyin (virgülle birleştirin; `pyproject.toml` tasarım gereği `[all]` aggregate'i tanımlamaz):

```shell
$ pip install 'forgelm[qlora,eval,tracking,merging,export,ingestion]'
```

Tam extras kataloğu: [Kurulum kullanıcı kılavuzu](../usermanuals/tr/getting-started/installation.md).

### Adım 2 — `forgelm doctor`'u çalıştırın (kanonik ilk komut)

**`pip install forgelm`'den sonra çalıştıracağınız ilk komut budur.** Python, torch + CUDA, GPU envanteri, kurduğunuz opsiyonel extra'lar, HuggingFace Hub erişilebilirliği, çalışma alanı disk alanı ve audit kimliğinizi probe eder:

```shell
$ forgelm doctor
forgelm doctor - environment check

  [+ pass] forgelm.install          ForgeLM 0.9.0 running from /home/me/.venv/lib/python3.11/site-packages/forgelm (inside site-packages).
  [+ pass] python.version           Python 3.11.4 (CPython).
  [+ pass] torch.cuda               torch 2.4.0 with CUDA 12.4.
  [+ pass] numpy.torch_abi          torch 2.4.0 + numpy 1.26.4 are ABI-compatible.
  [+ pass] gpu.inventory            1 GPU(s) — GPU0: NVIDIA RTX 4090 (24.0 GiB).
  [+ pass] extras.qlora             Discoverable, not import-tested (module bitsandbytes, purpose: 4-bit / 8-bit QLoRA training).
  [! warn] extras.tracking          Optional extra missing — install with: pip install 'forgelm[tracking]' (purpose: Weights & Biases experiment tracking).
  [+ pass] pypdf_normalise.turkish  Turkish glyph profile loaded: 5 single-char + 1 multi-char substitutions. Active profile: 'none'.
  [+ pass] hf_hub.reachable         HuggingFace Hub reachable at https://huggingface.co (HTTP 200).
  [+ pass] disk.workspace           Workspace /home/me/forgelm — 387.0 GiB free of 500.0 GiB.
  [! warn] operator.identity        FORGELM_OPERATOR not set; audit events will fall back to 'me@workstation'. Pin FORGELM_OPERATOR=<id> for CI / pipeline runs so the audit log identifies a stable identity.

Summary: 9 pass, 2 warn, 0 fail.
```

> Yukarıda bazı `extras.*` satırları kısalık için atlandı; `forgelm doctor` prob edilen her extra için bir satır yazar (bu yazı itibarıyla dokuz tane). `pyproject.toml`'daki `[project.optional-dependencies]` içindeki her girdi bir satır almaz: `merging` bilinçli olarak dışarıda bırakılmıştır çünkü model birleştirme çekirdek bağımlılıklarla çalışır; yalnızca araç zinciri için olan `dev` / `security` / `chat` extra'larının da probu yoktur. Kurulumunuz için tam listeyi görmek üzere `forgelm doctor`'u lokal olarak çalıştırın.

**Her probe ne anlama geliyor:**

- `forgelm.install` — her zaman `pass`; hangi ForgeLM'in gerçekten yüklendiğini kaydeder (çözülen paket konumu, sürüm ve `site-packages` içinde olup olmadığı) — böylece bir bug raporu hangi kodun çalıştığını tam olarak gösterir. Bilinçli olarak ilk satır — bunu bilmeden diğer her satır belirsizdir.
- `python.version` — `fail` <3.10, `warn` 3.10.x, `pass` >=3.11.
- `torch.cuda` — torch eksikse `fail`; sadece CPU ise `warn` (CPU çalıştırmaları *destekleniyor* ama yavaş); CUDA görünüyorsa `pass`.
- `numpy.torch_abi` — bilinen torch<2.3 + numpy>=2 binary-ABI uyumsuzluğunda (`_ARRAY_API not found`) `fail`; aksi halde, torch veya numpy'den biri eksik olsa bile (atlanır, cezalandırılmaz) `pass`.
- `gpu.inventory` — cihaz başına VRAM (GiB); LoRA rank / batch boyutlandırması için gerekir.
- `extras.<ad>` — kurulu (veya eksik) opsiyonel extra başına bir satır. `warn` satırı tam `pip install 'forgelm[<ad>]'` ipucunu taşır; kurulum ipuçları her zaman eyleme dönüktür. `pass`, modülün keşfedilebilir olduğu anlamına gelir, mutlaka temiz import edildiği anlamına gelmez.
- `pypdf_normalise.turkish` — Türkçe glyph-normalizasyon tablosu yüklenemezse veya kanonik fixture'ında hiçbir ikame üretmezse `fail`; aksi halde `pass`.
- `hf_hub.reachable` — `${HF_ENDPOINT}/api/models` üzerinde HEAD. Captive portal, corp proxy, bloklanmış çıkışı eğitim keşfetmeden önce yakalar.
- `disk.workspace` — <10 GiB `fail`, <50 GiB `warn`, aksi `pass`.
- `operator.identity` — `FORGELM_OPERATOR` set ise `pass`, `getpass.getuser()@hostname` fallback ile `warn`, ikisi de çözülmezse `fail` (`FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` opt-in set ise).

**Çıkış kodları** (CI/CD sözleşmesi): `0` = tüm kontroller geçti, `1` = en az bir `fail` (config-error sınıfı), `2` = bir probe'un kendisi çöktü (runtime-error sınıfı).

Tam flag referansı: [`docs/reference/doctor_subcommand-tr.md`](../reference/doctor_subcommand-tr.md).

### Adım 3 — CI / pipeline çalıştırmaları için `FORGELM_OPERATOR`'ı sabitleyin

Yukarıdaki `operator.identity` uyarısı en yaygın olanıdır. Geliştirici workstation'unda `getpass.getuser()@hostname` fallback'i yeterlidir; bir CI runner'ında stable bir kimlik istersiniz:

```shell
$ export FORGELM_OPERATOR="gha:Acme/repo:training:run-${GITHUB_RUN_ID}"
$ forgelm doctor   # şimdi [+ pass] operator.identity
```

Bu kimlik trainer'ın ürettiği her audit-log girişine stamp'lenir — bir EU AI Act Madde 12 incelemecisinin model provenance'ı atfetmek için okuduğu şeydir. Önerilen namespace şeması için [`docs/qms/access_control.md`](../qms/access_control-tr.md)'a bakın.

### Adım 4 — Gated-model kimlik doğrulamayı devredin

Llama / Gemma / Llama Guard HF token ister. Standart env değişkeni ile set edin:

```shell
$ export HF_TOKEN="hf_xxxxx"   # veya huggingface-cli login
```

`HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `HUGGINGFACE_TOKEN` hepsi okunur; değer her `forgelm doctor --output-format json` çıktısında `<set, N chars>` olarak maskelenir (secret-mask disiplini için bkz. [`doctor_subcommand-tr.md`](../reference/doctor_subcommand-tr.md#secret-mask-disiplini)).

### Adım 5 — Bir config üretin ve doğrulayın

```shell
$ forgelm quickstart customer-support --output configs/quickstart-customer-support.yaml
$ forgelm --config configs/quickstart-customer-support.yaml --dry-run
```

`--dry-run` YAML'ı parse eder, referanslanan her dosyayı kontrol eder, model metadata'sını indirir (weight indirmez) ve bir GPU saniyesi tüketmeden problemleri raporlar. Walk-through: [İlk Çalıştırma](../usermanuals/tr/getting-started/first-run.md).

### Adım 6 — Eğitin

```shell
$ forgelm --config configs/quickstart-customer-support.yaml
```

İlk 30 saniyede bir şey başarısız olursa önce `forgelm doctor`'u yeniden çalıştırın — erken hataların çoğu, son kontrolden sonra `warn`'dan `fail`'e dönen bir probe'tur (genellikle: başka bir süreç çalışma alanını doldurdu veya bir ağ değişikliği corp proxy'i bozdu).

### Adım 7 — JSON envelope ile doğrulayın (opsiyonel, CI için)

```shell
$ forgelm doctor --output-format json -q | jq '.success'
true
```

JSON envelope şekli kilitli: `{"success": bool, "checks": [...], "summary": {pass, warn, fail, crashed}}`. Şema [`docs/usermanuals/tr/reference/json-output.md`](../usermanuals/tr/reference/json-output.md)'de.

## Sık karşılaşılan tuzaklar

### "`pip install forgelm[qlora]` sonrası bile `extras.qlora` warn alıyorum"

macOS / Apple Silicon durumu: `bitsandbytes` şu an Metal/MPS desteklemediği için import sessiz başarısız olur ve doctor extra'yı eksik olarak raporlar. ForgeLM otomatik olarak full-precision eğitime düşer. 4-bit QLoRA için CUDA GPU'lu Linux host'a ihtiyacınız var.

### "`hf_hub.reachable` `warn` yerine `fail` raporluyor"

Bu, SSRF disiplininin probe'u reddetmesidir — genellikle `http://` (yani `https://` değil) bir endpoint veya private-IP `HF_ENDPOINT`. Ya:

- `HF_ENDPOINT`'i public `https://` URL'e set edin, ya da
- Ağ probe'unu tamamen atlamak için `forgelm doctor --offline` çalıştırın (cache probe'u onun yerini alır).

### "Doctor `operator.identity` için `fail` diyor"

Bu, `FORGELM_OPERATOR` set değil VE `getpass.getuser()` username çözemedi VE `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` set **değil** anlamına gelir. `AuditLogger`'ın kendisi bu durumda başlamayı reddeder — `FORGELM_OPERATOR=<id>` sabitleyin (önerilen) veya `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` set edin (Madde 12 record-keeping için önerilmez; yalnızca sandbox / smoke test için uygun).

### "Air-gap'teyim — bu rehber hâlâ geçerli mi?"

Evet — ama ağ probe'u yerine `forgelm doctor --offline` ile. Geri kalan her şey (extras, GPU, disk, operator identity) aynıdır. Tam air-gap operatör akışı için [Air-gap deployment](air_gap_deployment-tr.md)'a bakın.

### "Doctor geçiyor ama `forgelm --dry-run` başarısız oluyor"

`forgelm doctor` *ortamı* doğrular; `--dry-run` *config*'i doğrular. Birbirini tamamlarlar, kopyaları değildir. Yeşil doctor + kırmızı `--dry-run` genellikle eksik bir input dosyası, hatalı yazılmış bir model adı veya Pydantic validation hatası anlamına gelir. `--dry-run` çıktısı doğrudan suçlu YAML alanını gösterir.

## Ayrıca

- [`docs/reference/doctor_subcommand-tr.md`](../reference/doctor_subcommand-tr.md) — tam `forgelm doctor` flag + probe referansı.
- [`docs/reference/cache_subcommands-tr.md`](../reference/cache_subcommands-tr.md) — air-gap ön-getirme için `forgelm cache-models` / `cache-tasks`.
- [`docs/reference/safety_eval_subcommand-tr.md`](../reference/safety_eval_subcommand-tr.md) — bağımsız safety classifier çalıştırmaları için `forgelm safety-eval`.
- [Kurulum kılavuzu](../usermanuals/tr/getting-started/installation.md) — tam extras kataloğu.
- [İlk Çalıştırma](../usermanuals/tr/getting-started/first-run.md) — doctor yeşil olduktan sonra tam eğitim walkthrough.
- [Air-gap deployment](air_gap_deployment-tr.md) — kısıtlı-egress ortamlar için.
- [Sorun Giderme kılavuzu](../usermanuals/tr/operations/troubleshooting.md) — doctor fail dediğinde.
