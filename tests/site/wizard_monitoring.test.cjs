'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
const vm = require('node:vm');
const { root, element, loadWizard, state, descendants } = require('./wizard_harness.cjs');

const pythonBin = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python'];
const venvPython = path.join(process.env.VIRTUAL_ENV || path.join(root, '.venv'), ...pythonBin);
const python = process.env.FORGELM_TEST_PYTHON || (fs.existsSync(venvPython) ? venvPython : 'python3');

for (const tier of ['minimal-risk', 'limited-risk', 'high-risk', 'unacceptable']) {
  test(`monitoring remains opt-in at ${tier}; empty endpoint is valid`, () => {
    const wizard = loadWizard();
    const value = state(wizard, { riskClassification: tier });
    wizard.coerceForRisk(value);
    assert.equal(value.monitoringEnabled, false);
    assert.doesNotMatch(wizard.buildYaml(value), /\nmonitoring:/);
    value.monitoringEnabled = true;
    wizard.coerceForRisk(value);
    assert.equal(value.monitoringEnabled, true, 'an enabled declaration must survive tier coercion');
    assert.equal(value.alertOnDrift, false, 'drift alerts require an affirmative operator answer');
    assert.equal(wizard.validateStep(value, 'operations').length, 0);
    assert.match(wizard.buildYaml(value), /monitoring:\n  enabled: true/);
    assert.doesNotMatch(wizard.buildYaml(value), /\n  endpoint(?:_env)?:/);
  });
}

for (const [raw, expected] of [['env:MON_URL', 'MON_URL'], ['ENV: MON_URL ', 'MON_URL'], [' env:_mon_url ', '_mon_url']]) {
  test(`both endpoint inputs normalize ${JSON.stringify(raw)}`, () => {
    const wizard = loadWizard();
    const value = state(wizard, { monitoringEnabled: true, monitoringEndpoint: raw, webhookEnabled: true, webhookUrl: raw });
    assert.equal(wizard.validateStep(value, 'operations').length, 0);
    const yaml = wizard.buildYaml(value);
    assert.match(yaml, new RegExp(`endpoint_env: "${expected}"`));
    assert.match(yaml, new RegExp(`url_env: "${expected}"`));
  });
}
for (const raw of ['env:', 'env:   ', 'env:$MON_URL', 'env:https://host/token', 'env:MON URL', 'env:1MON', 'env:MÖN']) {
  test(`both endpoint inputs reject ${JSON.stringify(raw)}`, () => {
    const wizard = loadWizard();
    const value = state(wizard, { monitoringEnabled: true, monitoringEndpoint: raw, webhookEnabled: true, webhookUrl: raw });
    const fields = Array.from(wizard.validateStep(value, 'operations'), item => item.field);
    assert.deepEqual(fields.sort(), ['monitoringEndpoint', 'webhookUrl']);
    assert.doesNotMatch(wizard.buildYaml(value), /(?:endpoint_env|url_env):/);
  });
}

test('disabled plans omit stale endpoint, exporter, and drift policy', () => {
  const wizard = loadWizard();
  const value = state(wizard, { monitoringEnabled: false, monitoringEndpoint: 'env:MON_URL', metricsExport: 'prometheus', alertOnDrift: true });
  assert.doesNotMatch(wizard.buildYaml(value), /\nmonitoring:/);
});

for (const endpoint of ['prometheus.internal:9090', 'localhost:8080', 'monitor.example.com/x', 'https:host', 'https://host:65536', 'http://[::1', 'https://host/a b']) {
  test(`malformed literal endpoint ${JSON.stringify(endpoint)} blocks advancement`, () => {
    const wizard = loadWizard();
    const value = state(wizard, { monitoringEnabled: true, monitoringEndpoint: endpoint });
    assert.deepEqual(Array.from(wizard.validateStep(value, 'operations'), item => item.key), ['wizard.error.monitoring.endpoint.invalid']);
    assert.doesNotMatch(wizard.buildYaml(value), /\n  endpoint:/);
  });
}

for (const interval of [0, 721, 1.5, NaN]) {
  test(`invalid external check interval ${interval} blocks advancement`, () => {
    const wizard = loadWizard();
    const value = state(wizard, { monitoringEnabled: true, monitoringCheckIntervalHours: interval });
    assert.equal(wizard.validateStep(value, 'operations')[0].field, 'monitoringCheckIntervalHours');
    assert.doesNotMatch(wizard.buildYaml(value), /check_interval_hours:/);
  });
}

