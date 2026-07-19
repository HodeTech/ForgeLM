# Uyumluluk Özeti — EU AI Act + ISO 27001 + SOC 2

> **Kapsam.** ForgeLM'in EU AI Act (yüksek-riskli sistemler, Madde 17
> QMS + ilgili hükümler) ile deployer'ın ISO 27001 / SOC 2 Type II
> uyumluluğunu desteklemek için sağladığı kanıt, kontrol ve artefakt'ların
> kısa, makine-okunabilir özeti. Yasal tavsiye değildir.
>
> **Hedef kitle.** Compliance officer / denetçi / deployer engineering
> lead.
>
> Wave 4 / Faz 26 temizliği: bu doküman eskiden literal kaynak-kod satır
> numaralarına anchor'luyordu (örn. `compliance.py#L33`); kod tabanı
> evrildikçe drift ediyordu. Aşağıdaki referanslar artık symbol-name +
> module-path formunu kullanıyor — refactor'leri sağ atlatıyorlar.

## Hızlı sonuç

ForgeLM kutudan çıktığı gibi şunları ship eder:

- **EU AI Act Madde 9** risk-yönetimi kanıtı: strict gate
  (`_warn_high_risk_compliance`) + safety-eval auto-revert.
- **EU AI Act Madde 10** veri-yönetişim kanıtı:
  `data_governance_report.json` + `forgelm audit` PII / secrets /
  quality taraması.
- **EU AI Act Madde 11 + Annex IV** teknik dokümantasyon:
  `compliance.export_compliance_artifacts` + ZIP bundle.
- **EU AI Act Madde 12** kayıt-tutma: append-only `AuditLogger` (HMAC
  zinciri + manifest sidecar).
- **EU AI Act Madde 13** deployer talimatları:
  `generate_deployer_instructions`.
- **EU AI Act Madde 14** insan-gözetim kapısı: `forgelm approve` /
  `reject` Madde 14 staging.
- **EU AI Act Madde 15** model-bütünlüğü: `compute_artefact_sha256` +
  `model_integrity.json`; deployment-sonrası doğrulama:
  `forgelm verify-integrity`.
- **EU AI Act Madde 17** QMS şablonları: `docs/qms/` (Wave 0 baseline +
  Wave 4 ISO eklemeleri).
- **GDPR Madde 15** erişim hakkı: `forgelm reverse-pii`.
- **GDPR Madde 17** silinme hakkı: `forgelm purge`.
- **ISO 27001 / SOC 2 uyumluluğu** — bkz. Wave 4 design doc + deployer
  rehberi.

## EU AI Act yüksek-düzey checklist

Düzenleyicinin sorduğu vs ForgeLM'in cevapladığı:

| Düzenleyici sorusu | ForgeLM kanıtı |
|---|---|
| Risk sınıflandırma + yönetişim | `compliance.risk_classification` 5-tier; F-compliance-110 strict gate |
| QMS süreçleri + kayıtları | `docs/qms/` 9 SOP (5 Wave 0 + 4 Wave 4); audit chain |
| Veri kaynağı | `data_provenance.json`; `compute_dataset_fingerprint` (SHA-256 + size + mtime); `hf_revision_source` ile derecelendirilmiş dataset Hub commit SHA'sı |
| Teknik dokümantasyon | `annex_iv_metadata.json`; Annex IV §§1-9 kanonik düzen |
| Uygunluk kanıtı | `compliance_report.json`; `model_card.md`; `model_integrity.json` |
| İzleme + post-market gözetim | Webhook lifecycle (`notify_*`); `safety_trend.jsonl` cross-run trend |
| İnsan gözetimi | Madde 14 staging gate; `human_approval.required/granted/rejected` |

## ForgeLM her gereksinimi nerede karşılar

### Güvenlik değerlendirme + auto-revert

- Implementasyon: `forgelm.trainer` post-training değerlendirme zinciri;
  regresyonda baseline'a düşmeyi `auto_revert` flag tetikler.
