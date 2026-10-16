'use strict';

// Render smoke coverage for every wizard step (#879).
//
// The Compliance renderer called a helper that only three other renderers
// declared, so every visit threw a ReferenceError before the risk-assessment,
// benchmark and judge controls were built. Nothing rendered that step, so the
// crash shipped. These tests render every step through the real renderers.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { root, loadWizard, state, descendants, textOf, hasClass, renderStep, RISK_TIERS, scenarios } = require('./wizard_harness.cjs');

function rows(pane) {
  return descendants(pane).filter(node => node.tag === 'div' && hasClass(node, 'wizard-row'));
}

// The row whose caption reads `labelKey`. Outside a browser `tr()` has no
// translation table, so every caption renders as its own i18n key.
function rowFor(pane, labelKey) {
  const matches = rows(pane).filter(row => descendants(row).some(node =>
    (node.tag === 'label' || hasClass(node, 'wizard-toggle-label')) && textOf(node).startsWith(labelKey)));
  assert.equal(matches.length, 1, `exactly one row is captioned ${labelKey}`);
  return matches[0];
}

function controlIn(row) {
  const controls = descendants(row).filter(node => ['input', 'select', 'textarea'].includes(node.tag));
  assert.equal(controls.length, 1, 'a row hosts exactly one control');
  return controls[0];
}

function detailIn(row) {
  return descendants(row).filter(node => node.tag === 'p' && hasClass(node, 'wizard-row-detail'))
    .map(node => node.textContent);
}

test('every step renders in every tier, detail mode and gate combination', () => {
  const wizard = loadWizard();
  for (const overrides of scenarios()) {
    for (const step of wizard.STEPS) {
      const value = state(wizard, overrides);
      assert.doesNotThrow(() => renderStep(wizard, value, step.id), `${step.id} with ${JSON.stringify(overrides)}`);
    }
  }
});

for (const tier of RISK_TIERS) {
  for (const detailsVisible of [true, false]) {
    test(`compliance step renders its risk-assessment and evaluation controls at ${tier} (details ${detailsVisible ? 'on' : 'off'})`, () => {
      const wizard = loadWizard();
      const value = state(wizard, { riskClassification: tier, detailsVisible, benchmarkEnabled: true, judgeEnabled: true });
      const pane = renderStep(wizard, value, 'compliance');
      const expected = [
        ['wizard.eval.auto_revert.label', 'input', 'wizard.eval.auto_revert.detail'],
        ['wizard.eval.max_loss.label', 'input', 'wizard.eval.max_loss.detail'],
        ['wizard.eval.safety.label', 'input', 'wizard.eval.safety.detail'],
        ['wizard.compliance.approval.label', 'input', 'wizard.compliance.approval.detail'],
        ['wizard.risk_assessment.intended_use.label', 'textarea', 'wizard.risk_assessment.intended_use.detail'],
        ['wizard.risk_assessment.foreseeable_misuse.label', 'textarea', 'wizard.risk_assessment.foreseeable_misuse.detail'],
        ['wizard.risk_assessment.mitigation_measures.label', 'textarea', 'wizard.risk_assessment.mitigation_measures.detail'],
        ['wizard.risk_assessment.vulnerable_groups.label', 'input', 'wizard.risk_assessment.vulnerable_groups.detail'],
        ['wizard.benchmark.enabled.label', 'input', 'wizard.benchmark.enabled.detail'],
        ['wizard.benchmark.tasks.label', 'textarea', null],
        ['wizard.benchmark.min_score.label', 'input', null],
        ['wizard.judge.enabled.label', 'input', 'wizard.judge.enabled.detail'],
        ['wizard.judge.model.label', 'input', null],
        ['wizard.judge.api_key_env.label', 'input', null],
        ['wizard.judge.min_score.label', 'input', null]
      ];
      for (const [labelKey, tag, detailKey] of expected) {
        const row = rowFor(pane, labelKey);
        assert.equal(controlIn(row).tag, tag, labelKey);
        assert.deepEqual(detailIn(row), detailsVisible && detailKey ? [detailKey] : [], `${labelKey} detail paragraph`);
      }
    });
  }
}