test('operations renderer clears corrected endpoint and interval errors', () => {
  const wizard = loadWizard();
  const value = state(wizard, { monitoringEnabled: true, monitoringEndpoint: 'env:', monitoringCheckIntervalHours: 0, attemptedSteps: ['operations'] });
  const pane = element('main');
  wizard.STEP_RENDERERS.operations(pane, value, () => {}, () => {});
  const nodes = descendants(pane);
  assert.equal(nodes.filter(node => node.attrs.role === 'alert').length, 2);
  assert.deepEqual(nodes.filter(node => node.attrs.role === 'alert').map(node => node.textContent),
    ['wizard.error.env_ref.invalid', 'wizard.error.monitoring.interval.invalid']);
  const endpoint = nodes.find(node => node.tag === 'input' && node.attrs.type === 'text');
  endpoint.value = 'ENV: MON_URL';
  endpoint.events.input();
  assert.doesNotMatch(endpoint.className, /is-invalid/);
  assert.equal(descendants(pane).filter(node => node.attrs.role === 'alert').length, 1);
  const interval = nodes.find(node => node.attrs.id === 'wizard-field-monitoring-check-interval-hours');
  interval.value = '48';
  interval.events.input();
  assert.doesNotMatch(interval.className, /is-invalid/);
  assert.equal(descendants(pane).filter(node => node.attrs.role === 'alert').length, 0);
  assert.equal(value.monitoringCheckIntervalHours, 48);
  assert.match(wizard.buildYaml(value), /check_interval_hours: 48/);
  const toggle = nodes.find(node => node.tag === 'label' && node.children.some(child =>
    child.textContent === 'wizard.monitoring.enabled.label')).children[0];
  toggle.checked = false;
  toggle.events.change();
  assert.doesNotMatch(wizard.buildYaml(value), /\nmonitoring:/);
});

test('active locales cover all keys; deferred locales use the real English fallback for new keys', () => {
  const { translations } = loadWizard();
  const keys = Object.keys(translations.en).filter(key => key.startsWith('wizard.monitoring.') ||
    key.startsWith('wizard.error.monitoring.') || key === 'wizard.error.env_ref.invalid').sort();
  const deferred = ['wizard.error.env_ref.invalid', 'wizard.monitoring.interval.label', 'wizard.error.monitoring.interval.invalid', 'wizard.error.monitoring.endpoint.invalid'];
  const document = { documentElement: { lang: 'en' } };
  const window = { ForgeLMTranslations: translations };
  const context = vm.createContext({ window, document });
  vm.runInContext(fs.readFileSync(path.join(root, 'site/js/_shared.js'), 'utf8'), context);
  for (const locale of ['en', 'tr', 'de', 'fr', 'es', 'zh']) {
    const expectedKeys = ['en', 'tr'].includes(locale) ? keys : keys.filter(key => !deferred.includes(key));
    assert.deepEqual(Object.keys(translations[locale]).filter(key => keys.includes(key)).sort(), expectedKeys);
    assert.ok(translations[locale]['wizard.monitoring.enabled.detail'].includes('declarative_only: true'));
    for (const key of expectedKeys) assert.ok(translations[locale][key].trim(), `${locale}: ${key}`);
    if (!['en', 'tr'].includes(locale)) {
      document.documentElement.lang = locale;
      for (const key of deferred) assert.equal(window.ForgeLMShared.tr(key), translations.en[key], `${locale}: ${key}`);
    }
  }
});

