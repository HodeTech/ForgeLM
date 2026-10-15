'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const pythonBin = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python'];
const venvPython = path.join(process.env.VIRTUAL_ENV || path.join(root, '.venv'), ...pythonBin);
const python = process.env.FORGELM_TEST_PYTHON || (fs.existsSync(venvPython) ? venvPython : 'python3');
function element(tag) {
  const node = {
    tag, children: [], attrs: {}, events: {},
    appendChild(child) { this.children.push(child); child.parent = this; },
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener(key, handler) { this.events[key] = handler; },
    remove() { this.parent.children = this.parent.children.filter(child => child !== this); },
    querySelector(selector) {
      return descendants(this).find(child => (child.className || '').split(' ').includes(selector.slice(1))) || null;
    }
  };
  node.classList = {
    add() {}, toggle() {},
    remove(value) { node.className = (node.className || '').split(' ').filter(item => item !== value).join(' '); }
  };
  return node;
}
function loadWizard() {
  const document = {
    readyState: 'loading', addEventListener() {}, querySelector() { return null; }, createElement: element,
    createElementNS: (_, tag) => element(tag), createTextNode: text => ({ text })
  };
  const context = vm.createContext({ window: {}, document, URL });
  for (const file of ['wizard_defaults.js', 'translations.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, 'site/js', file), 'utf8'), context);
  }
  const marker = '  /* ── Boot ─';
  const source = fs.readFileSync(path.join(root, 'site/js/wizard.js'), 'utf8');
  assert.equal(source.split(marker).length, 2, 'test seam must identify the boot boundary exactly once');
  // Expose closure functions only in the VM; production has no test-only API.
  vm.runInContext(source.replace(marker,
    '  window.testWizard = {defaultState, buildYaml, validateStep, coerceForRisk, parseEnvRef, STEP_RENDERERS};\n' + marker), context);
  return { ...context.window.testWizard, translations: context.window.ForgeLMTranslations };
}
function state(wizard, overrides = {}) {
  return Object.assign(wizard.defaultState(), { model: 'org/model', datasetName: 'org/dataset' }, overrides);
}
function descendants(node) {
  return [node, ...(node.children || []).flatMap(descendants)];
}

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
  const interval = nodes.find(node => node.attrs.id === 'wizard-monitoring-interval');
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
  ].join('\n')], { cwd: root, input: JSON.stringify(configs), encoding: 'utf8', timeout: 30000 });
  assert.ifError(result.error);
  assert.equal(result.status, 0, `Python interpreter: ${python}\n${result.stderr}`);
});
