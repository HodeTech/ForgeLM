# QMS: Erişim Kontrolü

> Kalite Yönetim Sistemi rehberi — [YOUR ORGANIZATION]
> ISO 27001:2022 referansları: A.5.15, A.5.16, A.5.17, A.5.18, A.8.2, A.8.5
> SOC 2 referansları: CC1.5, CC6.1, CC6.2, CC6.3, CC6.5, CC8.1

## 1. Amaç

Operatörün kimlikleri, credential'ları ve erişim haklarını ForgeLM'in
audit-trail attribution modeli etrafında nasıl organize ettiğini
tanımla.

**Çekirdek contract:** ForgeLM'in emit ettiği her olay bir
`FORGELM_OPERATOR` kimliğiyle damgalanır. Audit chain operatör
değerini issue eden identity-management substrate'i kadar güçlüdür.
Aynı `FORGELM_OPERATOR`'ı paylaşan iki operatör chain'de ayırt
edilemez — bu operatör-tarafı kimlik hijyen başarısızlığıdır,
ForgeLM defect değildir.

## 2. Kapsam

Aşağıdaki kimlik-taşıyan yüzeyler:

| Yüzey | Kimliği taşıyan |
|---|---|
| `FORGELM_OPERATOR` env var | Her audit-log girişine damgalanır |
| Approval gate (`forgelm approve` / `reject`) | Onay anında operatör id |
| Reverse-PII / purge subcommand'ları | Her Madde 15 / 17 olayında operatör id |
| Webhook lifecycle olayları | Operatör id webhook payload'a yayılır |
| CI runner identity | CI runner'ın service-account credential'ı `FORGELM_OPERATOR`'a bound operatörün "gerçek" kimliğidir |

ForgeLM kendisi RBAC, MFA veya directory federation uygulamaz —
bunlar operatörün IdP'sinde (Okta, Azure AD, Google Workspace,
Keycloak) yaşar. ForgeLM'in yaptığı her audit girişini operatörün
`FORGELM_OPERATOR`'a koyduğu string'e bağlamaktır, böylece IdP
audit log'u ve ForgeLM audit log'u çapraz-referanslanabilir.

## 3. Operatör kimliği contract'ı

### 3.1 Resolution sırası

ForgeLM operatör kimliğini audit-event emit anında resolve eder:

1. `FORGELM_OPERATOR` env var (tercih edilen — açık set).
2. `getpass.getuser() + '@' + socket.gethostname()` (POSIX kullanıcıadı@hostname fallback).
3. `FORGELM_ALLOW_ANONYMOUS_OPERATOR=1` kimlik olmadan emit'e izin
   verir (yalnız kısa-ömürlü test koşumları içindir; aksi takdirde
   ConfigError).

### 3.2 Gerekli form

Free-text human name yerine **machine-readable, namespaced
identifier** kullanın:

| Pattern | Örnek | Neden |
|---|---|---|
| `<environment>/<purpose>/<runner-id>` | `prod/training/gh-runner-42` | CI runner pool'a haritalanır |
| `<email>` | `alice@acme.example` | İnsan-driven approval için OK |
| `oidc:<issuer>:<subject>` | `oidc:gha:repo:Acme/forgelm-pipelines:ref:refs/heads/main` | OIDC-token-bound |

Anti-patterns:

- `root` / `cemil` / `admin` — belirsiz, rotate edilemez.
- Boş / `unknown` — audit attribution amacını bozar.

### 3.3 Personel değiştiğinde rotasyon

ForgeLM'in audit chain'i **immutable**'dir. Eski `FORGELM_OPERATOR`
ID'leri sonsuza kadar chain'de kalır. Bir operatör ayrıldığında:

1. **CI runner kimliklerini iptal et** (OIDC trust'ı rotate et /
   IAM rolünü sil).
2. **Aynı `FORGELM_OPERATOR` string'ini yeniden issue ETME** yeni bir
   kişiye — onlara yeni bir tanımlayıcı verin böylece gelecek
   girişler ayrılan-personel girişleriyle karıştırılmaz.
3. **Önceki eylemleri denetle** tanımlayıcılarına grep ederek; bu
   sizin sonlandırma-günü forensic kaydınızdır.

### 3.4 `FORGELM_AUDIT_SECRET` rotasyonu

HMAC-chain imzalama anahtarı **audit koşumu başına**
`AuditLogger.__init__` (`forgelm/compliance.py:164`) içinde `SHA-256(FORGELM_AUDIT_SECRET ‖ run_id)`
olarak türetilir (birleştirme; verifier
`forgelm.compliance.verify_audit_log` aynı türetimi byte-byte
yansıtır). Not:
`<output_dir>/.forgelm_audit_salt`'ta yazılı per-output-dir salt
**ayrı bir konu**'dur — `forgelm purge` / `forgelm reverse-pii`
event'lerindeki tanımlayıcı hash'lemesini salt'lar
(`_purge._resolve_salt` / `_purge._hash_target_id`) ve chain-key
türetimine KATILMAZ. Secret'ı Tier-1 credential olarak ele al:

- **Uzunluk:** 32+ rastgele bayt (256 bit entropi).
- **Substrate:** KMS / Vault / eşdeğer. VCS'e checked-in `.env` asla.
- **Rotasyon cadence'i:** **output-dir lifecycle'lar arasında** —
  her girişin HMAC'ı emit anındaki secret'a bağlı olduğundan,
  rotasyon mevcut `audit_log.jsonl` + `.manifest.json` çiftini
  arşivledikten SONRA yapılmalıdır. Output-dir ortasında rotasyon
  karışık-secret aralığı için `forgelm verify-audit --require-hmac`'i
  kırar (tasarım gereği — verifier'ın contract'ı "her girişin HMAC'ı
  aynı secret'ı anahtarlar"). Cadence'ı taze `<output_dir>`
  kestiğiniz sıklığa eşle (sürüm başına / çeyrek başına / proje
  başına) ve şüpheli compromise'da hemen rotate et + aynı anda yeni
  bir output-dir başlat.
- **Rotasyon prosedürü:** önceki `<output_dir>`'i write-once
  storage'a arşivle, KMS'de taze bir secret üret, sonraki pipeline
  koşumunu YENİ bir `<output_dir>`'e yönlendir. Önceki chain önceki
  secret ile doğrulanabilir kalır (audit-log integrity per-output-dir;
  ForgeLM chain üzerinde anahtar migration'u DESTEKLEMEZ —
  tasarımdan).

## 4. CI runner identity binding

ForgeLM'i CI'dan çalıştırırken önerilen pattern:

### 4.1 GitHub Actions

```yaml
jobs:
  train:
    runs-on: ubuntu-latest
    permissions:
      id-token: write   # OIDC
      contents: read
    env:
      FORGELM_OPERATOR: gha:${{ github.repository }}:${{ github.workflow }}:run-${{ github.run_id }}
      FORGELM_AUDIT_SECRET: ${{ secrets.FORGELM_AUDIT_SECRET }}
    steps:
      - uses: actions/checkout@v5
      - run: pip install forgelm
      - run: forgelm --config config.yaml
```

**Bu binding'in nedeni:** run id workflow yürütümü başına benzersizdir;
bir denetçi tek bir audit-log girişini saniyeler içinde GitHub Actions
run sayfasına korelate edebilir.

### 4.2 GitLab CI

```yaml
train:
  variables:
    FORGELM_OPERATOR: "gitlab:${CI_PROJECT_PATH}:${CI_PIPELINE_ID}:job-${CI_JOB_ID}"
  # FORGELM_AUDIT_SECRET'ı projenin "Settings → CI/CD → Variables"
  # panelinden, operatörün secret manager'ından (HashiCorp Vault, AWS
  # Secrets Manager vb.) beslenen *masked + protected* bir variable
  # olarak inject et; literal secret'ı asla .gitlab-ci.yml'a yapıştırma.
  script:
    - forgelm --config config.yaml
```

### 4.3 Jenkins

```groovy
pipeline {
  agent any
  environment {
    FORGELM_OPERATOR = "jenkins:${env.JOB_NAME}:build-${env.BUILD_ID}"
    FORGELM_AUDIT_SECRET = credentials('forgelm-audit-secret')
  }
  stages {
    stage('Train') { steps { sh 'forgelm --config config.yaml' } }
  }
}
```

## 5. OS-seviyesi izolasyon

Tek bir eğitim-host içinde, birden fazla pipeline output dizinini
PAYLAŞMAMALIDIR:

- Pipeline başına bir Unix kullanıcı (`forgelm-prod`,
  `forgelm-staging`, `forgelm-research`).
- Her pipeline'ın dizininde `chmod 0700 <output_dir>`.
- `<output_dir>`'deki audit log'ları dizinin perm'ini devralır;
  KMS substrate'iniz read access gerektirmiyorsa bireysel
  chmod yapma.
- `.forgelm_audit_salt` dosyası ForgeLM'in `_resolve_salt`'taki
  atomic O_EXCL creation'ı başına `0600` (sadece owner-read).

## 6. Approval gate identity ayrılığı

Madde 14 staging onaylayan operatörün eğiten operatörden farklı
olmasını gerektirir (görev ayrılığı — ISO A.5.3, SOC 2 CC1.5):

```bash
# Job 1 — eğitim (CI runner identity)
FORGELM_OPERATOR="gha:Acme/pipelines:training:run-42" \
    forgelm --config config.yaml

# Job 2 — onay (insan reviewer identity; positional run_id)
FORGELM_OPERATOR="alice@acme.example" \
    forgelm approve <run-id> --output-dir <output_dir>
```

ForgeLM iki kimliğin farklı olduğunu zorlamaz — bu operatör-tarafı
IdP kontrolüdür. Audit chain her ikisini kaydeder, böylece bir
denetçi ihlalleri tespit edebilir:

```bash
# 1. Önce zincir bütünlüğünü doğrula (positional log_path; bu
#    subcommand'ta tasarım gereği --output-dir / --json flag'i yoktur).
forgelm verify-audit ./outputs/audit_log.jsonl --require-hmac

# 2. Single-pass jq: audit log'u slurp et, trainer'ları + approval'ları
#    project et, in-memory join. Operatör tanımlayıcılarını paylaşılan
#    /tmp/ dosyasından uzak tutar (multi-tenant host'larda 0644 /
#    world-readable olur ve §3.2'ye göre operatörün
#    FORGELM_OPERATOR'a koymuş olabileceği email'leri sızdırır).
#    Çıktı: koşumun eğiteni ile aynı operatör tarafından onaylanmış
#    her approval'ın TSV satırı (görev ayrılığı ihlali).
jq -rs '
    (map(select(.event == "training.started"))) as $trainers |
    map(select(.event == "human_approval.granted"))[] |
    . as $a |
    $trainers[] |
    select(.run_id == $a.run_id and .operator == $a.operator) |
    [.run_id, .operator] | @tsv
' ./outputs/audit_log.jsonl
```

## 7. Webhook secret ayrılığı

Webhook URL'leri env'e ait, asla YAML'a değil:

```yaml
webhook:
  url_env: SLACK_WEBHOOK_URL   # env'den resolve
  timeout: 10
```

ForgeLM webhook gövdelerini HMAC ile **imzalamaz** — `WebhookConfig`
üzerinde `webhook.secret_env` alanı yoktur (bkz.
`forgelm/config.py::WebhookConfig`). Hedef-tarafı atıf (a) HTTPS +
`webhook.url_env` üzerinden URL-gizliliğine, (b) curated payload
içinde taşınan `FORGELM_OPERATOR` kimliğine, ve (c) alıcı sistemin
kendi bearer-token / imzalı-istek kontrollerine (Slack signing
secret, Teams connector token) düşer.

Webhook wire-format olay sözlüğü (5 olay): `training.start`,
`training.success`, `training.failure`, `training.reverted`,
`approval.required` — Phase 8. In-process notifier metot adları
`notify_*` öneki kullanır (`notify_start`, `notify_success`,
`notify_failure`, `notify_reverted`, `notify_awaiting_approval`)
ve bu beş wire olayına dispatch eder. Her payload `FORGELM_OPERATOR`
kimliğini taşır böylece alıcı sistem (Slack, Teams, custom
incident-management) bildirimi attribute edebilir.

## 8. Doğrulama checklist'i

Erişim-kontrolü kanıtını yürüyen operatör denetçi için:

- [ ] Her CI runner pipeline'ı `FORGELM_OPERATOR`'ı machine-readable
      bir namespace'den set ediyor.
- [ ] İki aktif pipeline aynı `FORGELM_OPERATOR` değerini paylaşmıyor.
- [ ] `FORGELM_AUDIT_SECRET` KMS / Vault substrate'inde yaşıyor,
      VCS'de veya düz `.env` dosyasında değil.
- [ ] KMS audit log'u `FORGELM_AUDIT_SECRET` rotasyonunu taze
      `<output_dir>` provisioning event'ı ile eşli gösteriyor — her
      KMS rotasyon event'ının aynı KMS-event zaman penceresi içinde
      karşılık gelen yeni bir `<output_dir>/audit_log.jsonl.manifest.json`
      genesis pin'i olmak zorunda. Eşleşen genesis pin'i olmayan
      rotasyonlar (yani output-dir-ortası rotasyonlar) cross-secret
      aralık için `forgelm verify-audit --require-hmac`'i kırar.
- [ ] Son 90 gündeki her `human_approval.granted` event'i için
      approval-gate kimliği training kimliğinden farklı (sample-audit,
      exhaustive değil).
- [ ] `<output_dir>` izin maskeleri: `0700` (dizin),
      `.forgelm_audit_salt` `0600`.
- [ ] Chain'de `FORGELM_OPERATOR=root` / `=admin` / `=unknown`
      eventi yok.
- [ ] Webhook URL'leri env-resolved (`url_env`); committed YAML'da
      plaintext URL yok. (ForgeLM webhook gövdelerini HMAC ile
      imzalamaz; bu yüzden `secret_env` alanı yok — hedef-taraf
      kontrollerini alıcı sistem yönetir.)

## 9. İnceleme

| Versiyon | Tarih | Yazar | Değişiklikler |
|---------|------|--------|---------|
| 1.0 | [DATE] | [AUTHOR] | İlk versiyon (Wave 4 / Faz 23) |