test('compliance controls still write the operator\'s answers into the YAML', () => {
  const wizard = loadWizard();
  const value = state(wizard, { riskClassification: 'high-risk', benchmarkEnabled: true, judgeEnabled: true });
  const pane = renderStep(wizard, value, 'compliance');
  const misuse = controlIn(rowFor(pane, 'wizard.risk_assessment.foreseeable_misuse.label'));
  misuse.value = 'Prompted to give medical advice.';
  misuse.events.input();
  const tasks = controlIn(rowFor(pane, 'wizard.benchmark.tasks.label'));
  tasks.value = 'arc_easy\nhellaswag';
  tasks.events.input();
  const yaml = wizard.buildYaml(value);
  assert.match(yaml, /\n  foreseeable_misuse:\n    - "Prompted to give medical advice\."\n/);
  assert.match(yaml, /\n    tasks:\n      - "arc_easy"\n      - "hellaswag"\n/);
});

for (const tier of ['high-risk', 'unacceptable']) {
  test(`${tier === 'unacceptable' ? 'an' : 'a'} ${tier} configuration passes the compliance step once its rendered fields are filled`, () => {
    const wizard = loadWizard();
    // Both gate inputs ship with defaults; blank them so all eight required answers start missing.
    const value = state(wizard, { riskClassification: tier, benchmarkEnabled: true, judgeEnabled: true,
      benchmarkTasks: '', judgeApiKeyEnv: '' });
    const pane = renderStep(wizard, value, 'compliance');
    const required = {
      'wizard.compliance.provider_name.label': 'Acme AI Ltd.',
      'wizard.compliance.system_name.label': 'Support assistant',
      'wizard.compliance.intended_purpose.label': 'Triage support tickets',
      'wizard.risk_assessment.intended_use.label': 'Answer product questions',
      'wizard.risk_assessment.foreseeable_misuse.label': 'Medical advice',
      'wizard.risk_assessment.mitigation_measures.label': 'Refusal policy',
      'wizard.benchmark.tasks.label': 'arc_easy',
      'wizard.judge.api_key_env.label': 'OPENAI_API_KEY'
    };
    // Array.from: the VM returns arrays from another realm, which deepEqual rejects by prototype.
    assert.deepEqual(Array.from(wizard.validateStep(value, 'compliance'), error => error.field).sort(), [
      'benchmarkTasks', 'foreseeableMisuse', 'intendedPurpose', 'intendedUse', 'judgeApiKeyEnv',
      'mitigationMeasures', 'providerName', 'systemName']);
    for (const [labelKey, answer] of Object.entries(required)) {
      const control = controlIn(rowFor(pane, labelKey));
      control.value = answer;
      control.events.input();
    }
    assert.deepEqual(Array.from(wizard.validateStep(value, 'compliance'), error => error.field), []);
  });
}

test('after a failed Next every invalid field is marked and shows its error', () => {
  const wizard = loadWizard();
  const idFor = field => 'wizard-field-' + field.replace(/[A-Z]/g, c => '-' + c.toLowerCase());
  const failed = scenarios().filter(overrides => overrides.attemptedSteps);
  assert.ok(failed.length, 'the harness has error-state scenarios');
  for (const overrides of failed) {
    for (const step of ['model', 'dataset', 'compliance', 'operations']) {
      const value = state(wizard, overrides);
      const pane = renderStep(wizard, value, step);
      const errors = Array.from(wizard.validateStep(value, step));
      assert.ok(errors.length, `${step}: the scenario leaves fields invalid`);
      for (const { field, key } of errors) {
        const control = descendants(pane).find(node => node.getAttribute && node.getAttribute('id') === idFor(field));
        assert.ok(control && hasClass(control, 'is-invalid'), `${step}: ${field} is marked invalid`);
        const row = rows(pane).find(candidate => descendants(candidate).includes(control));
        assert.ok(descendants(row).some(node => node.getAttribute && node.getAttribute('role') === 'alert' && node.textContent === key),
          `${step}: ${field} shows ${key}`);
      }
    }
  }
});

