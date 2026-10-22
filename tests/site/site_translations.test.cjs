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
      /surface every row/, /drops a data-subject's rows/, /reports every row where they appear/, /\(Article 15 access\)/,
      /\(Article 17 erasure\)/, /deletes those rows/],
    tr: [/ikili[- ]kontrol/i, /otomatik siler/, /GDPR uyumlu TTL/, /erişim-talebi tarafını karşılar/,
      /Slack, Teams, Discord veya/, /Slack \/ Teams \/ Discord/, /upstream'de uygulanır/, /her ikisi de audit log'a bağlı/,
      /EU AI Act rehberiyle uyumlu/, /reddedilen modeller ne kadar tutulsun/, /adli olarak .{0,30}bağlanır/,
      /geçtiği her split/, /yüzeye çıkarır/,
      /satırlarını eğitim corpus'undan düşürür/, /geçtiği her satırı raporlar/, /\(Madde 15 erişim\)/, /\(Madde 17 silme\)/],
    de: [/jede Zeile jedes Splits/, /zum Vorschein/, /Datensubjekt/],
    fr: [/chaque ligne de chaque split/, /font remonter/],
    es: [/cada fila de cada split/, /sacan a la luz/],
    zh: [/每个 split 的每一行/, /浮现/],
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
    for (const key of ['wizard.retention.hint', 'wizard.retention.audit_log.hint', 'wizard.retention.staging.hint',
      'wizard.retention.ephemeral.hint', 'wizard.retention.raw_documents.hint']) {
      assert.match(table[key], /--check-policy/, `${locale} ${key}: a horizon is only reported, not enforced`);
    }
    assert.match(table['wizard.retention.hint'], /\b0\b/, `${locale}: the section hint says what 0 does`);
    assert.doesNotMatch(table['wizard.retention.title'], /5\+17/, `${locale}: the retention title cites Article 5(1)(e) only`);
    assert.doesNotMatch(table['wizard.retention.audit_log.hint'], /EU AI Act/, `${locale}: the 5-year default is not a legal period`);
  }
});

// The GDPR cards say what the two commands are for (an input to an Article 15 response, one step of an Article 17
// erasure) and that trained weights are untouched.  Every locale keeps the caveats: a reversed or softened sentence
// that still contains the word "weights" must not pass.
const weightsUnchanged = {
  en: /trained weights[^.;)]{0,80}(?:are )?not changed/,
  tr: /eğitilmiş ağırlıklar[^.;)]{0,80}değişmez/,
  de: /[Tt]rainierte Gewichte[^.;)]{0,80}bleiben unverändert/,
  fr: /poids entraînés[^;)]{0,100}(?:ne changent pas|ne sont pas modifiés)/,
  es: /pesos entrenados[^;)]{0,100}no cambian/,
  zh: /权重[^。;)]{0,40}不会改变/,
};
// Article 15: the card is an input to a response, not the response.
const accessInput = {
  en: /an input to/, tr: /bir girdisi/, de: /Baustein/, fr: /un élément/, es: /un elemento/, zh: /之一/,
};
const accessCopyAndInfo = {
  en: /15\(1\)\(a\)-\(h\)/, tr: /15\(1\)\(a\)-\(h\)/, de: /Abs\. 1 Buchst\. a bis h/,
  fr: /paragraphe 1, points a\) à h\)/, es: /apartado 1, letras a\) a h\)/, zh: /第 1 款第 \(a\) 至 \(h\) 项/,
};
const matchIsLiteral = {
  en: /literal and case-sensitive/, tr: /büyük\/küçük harfe duyarlı/, de: /Groß- und Kleinschreibung/,
  fr: /sensible à la casse/, es: /distingue mayúsculas/, zh: /区分大小写/,
};
const maskingIsOptIn = { en: /--pii-mask/, tr: /--pii-mask/, de: /--pii-mask/, fr: /--pii-mask/, es: /--pii-mask/, zh: /--pii-mask/ };

test('the GDPR cards keep their caveats in every locale', () => {
  const { translations } = loadWizard();
  for (const [locale, pattern] of Object.entries(weightsUnchanged)) {
    for (const key of ['home.cap.gdpr.body', 'compliance.gdpr17.body', 'features.gov.purge.body']) {
      assert.match(translations[locale][key], pattern, `${locale} ${key}: purge leaves trained weights unchanged`);
    }
  }
  for (const [locale, pattern] of Object.entries(accessInput)) {
    for (const key of ['home.cap.gdpr.body', 'compliance.gdpr15.body', 'features.gov.access.body']) {
      assert.match(translations[locale][key], pattern, `${locale} ${key}: reverse-pii is an input to an Article 15 response`);
    }
  }
  for (const [locale, pattern] of Object.entries(accessCopyAndInfo)) {
    assert.match(translations[locale]['compliance.gdpr15.body'], pattern, `${locale}: the response also needs a copy and the 15(1)(a)-(h) information`);
  }
  for (const [locale, pattern] of Object.entries(matchIsLiteral)) {
    assert.match(translations[locale]['compliance.gdpr15.body'], pattern, `${locale}: matching is literal and case-sensitive`);
  }
  for (const [locale, pattern] of Object.entries(maskingIsOptIn)) {
    for (const key of ['home.use.domain.body', 'features.data.pii.body']) {
      assert.match(translations[locale][key], pattern, `${locale} ${key}: masking is opt-in (--pii-mask)`);
    }
  }
  assert.doesNotMatch(translations.en['compliance.art10.body'], /bias detection/i, 'the audit does not detect bias');
});

// A page shows its static fallback until the script runs, so each fallback of a GDPR string equals the English one.
test('the static fallbacks of the GDPR strings equal the English translations', () => {
  const { translations } = loadWizard();
  const pairs = [
    ['index.html', 'home.cap.gdpr.body'],
    ['index.html', 'home.use.domain.body'],
    ['compliance.html', 'compliance.gdpr15.body'],
    ['compliance.html', 'compliance.gdpr17.body'],
    ['features.html', 'features.gov.access.body'],
    ['features.html', 'features.gov.purge.body'],
  ];
  for (const [page, key] of pairs) {
    const html = fs.readFileSync(path.join(root, 'site', page), 'utf8');
    const escaped = key.replace(/\./g, '\\.');
    const fallback = html.match(new RegExp(`data-i18n-html="${escaped}">([^<]*(?:<code>[^<]*<\\/code>[^<]*)*)<\\/p>`));
    assert.ok(fallback, `${page} keeps a static fallback for ${key}`);
    assert.equal(fallback[1], translations.en[key], `${page} ${key}: the static fallback equals the English string`);
  }
});
