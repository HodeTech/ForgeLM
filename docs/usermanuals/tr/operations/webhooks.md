---
title: Webhook'lar
description: Eğitim olaylarında Slack ve Teams bildirimleri — başlangıç, başarı, hata, otomatik geri alma.
---

# Webhook'lar

ForgeLM eğitim dönüm noktalarında yapılandırılmış webhook fırlatır. Bunları Slack, Teams veya JSON payload kabul eden herhangi bir incident aracına bağlayın — hiç kimsenin log izlemesine gerek kalmadan doğru bağlamı doğru insanlara getirin.

## Hızlı örnek

```yaml
webhook:
  url_env: "SLACK_WEBHOOK"           # URL'yi $SLACK_WEBHOOK'tan runtime'da okur
  notify_on_start: true              # default true
  notify_on_success: true            # default true
  notify_on_failure: true            # default true (training.failure + training.reverted'i kapsar)
```

Notifier generic JSON payload yayar — Slack ve Teams bunu incoming-webhook
uçlarından doğrudan kabul eder. Per-event abonelik şu an konfigüre
edilemiyor; üç `notify_on_*` flag'iyle hangi yaşam-döngüsü olaylarının
fırlatılacağı kaba ayarlanır.

ForgeLM `${SLACK_WEBHOOK}`'u environment variable'dan okur. Yaygın pattern:

```shell
$ export SLACK_WEBHOOK="https://hooks.slack.com/services/T.../B.../..."
$ forgelm --config configs/run.yaml
```

## Wire-format event'ler

