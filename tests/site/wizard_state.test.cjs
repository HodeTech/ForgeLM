'use strict';

// Every rebuilt control must show the state it was built from: a re-render
// after a toggle, a sibling tab's update or a step change rebuilds the whole
// step, so a control that read the wrong field, or none, would silently show
// a value the YAML preview does not use.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadWizard, state, descendants, isElement, hasClass, renderStep, scenarios } = require('./wizard_harness.cjs');

const idFor = field => 'wizard-field-' + field.replace(/[A-Z]/g, c => '-' + c.toLowerCase());

// Beyond the scenarios: every boolean the other way round, and options other
// than the first, so a control that ignores the state cannot pass by showing
// its default.
function counterpoints(wizard) {
  const flipped = Object.fromEntries(Object.entries(wizard.defaultState())
    .filter(([, value]) => typeof value === 'boolean').map(([field, value]) => [field, !value]));
  return [flipped, {
    learningRate: '1e-5', batchSize: 8, galoreEnabled: true, galoreOptim: 'galore_adafactor', monitoringEnabled: true,
    metricsExport: 'datadog', retentionEnforce: 'block_on_excess', riskClassification: 'limited-risk', loraR: 32,
    loraAlpha: 64, epochs: 5, maxLength: 4096
  }];
}

test('every rebuilt control shows the state it was built from', () => {
  const wizard = loadWizard();
  let audited = 0;
  for (const overrides of [...scenarios(), ...counterpoints(wizard)]) {
    for (const step of wizard.STEPS) {
      const value = state(wizard, overrides);
      wizard.coerceForRisk(value);
      const pane = renderStep(wizard, value, step.id);
      const fields = Object.keys(value);
      for (const node of descendants(pane).filter(isElement)) {
        const id = node.getAttribute('id');
        if (!id) continue;
        const where = `${step.id} ${id} ${JSON.stringify(overrides)}`;
        const field = fields.find(name => idFor(name) === id);
        if (field && node.getAttribute('type') === 'checkbox') {
          assert.equal(!!node.checked, !!value[field], `${where}: checked`);
        } else if (field && node.tag === 'select') {
          const options = node.children.filter(isElement);
          const chosen = options.find(option => option.selected) || options[0];
          assert.equal(chosen.getAttribute('value'), String(value[field]), `${where}: selected option`);
        } else if (field && ['input', 'textarea'].includes(node.tag)) {
          const shown = node.value !== undefined ? node.value : node.getAttribute('value');
          assert.equal(String(shown ?? ''), String(value[field] ?? ''), `${where}: value`);
        } else if (node.tag === 'button' && hasClass(node, 'wizard-card')) {
          // An option card is `<field id>-<option>`; the longest field prefix names its field.
          const owner = fields.filter(name => id.startsWith(idFor(name) + '-'))
            .sort((a, b) => idFor(b).length - idFor(a).length)[0];
          assert.ok(owner, `${where}: a card names the field it sets`);
          const option = id.slice(idFor(owner).length + 1);
          assert.equal(hasClass(node, 'is-selected'), String(value[owner]) === option, `${where}: selection`);
        } else {
          continue;
        }
        audited += 1;
      }
    }
  }
  assert.ok(audited > 1000, `audited ${audited} controls`);
});

test('locked toggles are disabled in the strict tiers only', () => {
  const wizard = loadWizard();
  const disabled = tier => descendants(renderStep(wizard, state(wizard, { riskClassification: tier }), 'compliance'))
    .filter(node => isElement(node) && node.getAttribute('type') === 'checkbox' && node.disabled)
    .map(node => node.getAttribute('id'));
  for (const tier of ['high-risk', 'unacceptable']) assert.ok(disabled(tier).length >= 2, tier);
  for (const tier of ['unknown', 'minimal-risk', 'limited-risk']) assert.deepEqual(disabled(tier), [], tier);
});
