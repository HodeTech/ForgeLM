# Supply-chain güvenliği — SBOM + zafiyet taraması

> ForgeLM'in supply-chain güvenlik tooling'i için referans doküman:
> SBOM üretimi (Wave 2 dönemi), pip-audit nightly (Wave 4 / Faz 23),
> bandit static analysis (Wave 4 / Faz 23). Hedef kitle: operatör
> compliance ekibi + denetçi.

## Software Bill of Materials (SBOM)

### Format

ForgeLM **CycloneDX 1.5 JSON** emit eder. Sebepler:

- ISO 27001 / SOC 2 denetçileri CycloneDX veya SPDX'i kabul eder;
  open-source dependency-scanning ekosistemi (Dependency-Track ≥
  4.10, OWASP Dep-Check, Snyk Open Source) CycloneDX 1.5'i doğal
  olarak içe alır.
- ForgeLM emitter'ı (`tools/generate_sbom.py`) saf-stdlib + sıfır
  external bağımlılık — SBOM adımı aksi takdirde yeşil bir release
  matrisini sessizce bozamaz.
- SPDX'e dönüşüm, belirli bir operatörün SPDX gerektirmesi durumunda
  tek satırlık bir `cyclonedx-py` çağrısıdır.

### Üretim

Emitter her release tag'inde (`v*` pattern) publish workflow'u
içinde çalışır:

```yaml
# .github/workflows/publish.yml (excerpt)
- name: Generate SBOM (CycloneDX 1.5)
  shell: bash
  run: python tools/generate_sbom.py > sbom-${{ matrix.os }}-py${{ matrix.python }}.json

- uses: actions/upload-artifact@v5
  with:
    name: sbom-${{ matrix.os }}-py${{ matrix.python }}
    path: sbom-${{ matrix.os }}-py${{ matrix.python }}.json
```

Cross-OS test matrisinin her (OS × Python-version) hücresi için bir
SBOM. Artefaktlar GitHub release sayfasından indirilebilir.

### Determinism contract

Wave 4 / Faz 23 bir determinism testi ekler
(`tests/test_supply_chain_security.py`):

- Aynı Python ortamında iki ardışık çağrı içerik-eşit SBOM'lar
  üretmek zorundadır (CycloneDX semantiği gereği kasıtlı olarak
  varyant olan `serialNumber` ve `metadata.timestamp` modulo).
- `tests/test_supply_chain_security.py::TestGenerateSbomDeterministic::test_two_invocations_produce_same_content`
  CI'da bunu pin'ler.

Bir denetçi karşılık gelen `git tag`'den SBOM'u yeniden emit edip
GitHub release'e ekli artefaktla diff alabilir; trivial olmayan
herhangi bir fark build ortamında dependency-resolution drift'ine
işaret eder.

### SBOM'u tüketme

```bash
# Bir release için tüm SBOM'ları indir. v<X.Y.Z>'yi CI'da pinlediğiniz
# tag ile değiştirin; SBOM artefakt formatı release'ler arasında stabildir.
gh release download v<X.Y.Z> --pattern 'sbom-*'

# İnsan inceleme için pretty-print.
jq . sbom-ubuntu-latest-py3.11.json | less

# Her bağımlılık adı + sürümünü listele.
jq -r '.components[] | "\(.name) \(.version)"' sbom-ubuntu-latest-py3.11.json

# İki release arasında diff.
diff <(jq -S '.components | sort_by(.purl)' sbom-prev.json) \
     <(jq -S '.components | sort_by(.purl)' sbom.json)

# Dependency-Track'e ingest.
curl -X POST -H "X-Api-Key: $DT_KEY" -H "Content-Type: application/octet-stream" \
    --data-binary @sbom-ubuntu-latest-py3.11.json \
    https://deptrack.example.com/api/v1/bom
```

## pip-audit (transitive CVE taraması)

Wave 4 / Faz 23 nightly workflow'a `pip-audit` ekler. Davranış:

- `closure/wave3-integration`-türetilmiş development branch + main'de
  her gün 03:00 UTC'de çalışır.
