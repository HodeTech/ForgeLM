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