test('every locale\'s monitoring hint still names the optional metadata, the Art. 72 system and the Annex IV plan', () => {
  const { translations } = loadWizard();
  // The Art. 72 term of each official EU language version and of the Turkish glossary; ZH accepts its
  // established alternatives, so a native review can choose between them without editing this test.
  const postMarket = { en: /post-market monitoring/, tr: /piyasaya arz sonrası izleme/,
    de: /Beobachtung nach dem Inverkehrbringen/, fr: /surveillance après commercialisation/,
    es: /vigilancia poscomercialización/, zh: /上市后监[控测]/ };
  const metadata = { en: 'metadata', tr: 'metadata', de: 'Metadaten', fr: 'métadonnées', es: 'metadatos', zh: '元数据' };
  // The wording of Annex IV §9 in each official language version (Regulation (EU) 2024/1689).
  const describes = { en: /detailed description/, tr: /ayrıntılı açıklama/, de: /detaillierte Beschreibung/,
    fr: /description détaillée/, es: /descripción detallada/, zh: /详细描述/ };
  const plan = { en: /including the monitoring plan/, tr: /izleme planı dahil/,
    de: /einschließlich des Plans für die Beobachtung nach dem Inverkehrbringen/,
    fr: /y compris le plan de surveillance après commercialisation/, es: /incluido el plan de vigilancia poscomercialización/,
    zh: /包括上市后监控计划/ };
  // Art. 72 binds providers of high-risk systems, and recording the plan in ForgeLM does not discharge it.
  const highRisk = { en: /providers of high-risk AI systems/, tr: /yüksek riskli yapay zekâ sistemlerinin sağlayıcılarının/,
    de: /Anbieter von Hochrisiko-KI-Systemen/, fr: /fournisseurs de systèmes d'IA à haut risque/,
    es: /proveedores de sistemas de IA de alto riesgo/, zh: /高风险 AI 系统提供者/ };
  const notEnough = { en: /declaration alone does not satisfy/, tr: /tek başına karşılamaz/, de: /Erklärung allein erfüllt keine/,
    fr: /déclaration seule ne remplit pas/, es: /declaración por sí sola no cumple/, zh: /仅此声明并不能履行/ };
  for (const locale of Object.keys(postMarket)) {
    const hint = translations[locale]['wizard.monitoring.enabled.hint'];
    const detail = translations[locale]['wizard.monitoring.enabled.detail'];
    assert.match(hint, postMarket[locale], `${locale}: the hint names the provider's Art. 72 system`);
    assert.match(detail, postMarket[locale], `${locale}: the detail names the provider's Art. 72 obligation`);
    assert.ok(hint.includes(metadata[locale]), `${locale}: ForgeLM's optional record is called metadata`);
    assert.match(hint, /72/, `${locale}: cites Art. 72`);
    assert.match(hint, /Annex IV §9/, `${locale}: cites Annex IV §9`);
    // Annex IV §9 asks for a detailed description of the system, its plan included — not "the detailed plan".
    assert.match(hint, describes[locale], `${locale}: Annex IV §9 asks for a detailed description`);
    assert.match(hint, plan[locale], `${locale}: the description includes the monitoring plan`);
    assert.match(hint, highRisk[locale], `${locale}: Art. 72 binds providers of high-risk systems`);
    assert.match(detail, notEnough[locale], `${locale}: the declaration alone does not discharge Art. 72`);
  }
  assert.match(translations.en['wizard.monitoring.enabled.hint'],
    /Annex IV §9 asks for a detailed description of it, including the monitoring plan\./);
});

test('the French and Spanish copy says alerts and the YAML in its own words', () => {
  const { translations } = loadWizard();
  assert.match(translations.fr['wizard.webhook.hint'], /Envoyez des alertes /);
  assert.doesNotMatch(translations.fr['wizard.webhook.hint'], /\balerts\b/);
  assert.match(translations.fr['wizard.monitoring.endpoint.hint'], /le YAML copié/);
  assert.match(translations.es['wizard.monitoring.endpoint.hint'], /el YAML copiado/);
  for (const locale of ['fr', 'es']) {
    for (const [key, value] of Object.entries(translations[locale])) {
      assert.doesNotMatch(value, /\bla YAML\b/, `${locale} ${key}: YAML is masculine`);
    }
  }
});

test('monitoring and webhook copy keeps one apostrophe style, and Chinese punctuation and endpoint term', () => {
  const { translations } = loadWizard();
  const scoped = key => key.startsWith('wizard.monitoring.') || key.startsWith('wizard.step.operations.') ||
    key.startsWith('wizard.webhook.');
  // Citations such as 17(1)(h) and code tokens such as endpoint_env are not prose.
  const prose = value => value.replace(/\d+(?:\(\w+\))+/g, ' ').replace(/\b[a-z]+(?:_[a-z]+)+\b/g, ' ');
  for (const locale of ['en', 'tr', 'de', 'fr', 'es', 'zh']) {
    for (const [key, value] of Object.entries(translations[locale]).filter(([name]) => scoped(name))) {
      assert.doesNotMatch(value, /’/, `${locale} ${key}: the monitoring copy writes the straight apostrophe`);
      if (locale !== 'zh') continue;
      assert.doesNotMatch(prose(value), /[一-鿿][,(:;!?]|[,);!?][一-鿿]/, `zh ${key}: full-width punctuation in Chinese text`);
      assert.doesNotMatch(prose(value), /endpoint/i, `zh ${key}: the copy's term for an endpoint is 端点`);
    }
  }
});