- `tools/check_pip_audit.py` üzerinden severity politikası:
  - **HIGH / CRITICAL** → exit 1 (nightly fail eder; mevcut
    notify-failure job'u üzerinden GitHub issue açar).
  - **MEDIUM / MODERATE** → `::warning::` annotation; nightly yeşil
    kalır.
  - **LOW** → sessiz.
  - **UNKNOWN** → exit 1 (run'ı fail eder, bulgu başına bir `::error::`).
    pip-audit'in JSON'u OSV severity taşımadığı için gerçek bulguların
    neredeyse tamamı buraya düşer — fail-closed davranış, eksik bir
    severity alanının gate'i sessizce atlamasına izin vermek yerine
    operatörü açık triyaja zorlar. Belirli bir CVE'yi kabul etmek için
    opt-in ignore dosyasıyla dokümante edin (aşağıdaki Suppression'a
    bakın); yeşil kalmak için UNKNOWN kovasına güvenmeyin.
- OSV / GHSA veritabanlarını kullanır (pip-audit varsayılanı).

Operatörler aynı tooling'i lokalde kurar:

```bash
pip install forgelm[security]
pip-audit --strict --format json --output /tmp/pip-audit.json
python3 tools/check_pip_audit.py /tmp/pip-audit.json
```

### Suppression (kasıtlı CVE kabulü)

ForgeLM varsayılan bir proje-seviyesi ignore listesi göndermez —
`python3 tools/check_pip_audit.py /tmp/pip-audit.json`'u standalone
çalıştıran bir deployer hiçbir suppression miras almaz ve tam
filtresiz gate'i görür. Deployer-tarafı kabulleri kendi risk
acceptance log'unuzda dokümante edin ve opt-in flag ile geçin:

```bash
python3 tools/check_pip_audit.py /tmp/pip-audit.json \
    --ignores path/to/your_ignores.yaml
```

YAML dosyasındaki her giriş `id`, `package`, `reason`, `threat_model`,
`verified_at`, ve `reevaluate_after` taşımalıdır (opsiyonel: `aliases`,
`references`); herhangi bir zorunlu alanın eksikliği — ya da bir alanın
hatalı değer taşıması (boş string, `YYYY-MM-DD` olmayan bir
`verified_at`, ya da string listesi olmayan `aliases`) — gate'in
kapalı fail etmesine yol açar; böylece dokümante edilmemiş bir
suppression sessizce inemez. Her eşleşme run summary'de `::notice::`
annotation olarak loglanır; audit trail görünür kalır.

ForgeLM'in kendi nightly'si proje-içi triage için check-in edilmiş
bir [`tools/pip_audit_ignores.yaml`](../../tools/pip_audit_ignores.yaml)
taşır (şu an dokuz no-fix torch advisory'si ve bir
OSV-yanlış-sınıflandırılmış markdown kaydı; daha önce taşıdığı iki
transformers advisory'si `transformers>=5.3.0` floor'u onları pip-audit
tarafından raporlanamaz hâle getirince kaldırıldı). Bu dosya yalnız projenin kendi workflow'u tarafından
`--ignores` ile tüketilir; deployer'lar miras almaz. Her release
cycle'da gözden geçirilir.

## bandit (static security analysis)

Wave 4 / Faz 23 `bandit`'i şuralara ekler:

- `.github/workflows/ci.yml` — main'e her PR + push.
- `.github/workflows/nightly.yml` — günlük 03:00 UTC.

Scope: `forgelm/` (yalnız üretim kodu). `tests/` hariç tutulur çünkü
test fixture'ları meşru olarak güvensiz pattern kullanır (`assert`,
dummy secrets, test girdilerinde `pickle.loads`).

`tools/check_bandit.py` üzerinden severity politikası:

- **HIGH** → exit 1 (CI / nightly fail eder).
- **MEDIUM** → `::warning::` annotation; CI / nightly yeşil kalır.
- **LOW / UNDEFINED** → sessiz.

`pyproject.toml`'daki yapılandırma:

```toml
[tool.bandit]
exclude_dirs = ["tests", ".venv", "build", "dist"]
skips = ["B101"]  # assert_used — tests/ hariç tutulduğunda anlamsız
```

### Suppression syntax

Üretim kodunda dokümante edilmiş bir gerekçe ile meşru olarak yangın
eden belirli bir bandit kuralı için:

```python
import subprocess
# nosec B603 B607 — args literal, operatör-kontrollü değil
result = subprocess.run([sys.executable, "-m", "pip", "list"], ...)
```

Format: `# nosec <RULE>[ <RULE2>]` ardından açıklama. Bandit satırı
yok sayar; `tools/check_bandit.py` suppression'ın trailing prose ile
gerekçelendirildiğini teyit eder.

## Opsiyonel `[security]` extra

`pyproject.toml` security tooling'i opt-in extra olarak sunar:

```toml
[project.optional-dependencies]
security = [
  "pip-audit>=2.7.0,<3.0.0",
  "bandit[toml]>=1.7.0,<2.0.0",
]
```

Operatörler şöyle kurar:

```bash
pip install forgelm[security]
```

Bu, ForgeLM'in nightly + CI workflow'larının uyguladığı aynı
kontrolleri PR push'undan önce lokalde çalıştırılabilir hale
getirmek için `pip-audit` ve `bandit`'i çeker.

## İlgili kontroller

| Standart | Kontrol | Eşleşme |
|---|---|---|
| ISO 27001:2022 | A.5.21 | ICT tedarik zincirinde bilgi güvenliği yönetimi |
| ISO 27001:2022 | A.8.8 | Teknik zafiyetlerin yönetimi |
| SOC 2 | CC7.1 | Zafiyetleri tespit eder |
| SOC 2 | CC8.1 | Değişimleri yetkilendirir (son release ile SBOM diff) |
| SOC 2 | CC9.2 | Tedarikçi + iş ortağı riskini yönetir |
| EU AI Act | Md. 9 risk yönetimi | Supply-chain riski genel risk register'ın parçası |

## Bkz.

- [`../guides/iso_soc2_deployer_guide-tr.md`](../guides/iso_soc2_deployer_guide-tr.md) — operatör denetim cookbook'u.
- [`iso27001_control_mapping-tr.md`](iso27001_control_mapping-tr.md) — ISO 27001 Annex A kontrolleri × ForgeLM kanıtı.
- [`soc2_trust_criteria_mapping-tr.md`](soc2_trust_criteria_mapping-tr.md) — SOC 2 Trust Services Criteria × ForgeLM kanıtı.
- [`../qms/risk_treatment_plan-tr.md`](../qms/risk_treatment_plan-tr.md) — pre-populated risk register.
- [`../qms/sop_change_management-tr.md`](../qms/sop_change_management-tr.md) — change management runbook.
- [`../qms/sop_incident_response-tr.md`](../qms/sop_incident_response-tr.md) — incident response runbook.
- `tools/generate_sbom.py` — CycloneDX 1.5 emitter.
- `tools/check_pip_audit.py` — pip-audit severity gate.
- `tools/check_bandit.py` — bandit severity gate.
