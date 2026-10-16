'use strict';

// The wizard controller (initWizard): step bookkeeping, focus after a re-render,
// goNext's focus on the first invalid field, and the cross-tab `storage` handler.
// The renderer tests cannot see these: they call STEP_RENDERERS directly.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadWizard, bootWizard, state } = require('./wizard_harness.cjs');

const defaults = loadWizard();

function saved(overrides) {
  return state(defaults, overrides);
}

test('the counter, step name, YAML preview and saved state track every step (#879)', () => {
  const extras = { riskClassification: 'high-risk', benchmarkEnabled: true, judgeEnabled: true, webhookEnabled: true };
  for (const [index, step] of defaults.STEPS.entries()) {
    const page = bootWizard(saved({ ...extras, step: index }));
    assert.equal(page.counterCurrent.textContent, String(index + 1), `${step.id}: counter`);
    assert.equal(page.stepName.textContent, step.nameKey, `${step.id}: step name`);
    assert.match(page.yamlOutput.innerHTML, /name_or_path/, `${step.id}: YAML preview`);
    assert.equal(page.saved().step, index, `${step.id}: saved step`);
    assert.ok(page.pane.children.length, `${step.id}: the pane rendered`);
  }
});

test('a toggle that reveals fields keeps focus on its rebuilt checkbox', () => {
  const page = bootWizard(saved({ step: 6, riskClassification: 'minimal-risk' }));
  const toggle = page.byId('wizard-field-benchmark-enabled');
  toggle.focus();
  toggle.checked = true;
  toggle.events.change();
  const rebuilt = page.byId('wizard-field-benchmark-enabled');
  assert.notEqual(rebuilt, toggle, 'the step was rebuilt');
  assert.equal(page.document.activeElement, rebuilt);
  assert.ok(page.byId('wizard-field-benchmark-tasks'), 'the revealed field is there');
});

test('the risk select and an option card keep focus when they re-render the step', () => {
  const compliance = bootWizard(saved({ step: 6, riskClassification: 'minimal-risk' }));
  const select = compliance.byId('wizard-field-risk-classification');
  select.focus();
  select.value = 'high-risk';
  select.events.change();
  assert.equal(compliance.document.activeElement, compliance.byId('wizard-field-risk-classification'));
  assert.ok(compliance.byId('wizard-field-provider-name'), 'high-risk reveals the provider fields');

  const trainer = bootWizard(saved({ step: 2 }));
  const card = trainer.byId('wizard-field-trainer-type-dpo');
  card.focus();
  card.events.click();
  assert.notEqual(trainer.byId('wizard-field-trainer-type-dpo'), card);
  assert.equal(trainer.document.activeElement, trainer.byId('wizard-field-trainer-type-dpo'));
});

test('a step change does not move focus into the next step', () => {
  const page = bootWizard(saved({ step: 0 }));
  const card = page.byId('wizard-field-experience-expert');
  card.focus();
  page.next.events.click();
  assert.equal(page.counterCurrent.textContent, '2');
  assert.ok(!page.pane.contains(page.document.activeElement));
});

test('Next on an invalid step focuses its first invalid field', () => {
  const page = bootWizard(saved({ step: 6, riskClassification: 'high-risk' }));
  page.next.events.click();
  assert.equal(page.counterCurrent.textContent, '7', 'the step does not advance');
  assert.equal(page.document.activeElement, page.byId('wizard-field-provider-name'));
});

test('a sibling tab\'s update keeps focus and the caret in the field being edited', () => {
  const page = bootWizard(saved({ step: 4, datasetName: 'org/data' }));
  const field = page.byId('wizard-field-dataset-name');
  field.focus();
  field.setSelectionRange(3, 5);
  page.listeners.storage({ key: 'forgelm.wizard.state', newValue: page.storage.get('forgelm.wizard.state') });
  const rebuilt = page.byId('wizard-field-dataset-name');
  assert.notEqual(rebuilt, field, 'the step was rebuilt');
  assert.equal(page.document.activeElement, rebuilt);
  assert.deepEqual([rebuilt.selectionStart, rebuilt.selectionEnd], [3, 5]);
});
