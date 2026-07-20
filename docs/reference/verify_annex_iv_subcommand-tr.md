# `forgelm verify-annex-iv` — Referans

> **Hedef kitle:** Sunumdan önce Annex IV teknik dokümantasyon artifact'larını doğrulayan uyumluluk operatörleri ve CI kapıları.
> **Ayna:** [verify_annex_iv_subcommand.md](verify_annex_iv_subcommand.md)

`verify-annex-iv` alt-komutu bir Annex IV teknik dokümantasyon JSON dosyasını okur, EU AI Act Annex IV §1-9 başına dokuz zorunlu alan kategorisini doğrular ve üretimden sonra tahrifat olup olmadığını tespit etmek için manifest hash'ini yeniden hesaplar. CLI, kütüphane giriş noktası `forgelm.verify.verify_annex_iv_artifact`'a delegasyon yapar (paket kökünde `forgelm.verify_annex_iv_artifact` olarak da erişilebilir) ve `forgelm.compliance.build_annex_iv_artifact` içindeki yazıcı ile aynı kanonikalleştirme rutinini (`forgelm.compliance.compute_annex_iv_manifest_hash`) kullanır — böylece geçerli bir artefakt yazıcı/doğrulayıcı bayt sapması nedeniyle kendi doğrulayıcısında asla başarısız olamaz.

## Söz dizimi

```text
forgelm verify-annex-iv [--pipeline] [--output-format {text,json}]
                        [-q] [--log-level {DEBUG,INFO,WARNING,ERROR}]
                        path
```

`path` (pozisyonel, zorunlu) — Annex IV JSON artifact yolu (genellikle eğitim çıktı dizini altında `compliance/annex_iv_<run>.json`). `--pipeline` ile `path`, `compliance/pipeline_manifest.json` içeren bir pipeline koşu **dizini** olarak yorumlanır.

## Bayraklar

