'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
function element(tag) {
  return {
    tag, children: [], attrs: {}, events: {},
    appendChild(child) { this.children.push(child); },
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener(key, handler) { this.events[key] = handler; },
    classList: { add() {}, toggle() {} },
    querySelector() { return null; }
  };
}
function loadWizard() {
  const document = {
    readyState: 'loading', addEventListener() {}, querySelector() { return null; }, createElement: element,
    createElementNS: (_, tag) => element(tag), createTextNode: text => ({ text })
  };
  const context = vm.createContext({ window: {}, document });
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
    assert.ok(yaml.includes(`endpoint_env: "${expected}"`));
    assert.ok(yaml.includes(`url_env: "${expected}"`));
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

for (const interval of [0, 721, 1.5, NaN]) {
  test(`invalid external check interval ${interval} blocks advancement`, () => {
    const wizard = loadWizard();
    const value = state(wizard, { monitoringEnabled: true, monitoringCheckIntervalHours: interval });
    assert.equal(wizard.validateStep(value, 'operations')[0].field, 'monitoringCheckIntervalHours');
    assert.doesNotMatch(wizard.buildYaml(value), /check_interval_hours:/);
  });
}

test('operations renderer accepts plan edits and reports invalid env references', () => {
  const wizard = loadWizard();
  const value = state(wizard, { monitoringEnabled: true, monitoringEndpoint: 'env:', attemptedSteps: ['operations'] });
  const pane = element('main');
  wizard.STEP_RENDERERS.operations(pane, value, () => {}, () => {});
  const nodes = descendants(pane);
  assert.ok(nodes.some(node => node.attrs.role === 'alert' && node.textContent === 'wizard.error.env_ref.invalid'));
  const interval = nodes.find(node => node.attrs.id === 'wizard-monitoring-interval');
  interval.value = '48';
  interval.events.input();
  assert.equal(value.monitoringCheckIntervalHours, 48);
  assert.match(wizard.buildYaml(value), /check_interval_hours: 48/);
  const toggle = nodes.find(node => node.tag === 'label' && node.children.some(child =>
    child.textContent === 'wizard.monitoring.enabled.label')).children[0];
  toggle.checked = false;
  toggle.events.change();
  assert.doesNotMatch(wizard.buildYaml(value), /\nmonitoring:/);
});

test('all six locales expose the same monitoring and validation keys', () => {
  const { translations } = loadWizard();
  const keys = Object.keys(translations.en).filter(key => key.startsWith('wizard.monitoring.') ||
    key === 'wizard.error.env_ref.invalid' || key === 'wizard.error.monitoring.interval.invalid').sort();
  for (const locale of ['en', 'tr', 'de', 'fr', 'es', 'zh']) {
    assert.deepEqual(Object.keys(translations[locale]).filter(key => keys.includes(key)).sort(), keys);
    assert.ok(translations[locale]['wizard.monitoring.enabled.detail'].includes('declarative_only: true'));
    for (const key of keys) assert.ok(translations[locale][key].trim(), `${locale}: ${key}`);
  }
});

test('generated monitoring YAML passes the real ForgeConfig schema', () => {
  const wizard = loadWizard();
  const configs = [];
  for (const tier of ['minimal-risk', 'limited-risk', 'high-risk', 'unacceptable']) {
    for (const endpoint of ['', 'ENV: MON_URL ', 'https://monitor.example.com/plan']) {
      const value = state(wizard, { riskClassification: tier, monitoringEnabled: true, monitoringEndpoint: endpoint, monitoringCheckIntervalHours: 720 });
      wizard.coerceForRisk(value);
      configs.push(wizard.buildYaml(value));
    }
  }
  const result = spawnSync('python3', ['-c', [
    'import json, sys, yaml',
    'from forgelm.config import ForgeConfig',
    'for text in json.load(sys.stdin):',
    '    config = ForgeConfig(**yaml.safe_load(text))',
    '    assert config.monitoring.enabled is True',
    '    assert config.monitoring.check_interval_hours == 720',
    '    assert {"alert_on_drift", "check_interval_hours"} <= config.monitoring.model_fields_set'
  ].join('\n')], { cwd: root, input: JSON.stringify(configs), encoding: 'utf8', timeout: 30000 });
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
});
