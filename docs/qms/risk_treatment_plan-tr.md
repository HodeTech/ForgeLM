# QMS: Risk Treatment Plan (RTP)

> Kalite Yönetim Sistemi — [YOUR ORGANIZATION]
> ISO 27001:2022 referansları: A.5.7, A.5.8, A.5.9, A.5.31, A.6.8, A.8.8, A.8.30
> SOC 2 referansları: CC3.2, CC3.3, CC9.1, CC9.2

## 1. Amaç

ForgeLM-introduced'ın operatörün eğitim pipeline'ına getirdiği
riskleri, ForgeLM'in out-of-the-box gönderdiği treatment'ları ve
operatörün kabul ettiği veya daha fazla mitigate ettiği residual
riski dokümante et.

Bu yaşayan bir dokümandır — her quarterly risk review'da ve
herhangi bir önemli olaydan sonra (`pipeline.failed`,
`data.erasure_failed`, `audit.classifier_load_failed`,
serious-incident raporu) güncelle.

## 2. Metodoloji

ISO 27005 risk-management prensiplerini takip ediyor:

- **Likelihood** (L): riskin ne sıklıkta materialize olabileceği —
  `Low` (yıllık), `Med` (quarterly), `High` (aylık+).
- **Impact** (I): materialize olursa blast radius — `Low`
  (operatör nuisance), `Med` (tek koşum / tek özne), `High`
  (regulator-reportable olay, model recall).
- **Inherent risk** = L × I, herhangi bir treatment'tan önce.
- **Residual risk** = L × I, ForgeLM'in gönderdiği treatment +
  operatör-tarafı kontrollerden sonra.

Residual `High`, AI Officer tarafından açık **risk acceptance**
gerektirir (bkz. `roles_responsibilities.md`).

## 3. Risk register

ForgeLM'in kendi tehdit modelinin tanımladığı risklerle pre-populated.
Operatörün compliance ekibi §4 altına organizasyon-spesifik satırlar
ekler.

### 3.1 Eğitim-pipeline riskleri

#### R-01 Eğitim-veri zehirlenmesi (adversarial corpus)

| Alan | Değer |
|---|---|
| Açıklama | Adversarial actor model davranışını biaslamak için eğitim corpusuna kötü amaçlı örnekler enjekte eder |
| L × I (inherent) | Med × High = HIGH |
| Treatment | `forgelm audit` PII / secrets / quality scan; `data_audit_report.json` severity tier'ları flag'ler; operatör flag'lenen satırların pre-flight review'u; `compute_dataset_fingerprint` SHA-256 manifest'e "ne üzerinde eğitildi"yi damgalar |
| Residual L × I | Med × Low = MED |
| Sahip | Data Steward |
| Review cadence | Eğitim koşumu başına (pre-flight) |

#### R-02 Supply-chain compromise (compromised PyPI bağımlılığı)

| Alan | Değer |
|---|---|
| Açıklama | PyPI'daki upstream package kötü amaçlı release alır; ForgeLM pip install üzerinden tüketir |
| L × I (inherent) | Low × High = MED |
| Treatment | Her release tag'inde SBOM (CycloneDX 1.5); `pip-audit` nightly (Wave 4 / Faz 23); `forgelm doctor` pre-flight env check; `pyproject.toml`'da pinned upper bound'lar; transitive-dep CVE feed |
| Residual L × I | Low × Med = LOW |
| Sahip | ML Engineer + Compliance Officer |
| Review cadence | Continuous (nightly) + per-release (SBOM diff) |

#### R-03 Credential leak (HF token / webhook secret config'de veya audit log'da)

| Alan | Değer |
|---|---|
| Açıklama | Operatör `HF_TOKEN: ghp_...` literal'ıyla bir config commit eder veya gömülü credential'lı bir webhook URL'i |
| L × I (inherent) | Med × Med = MED |
| Treatment | `safe_post` error log'larında Authorization header'ları masklar; `forgelm audit` her zaman açık olan bir `_SECRET_PATTERNS` regex taraması çalıştırır ve corpus'taki credential'ları flag'ler (opt-in flag yok — bkz. `forgelm/data_audit/_secrets.py`); `_sanitize_md_list` deployer instructions'ta operatör-kontrollü string'leri escape eder; `forgelm doctor` pre-flight HF-auth probe; webhook payload-format curation asla config-derived secret taşımaz |
| Residual L × I | Low × Low = LOW |
| Sahip | ML Engineer |
| Review cadence | Pre-merge config review |

#### R-04 Audit-log tampering