| Bayrak | Varsayılan | Açıklama |
|---|---|---|
| `--pipeline` | _kapalı_ | `path`'i çok aşamalı bir pipeline koşu dizini olarak yorumlar ve zincir düzeyindeki `pipeline_manifest.json`'ı doğrular — kendi içerik hash'i, zincir bütünlüğü, aşama indeksi sıralaması, `stopped_at` tutarlılığı ve tamamlanmış her aşamanın Annex IV kanıtının derin ayrıştırması. Bkz. [Pipeline modu](#pipeline-modu). |
| `--output-format {text,json}` | `text` | `text` (varsayılan) `OK:` / `FAIL:` ile birlikte bölüm-başına nedeni ve eksik alan maddelerini yazar; `json` tüm `VerifyAnnexIVResult` zarfını yazar (`{"success", "valid", "reason", "missing_fields", "manifest_hash_actual", "manifest_hash_expected", "path"}`). |
| `-q`, `--quiet` | _kapalı_ | INFO loglarını bastırır. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO` | Log ayrıntı seviyesi. |
| `-h`, `--help` | — | Argparse yardımını gösterir ve çıkar. |

## Çıkış kodları

| Kod | Anlam |
|---|---|
| `0` | Tüm gerekli Annex IV §1-9 alanları doldurulmuş VE (mevcutsa) `metadata.manifest_hash` yeniden hesaplanan hash ile eşleşiyor. |
| `1` | Çağıran/girdi hatası: dosya bulunamadı / normal dosya değil; bozuk JSON; geçerli UTF-8 değil; kök bir JSON nesnesi değil; VEYA doğrulama başarısız — gerekli bir alan eksik / boş (artefakt hiç tam doldurulmamış). Operatör eylemli: artefakt mevcut hâliyle Annex IV uyumlu değil, ve hiçbir manifest-hash karşılaştırması hiç yapılmadı. |
| `2` | Mevcut bir dosyada gerçek runtime I/O hatası — okuma hatası, ayrıştırma sırasında izin reddi vb. Yol `os.path.isfile`'a erişilebilirdi ama doğrulama sırasında okunamaz hâle geldi. |
| `6` | Bütünlük arızası: tüm gerekli §1-9 alanları doldurulmuş, artefakt bir `metadata.manifest_hash` taşıyor ve yeniden hesaplanan hash onunla uyuşmuyor. Belge üretimden sonra düzenlenmiş. |

Kodlar `forgelm/cli/subcommands/_verify_annex_iv.py::_run_verify_annex_iv_cmd` tarafından emit edilir; bu, yapısal (asla string-eşleşmeli değil) predicate `forgelm.verify.is_annex_iv_integrity_failure` üzerinden yönlenir — gerekli-alan eksikleri her zaman `1`'dir, aksi hâlde tam bir artefaktta manifest-hash uyuşmazlığı her zaman `6`'dır; sonucun tipli alanlarına göre karar verilir, böylece yeniden yazılmış bir `reason` string'i exit kodunu asla çeviremez. Kamuya açık sözleşme semantiği `docs/standards/error-handling.md`'de sabitlenmiştir.

Aynı üçlü anlam `--pipeline` modunda da geçerlidir ve bu sayfa boyunca akıl yürütülecek kural budur:

- `6` — doğrulayıcı **bir şeyi karşılaştırdı ve eşleşmedi**.
- `1` — doğrulayıcı **hiçbir şeyi karşılaştırmaya hiç ulaşamadı**: girdi yok, girdi ayrıştırılamıyor, ya da kanıt var fakat hiçbir şey ona tanıklık etmiyor.
- `2` — erişilebilir bir yolda gerçek bir runtime I/O hatası.

## Pipeline modu

`forgelm verify-annex-iv --pipeline <run_dir>`, `<run_dir>/compliance/pipeline_manifest.json` dosyasını okur ve çok aşamalı koşuyu bir bütün olarak doğrular. Yukarıda tarif edilen aşama-başı doğrulayıcının zincir düzeyindeki toplayıcısıdır: pipeline manifesti indekstir, tamamlanmış her aşamanın Annex IV artefaktı ise indeksin işaret ettiği kanıttır.

Doğrulama tek geçişte üç katmanda çalışır:

1. **Yapısal + zincir kuralları** — zorunlu üst düzey anahtarlar, aşama indeksi monotonluğu, `stopped_at` tutarlılığı ve zincir bütünlüğü (`input_source: chain` olan her aşamada, önceki aşamanın `output_model`'i bu aşamanın `input_model`'ine eşit olmalıdır).
2. **Zincir manifestinin kendi içerik hash'i** — aşağıya bakın.
3. **Aşama başına kanıt derin ayrıştırması** — aşağıya bakın.

### Zincir manifest hash'i

`generate_pipeline_manifest`, yazma anında `metadata.manifest_hash` damgasını basar: `metadata` bloğu çıkarılmış, kanonikleştirilmiş manifest üzerinde bir SHA-256; tek-artefakt yolunun kullandığı `forgelm.compliance.compute_annex_iv_manifest_hash` rutininin aynısıyla hesaplanır. Rutinin paylaşılması kasıtlıdır — yazıcı ile doğrulayıcı iki ayrı implementasyon arasında bayt-bayt ayrışamaz.

**Neyi kapsar:** `metadata` bloğu dışındaki her şeyi. Buna yapısal ve zincir kurallarının göremediği alanlar da dâhildir — sağlayıcı meta verisi, aşama başına `metrics`, `gate_decision`, `final_status` ve aşama başına `error` dizeleri. Hash var olmadan önce bunların tümü üretim sonrası düzenlenebiliyor ve doğrulayıcı manifesti yine de geçerli raporluyordu.

**Kasıtlı olarak kapsamadıkları:** `metadata` bloğunun kendisi (hash'i tuttuğu için dâhil edilmesi döngüsel olurdu) ve aşama başına kanıt dosyalarının *içerikleri*. Zincir hash'i indeksi sabitler, indeksin atıfta bulunduğu belgeleri değil; onlar 3. katman ve kendi artefakt-başı hash'leri tarafından kapsanır.

**Geçerli ile doğrulanmış farklı durumlardır ve CLI hangisini aldığınızı söyler.** JSON zarfında `hash_state` olarak raporlanan üç sonuç vardır:

| `hash_state` | Anlamı | Çıkış kodu | Metin çıktısı |
|---|---|---|---|
| `verified` | Bir `manifest_hash` vardı ve yeniden hesaplanan özet onunla eşleşti. Üretim sonrası hiçbir şey düzenlenmemiş. | başka bulgu yoksa `0` | `OK: … (hash verified, N stage artefact(s))` |
| `absent` | Manifestte `manifest_hash` yok — damga var olmadan önce yazılmış bir arşiv. Yapısal ve zincir kuralları geçti, fakat zincir dışı alanlara hiçbir şey tanıklık etmedi. | başka bulgu yoksa `0` | `OK (UNVERIFIED): … — no manifest_hash; tampering not checked` |
| `mismatch` | Bir `manifest_hash` vardı ve yeniden hesaplanan özet onunla uyuşmuyor. Manifest üretim sonrası değiştirilmiş. | `6` | `FAIL: …` ve bir `manifest hash mismatch` ihlali |

`absent` durumu, bir operatörün yanlış okumaması gereken durumdur. `0` ile çıkar, çünkü hash öncesi bir arşiv tahrifat kanıtı değildir ve reddedilmesi damga sevk edilmeden önce yazılmış her manifesti geriye dönük olarak geçersiz kılardı. Bu **temiz bir sağlık raporu değildir**: hiçbir karşılaştırma yapılmadı. İkisini metin modunda `OK (UNVERIFIED)` ön ekiyle, JSON modunda `hash_state` ile ayırt edin — asla yalnızca çıkış koduyla ve asla yeniden çalıştırıp `0` görerek değil.

### Aşama başına kanıt derin ayrıştırması

`status` değeri `completed` olan her aşama için doğrulayıcı, aşamanın kanıt işaretçisini çözer ve işaret ettiği artefaktı ayrıştırır. Önceden bu bir `os.path.isfile` varlık kontrolüydü; dolayısıyla sıfır baytlık, bozuk veya tahrif edilmiş bir artefakt geçiyordu.

**`completed` artık neyi garanti eder.** Tamamlanmış her aşama için kanıt dosyası bulundu, okundu, JSON olarak ayrıştırıldı, bir JSON nesnesi olduğu doğrulandı, zorunlu her Annex IV §1-9 alanını taşıdığı kontrol edildi ve — bir `manifest_hash` taşıyorsa — kendi içeriğine karşı hash doğrulaması yapıldı. `evidence_verified` içinde sayılan bir aşamada bu altısının tamamı sağlanmıştır.

Kontrol kapalı biçimde başarısız olur (fail closed). Aşağıdakilerin her biri bir ihlaldir (çıkış `6`):

| Koşul | Gerekçe |
|---|---|
| Tamamlanmış aşama hiç kanıt işaretçisi kaydetmemiş | Manifest tamamlanmış bir aşama iddia ediyor; kanıtsız bir iddia doğrulanabilir değildir. |
| Pipeline dizininden dışarı çıkan göreli bir işaretçi | `../../../etc/hosts` bir aşama artefaktı değildir. Mutlak işaretçiler koşulsuz izinlidir, çünkü bir aşamanın `training.output_dir`'i yapılandırmayla bildirilir ve meşru olarak pipeline ağacının dışında yaşayabilir. |
| İşaretçi bir symlink | Kanıt, doğrulama anında bağlantının çözüldüğü şey olurdu; bu, arşivlenmiş koşunun bir özelliği değildir. |
| İşaretçi bir dizin | Traceback yerine bir hükümle reddedilir. |
| Sıfır bayt | Boş bir dosya kanıt değildir. |
| 8 MiB'den büyük | **Okunmadan** reddedilir. Kendi girdisi tarafından OOM ile öldürülebilen bir doğrulayıcı, doğrulayıcı değildir. |
| Bozuk JSON ya da geçersiz UTF-8 | Ayrıştırılamaz, dolayısıyla karşılaştırılamaz. |
| Kök bir JSON nesnesi değil | Zorunlu alanların karşılaştırılacağı bir şey yok. |
| Zorunlu bir Annex IV alanı eksik ya da boş | Kasıtlı ayrışmaya dikkat: tek başına doğrulandığında eksik bir artefakt `1` ile çıkar; zincir kanıtı olarak `6` ile çıkar, çünkü pipeline manifesti bu aşamanın geçerli kanıtla tamamlandığını *iddia eder* ve bu iddia karşılaştırıldı, tutmadı. |
| Artefaktın kendi `manifest_hash`'i içeriğiyle uyuşmuyor | Kanıtın kendisinde tahrifat tespiti. |

İki koşul ihlal yerine **UNVERIFIED** (çıkış `1`) raporlar, çünkü bir karşılaştırmanın başarısız olduğunu değil, doğrulayıcının karşılaştırmaya hiç ulaşamadığını gösterirler:

- Artefakt yapısal olarak tamdır fakat `manifest_hash` taşımaz. Tahrifat kontrol edilemedi.
- Kanıt işaretçisi `training_manifest.json` adını taşır ve yanında bir `annex_iv_metadata.json` yoktur. Sevk edilmiş hiçbir ForgeLM sürümü `training_manifest.json` yazmaz — `export_compliance_artifacts`, `training_manifest.yaml` ve `annex_iv_metadata.json` üretir — dolayısıyla bu eski işaretçi her zaman boşta kalmıştır. Doğrulayıcı, eski dosya adını yanındaki `annex_iv_metadata.json`'a çözer ve onu normal biçimde doğrular; kardeş dosya yoksa UNVERIFIED raporlar, çünkü yazıcı tarafındaki bir kusur operatöre tahrifat olarak raporlanmamalıdır.

Varlık kontrolünü geçmiş bir yolda stat hatası IO_ERROR (çıkış `2`) raporlar.

**Boş küme deliğini kapatan bir kural daha:** `final_status: completed` iddia ederken hiç tamamlanmış aşama taşımayan bir manifest başlı başına bir ihlaldir (çıkış `6`). Bu olmadan, doğrulayıcının en mutlu yolu hiçbir şeyi incelemediği yol olurdu.

**Öncelik.** Birden fazla bulgu bir arada olduğunda bütünlük kazanır: aynı koşuda okunamayan (`2`) veya tanıklanmamış (`1`) bir bulgu raporlanmış olsa bile, etiketsiz her ihlal `6`'ya yönlenir. Zayıf bir bulgu asla güçlü olanı maskelememelidir.

### Pipeline modu JSON zarfı

`--pipeline --output-format json`, "OK"i açık kılan sayaçları da yayınlar:

| Anahtar | Tip | Anlamı |
|---|---|---|
| `success` | bool | İhlal listesi boşken `true`. `hash_state == "verified"` anlamına **gelmez**. |
| `mode` | dize | Bu modda her zaman `"pipeline"`. |
| `path` | dize | Koşu dizininin mutlak yolu. |
| `violations` | dize dizisi | İç yönlendirme token'ları çıkarılmış, insan-okunur bulgular. |
| `stages_examined` | int | Kanıt katmanının baktığı tamamlanmış aşama sayısı. |
| `evidence_verified` | int | Bunlardan kaçı kendi hash'i dâhil her kontrolü geçti. |
| `evidence_unverified` | int | Bunlardan kaçına ulaşıldı fakat tanıklanmadı. |
| `hash_state` | dize | `verified` / `absent` / `mismatch` — zincir manifestinin kendi hash'i. |

"Yalnızca geçerli değil, doğrulanmış" isteyen bir CI kapısı, sadece çıkış `0`'ı değil; `hash_state == "verified"`, `evidence_verified == stages_examined` ve `stages_examined > 0` koşullarını doğrulamalıdır.

## Zorunlu Annex IV alanları

Doğrulayıcı statik bir katalogu (`_ANNEX_IV_REQUIRED_FIELDS`) yürür; böylece gelecekteki bir şema eklemesi her çağrı yerinde kod düzenlemesi değil, demette tek bir satırdır. Bir alan; anahtar yoksa VEYA değer `None`, boş string, boş liste ya da boş dict ise (operatör muhtemelen otomatik üretim şablonundan doldurmayı unutmuş) "eksik" sayılır.

| Üst-seviye anahtar | Annex IV bölümü |
|---|---|
| `system_identification` | §1 — sistem tanıtımı (ad, sürüm, sağlayıcı, intended_purpose). |
| `intended_purpose` | §1 — amaçlanan kullanım beyanı. |
| `system_components` | §2 — yazılım / donanım bileşenleri + tedarikçi listesi. |
| `computational_resources` | §2(g) — eğitim sırasında kullanılan hesaplama kaynakları. |
| `data_governance` | §2(d) — veri kaynakları, yönetişim, doğrulama metodolojisi. |
| `technical_documentation` | §3-5 — tasarım + geliştirme metodolojisi. |
| `monitoring_and_logging` | §6 — pazara-sonrası izleme + audit-log varlığı. |
| `performance_metrics` | §7 — doğruluk / dayanıklılık / siber güvenlik metrikleri. |
| `risk_management` | §9 — risk yönetim sistemi referansı (Madde 9 hizalaması). |

## Emit edilen audit event'leri

`forgelm verify-annex-iv` **salt-okunur bir doğrulayıcıdır** ve `audit_log.jsonl`'a **hiçbir** kayıt eklemez. Annex IV *üretimini* (doğrulamayı değil) işaretleyen event — `compliance.artifacts_exported` — [audit_event_catalog.md](audit_event_catalog-tr.md)'nin Madde 11 + Annex IV bölümünde kataloglanmıştır. Doğrulama-anı kaydı isteyen operatörler bu alt-komutu CI'dan çağırıp JSON çıktısını artifact paketinin yanında saklayabilir.

## Örnekler

### Metin çıktısı (varsayılan)

```shell
$ forgelm verify-annex-iv checkpoints/run/compliance/annex_iv.json
OK: checkpoints/run/compliance/annex_iv.json
  All Annex IV §1-9 fields populated; manifest hash matches.