ForgeLM tam **beş** webhook event'i yayar. Aşağıdaki tablo
[GitHub'daki Audit Event Kataloğu](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md)
ile aynalanan kanonik yüzeydir:

| Event | Ne zaman fırlar | Gate |
|---|---|---|
| `training.start` | `train()` başlar, model yüklenmeden önce. | `webhook.notify_on_start` |
| `training.success` | Tüm gate'ler geçer; insan-onay gereksinimi yok. | `webhook.notify_on_success` |
| `training.failure` | Eğitim raise ediyor (OOM, dataset hatası, yakalanmamış istisna). | `webhook.notify_on_failure` |
| `training.reverted` | Eğitim-sonrası bir gate (eval / safety / judge / benchmark) koşumu reddetti ve `_revert_model` adapter'ları geri aldı. | `webhook.notify_on_failure` |
| `approval.required` | Koşum başarılı, `evaluation.require_human_approval=true` set, model review için staged (EU AI Act Madde 14). | `webhook.notify_on_success` |

## Payload yapısı

Tek generic JSON şekil — Slack / Teams / Discord hepsi bunu incoming-webhook
uçlarından doğrudan kabul eder; ForgeLM provider'a-özel template'lerle
sarmalama **yapmaz**:

```json
{
  "event": "training.reverted",
  "run_name": "customer-support-v1.2.0",
  "status": "reverted",
  "reason": "safety regression: S5 hate-speech +0.18 over baseline"
}
```

Payload anahtarları event'e göre değişir; tam per-event alan listesi
[GitHub'daki Audit Event Kataloğu](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md)
*Webhook lifecycle events* tablosundadır.

## Slack / Teams / Discord ingest

Yukarıdaki tek generic JSON payload, Slack, Teams ve Discord'un
incoming-webhook endpoint'lerinde beklediği şeydir. `WebhookConfig`'te
**provider başına template yoktur** (no `template:`, no `events:`
allow-list, no `channel:` / `mention_on_failure:` formatlama knob'ları,
no per-destination fan-out array). Routing ve formatlama alıcı
tarafta yapılır:

- **Slack** — payload'u Slack workflow veya incoming-webhook
  uygulamasına yapıştırın; Slack JSON'un top-level alanlarını render
  eder. Daha zengin formatlanmış bir kart için, webhook'u ForgeLM
  payload'unu Slack Block Kit'e çeviren bir relay'e (Slack
  workflow / AWS Lambda / kendi gateway'iniz) yönlendirin.
- **Microsoft Teams** — benzer pattern. Teams gelen JSON'u natively
  render eder ama görsel düz; MessageCard / Adaptive Card
  formatlama için bir relay çalıştırın.
- **Discord** — JSON'u doğrudan bot/webhook URL üzerinden kabul eder.

Birden çok hedef için, her biri kendi `webhook.url_env`'ine
pinlenmiş birden çok ayrı ForgeLM training config'i çalıştırın
veya tek bir ForgeLM webhook'undan relay katmanında birden çok
downstream araca fan-out yapın. ForgeLM natively bir
`webhooks: [...]` array'ini desteklemez.

## Cross-cutting webhook alanları

Gerçek `WebhookConfig` (bkz. `forgelm/config.py::WebhookConfig`):

| Alan | Vars. | Notlar |
|---|---|---|
| `url` | `null` | Inline URL — secret hijyeni için `url_env`'i tercih edin. |
| `url_env` | `null` | URL'i taşıyan env var adı. Set edildiğinde `url`'i override eder. |
| `notify_on_start` | `true` | `training.start` olayını gate'ler. |
| `notify_on_success` | `true` | `training.success` VE `approval.required`'ı gate'ler. |
| `notify_on_failure` | `true` | `training.failure` VE `training.reverted`'ı gate'ler. |
| `timeout` | `10` | HTTP timeout saniye; ≥ 1s'e clamp'lenir. |
| `allow_private_destinations` | `false` | RFC 1918 / loopback / link-local hedefler için opt-in (in-cluster Slack proxy, on-prem Teams gateway). Varsayılan reddeder — SSRF guard. |
| `tls_ca_bundle` | `null` | Özel CA bundle yolu (kurumsal MITM CA). Set edilmediğinde `certifi`'nin bundled store'u kullanılır. |

`template:`, `events: [...]`, `headers: {...}`, `retries:`,
`redact:`, `allow_private:`, `channel:` veya `mention_on_failure:`
alanı yoktur. Header injection, retry strategy, redaction (curated
payload zaten curated), ve routing hepsi ForgeLM'in dışında yaşar.

## Güvenlik

- **TLS şiddetle önerilir.** ForgeLM hem HTTPS hem HTTP webhook URL'lerine izin verir — HTTP hedefleri `Webhook URL uses HTTP (not HTTPS). Data will be sent unencrypted.` uyarısı loglar ama reddedilmez (bkz. `forgelm/webhook.py` `_send`). Üretimde `https://` URL'leri pinleyin.
- **Curated payload.** ForgeLM webhook payload'larına asla raw eğitim verisi, tam config'ler veya unredacted PII koymaz. Notifier sabit-şekilli bir JSON sarar; `webhook.redact` toggle'ı yoktur çünkü kullanıcı-kontrollü redakte edilecek bir şey yok.
- **SSRF guard.** ForgeLM iç IP'lere (RFC 1918, loopback, link-local, 169.254.x) işaret eden webhook URL'lerini engeller; `webhook.allow_private_destinations: true` ile açıkça opt-in olmadıkça. Yanlış konfigüre koşuların iç ağınızı sondalamasını önler.
- **HMAC body imzalama yok.** ForgeLM webhook gövdelerini imzalamaz — hedef-tarafı authenticity TLS + `url_env` üzerinden URL gizliliği artı alıcı sistemin bearer-token / signed-request kontrollerine (Slack signing secret, Teams connector token) düşer.

## Sık hatalar

:::warn
**Webhook sessiz başarısız.** Webhook endpoint'inden 4xx yanıtı eğitimi başarısız etmemeli ama ForgeLM hatayı sessizce yutmamalı. `audit_log.jsonl`'da `webhook_failed` olaylarına bakın; endpoint'inizin neden reddettiğini araştırın.
:::

:::warn
**Per-epoch webhook beklemek.** ForgeLM per-epoch event yayınlamaz — yukarıda listelenen yalnızca beş lifecycle event'i. Per-epoch progress gerekiyorsa, webhook fan-out beklemek yerine trainer'ın stdout'undan / `audit_log.jsonl`'den scrape edin.
:::

:::tip
**Canlıya geçmeden önce webhook'ları smoke-test edin.** ForgeLM `--webhook-test` flag'i göndermez. `--dry-run` *yalnızca* config validate eder ve trainer lifecycle'ını çalıştırmaz, dolayısıyla webhook'ları end-to-end test etmez. Doğru smoke-test seçenekleri: (a) küçük bir veri seti ve düşük `num_train_epochs` ile **gerçek bir küçük training koşumu** çalıştırın (lifecycle event'leri ateş eder); veya (b) hedefin doğru render ettiğini teyit etmek için curated bir sentetik payload'ı `curl` ile POST'layın.
:::

## Bkz.

- [CI/CD Hatları](#/operations/cicd) — webhook'ların doğal yuvası.
- [Otomatik Geri Alma](#/evaluation/auto-revert) — en eyleme dönük webhook'u üretir.
- [İnsan Gözetimi](#/compliance/human-oversight) — webhook-tabanlı onay akışı.