| Alan | Değer |
|---|---|
| Açıklama | `audit_log.jsonl`'a write erişimi olan adversary girişleri yeniden yazıp bir dağıtımı gizler / onaylanmamış bir modeli onaylar |
| L × I (inherent) | Low × High = MED |
| Treatment | Append-only `O_APPEND` + `flock` + per-line `fsync`; HMAC chain (per-line `_hmac` alanı, per-run imzalama anahtarı `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)` olarak türetilir — `forgelm/compliance.py:164` — per-output-dir `.forgelm_audit_salt` ayrı bir konudur, purge / reverse-pii tanımlayıcı hash'lerini salt'lar ve chain-key türetimine KATILMAZ); SHA-256 prev-hash chain; genesis manifest sidecar (`_check_genesis_manifest` truncate-and-resume reddeder); `forgelm verify-audit` uçtan uca doğrular |
| Residual L × I | Low × Low = LOW |
| Sahip | AI Officer |
| Review cadence | Continuous (`forgelm verify-audit` cron) |

#### R-05 Kaldırılan PII'nin memorisation'ı (Madde 17 silme tamamlanmamış)

| Alan | Değer |
|---|---|
| Açıklama | Operatör `forgelm purge --row-id alice@example.com` corpusa karşı çalıştırır, ama haftalar önce satırı eğiten model öğrenilen ağırlıklardan onu hala üretebilir |
| L × I (inherent) | High × Med = HIGH |
| Treatment | ForgeLM, silinen satırı tüketen koşum bir `final_model/` artefaktına sahipse `data.erasure_warning_memorisation` event'ı yayar — bu bir **detection** sinyalidir, mitigation değildir. Operatör residual riski kabul eder ve organizasyonel kontrolleri uygular (özne bildirimi, yüksek-stakes dağıtımlar için sıfırdan yeniden eğit, DPIA güncellemesi). |
| Residual L × I | High × Med = HIGH (risk **kabul edildi**, bkz. §5) |
| Sahip | Data Protection Officer (DPO) |
| Review cadence | Madde 17 talebi başına |

### 3.2 Güvenlik + alignment riskleri

#### R-06 Safety-classifier load arızası

| Alan | Değer |
|---|---|
| Açıklama | Llama Guard / yapılandırılmış safety classifier yüklenemiyor (HF Hub down, OOM, version mismatch); ForgeLM gate yangın etmeden devam eder |
| L × I (inherent) | Med × High = HIGH |
| Treatment | F-compliance-110 strict gate: `risk_classification` ∈ `{high-risk, unacceptable}` + `evaluation.safety.enabled: false` `ConfigError` raise eder; `audit.classifier_load_failed` event arızayı kaydeder; operatör pipeline'ı non-zero exit'te durur |
| Residual L × I | Low × Low = LOW |
| Sahip | ML Lead |
| Review cadence | Eğitim koşumu başına |

#### R-07 Auto-revert false positive

| Alan | Değer |
|---|---|
| Açıklama | `evaluation.auto_revert: true` geçici bir eval regression'ında tetiklenir; sonuç `model.reverted` downstream denetçiye safety failure gibi görünür |
| L × I (inherent) | Med × Low = LOW |
| Treatment | `model.reverted` audit event regression delta + threshold taşır; `safety_trend.jsonl` cross-run context sağlar; operatör dashboard "gerçek" revert'leri threshold-tuning sorunlarından ayırır |
| Residual L × I | Low × Low = LOW |
| Sahip | ML Lead |
| Review cadence | Haftalık trend review |

### 3.3 Webhook + outbound-comms riskleri

#### R-08 Webhook SSRF / data exfiltration

