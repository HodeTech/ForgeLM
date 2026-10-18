---
title: Webhook'lar
description: Eğitim olaylarında Slack biçiminde webhook bildirimleri — başlangıç, başarı, hata, otomatik geri alma. Teams ve Discord için relay gerekir.
---

# Webhook'lar

ForgeLM eğitim dönüm noktalarında yapılandırılmış webhook fırlatır. Bunları Slack'e ya da bu JSON payload'u kabul eden herhangi bir incident aracına veya relay'e bağlayın (Microsoft Teams ve Discord için relay gerekir; aşağıya bakın) — hiç kimsenin log izlemesine gerek kalmadan doğru bağlamı doğru insanlara getirin.

## Hızlı örnek

```yaml
webhook:
  url_env: "SLACK_WEBHOOK"           # URL'yi $SLACK_WEBHOOK'tan runtime'da okur
  notify_on_start: true              # default true
  notify_on_success: true            # default true
  notify_on_failure: true            # default true (training.failure + training.reverted'i kapsar)
```

Notifier, Slack tarzı bir `attachments` bloğu taşıyan tek bir generic JSON payload yayar.
Slack'in incoming webhook'ları bunu kabul eder; Microsoft Teams ve Discord etmez
(aşağıdaki "Slack / Teams / Discord ingest" bölümüne bakın). Per-event abonelik şu an konfigüre
edilemiyor; üç `notify_on_*` flag'iyle hangi yaşam-döngüsü olaylarının
fırlatılacağı kaba ayarlanır.

ForgeLM `${SLACK_WEBHOOK}`'u environment variable'dan okur. Yaygın pattern:

```shell
$ export SLACK_WEBHOOK="https://hooks.slack.com/services/T.../B.../..."
$ forgelm --config configs/run.yaml
```

## Wire-format event'ler