- Kanıt: `safety_results.json` (prompt-başına sınıflandırma);
  `model.reverted` audit event regresyon delta ile.
- Konfigürasyon: `evaluation.safety.enabled`,
  `evaluation.auto_revert`, `evaluation.safety.scoring`,
  `evaluation.safety.min_safety_score`.

### Güvenlik sınıflandırıcısı + 3-katmanlı kapı

- Implementasyon: `forgelm.safety`, Llama Guard 3'ü (ya da operatör-
  konfigüre sınıflandırıcıyı) bundled
  `forgelm/safety_prompts/default_probes.jsonl` corpus'unda — 18 harm
  kategorisinde 51 prompt (`benign-control`, `animal-cruelty`,
  `biosecurity`, `controlled-substances`, `credentials`, `csam`,
  `cybersecurity`, `extremism`, `fraud`, `harassment`, `hate-speech`,
  `jailbreak`, `malware`, `medical-misinfo`, `privacy-violence`,
  `self-harm`, `sexual-content`, `weapons-violence`) — çalıştırır.
  Daha büyük dış corpus'ları olan operatörler `--probes`'u kendi
  JSONL'lerine yönlendirir.
- 3-katman gate: binary safe-ratio → confidence-weighted score →
  şiddet eşiği. Her katman koşumu ayrı bir `audit.classifier_*` event
  ile reddeder; operatör reddedişin nedenini eşleştirebilir.

### Veri kaynağı (SHA-256) + uyumluluk export

- Implementasyon: `forgelm.compliance` corpus-başına fingerprint hesaplar
  (`_fingerprint_local_file`, `_fingerprint_hf_revision`) ve
  `data_provenance.json` yazar; `export_compliance_artifacts` paketi
  ZIP'ler.  Hub dataset'leri `forgelm.data._resolve_hub_dataset_revision`
  tarafından çözülen bir commit'te yüklenir ve `_fingerprint_hf_revision`
  `hf_revision_source: loaded` olarak ayrı bir sorguyu değil, tam olarak o
  SHA'yı kaydeder.
- CLI: `forgelm --config job.yaml --compliance-export ./out/`.

### Annex IV paketi provenance alanları

Paket, bir koşumun *hangi upstream artefaktları* kullandığını kaydeder ve
— denetçi için asıl önemli kısım budur — *her kaydın ne kadar güçlü
kanıtlandığını* da kaydeder. Bir SHA'yı asla yanındaki dereceyi okumadan
okumayın.

**Dataset**, `data_provenance.json` içinde ve `compliance_report.json`'un
`data_provenance` bloğunda:

| Anahtar | Anlamı |
|---|---|
| `hf_revision` | Dataset deposunun Hub commit SHA'sı. Hiçbiri bilinmiyorsa yoktur. **Asla bir branch adı, tag veya hareketli ref değildir** — ya 40 karakterlik küçük-harf hex commit SHA'sı ya da hiçbir şey. |
| `hf_revision_source` | Derece. **Her** dataset fingerprint'inde bulunur; birbirini dışlayan dört değerden biri — aşağıdaki tabloya bakın. |
| `hf_revision_reason` | Yalnızca `hf_revision_source` `unresolved` iken bulunur; nedenini belirten serbest metin (≤200 karakter). |
| `source` | Hub-id şeklindeki bir yol için `huggingface_hub`; dizin korpusu için `local_directory`; ne diskte olan ne de Hub-id şeklinde olan bir yol için `unknown`. Yerel bir **dosya** hiç `source` anahtarı yazmaz. |
| `dataset_id` | Hub depo kimliği. Yalnızca `source: huggingface_hub` altında yazılır — bir dizin korpusunun Hub kimliği yoktur ve artık bu anahtarı taşımaz. |
| `resolved_path` | Yerel dosya veya dizin yolu bir symlink olduğunda, symlink hedefi. |

`hf_revision_source` değerleri:

