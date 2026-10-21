'use strict';

// A page fills a `data-i18n` element with textContent, so markup or an HTML
// entity in its string shows literally: "<code>safety_trend.jsonl</code>",
// "monitoring &amp; logging". Strings with markup belong in `data-i18n-html`.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { root, loadWizard } = require('./wizard_harness.cjs');

test('a string a page shows as plain text holds no markup or HTML entity', () => {
  const { translations } = loadWizard();
  const site = path.join(root, 'site');
  const plain = new Map();
  for (const page of fs.readdirSync(site).filter(name => name.endsWith('.html'))) {
    for (const match of fs.readFileSync(path.join(site, page), 'utf8').matchAll(/data-i18n="([^"]+)"/g)) {
      plain.set(match[1], page);
    }
  }
  assert.ok(plain.size > 100, `${plain.size} plain-text keys`);
  const literal = [];
  for (const [locale, table] of Object.entries(translations)) {
    for (const [key, page] of plain) {
      if (/&(?:[a-z]+|#\d+);|<\/?[a-z]/i.test(table[key] || '')) literal.push(`${page} ${key} (${locale})`);
    }
  }
  assert.deepEqual(literal, []);
});

// Site copy that promised more than the code does (the Wave 0 triage). The English and Turkish tiers are matched
// by phrase; the other four locales are checked for a token every translation of the corrected string keeps.
test('claims the code does not back stay out of the site copy', () => {
  const { translations } = loadWizard();
  const site = path.join(root, 'site');
  const retired = {
    en: [/dual-control/i, /\bauto-deletes\b/, /run fails \(exit 3\)/, /satisfying the access-request/,
      /Slack, Teams, Discord, or any HTTP/, /Slack \/ Teams \/ Discord/, /enforced upstream/, /GDPR-compliant TTLs/,
      /SHA-256 \+ size manifest/, /6 quant levels and SHA-256 manifest/, /ZeRO-2 \/ ZeRO-3 presets/,
      /PII redacted at ingest/, /GGUF export at q4_k_m/, /wired into the audit log/, /Article 17 erasure with/, /matches EU AI Act guidance/,
      /rejected models in final_model\.staging/, /forensically linked/, /every row of every split/,
      /surface every row/],
    tr: [/ikili[- ]kontrol/i, /otomatik siler/, /GDPR uyumlu TTL/, /erişim-talebi tarafını karşılar/,
      /Slack, Teams, Discord veya/, /Slack \/ Teams \/ Discord/, /upstream'de uygulanır/, /her ikisi de audit log'a bağlı/,
      /EU AI Act rehberiyle uyumlu/, /reddedilen modeller ne kadar tutulsun/, /adli olarak bağlanır/,
      /geçtiği her split/, /yüzeye çıkarır/],
  };
  const found = [];
  for (const [locale, patterns] of Object.entries(retired)) {
    for (const [key, value] of Object.entries(translations[locale])) {
      for (const pattern of patterns) if (pattern.test(value)) found.push(`${locale} ${key}: ${pattern}`);
    }
  }
  for (const page of fs.readdirSync(site).filter(name => name.endsWith('.html'))) {
    const html = fs.readFileSync(path.join(site, page), 'utf8');
    for (const pattern of retired.en) if (pattern.test(html)) found.push(`${page}: ${pattern}`);
  }
  assert.deepEqual(found, []);
  for (const [locale, table] of Object.entries(translations)) {
    assert.match(table['wizard.retention.enforce.hint'], /--check-policy/, `${locale}: retention is only reported`);
    for (const key of ['wizard.retention.audit_log.hint', 'wizard.retention.staging.hint']) {
      assert.match(table[key], /--check-policy/, `${locale} ${key}: a horizon is only reported, not enforced`);
    }
    assert.doesNotMatch(table['wizard.retention.audit_log.hint'], /EU AI Act/, `${locale}: the 5-year default is not a legal period`);
  }
});

// The home-page GDPR card says what the two commands are for (an input to an Article 15 response, one step of an
// Article 17 erasure) and that trained weights are untouched. Every locale keeps the weights caveat.
test('the home GDPR card keeps the trained-weights caveat in every locale', () => {
  const { translations } = loadWizard();
  const caveat = { en: /trained weights are not changed/, tr: /eğitilmiş ağırlıklar değişmez/,
    de: /Gewichte bleiben unverändert/, fr: /poids entraînés ne changent pas/, es: /pesos entrenados no cambian/,
    zh: /权重不会改变/ };
  for (const [locale, pattern] of Object.entries(caveat)) {
    assert.match(translations[locale]['home.cap.gdpr.body'], pattern, `${locale}: home.cap.gdpr.body`);
  }
  const weights = { en: /weights/, tr: /ağırlıklar/, de: /Gewichte/, fr: /poids/, es: /pesos/, zh: /权重/ };
  for (const [locale, pattern] of Object.entries(weights)) {
    assert.match(translations[locale]['compliance.gdpr17.body'], pattern, `${locale}: purge does not change trained weights`);
  }
  const html = fs.readFileSync(path.join(root, 'site', 'index.html'), 'utf8');
  const fallback = html.match(/data-i18n-html="home\.cap\.gdpr\.body">([^<]*(?:<code>[^<]*<\/code>[^<]*)*)<\/p>/);
  assert.ok(fallback, 'index.html keeps a static fallback for the card');
  assert.equal(fallback[1], translations.en['home.cap.gdpr.body'], 'the static fallback equals the English string');
});
