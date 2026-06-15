# `forgelm verify-annex-iv` — Referans

> **Hedef kitle:** Sunumdan önce Annex IV teknik dokümantasyon artifact'larını doğrulayan uyumluluk operatörleri ve CI kapıları.
> **Ayna:** [verify_annex_iv_subcommand.md](verify_annex_iv_subcommand.md)

`verify-annex-iv` alt-komutu bir Annex IV teknik dokümantasyon JSON dosyasını okur, EU AI Act Annex IV §1-9 başına dokuz zorunlu alan kategorisini doğrular ve üretimden sonra tahrifat olup olmadığını tespit etmek için manifest hash'ini yeniden hesaplar. CLI, kütüphane giriş noktası `forgelm.cli.subcommands._verify_annex_iv.verify_annex_iv_artifact`'a delegasyon yapar ve `forgelm.compliance.build_annex_iv_artifact` içindeki yazıcı ile aynı kanonikalleştirme rutinini (`forgelm.compliance.compute_annex_iv_manifest_hash`) kullanır — böylece geçerli bir artefakt yazıcı/doğrulayıcı bayt sapması nedeniyle kendi doğrulayıcısında asla başarısız olamaz.

## Söz dizimi

```text
forgelm verify-annex-iv [--output-format {text,json}]
                        [-q] [--log-level {DEBUG,INFO,WARNING,ERROR}]
                        path
```

`path` (pozisyonel, zorunlu) — Annex IV JSON artifact yolu (genellikle eğitim çıktı dizini altında `compliance/annex_iv_<run>.json`).

## Bayraklar

| Bayrak | Varsayılan | Açıklama |
|---|---|---|
| `--output-format {text,json}` | `text` | `text` (varsayılan) `OK:` / `FAIL:` ile birlikte bölüm-başına nedeni ve eksik alan maddelerini yazar; `json` tüm `VerifyAnnexIVResult` zarfını yazar (`{"success", "valid", "reason", "missing_fields", "manifest_hash_actual", "manifest_hash_expected", "path"}`). |
| `-q`, `--quiet` | _kapalı_ | INFO loglarını bastırır. |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO` | Log ayrıntı seviyesi. |
| `-h`, `--help` | — | Argparse yardımını gösterir ve çıkar. |

## Çıkış kodları

| Kod | Anlam |
|---|---|
| `0` | Tüm gerekli Annex IV §1-9 alanları doldurulmuş VE (mevcutsa) `metadata.manifest_hash` yeniden hesaplanan hash ile eşleşiyor. |
| `1` | Çağıran/girdi hatası veya doğrulama başarısız (`valid=False`): dosya bulunamadı / normal dosya değil; gerekli alan eksik / boş; bozuk JSON; kök bir JSON nesnesi değil; manifest hash uyuşmazlığı. Operatör eylemli: artefakt mevcut hâliyle Annex IV uyumlu değil. |
| `2` | Mevcut bir dosyada gerçek runtime I/O hatası — okuma hatası, ayrıştırma sırasında izin reddi vb. Yol `os.path.isfile`'a erişilebilirdi ama doğrulama sırasında okunamaz hâle geldi. |

Kodlar `forgelm/cli/subcommands/_verify_annex_iv.py::_run_verify_annex_iv_cmd` tarafından emit edilir. Kamuya açık sözleşme semantiği `docs/standards/error-handling.md`'de sabitlenmiştir.

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
1
```

### Hata: bozuk JSON

```shell
$ forgelm verify-annex-iv compliance/annex_iv.json
ERROR: Annex IV artifact at 'compliance/annex_iv.json' is not valid JSON: Expecting value (line 1).
$ echo $?
1
```

## Bkz.

- [`audit_event_catalog.md`](audit_event_catalog-tr.md) — `compliance.artifacts_exported` (Madde 11 + Annex IV) ve kanonik event sözlüğünün geri kalanı.
- [`verify_audit.md`](verify_audit-tr.md) — `audit_log.jsonl` için kardeş doğrulayıcı.
- [`verify_gguf_subcommand.md`](verify_gguf_subcommand-tr.md) — export edilmiş GGUF artifact'ları için kardeş doğrulayıcı.
- [Annex IV kullanım kılavuzu sayfası](../usermanuals/tr/compliance/annex-iv.md) — tam hızlı başlangıç örneği içeren operatör-odaklı kılavuz.
- `forgelm.compliance.build_annex_iv_artifact` ve `forgelm.compliance.compute_annex_iv_manifest_hash` — bu doğrulayıcının yazıcı tarafındaki muadilleri.