| Değer | `hf_revision` ne tutar | Denetçi ne sonuç çıkarabilir |
|---|---|---|
| `loaded` | 40-hex commit SHA'sı. | **Kanıt.** `forgelm.data` SHA'yı çözdü, `load_dataset(..., revision=...)`'a geçirdi ve yükleme döndü. **Bir denetçinin neyin üzerinde eğitildiğinin kanıtı sayabileceği tek değer budur.** `hf_revision_reason` yazılmaz. |
| `unverified` | Şekli doğrulanmış 40-hex commit SHA'sı. | **İpucu, kanıt değil.** Bu süreçteki hiçbir yükleme bu dataset'i sabitlemedi; SHA manifest zamanındaki bir Hub sorgusundan geldi — kanonik örnek, korpusu hiç okumadan manifest yazan `forgelm compliance-only`'dir. "Manifest yazıldığında deponun varsayılan-dal başı" olarak okuyun. Upstream depo, yükleme ile manifest arasında hareket ettiyse bu değer, koşumun hiç okumadığı bir commit'i adlandırır. |
| `local_path` | Yoktur. | **Dürüst bir boşluk.** Korpus diskteki dosyalardır, dolayısıyla bir Hub commit'i yoktur ve aranmamıştır; hiçbir şey başarısız olmadığı için `hf_revision_reason` yazılmaz. Hem yerel dosya hem yerel dizin için ayarlanır. Yerel bir **dosya** için kanıt `sha256` içerik hash'idir; yerel bir **dizin için içerik hash'i yoktur** — kayıt yalnızca yolu tanımlar. Model tarafındaki `resolution_source` sözlüğündeki `local_path`'i yansıtır. |
| `unresolved` | Yoktur — asla uydurulmaz. | **Dürüst bir boşluk.** Bir sorgu denendi ve başarısız oldu ya da reddedildi. `hf_revision_reason` nedenini belirtir. |

Kural: `loaded` kanıttır; `unverified` bir ipucudur; `local_path` ve
`unresolved` dürüst boşluklardır — ve bir boşluk asla bir revision çıkarımı
yapmak için gerekçe değildir.

`unresolved` altında bir denetçinin göreceği nedenler:

| `hf_revision_reason` | Anlamı |
|---|---|
| `offline mode — no Hub lookup was attempted` | Koşum izole (air-gapped) idi (`model.offline: true` veya `HF_HUB_OFFLINE` / `HF_DATASETS_OFFLINE` / `TRANSFORMERS_OFFLINE`). Hiçbir şey sorulmadı. |
| `huggingface_hub is not installed` | Ortamda Hub istemcisi yok. |
| `<ExcType>: <message>` | Sorgu yapıldı ve hata fırlattı — Hub erişilemez, gated depo, transport hatası. |
| `HF Hub returned no commit SHA for this dataset` | Sorgu başarılı oldu ama hiç SHA taşımadı. |
| `HF Hub returned a non-commit revision for this dataset: <repr>` | Hub, 40-hex küçük-harf commit olmayan bir şeyle yanıt verdi — örn. `'main'` — ve bu kaydedilmek yerine **reddedildi**. Denetçilerin commit olarak okuduğu bir alandaki hareketli bir ref, tam olarak bu sözlüğün önlemek için var olduğu hatadır. |
| `path is neither a local file or directory nor a Hugging Face Hub dataset id` | Yanlış yazılmış veya başka türlü kullanılamaz bir yol. Onun adına hiçbir Hub isteği yapılmadı. |

**"Dosya değilse Hub'dır" varsayan tüketicilerin güncellenmesi gerekir.** Bu
sürümden önce dosya olmayan her yol — dizinler ve yazım hataları dâhil — bir
`dataset_id` ile `source: huggingface_hub` olarak etiketleniyordu ve
`hf_revision_source` yalnızca Hub dalında yazılıyordu, dolayısıyla yokluğu "eski
bir artefakt" ile "yerel bir korpus" arasında belirsizdi.