| Alan | Değer |
|---|---|
| Açıklama | Adversary AWS instance credential'ları exfiltre etmek için `webhook.url_env=http://internal-metadata-server/...` yapılandırır |
| L × I (inherent) | Low × Med = LOW |
| Treatment | `safe_post` (Phase 7) — HTTPS-only, SSRF guard RFC 1918 / loopback / link-local (169.254.169.254'teki cloud IMDS dahil) / RFC 6598 Shared Address Space (`100.64.0.0/10`, Alibaba Cloud IMDS `100.100.100.200` dahil) / reserved / multicast reddeder, no redirect-following, error log'larda masked auth header'lar; webhook URL `url_env`'den gelmeli, asla inline değil |
| Residual L × I | Low × Low = LOW |
| Sahip | Güvenlik |
| Review cadence | Config review başına |

#### R-09 Webhook target ele geçirildi

| Alan | Değer |
|---|---|
| Açıklama | Operatörün Slack / Teams workspace'i ele geçirilir; saldırgan notify_start payload'larını okur ve model-deployment cadence'ini öğrenir |
| L × I (inherent) | Low × Low = LOW |
| Treatment | Webhook payload curation asla raw eğitim verisi veya unredacted PII taşımaz; ForgeLM webhook gövdelerini HMAC ile **imzalamaz** (`FORGELM_AUDIT_SECRET` HMAC'i yalnızca `audit_log.jsonl` chain bütünlüğüne özeldir — `forgelm/webhook.py` curated JSON'u signature header'ı olmadan POST eder), bu yüzden hedef-tarafı atıf TLS + `webhook.url_env` üzerinden URL-gizliliği + payload içindeki `FORGELM_OPERATOR` kimliğine düşer; operatör olayda `webhook.url_env` secret'ı rotate eder |
| Residual L × I | Low × Low = LOW |
| Sahip | Güvenlik + ML Lead |
| Review cadence | Webhook-target olayı başına |

### 3.4 ReDoS + ingestion riskleri

#### R-10 `--type custom` regex (reverse-pii) üzerinden ReDoS

| Alan | Değer |
|---|---|
| Açıklama | `forgelm reverse-pii --type custom --query "(a+)+$"` üzerindeki operatör-tedarikli regex catastrophic backtracking tetikler |
| L × I (inherent) | Low × Low = LOW |
| Treatment | `_scan_file_with_alarm`'da POSIX SIGALRM 30s per-file budget (Faz 38 / Wave 3 followup F-W3FU-T-01); thread-safety guard non-main thread'de skip eder; outer alarm korunur |
| Residual L × I | Low × Low = LOW |
| Sahip | Güvenlik |
| Review cadence | Wave 3 kapatıldı; başka eylem yok |

#### R-11 Cross-tool digest mismatch (purge ↔ reverse-pii)

| Alan | Değer |
|---|---|
| Açıklama | `forgelm purge` bir salt ile çalışır; aynı identifier için `forgelm reverse-pii` başka bir salt ile çalışır (örn. koşumlar arası `FORGELM_AUDIT_SECRET` değişimi) — digest eşleşmez, audit chain inconsistent görünür |
| L × I (inherent) | Low × Med = LOW |
| Treatment | Her audit event'te `salt_source` kaydedilir (Wave 3 followup F-W3-PS-07); env secret set olsa bile explicit `--salt-source per_dir` honor edilir; cross-tool correlation testi (`test_purge_target_id_matches_reverse_pii_query_hash_on_same_output_dir`) CI'da pin'lenmiş |
| Residual L × I | Low × Low = LOW |
| Sahip | DPO |
| Review cadence | Madde 15/17 talebi başına |

### 3.5 Dağıtım riskleri

#### R-12 Yetkisiz model dağıtımı

| Alan | Değer |
|---|---|
| Açıklama | Operatör human approval'ı atlar; high-risk model ML Lead / AI Officer sign-off olmadan üretime ulaşır |
| L × I (inherent) | Med × High = HIGH |
| Treatment | `risk_classification` ∈ `{high-risk, unacceptable}` için `evaluation.require_human_approval: true`; staging dizini `forgelm approve` olana kadar modeli tutar. (Not: F-compliance-110 strict gate yüksek-risk koşumlar için `evaluation.safety.enabled: true`'yi zorunlu kılar — `require_human_approval`'i değil — yukarıdaki R-06 satırına bakın; insan-onay kapısı bu satırın pinlediği deployer-tarafı bir disiplindir.) `human_approval.required/granted/rejected` chain her kararı forensic olarak kaydeder; **ForgeLM kapıda eğitici ≠ onaylayıcı kuralını zorlamaz** — onaylayıcı kimliğini `FORGELM_OPERATOR`'dan kaydeder, deployer'ın CI / IdP'si runner-kimliği ile reviewer-kimliği namespace'lerini ayrık tutmalıdır |
| Residual L × I | Low × Med = LOW |
| Sahip | ML Lead + AI Officer |
| Review cadence | High-risk için eğitim koşumu başına |

## 4. Operatöre özgü satırlar

[ForgeLM'in kendi tehdit modelinin öngörmediği organizasyon-spesifik
riskler için buraya satır ekle — örn. endüstri-spesifik düzenleyici
gereksinimleri, üçüncü-taraf yasal taahhütler, entegrasyon-spesifik
riskler. Aynı alan yapısını §3 olarak kullan.]

## 5. Risk acceptance log'u

Bir residual risk `Med` veya `High` olduğunda, AI Officer kabulü
imzalamalıdır:

| Risk ID | Inherent | Residual | Kabul eden | Tarih | Gerekçe |
|---|---|---|---|---|---|
| R-05 | HIGH | HIGH (kabul edildi) | [AI Officer] | [TARİH] | ForgeLM yalnız `data.erasure_warning_memorisation` detection sinyalini katar; operatör residual memorisation riskini kabul eder ve özne bildirimi + yüksek-stakes dağıtımlar için sıfırdan yeniden eğit + DPIA güncellemesi yoluyla deşarj eder. |
| ... | ... | ... | ... | ... | ... |

## 6. İnceleme

| Versiyon | Tarih | Yazar | Değişiklikler |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | İlk versiyon (Wave 4 / Faz 23) — 12 ForgeLM-tanımlı risk |

Quarterly review cadence:

- Her satırın L × I'sını geçen quarter'ın olay sıklığına karşı
  yeniden skorla.
- Retro'da tanımlanan operatör-spesifik riskler için yeni satır
  ekle.
- Residual-risk acceptance log'unun güncel olduğunu teyit et.