test('Turkish copy follows the glossary: monitoring terms, alarm / bildirim / uyarı, annex points, apostrophes', () => {
  const { translations } = loadWizard();
  const { en, tr } = translations;
  for (const [key, value] of Object.entries(tr)) {
    assert.doesNotMatch(value, /’/, `tr ${key}: Turkish writes the straight apostrophe`);
    assert.doesNotMatch(value, /pazar sonrası|Annex IV madde|aktarım sistemi/i, `tr ${key}: a superseded term`);
    // An alert is an *alarm*, or a webhook's *bildirim*; *uyarı* is a warning.
    const english = en[key] || '';
    if (/\balerts?\b/i.test(english) && !/\bwarn/i.test(english)) {
      assert.doesNotMatch(value, /uyarı|alert/i, `tr ${key}: an alert is an alarm or a bildirim`);
    }
  }
  assert.match(tr['wizard.monitoring.enabled.hint'], /Annex IV §9/);
  assert.match(tr['wizard.monitoring.metrics_export.label'], /exporter/);
  assert.match(tr['wizard.monitoring.alert_on_drift.label'], /alarm/);
  assert.match(tr['wizard.webhook.hint'], /bildirim/);
  assert.match(tr['wizard.webhook.enabled.detail'], /uyarı/, 'a delivery failure is still a log warning');
  // Neighbouring corrections: the Regulation's Turkish citation, one term for trend tracking, a possessive.
  assert.match(tr['compliance.mapping.lead'], /\(AB\) 2024\/1689 sayılı Tüzüğün bir maddesini, onu /);
  assert.match(tr['compliance.art9.body'], /trend izleme/);
  assert.match(tr['wizard.error.governance.annotation_process.required'], /SFT'si için/);
});

test('generated YAML preserves monitoring and webhook values through the real ForgeConfig schema', () => {
  const wizard = loadWizard();
  const configs = [];
  for (const tier of ['minimal-risk', 'limited-risk', 'high-risk', 'unacceptable']) {
    for (const [endpoint, literal, envName] of [
      ['', '', null], ['ENV: _mon_url ', '', '_mon_url'],
      ['https://monitor.example.com:8443/plan', 'https://monitor.example.com:8443/plan', null],
      ['http://[::1]:9090/m', 'http://[::1]:9090/m', null]
    ]) {
      for (const exporter of ['none', 'prometheus', 'datadog', 'custom_webhook']) {
        for (const interval of [1, 720]) {
          for (const drift of [false, true]) {
            const webhookLiteral = 'https://notify.example.com/service';
            const value = state(wizard, { riskClassification: tier, monitoringEnabled: true, monitoringEndpoint: endpoint,
              metricsExport: exporter, monitoringCheckIntervalHours: interval, alertOnDrift: drift,
              webhookEnabled: true, webhookUrl: envName ? endpoint : webhookLiteral });
            wizard.coerceForRisk(value);
            assert.equal(wizard.validateStep(value, 'operations').length, 0, JSON.stringify(value));
            configs.push({ yaml: wizard.buildYaml(value), expected: {
              endpoint: literal, endpoint_env: envName, metrics_export: exporter, check_interval_hours: interval,
              alert_on_drift: drift, webhook_url: envName ? null : webhookLiteral, webhook_env: envName,
              endpoint_field: envName ? 'endpoint_env' : literal ? 'endpoint' : null
            }});
          }
        }
      }
    }
  }
  const result = spawnSync(python, ['-c', [
    'import json, sys, yaml',
    'from forgelm.config import ForgeConfig',
    'for case in json.load(sys.stdin):',
    '    config = ForgeConfig(**yaml.safe_load(case["yaml"]))',
    '    expected = case["expected"]',
    '    assert config.monitoring.enabled is True',
    '    for field in ("endpoint", "endpoint_env", "metrics_export", "alert_on_drift", "check_interval_hours"):',
    '        assert getattr(config.monitoring, field) == expected[field], (field, expected, case["yaml"])',
    '    assert {"metrics_export", "alert_on_drift", "check_interval_hours"} <= config.monitoring.model_fields_set',
    '    actual_endpoint_fields = config.monitoring.model_fields_set & {"endpoint", "endpoint_env"}',
    '    assert actual_endpoint_fields == ({expected["endpoint_field"]} if expected["endpoint_field"] else set())',
    '    assert config.webhook.url == expected["webhook_url"], expected',
    '    assert config.webhook.url_env == expected["webhook_env"], expected'
  ].join('\n')], { cwd: root, input: JSON.stringify(configs), encoding: 'utf8', timeout: 120000 });  // a loaded CI host is slow
  assert.ifError(result.error);
  assert.equal(result.status, 0, `Python interpreter: ${python}\n${result.stderr}`);
});