**Offline davranışı.** `model.offline: true` artık veri ve provenance yolundaki
tüm Hub trafiğini ortam yan-etkisiyle değil argüman geçirerek bastırır, yani bir
kütüphane tüketicisi CLI kullanıcısıyla aynı korumayı alır. Offline modda
dataset-metadata çekimi de atlanır, dolayısıyla `version`, `description` ve
`download_size_bytes` izole bir manifest'te bulunmaz.

**Temel model**, `compliance_report.json` içinde
`model_lineage.base_model_revision` altında:

| Anahtar | Anlamı |
|---|---|
| `repo_id` | `model.name_or_path` aynen. |
| `revision_requested` | `model.revision` aynen veya `null`. Çözülen SHA'nın yanında tutulur, böylece `main` veya `v1.0` gibi sembolik bir pin, bir commit yerine geçmek yerine açıkça hareketli bir ref olarak görünür. |
| `revision_resolved` | Teyit edilmiş 40-hex commit SHA'sı veya `null`. **Buradaki bir değer, o koşumdaki temel-model yüklemesinin ona sabitlendiği anlamına her zaman gelir.** Asla istenen dizenin geri yansıtılması değildir ve asla bağımsız bir Hub sorgusundan gelen bir SHA değildir. |
| `resolution_source` | `local_path` (diskteki bir dizin — Hub commit'i yok); `resolved` (pin istenmedi, Hub SHA'yı teyit etti); `pinned_resolved` (pin istendi ve teyit edildi); `cache` (SHA yerel commit-adresli HF önbelleğinden okundu); `pinned_unverified` (pin istendi, hiçbir şey teyit etmedi); `unresolved` (hiçbir şey belirlenemedi). |
| `revision_pinned` | `revision=` parametresine verilen tam dize. Bir SHA teyit edildiğinde `revision_resolved`'a eşittir; operatör hiçbir şeyin teyit edemediği bir ref'i sabitlediğinde `revision_requested`'a eşittir; yükleme sabitlenmemişse `null`'dur. |
| `reason` | **Yalnızca** o süreçte hiç temel-model yüklemesi olmadığında bulunur ve bunu sözle belirtir. `forgelm compliance-only` kanonik örnektir: modeli hiç yüklemeden paket yazar ve SHA uydurmak yerine bu neden ile `resolution_source: unresolved` raporlar. |

Manifest üretimi temel model için **kendi başına hiçbir Hub sorgusu
yapmaz** — bu tasarım gereğidir ve yeniden eklenirse düşen bir testle
korunur. Provenance yalnızca yükleme döndükten sonra yazılır; hata
fırlatan bir yükleme geriye hiçbir iddia bırakmaz.

**Sabitlenmiş diğer her rol**, `compliance_report.json` içinde
`model_lineage.component_revisions` altında — `base_model_revision`'ın
(değişmemiş ve hâlâ mevcut) kardeşi olan bir **liste**; `(role, repo_id)`'ye
göre sıralanır, böylece aynı modelleri farklı sırayla yükleyen koşumlar arasında
artefakt bayt düzeyinde kararlıdır:

| Anahtar | Anlamı |
|---|---|
| `role` | Asla değişmeyen altı sözleşme değerinden biri: `base_model`, `safety_classifier`, `llm_judge`, `grpo_reward_model`, `teacher_model`, `fit_check`. |
| `repo_id` | Yüklemenin adlandırdığı depo. Bir rol benzersiz değildir — iki rol meşru olarak aynı depoyu adlandırabilir (Llama-Guard hem sınıflandırıcı hem judge olarak) ve GRPO ikinci bir ödül modeline karşı yeniden koşturulabilir. |
| `revision_requested` | Operatörün literali veya `null`. |
| `revision_resolved` | Teyit edilmiş 40-hex commit SHA'sı veya `null`. **Asla istenen dizenin geri yansıtılması değildir.** |
| `resolution_source` | `base_model_revision` ile aynı sözlük: `local_path`, `resolved`, `pinned_resolved`, `cache`, `pinned_unverified`, `unresolved`. |
| `revision_pinned` | `revision=` parametresine verilen tam dize; bu **hareketli bir ref olabilir**. |

