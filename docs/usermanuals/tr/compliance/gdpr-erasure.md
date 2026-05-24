---
title: GDPR Silme & Erişim
description: `forgelm purge` ve `forgelm reverse-pii` subcommand'ları ile Madde 15 (erişim hakkı) ve Madde 17 (silinme hakkı) taleplerini karşılayın.
---

# GDPR Silme & Erişim

ForgeLM, en yaygın iki GDPR veri-sahibi hakkı için kardeş subcommand'lar ship eder: `forgelm purge` (Madde 17 — silinme hakkı) ve `forgelm reverse-pii` (Madde 15 — erişim hakkı). Per-output-dir salt'ı paylaşırlar; böylece compliance reviewer aynı veri sahibi için silme ve erişim taleplerini tek bir tamper-evident audit zincirinde — cleartext identifier operatör terminalinden hiç çıkmadan — korele edebilir.

Bu sayfa operatör hızlı-referansıdır. Daha derin deployer akışı [GitHub'daki GDPR silme deployer kılavuzunda](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/gdpr_erasure-tr.md) bulunur; flag-başına referanslar manuel içindeki [CLI Referansı](#/reference/cli) ve GitHub'daki özel subcommand sayfaları [`purge_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/purge_subcommand-tr.md) + [`reverse_pii_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/reverse_pii_subcommand-tr.md) sayfalarındadır.

## İki hak, tek zincir

| Hak | Subcommand | Ne yanıtlar |
|---|---|---|
| **Madde 15** — erişim hakkı | `forgelm reverse-pii` | "Hakkımda kayıt tutuyor musunuz?" — JSONL corpus'ları subject identifier'ı için tarar. |
| **Madde 17** — silinme hakkı | `forgelm purge` | "Hakkımdaki her kaydı silin." — bir satırı, bir staging dizinini veya bir compliance bundle'ını atomik olarak siler. |

İki subcommand da audit satırını kaydetmeden önce subject identifier'ını **aynı** per-output-dir salt (`<output_dir>/.forgelm_audit_salt`) ile hash'ler. `data.access_request_query` üzerindeki `query_hash` ve `data.erasure_*` üzerindeki `target_id` aynı subject için byte-byte aynıdır; reviewer bu sayede ikisini cleartext olmadan korele edebilir.

## Madde 17 — `forgelm purge`

### Üç mod

```shell
# Corpus-satır silme
forgelm purge --row-id "ali@example.com" --corpus train.jsonl \
    --output-dir ./outputs --justification "GDPR Mad.17 ticket #1234"

# Koşum-kapsamlı silme
forgelm purge --run-id fg-abc123def456 --kind staging --output-dir ./outputs
forgelm purge --run-id fg-abc123def456 --kind artefacts --output-dir ./outputs

# Retention-policy raporu (salt-okunur)
forgelm purge --check-policy --config configs/run.yaml --output-dir ./outputs
```

### Neyi kaydeder

Audit zincirine altı event yazılır (bkz. [GitHub'daki Audit Event Kataloğu](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog-tr.md)):

- `data.erasure_requested` — herhangi bir silmeden ÖNCE, ilk yayılır.
- `data.erasure_completed` — disk operasyonu başarılı sonra son yayılır.
- `data.erasure_failed` — disk operasyonu raise ettiğinde veya eşleşen satır/koşum bulunmadığında bunun yerine yayılır.
- `data.erasure_warning_memorisation` — corpus-satır silme × bu corpus'u tüketen herhangi bir koşum için `final_model/` mevcut (satır diskten gitti ama eğitilmiş ağırlıklarda hâlâ memorize edilmiş olabilir).
- `data.erasure_warning_synthetic_data_present` — corpus-satır silme × `output_dir`'de `synthetic_data*.jsonl` mevcut.
- `data.erasure_warning_external_copies` — yüklü config boş-olmayan `webhook` block'u içeriyor; downstream tüketiciler bildirim almış olabilir.

### Neyi YAPMAZ

- **Modelleri yeniden eğitmez.** Bir satırı corpus'tan kaldırmak onu zaten eğitilmiş ağırlıklardan unutturmaz — silinmiş satır olmadan tam yeniden eğitim tek doğru çözümdür. Tool `data.erasure_warning_memorisation` yayar; böylece boşluk görünür olur.
- **Audit log'unu silmez.** Madde 17(3)(b) audit / muhasebe kayıtlarını hukuki yükümlülük savunması olarak korur; tool silmeyi geçmişi yeniden yazmak yerine yeni bir event olarak kaydeder.
- **Downstream tüketicilere bildirim göndermez.** Webhook receiver'ları, dataset mirror'ları ve deploy edilmiş model endpoint'leri operatörün runtime-katmanı sorumluluğudur (Madde 17(2)). `data.erasure_warning_external_copies` event'ı açık hatırlatmadır.

## Madde 15 — `forgelm reverse-pii`

### İki scan modu

```shell
# Plaintext residual scan: mask sızıntılarını tespit eder (operatör
# corpus'un maskelendiğini sandı ama bir residual span sızdı).
forgelm reverse-pii --query "alice@example.com" --type email \
    --output-dir ./outputs data/*.jsonl

# Hash-mask scan: corpus, purge'ün per-output-dir salt'ını paylaşılan
# secret olarak kullanan HARİCİ bir pipeline tarafından SHA256(salt +
# identifier) digest'leriyle maskelenmişti.
forgelm reverse-pii --query "alice@example.com" --type email \
    --salt-source per_dir --output-dir ./outputs data/*.jsonl
```

### `reverse-pii` neyi kaydeder

Çağrı başına bir event (bkz. katalog):

- `data.access_request_query` — scan'den sonra (veya mid-scan I/O hatası sonrası, `error_*` field'larıyla) yayılır. **Hash'lenmiş** `query_hash`'i, `identifier_type`'ı, `scan_mode`'u, `files_scanned` listesini, `match_count`'u ve `salt_source`'ü taşır.

Chain satırı cleartext identifier'ı asla taşımaz — salt audit log'un kendisine karşı wordlist saldırılarına karşı korur.

### `--type custom` regex caveat

`--type custom` `--query`'yi Python regex olarak yorumlar. POSIX main-thread çağrılarında dosya başına 30s SIGALRM bütçesi ReDoS hang'lerine karşı koruma sağlar. **Windows'ta VE POSIX worker thread'lerinde SIGALRM guard no-op'tur** — worker thread'den veya Windows'ta `--type custom` çalıştıran operatörler regex'lerini kendileri vet etmelidir.

## Salt + audit-secret ayrımı

`<output_dir>/.forgelm_audit_salt`'taki per-output-dir salt (ilk kullanımda mode `0600`, atomik O_EXCL yazma) **identifier-hash salt'ıdır**. Audit-chain HMAC anahtarına katılmaz; o anahtar bağımsız olarak ForgeLM'in `AuditLogger`'ı içinde `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` şeklinde türetilir.

İki primitif kasıtlı olarak ayrıdır:

- `FORGELM_AUDIT_SECRET` rotasyonu **yalnızca chain HMAC anahtarını** rotate eder. Identifier hash'leri per-output-dir salt'tan türetilir ve **env-var rotasyonlarına karşı stabildir**; rotasyondan sonra aynı `--output-dir` üzerinde mount edilen bir `forgelm reverse-pii` sorgusu, rotasyondan ÖNCE kaydedilmiş `forgelm purge` event'leriyle hâlâ korele eder.
- Birini incelemek diğerini ifşa etmez.
- Per-output-dir salt, identifier hash'lemenin tek girdisidir; output dizininin yaşam süresi boyunca kalıcıdır ve chain HMAC'in rotasyon kadansını cross-tool digest korelasyonundan izole eder.

## Exit kodları

İki subcommand da projenin `0/1/2/3/4` sözleşmesine uyar:

| Kod | Anlamı |
|---|---|
| 0 | Başarı. İhlal bulunduğunda bile `forgelm purge --check-policy` 0 ile çıkar (report-not-gate semantik). Eşleşme bulunmadığında bile `forgelm reverse-pii` 0 ile çıkar (Madde 15 "kayıt yok"u geçerli yanıt olarak açıkça kabul eder). |
| 1 | Config hatası: kötü flag'ler, okunamaz corpus, hatalı regex, çakışan retention değerleri, yazılamaz `--audit-dir`. |
| 2 | Runtime hatası: I/O başarısızlığı, izin reddedildi, ReDoS SIGALRM timeout, atomic-rename başarısızlığı. |

Kod 3 (`EXIT_EVAL_FAILURE`) ve 4 (`EXIT_AWAITING_APPROVAL`) iki subcommand'ın da yüzeyinin parçası değildir.

## Sık hatalar

:::warn
**Subject identifier'ını `--justification`'a yapıştırmak.** Justification audit zincirine verbatim kaydedilir. Bunun yerine dahili ticket id'nizi referans verin ("GDPR Mad.17 ticket #1234").
:::

:::warn
**Memorization'ı kontrol etmeden `forgelm purge` çalıştırmak.** Corpus'u tüketen herhangi bir koşum için `final_model/` mevcutsa, satır diskten gitti ama ağırlıklarda hâlâ encoded olabilir. `data.erasure_warning_memorisation` event'ı boşluğu yüzeyselleştirir; harekete geçin (silinmiş satır olmadan yeniden eğitin veya DPIA'nızda residual riski belgeleyin).
:::

:::tip
**`--output-dir`'i explicit olarak pin'leyin.** İlk corpus path'inden `--output-dir`'i çözen implicit fallback çözülen dizini adlandıran bir WARNING yayar; `purge` ve `reverse-pii` arasında cross-tool korelasyon için her zaman `--output-dir`'i explicit verin; iki subcommand da aynı `.forgelm_audit_salt`'ı görsün.
:::

:::tip
**`forgelm verify-audit`'i DSAR kapanışınıza wire'layın.** Erasure / access event zincire girdikten sonra `forgelm verify-audit <output_dir>/audit_log.jsonl --require-hmac` çalıştırın ve doğrulama çıktısını DSAR ticket'a iliştirin. Auditor okuyacak.
:::

## Bkz.

- [Audit Log](#/compliance/audit-log) — `data.*` event'lerinin kaydedildiği yer.
- [İnsan Gözetimi](#/compliance/human-oversight) — yüksek-riskli deployment'lar için GDPR haklarıyla eşleşen Madde 14 kardeş kapısı.
- [GDPR silme deployer kılavuzu](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/gdpr_erasure-tr.md) — tam deployer akışı (GitHub kaynağı).
- [`purge_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/purge_subcommand-tr.md) — `forgelm purge` flag-başına referans (GitHub kaynağı).
- [`reverse_pii_subcommand-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/reverse_pii_subcommand-tr.md) — `forgelm reverse-pii` flag-başına referans (GitHub kaynağı).