```

### JSON çıktısı (CI tüketicileri için)

```shell
$ forgelm verify-annex-iv --output-format json \
    checkpoints/run/compliance/annex_iv.json
{
  "success": true,
  "valid": true,
  "reason": "All Annex IV §1-9 fields populated; manifest hash matches.",
  "missing_fields": [],
  "manifest_hash_actual": "sha256:abcdef…",
  "manifest_hash_expected": "sha256:abcdef…",
  "path": "/abs/path/checkpoints/run/compliance/annex_iv.json"
}
```

### Hata: gerekli alanlar eksik

```shell
$ forgelm verify-annex-iv checkpoints/run/compliance/annex_iv.json
FAIL: checkpoints/run/compliance/annex_iv.json
  Missing or empty required Annex IV field(s): risk_management, performance_metrics.
    - missing: risk_management
    - missing: performance_metrics
$ echo $?
1
```

### Hata: tahrifat tespiti

```shell
$ forgelm verify-annex-iv checkpoints/run/compliance/annex_iv.json
FAIL: checkpoints/run/compliance/annex_iv.json
  Manifest hash mismatch — artifact may have been modified after generation.
$ echo $?
6
```

### Hata: bozuk JSON

```shell
$ forgelm verify-annex-iv compliance/annex_iv.json
ERROR: Annex IV artifact at 'compliance/annex_iv.json' is not valid JSON: Expecting value (line 1).
$ echo $?
1
```

### Pipeline modu: doğrulanmış mı yoksa yalnızca geçerli mi

```shell
$ forgelm verify-annex-iv --pipeline ./pipeline_run
OK: pipeline manifest at ./pipeline_run (hash verified, 3 stage artefact(s))
$ echo $?
0
```

Aynı komut, hash damgası var olmadan önce yazılmış bir arşive karşı:

```shell
$ forgelm verify-annex-iv --pipeline ./archived_run_2026_03
OK (UNVERIFIED): pipeline manifest at ./archived_run_2026_03 — no manifest_hash; tampering not checked
$ echo $?
0
```

İkisi de `0` ile çıkar; yalnızca ikincisinde tahrifat kontrol edilmemiş kalır.

### Pipeline modu: çürük aşama kanıtı

```shell
$ forgelm verify-annex-iv --pipeline ./pipeline_run
FAIL: pipeline manifest at ./pipeline_run
  - Stage 'dpo-preference': evidence at './pipeline_run/dpo/compliance/training_manifest.json' is zero bytes