Hangi config alanı hangi rolü üretir: `model.revision` → `base_model` (bu rol
ayrıca kendi `base_model_revision` bloğunu da korur; aynı registry girdisinden
gelir, dolayısıyla ikisi asla çelişemez);
`evaluation.safety.classifier_revision` → `safety_classifier`;
`evaluation.llm_judge.judge_model_revision` → `llm_judge`;
`training.grpo_reward_model_revision` → `grpo_reward_model`;
`synthetic.teacher_revision` → `teacher_model`.

Bir denetçinin yapmaması gereken iki okuma:

- **`component_revisions: []`, "hiçbir pin yapılandırılmadı" anlamına
  gelmez.** O süreçte hiçbir sabitlenmiş yüklemenin tamamlanmadığı anlamına
  gelir — `forgelm compliance-only`, tamamı yerel yollardan oluşan bir config
  veya herhangi bir yüklemeden önce yazılmış bir manifest.
- **Null bir `revision_resolved`, koşumun sabitlenmemiş olduğu anlamına
  gelmez.** Hiçbir SHA'nın teyit edilemediği anlamına gelir; koşum yine de bir
  ref'e sabitlenmiş olabilir ve `revision_pinned` bunu aynen kaydeder.

Açıkça belirtilecek üç sınır:

- **Güvenlik sınıflandırıcısı** pin'i yalnızca eğitim zamanı kapısı için
  geçerlidir. Bağımsız `forgelm safety-eval` hiçbir `--config` almaz ve
  `--classifier-revision` bayrağı yoktur, dolayısıyla sınıflandırıcı yüklemesi
  sabitlenmemiştir ve depoyu adlandıran bir UNPINNED uyarısı yazar. O alt
  komuttan gelen bir karar sabitlenmiş kanıt değildir.
- **`fit_check` rolü ayrılmıştır ama hiçbir zaman yayılmaz.**
  `model.revision`, VRAM-tahmini `AutoConfig` problamasına *iletilir*,
  dolayısıyla o yükleme sabitlenmiştir, ama problama hiçbir provenance
  kaydetmez ve hiçbir zaman bir `fit_check` girdisi görünmez.
- Düzleştirilmiş **`training_manifest.yaml`** yan dosyası bunların
  hiçbirini taşımaz. O bir operatör özetidir (`base_model`,
  `adapter_method`, `trainer_type`, `dataset`, `epochs`, `final_metrics`)
  ve içinde hiç `model_lineage` veya `data_provenance` bloğu yoktur —
  provenance için `compliance_report.json`'u okuyun.

**Geriye dönük uyumluluk.** `component_revisions` tamamen eklemelidir.
`forgelm verify-annex-iv` üst düzey Annex IV bölümlerine bakar ve
`model_lineage`'i incelemez, dolayısıyla bu sürümden önce yazılmış artefaktlar
geçerli kalır ve sonrasında yazılanlar aynı şekilde doğrulanır. Yeni üretilen
bir artefakt, aynı koşumun değişiklik öncesi bir yapısının üreteceğinden
doğal olarak farklı bir `manifest_hash` taşır; arşivlenmiş bir artefakt kendi
iç tutarlı hash'ini korur.

