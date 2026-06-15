---
title: Tedarik Zinciri
description: ForgeLM SBOM + pip-audit + bandit pipeline'ı için operatör yüzeyi — gecelik ne çalışır, tag başına ne çalışır, artefaktlar nereden alınır.
---

# Tedarik Zinciri

ForgeLM sürüm tag'i başına bir CycloneDX 1.5 SBOM yayınlar ve `pip-audit` + `bandit`'i `nightly.yml` workflow'u üzerinden gecelik çalıştırır (her sürüm-tag yayınında da yeniden koşar). Bu sayfa operatörün zihinsel modelidir: ne zaman ne çalışır, artefaktlar nereden alınır ve aynı kontroller lokalde nasıl yansıtılır.

## Ne zaman ne çalışır

| Tetikleyici | Araç | Sonuç | Hata politikası |
|---|---|---|---|
| Sürüm tag'i (`v*`) | `tools/generate_sbom.py` | (OS × Python-sürüm) hücresi başına bir CycloneDX 1.5 JSON, GitHub release'e iliştirilir | SBOM adımı pure-stdlib; yeşil sürüm matrisini sessizce bozamaz |
| Gecelik 03:00 UTC | `pip-audit` (`tools/check_pip_audit.py` üzerinden) | Kurulu bağımlılıklara karşı OSV / GHSA taraması | HIGH/CRITICAL → exit 1 + GitHub issue; MEDIUM → `::warning::`; LOW → sessiz |
| Gecelik 03:00 UTC + sürüm tag'i | `bandit` (`tools/check_bandit.py` üzerinden) | `forgelm/` üzerinde statik güvenlik taraması (`tests/` hariç) | HIGH → exit 1; MEDIUM → `::warning::`; LOW → sessiz |

## SBOM nereden alınır

```bash
# Bir sürüm için tüm SBOM'lar.
gh release download v0.7.0 --pattern 'sbom-*'

# Birini güzel-yazdır.
jq . sbom-ubuntu-latest-py3.11.json | less

# İki sürüm arası diff (eklenen/kaldırılan bağımlılıkları listeler).
diff <(jq -S '.components | sort_by(.purl)' sbom-prev.json) \
     <(jq -S '.components | sort_by(.purl)' sbom.json)

# CVE korelasyonu için Dependency-Track'a yükle.
curl -X POST -H "X-Api-Key: $DT_KEY" -H "Content-Type: application/octet-stream" \
    --data-binary @sbom-ubuntu-latest-py3.11.json \
    https://deptrack.example.com/api/v1/bom
```

SBOM determinizm-pinli — aynı Python ortamında ardışık iki yayın content-identical JSON üretir (CycloneDX semantiği gereği kasıtlı olarak değişen `serialNumber` ve `metadata.timestamp` hariç).

## Gecelik kontrolü lokalde yansıtın

Bir PR push'lamadan önce, ForgeLM'in gecelik dayattığı aynı kontrolü çalıştırın:

```bash
pip install 'forgelm[security]'
pip-audit --strict --format json --output /tmp/pip-audit.json
python3 tools/check_pip_audit.py /tmp/pip-audit.json
```

Exit 0, ForgeLM CI'nın uyguladığı aynı severity politikasının geçtiği anlamına gelir. Exit 1, gecelik ateşlenmeden önce bir HIGH/CRITICAL açığın ele alınması gerektiği anlamına gelir.

## Bandit kontrolünü lokalde yansıtın

```bash
pip install 'forgelm[security]'
bandit -r forgelm/ -f json -o /tmp/bandit.json
python3 tools/check_bandit.py /tmp/bandit.json
```

`tests/` hariçtir çünkü test fixture'ları meşru olarak güvensiz desenler kullanır (`assert`, dummy secret'lar). `forgelm/` altındaki production kod kapsamdır.

## Bir CVE kabul edildiğinde ama henüz düzeltilemediğinde

Upstream henüz düzeltmeyi yayınlamadıysa ve CVE'yi operatör-tarafı risk acceptance log'unuzda belgelediyseniz, bir YAML ignore dosyası yazıp `check_pip_audit.py`'ye opt-in `--ignores` flag'i üzerinden geçirin:

```yaml
# your_ignores.yaml
ignores:
  - id: CVE-2026-XXXX
    package: some-pkg
    reason: tek satırlık kısa özet
    threat_model: deployment yüzeyinizin etkilenen API'yi neden açığa çıkarmadığı
    verified_at: '2026-05-21'
    reevaluate_after: her quarter, ya da upstream fix gönderdiğinde
```

```bash
pip-audit --strict --format json --output /tmp/pip-audit.json
python3 tools/check_pip_audit.py /tmp/pip-audit.json --ignores your_ignores.yaml
```

Zorunlu alanlardan birinin (`id`, `package`, `reason`, `threat_model`, `verified_at`, `reevaluate_after`) eksikliği — ya da bir alanın hatalı değer taşıması (boş string, `YYYY-MM-DD` olmayan `verified_at`, ya da string listesi olmayan `aliases`) — gate'in kapalı fail etmesine yol açar; böylece dokümante edilmemiş bir suppression sessizce inemez. Her eşleşme run summary'de `::notice::` annotation olarak loglanır.

ForgeLM **varsayılan proje-seviyesi bir ignore listesi yayınlamaz**. Projenin kendi nightly'si check-in edilmiş bir `tools/pip_audit_ignores.yaml` taşır (proje-içi triage için), ama `check_pip_audit.py` `--ignores` olmadan hiçbir ignore okumaz; bu yüzden tool'u standalone çalıştıran deployer'lar hiçbir şey miras almaz. Her operatör-tarafı suppression kendi risk acceptance log'unuzda dokümante edilir ve quarterly-review yapılır.

## Daha fazla okumak için nereye

- Tam referans (severity politikası, suppression syntax'ı, ilgili ISO/SOC 2 kontrolleri):
  [`supply_chain_security-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/supply_chain_security-tr.md) (GitHub kaynağı).
- Operatör denetim cookbook'u (Q4 SBOM'u yürür, Q5 erişim kontrollerini yürür, Q7 olay müdahalesini yürür):
  [`iso_soc2_deployer_guide-tr.md`](https://github.com/HodeTech/ForgeLM/blob/main/docs/guides/iso_soc2_deployer_guide-tr.md) (GitHub kaynağı).
- SBOM emitter kaynağı (pure stdlib, sıfır dep):
  [`tools/generate_sbom.py`](https://github.com/HodeTech/ForgeLM/blob/main/tools/generate_sbom.py) (GitHub kaynağı).

## Ayrıca bakınız

- [ISO 27001 / SOC 2 Operatörü](#/operations/iso-soc2-deployer) — denetim katı cookbook girişi.
- [CI/CD Pipeline'ları](#/operations/cicd) — gecelik + PR-başına kontrollerin indiği yer.
- [Air-gap Ön-cache](#/operations/air-gap) — SBOM determinizmi için bağımlılıkları ön-cache'leme.