test('toggles that reveal fields show and hide them at once', () => {
  const wizard = loadWizard();
  const has = (pane, id) => descendants(pane).some(node => node.getAttribute && node.getAttribute('id') === id);
  const flip = (pane, id) => {
    const toggle = descendants(pane).find(node => node.getAttribute && node.getAttribute('id') === id);
    toggle.checked = !toggle.checked;
    toggle.events.change();
  };
  const compliance = renderStep(wizard, state(wizard, { autoRevert: false }), 'compliance');
  assert.ok(!has(compliance, 'wizard-field-max-acceptable-loss'));
  flip(compliance, 'wizard-field-auto-revert');
  assert.ok(has(compliance, 'wizard-field-max-acceptable-loss'), 'auto-revert on shows the loss threshold');
  flip(compliance, 'wizard-field-auto-revert');
  assert.ok(!has(compliance, 'wizard-field-max-acceptable-loss'), 'auto-revert off hides it');

  const training = renderStep(wizard, state(wizard, { galoreEnabled: false }), 'training');
  assert.ok(has(training, 'wizard-field-lora-r') && !has(training, 'wizard-field-galore-optim'));
  flip(training, 'wizard-field-galore-enabled');
  assert.ok(has(training, 'wizard-field-galore-optim') && !has(training, 'wizard-field-lora-r'),
    'GaLore replaces the LoRA rank with its optimiser choice');

  // Each optional gate shows its own fields while it is on, and only then.
  const gates = [
    ['compliance', 'benchmarkEnabled', ['wizard-field-benchmark-tasks', 'wizard-field-benchmark-min-score']],
    ['compliance', 'judgeEnabled', ['wizard-field-judge-model', 'wizard-field-judge-api-key-env', 'wizard-field-judge-min-score']],
    ['operations', 'webhookEnabled', ['wizard-field-webhook-url']],
    ['operations', 'monitoringEnabled', ['wizard-field-monitoring-endpoint', 'wizard-field-monitoring-check-interval-hours']],
    ['operations', 'syntheticEnabled',
      ['wizard-field-synthetic-teacher-model', 'wizard-field-synthetic-seed-file', 'wizard-field-synthetic-api-key-env']]
  ];
  for (const [step, field, ids] of gates) {
    const toggle = 'wizard-field-' + field.replace(/[A-Z]/g, c => '-' + c.toLowerCase());
    const pane = renderStep(wizard, state(wizard, { [field]: false }), step);
    assert.deepEqual(ids.filter(id => has(pane, id)), [], `${field} off`);
    flip(pane, toggle);
    assert.deepEqual(ids.filter(id => !has(pane, id)), [], `${field} on shows its fields`);
    flip(pane, toggle);
    assert.deepEqual(ids.filter(id => has(pane, id)), [], `${field} off again hides them`);
  }

  // The custom-model card shows the model id field, and another preset hides it.
  const model = renderStep(wizard, state(wizard, { modelPreset: 'llama3-8b' }), 'model');
  const click = id => descendants(model).find(node => node.getAttribute && node.getAttribute('id') === id).events.click();
  assert.ok(!has(model, 'wizard-field-model'));
  click('wizard-field-model-preset-custom');
  assert.ok(has(model, 'wizard-field-model'), 'the custom card shows the model id');
  click('wizard-field-model-preset-llama3-8b');
  assert.ok(!has(model, 'wizard-field-model'), 'a preset hides it');
});

test('with details on, every detail paragraph a renderer names is shown', () => {
  const source = fs.readFileSync(path.join(root, 'site/js/wizard.js'), 'utf8');
  const named = [...source.matchAll(/appendDetail\(state, \w+, '([\w.]+)'\)/g)].map(match => match[1]);
  assert.ok(named.length >= 15, `${named.length} detail keys`);
  const wizard = loadWizard();
  const shown = new Set();
  for (const overrides of scenarios()) {
    for (const step of wizard.STEPS) {
      const pane = renderStep(wizard, state(wizard, { ...overrides, detailsVisible: true }), step.id);
      for (const node of descendants(pane).filter(node => hasClass(node, 'wizard-row-detail'))) shown.add(textOf(node));
    }
  }
  assert.deepEqual(named.filter(key => !shown.has(key)), []);
});

test('a detail line names code as code, never as literal tags', () => {
  const wizard = loadWizard('en');
  const pane = renderStep(wizard, state(wizard, { trainerType: 'grpo', detailsVisible: true }), 'training');
  const details = descendants(pane).filter(node => hasClass(node, 'wizard-row-detail'));
  assert.ok(details.length > 0);
  assert.deepEqual(details.filter(node => textOf(node).includes('<code>')), [], 'no tag shows as text');
  const codes = details.flatMap(node => descendants(node).filter(child => child.tag === 'code')).map(textOf);
  assert.deepEqual(codes.sort(), ['combined_format_length_reward', 'gold_answer']);
  // The text around the code survives too: the line reads as the translation without its tags.
  const copy = wizard.translations.en['wizard.training.grpo_reward_model.detail'];
  assert.ok(details.some(node => textOf(node) === copy.replace(/<\/?code>/g, '')), 'the whole line, in order');
});