Tam alan semantiği ve config yüzeyi:
[`configuration-tr.md`](configuration-tr.md#hub-revision-pinleme).

### Audit chain (Madde 12)

- Implementasyon: `forgelm.compliance.AuditLogger` —
  `<output_dir>/audit_log.jsonl`'da JSON Lines append-only log,
  `AuditLogger.__init__` içinde `SHA-256(FORGELM_AUDIT_SECRET ‖
  run_id)` ile türetilen per-run signing key ile HMAC-zincirli
  (`AuditLogger.log_event` writer'ı ve
  `forgelm.compliance.verify_audit_log` doğrulayıcısı aynı türetimi
  yansıtır). Per-output-dir salt'ı (`<output_dir>/.forgelm_audit_salt`)
  **ayrı bir primitif**'tir — `forgelm purge` / `forgelm reverse-pii`
  event'lerinde identifier hashing'i besler (`_purge._resolve_salt`)
  ve chain-key türetimine katılmaz. Genesis manifest sidecar
  (`audit_log.jsonl.manifest.json`) truncate-and-resume tahrifatını reddeder.
- Doğrulama: `forgelm verify-audit [--require-hmac]` zinciri uçtan
  uca doğrular; 0 (geçerli) veya 1 (herhangi bir hata — ayrıştırma
  hatası, HMAC uyuşmazlığı, manifest sapması, dosya bulunamadı,
  seçenek hatası) ile çıkar. Daha zengin 0/1/2/3 exit-code yüzeyi
  **trainer** giriş noktasına (`forgelm --config ...`) uygulanır,
  `verify-audit`'e değil.

### Madde 14 staging kapısı

- Implementasyon: `evaluation.require_human_approval: true` olduğunda
  eğitilmiş model `<output_dir>/final_model.staging.<run_id>/`'a iner
  ve trainer-olmayan bir operatörden `forgelm approve <run_id>
  --output-dir <output_dir>` bekler (pozisyonel `run_id`; `--run-id`
  bayrağı yoktur).
- Listeleme: `forgelm approvals --pending --output-dir <dir>` (Phase 37 — `--output-dir` zorunludur).
- Audit: `human_approval.required/granted/rejected` event'leri.

### GDPR Madde 15 + 17 (Wave 2b + Wave 3)

- Madde 17 silme: `forgelm purge --row-id <id> --corpus
  data/file.jsonl`, salted-hash audit ile (`data.erasure_*` event'leri).
- Madde 15 erişim: `forgelm reverse-pii --query <id> --type
  email|phone|... data/*.jsonl`, salted-hash audit ile
  (`data.access_request_query` event'i).

### ISO 27001 / SOC 2 Type II uyumluluğu (Wave 4)

- Design doc: [`../design/iso27001_soc2_alignment.md`](../design/iso27001_soc2_alignment.md)
  (~865 satır, tam 93-control coverage map).
- Deployer cookbook: [`../guides/iso_soc2_deployer_guide-tr.md`](../guides/iso_soc2_deployer_guide-tr.md).
- Referans tabloları: [`iso27001_control_mapping-tr.md`](iso27001_control_mapping-tr.md),
  [`soc2_trust_criteria_mapping-tr.md`](soc2_trust_criteria_mapping-tr.md).
- Supply chain: [`supply_chain_security-tr.md`](supply_chain_security-tr.md)
  — CycloneDX 1.5 SBOM, `pip-audit` gecelik, `bandit` CI.

## Boşluklar + operatör-tarafı kalan hususlar

ForgeLM, 93 ISO 27001 Annex A kontrolünün ~59'una teknik kanıt sağlar
(`FL` 11 + `FL-helps` 48; bkz. ISO control mapping doğrulu sayım); kalan
~34 deployer-tarafı (fiziksel güvenlik, HR süreçleri, ağ ayrıştırması
vs.). Deployer'ın ISMS duruşu için:

- **Encryption at rest** — ForgeLM şifreleme-substrate-agnostik; per-
  artefakt sınıfı substrate önerileri için bkz.
  [`../qms/encryption_at_rest-tr.md`](../qms/encryption_at_rest-tr.md).
- **Erişim kontrolü** — operatör kimliği sözleşmesi +
  `FORGELM_AUDIT_SECRET` rotasyon kadansı:
  [`../qms/access_control-tr.md`](../qms/access_control-tr.md).
- **Risk treatment** — 12-satırlı önceden doldurulmuş kayıt:
  [`../qms/risk_treatment_plan-tr.md`](../qms/risk_treatment_plan-tr.md).
- **Statement of Applicability** — 93-kontrol matrisi:
  [`../qms/statement_of_applicability-tr.md`](../qms/statement_of_applicability-tr.md).

## Önerilen benimseme sırası

1. `docs/qms/` SOP'larını benimse ([Model Training](../qms/sop_model_training-tr.md),
   [Data Management](../qms/sop_data_management-tr.md),
   [Incident Response](../qms/sop_incident_response-tr.md),
   [Change Management](../qms/sop_change_management-tr.md),
   [Roles & Responsibilities](../qms/roles_responsibilities-tr.md)).
2. `FORGELM_OPERATOR` + `FORGELM_AUDIT_SECRET`'ı şuna göre set'le:
   [`../qms/access_control-tr.md`](../qms/access_control-tr.md).
3. Her yüksek-riskli koşum için
   `evaluation.require_human_approval: true` konfigüre et.
4. Haftalık `forgelm verify-audit` cron zamanla.
5. Production eğitiminde `auto_revert: true` etkinleştir.
6. `audit_log.jsonl`'ı write-once storage'a gönder.
7. Tam ISO / SOC 2 uyumluluğu için
   [`../guides/iso_soc2_deployer_guide-tr.md`](../guides/iso_soc2_deployer_guide-tr.md)'i yürü.

## Kanıt konumları (symbol referansları — satır-stabil)

Wave 4 / Faz 26 temizliği: her link bir line anchor'a değil, dosya
köküne işaret eder. Denetçi dosyayı açar ve cited symbol adını grep'ler;
bu, önceki `#L33` formunun başaramadığı refactor'leri sağ atlatır.

- **Auto-revert + safety-eval kapısı**: `forgelm.trainer` (`_revert_model`,
  `auto_revert`, `_run_safety_eval` ara).
- **Güvenlik sınıflandırıcısı + 3-katman gate**: `forgelm.safety`
  (`LlamaGuardClassifier`, `_evaluate_3_layer_gate` ara).
- **Audit chain + HMAC + manifest**: `forgelm.compliance` (`AuditLogger`,
  `_check_genesis_manifest`, `generate_model_integrity` ara).
- **Salted identifier hashing**: `forgelm.cli.subcommands._purge`
  (`_resolve_salt`, `_read_persistent_salt`, `_hash_target_id` ara).
- **GDPR Madde 15 reverse-pii**: `forgelm.cli.subcommands._reverse_pii`.
- **Madde 14 staging + approve / reject**:
  `forgelm.cli.subcommands._approve`,
  `forgelm.cli.subcommands._reject`,
  `forgelm.cli.subcommands._approvals`.
- **Webhook lifecycle**: `forgelm.webhook` (`notify_start`,
  `notify_success`, `notify_failure`, `notify_reverted`,
  `notify_awaiting_approval` ara).
- **HTTP discipline**: `forgelm._http` (`safe_post`, `safe_get` ara).
- **Config validation**: `forgelm.config` (`_warn_high_risk_compliance`,
  `_validate_galore`, `_validate_distributed`).

## Bkz.

- [Audit event catalog](audit_event_catalog-tr.md) — tam event sözlüğü.
- [ISO 27001 control mapping](iso27001_control_mapping-tr.md) — Annex A × ForgeLM kanıtı.
- [SOC 2 Trust Services Criteria mapping](soc2_trust_criteria_mapping-tr.md) — TSC × ForgeLM kanıtı.
- [Supply chain security](supply_chain_security-tr.md) — SBOM + pip-audit + bandit.
- [QMS index](../qms/README-tr.md) — SOP şablonları.
- [GDPR erasure rehberi](../guides/gdpr_erasure-tr.md) — Madde 15 + 17 iş akışları.
- [Safety + uyumluluk rehberi](../guides/safety_compliance-tr.md) — operatör-yönlü how-to.
- [ISO / SOC 2 deployer rehberi](../guides/iso_soc2_deployer_guide-tr.md) — audit cookbook (Wave 4).