ForgeLM tam **sekiz** webhook event'i yayar: beş tek-aşamalı yaşam
döngüsü event'i ve çok-aşamalı orkestratörün bunların yanı sıra emit
ettiği üç `pipeline.*` event'i. Aşağıdaki tablo
[GitHub'daki Audit Event Kataloğu](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/audit_event_catalog.md)
ile aynalanan kanonik yüzeydir:

| Event | Ne zaman fırlar | Gate |
|---|---|---|
| `training.start` | `train()` başlar, model yüklenmeden önce. | `webhook.notify_on_start` |
| `training.success` | Tüm gate'ler geçer; insan-onay gereksinimi yok. | `webhook.notify_on_success` |
| `training.failure` | Eğitim raise ediyor (OOM, dataset hatası, yakalanmamış istisna). | `webhook.notify_on_failure` |
| `training.reverted` | Eğitim-sonrası bir gate (eval / safety / judge / benchmark) koşumu reddetti ve `_revert_model` adapter'ları geri aldı. | `webhook.notify_on_failure` |
| `approval.required` | Koşum başarılı, `evaluation.require_human_approval=true` set, model review için staged (EU AI Act Madde 14). | `webhook.notify_on_success` |
| `pipeline.started` | Çok-aşamalı bir pipeline koşusu başlar, herhangi bir aşama çalışmadan önce. | `webhook.notify_on_start` |
| `pipeline.completed` | Çok-aşamalı bir pipeline koşusu terminal durumuna ulaşır. | `webhook.notify_on_success` / `webhook.notify_on_failure` |
| `pipeline.stage_reverted` | Bir pipeline aşaması auto-revert olur, aşağı akış aşamaları skip işaretlenmeden önce. | `webhook.notify_on_failure` |

## Payload yapısı

Tek generic JSON şekil. Her payload `event`, `run_name`, `status`, `metrics`, `reason`,
`model_path` ve tek bir Slack tarzı blok (`title`, `text`, `color`) içeren bir `attachments`
dizisi taşır. ForgeLM provider'a-özel template'lerle sarmalama **yapmaz** ve üst düzeyde
`text`, `content` veya `embeds` alanı yoktur:

```json
{
  "event": "training.reverted",
  "run_name": "llama3-support-sft",
  "status": "reverted",
  "metrics": {},
  "reason": "Safety gate failed: unsafe_rate 0.08 exceeds max_unsafe_rate 0.05",
  "model_path": null,
  "attachments": [
    {
      "title": "Training Reverted: llama3-support-sft",
      "text": "Auto-revert fired. Generated artifacts were deleted because a post-training gate (evaluation, safety, judge, or benchmark) rejected the run.\n\nReason: Safety gate failed: unsafe_rate 0.08 exceeds max_unsafe_rate 0.05",
      "color": "#ff9900"
    }
  ]
}
```

`pipeline.*` event'leri birkaç alan ekler. Tam per-event alan listesi
[GitHub'daki Webhook Payload Şeması](https://github.com/HodeTech/ForgeLM/blob/main/docs/reference/webhook_schema.md)
sayfasındadır.

## Slack / Teams / Discord ingest

Yukarıdaki payload Slack tarzı bir gövdedir. `WebhookConfig`'te
**provider başına template yoktur** (no `template:`, no `events:`
allow-list, no `channel:` / `mention_on_failure:` formatlama knob'ları,
no per-destination fan-out array). Routing ve formatlama alıcı
tarafta yapılır:

- **Slack** — incoming webhook gövdeyi kabul eder ve `attachments`
  bloğunu (başlık, metin, renk) gösterir. Bu eski attachment biçimidir
  ve Slack onu ikincil sayar; daha zengin bir kart için webhook'u
  ForgeLM payload'unu Slack Block Kit'e çeviren bir relay'e (Slack
  workflow / AWS Lambda / kendi gateway'iniz) yönlendirin.
- **Microsoft Teams** — doğrudan kabul edilmez. Office 365 Connector'lar
  Mayıs 2026'da kapatıldı ve bir Teams Workflows (Power Automate)
  webhook'u Adaptive Card mesajı bekler; bu payload öyle değildir.
  Payload'u Adaptive Card'a saran bir relay çalıştırın.
- **Discord** — doğrudan kabul edilmez. Discord webhook'u gövdede
  `content` veya `embeds` (ya da dosya ya da anket) ister; ForgeLM bunların
  hiçbirini göndermez, bu yüzden Discord HTTP 400 döner. `attachments[0]`'ı
  `content`'e veya bir embed'e eşleyen bir relay çalıştırın.

Teslimatı reddeden bir alıcı run'ı başarısız etmez ve kimseye alarm gitmez:
ret, run log'unda bir `WARNING`'dir (bkz. *Sık hatalar*). Yeni bir hedefe güvenmeden
önce ona bir test event'i gönderin.

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
| `url_env` | `null` | URL'i taşıyan env var adı. Yalnızca `url` boşken okunur; ikisi de set ise `url` kazanır. |
| `notify_on_start` | `true` | `training.start` VE `pipeline.started`'ı gate'ler. |
| `notify_on_success` | `true` | `training.success`, `approval.required` VE başarılı bir `pipeline.completed`'ı gate'ler. |
| `notify_on_failure` | `true` | `training.failure`, `training.reverted`, `pipeline.stage_reverted` VE başarısız bir `pipeline.completed`'ı gate'ler. |
| `timeout` | `10` | HTTP timeout saniye; ≥ 1s'e clamp'lenir. |
| `allow_private_destinations` | `false` | RFC 1918 / loopback / link-local / RFC 6598 CGNAT (`100.64.0.0/10`) hedefler için opt-in (in-cluster Slack proxy, on-prem Teams gateway). Varsayılan reddeder — SSRF guard. |
| `require_https` | `false` | TLS-only zorlama. `true`, plaintext bir `http://` URL'ini reddeder (SSRF guard raise eder; POST atlanır), warn-then-send yerine. Varsayılan `false`, warn-then-send davranışını korur. |
| `tls_ca_bundle` | `null` | Özel CA bundle yolu (kurumsal MITM CA). Set edilmediğinde `certifi`'nin bundled store'u kullanılır. |

`template:`, `events: [...]`, `headers: {...}`, `retries:`,
`redact:`, `allow_private:`, `channel:` veya `mention_on_failure:`
alanı yoktur. Header injection, retry strategy, redaction (curated
payload zaten curated), ve routing hepsi ForgeLM'in dışında yaşar.

## Güvenlik

- **TLS şiddetle önerilir.** ForgeLM hem HTTPS hem HTTP webhook URL'lerine izin verir — HTTP hedefleri `Webhook URL uses HTTP (not HTTPS). Data will be sent unencrypted.` uyarısı loglar ama varsayılan olarak reddedilmez (bkz. `forgelm/webhook.py` `_send`). Regüle bir ortamda `webhook.require_https: true` ile plaintext bir `http://` URL'ini hard failure yapın (teslimat reddedilir, gönderilmez). Üretimde `https://` URL'leri pinleyin.
- **Curated payload.** ForgeLM webhook payload'larına asla raw eğitim verisi, tam config'ler veya unredacted PII koymaz. Notifier sabit-şekilli bir JSON sarar; `webhook.redact` toggle'ı yoktur çünkü kullanıcı-kontrollü redakte edilecek bir şey yok.
- **SSRF guard.** ForgeLM iç IP'lere işaret eden webhook URL'lerini engeller — RFC 1918, loopback, link-local (bulut IMDS `169.254.169.254` dahil), RFC 6598 Shared Address Space / Carrier-Grade-NAT (`100.64.0.0/10`; bu blok, Alibaba Cloud ECS'in IMDS'ini de kapsar — `100.100.100.200` — stdlib `ipaddress` predicate'leri bu bloğu kendiliğinden private veya reserved olarak işaretlemez), ayrıca reserved ve multicast hedefler — `webhook.allow_private_destinations: true` ile açıkça opt-in olmadıkça. Yanlış konfigüre koşuların iç ağınızı sondalamasını veya payload'ı bir bulut metadata servisine sızdırmasını önler.
- **HMAC body imzalama yok.** ForgeLM webhook gövdelerini imzalamaz — hedef-tarafı authenticity TLS + `url_env` üzerinden URL gizliliği artı alıcı sistemin bearer-token / signed-request kontrollerine (örneğin Slack signing secret) düşer.

## Sık hatalar

:::warn
**Webhook sessiz başarısız.** Webhook endpoint'inden gelen 4xx yanıtı eğitim koşusunu başarısız kılmaz — teslimat bilinçli olarak ölümcül değildir. **Teslimat hataları audit log'a yazılmaz**: `forgelm/webhook.py` her hatayı stderr'e bir `logger.warning` ile bildirir ve hiçbir audit olayı yaymaz. `webhook_failed` diye bir olay yoktur (bu sayfanın önceki sürümleri böyle bir olayı aramanızı söylüyordu; hiç var olmadı ve audit olay kataloğunda da bulunmuyor). Bunun yerine koşunuzun stderr çıktısında veya yakalanan CI log'larında `forgelm.webhook` kaynaklı `WARNING` satırlarını arayın.
:::

:::warn
**Per-epoch webhook beklemek.** ForgeLM per-epoch event yayınlamaz — yukarıda listelenen yalnızca sekiz event. Per-epoch progress gerekiyorsa, webhook fan-out beklemek yerine trainer'ın stdout'undan / `audit_log.jsonl`'den scrape edin.
:::

:::tip
**Canlıya geçmeden önce webhook'ları smoke-test edin.** ForgeLM `--webhook-test` flag'i göndermez. `--dry-run` *yalnızca* config validate eder ve trainer lifecycle'ını çalıştırmaz, dolayısıyla webhook'ları end-to-end test etmez. Doğru smoke-test seçenekleri: (a) küçük bir veri seti ve düşük `num_train_epochs` ile **gerçek bir küçük training koşumu** çalıştırın (lifecycle event'leri ateş eder); veya (b) hedefin doğru render ettiğini teyit etmek için curated bir sentetik payload'ı `curl` ile POST'layın.
:::

## Bkz.

- [CI/CD Hatları](#/operations/cicd) — webhook'ların doğal yuvası.
- [Otomatik Geri Alma](#/evaluation/auto-revert) — en eyleme dönük webhook'u üretir.
- [İnsan Gözetimi](#/compliance/human-oversight) — webhook-tabanlı onay akışı.