$ echo $?
6
```

### Pipeline modu: bir CI kapısı için JSON zarfı

```shell
$ forgelm verify-annex-iv --pipeline --output-format json ./pipeline_run
{
  "success": true,
  "mode": "pipeline",
  "path": "/abs/path/pipeline_run",
  "violations": [],
  "stages_examined": 3,
  "evidence_verified": 3,
  "evidence_unverified": 0,
  "hash_state": "verified"
}
```

## Bkz.

- [`audit_event_catalog.md`](audit_event_catalog-tr.md) — `compliance.artifacts_exported` (Madde 11 + Annex IV) ve kanonik event sözlüğünün geri kalanı.
- [`webhook_schema-tr.md`](webhook_schema-tr.md) — webhook olay sözlüğü; çok aşamalı bir koşunun, bu komutun doğruladığı manifesti üretirken emit ettiği üç `pipeline.*` olayı dâhil.
- [`../guides/pipeline-tr.md`](../guides/pipeline-tr.md) — çok aşamalı pipeline koşularına operatör kılavuzu.
- [`verify_audit.md`](verify_audit-tr.md) — `audit_log.jsonl` için kardeş doğrulayıcı.
- [`verify_gguf_subcommand.md`](verify_gguf_subcommand-tr.md) — export edilmiş GGUF artifact'ları için kardeş doğrulayıcı.
- [Annex IV kullanım kılavuzu sayfası](../usermanuals/tr/compliance/annex-iv.md) — tam hızlı başlangıç örneği içeren operatör-odaklı kılavuz.
- `forgelm.compliance.build_annex_iv_artifact` ve `forgelm.compliance.compute_annex_iv_manifest_hash` — bu doğrulayıcının yazıcı tarafındaki muadilleri.
