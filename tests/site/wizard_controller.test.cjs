'use strict';

// The wizard controller (initWizard): step bookkeeping, focus after a re-render,
// goNext's focus on the first invalid field, and the cross-tab `storage` handler.
// The renderer tests cannot see these: they call STEP_RENDERERS directly.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadWizard, bootWizard, state, descendants, hasClass, isElement, idFor, closest } = require('./wizard_harness.cjs');

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
    assert.ok(page.pane.children.length > 0, `${step.id}: the pane rendered`);
  }
  // Walk the steps with Next: each render refreshes the preview and saves the step it shows.
  const page = bootWizard(saved({ step: 0, modelPreset: 'custom', model: 'org/walked-model' }));
  for (const [index, step] of defaults.STEPS.entries()) {
    assert.equal(page.saved().step, index, `${step.id}: saved step`);
    assert.match(page.yamlOutput.innerHTML, /org\/walked-model/, `${step.id}: YAML preview`);
    page.yamlOutput.innerHTML = 'stale';
    if (index < defaults.STEPS.length - 1) page.next.events.click();
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

test('a field Next flagged drops its error, in place, once its value validates (#952)', () => {
  const cases = [
    { step: 3, overrides: { modelPreset: 'custom', model: '' }, id: 'wizard-field-model', valid: 'org/model' },
    { step: 4, overrides: { datasetName: '' }, id: 'wizard-field-dataset-name', valid: 'org/data' }
  ];
  for (const { step, overrides, id, valid } of cases) {
    const page = bootWizard(saved({ step, ...overrides }));
    const alerts = () => descendants(page.pane).filter(node => node.getAttribute && node.getAttribute('role') === 'alert');
    page.next.events.click();
    const field = page.byId(id);
    assert.ok(hasClass(field, 'is-invalid') && field.getAttribute('aria-invalid') === 'true', `${id}: flagged by Next`);
    assert.equal(alerts().length, 1, `${id}: one error row`);

    field.value = '  ';
    field.events.input();
    assert.ok(hasClass(field, 'is-invalid') && alerts().length === 1, `${id}: a value that is still invalid keeps the error`);

    if (id === 'wizard-field-model') {
      field.value = 'abc';
      field.events.input();
      assert.equal(alerts()[0].textContent, 'wizard.error.model.format', `${id}: names the check it fails now`);
    }

    field.value = valid;
    field.events.input();
    assert.equal(page.byId(id), field, `${id}: cleared in place, not rebuilt`);
    assert.ok(!hasClass(field, 'is-invalid'), `${id}: no invalid class`);
    assert.equal(field.getAttribute('aria-invalid'), 'false', `${id}: aria-invalid cleared`);
    assert.equal(alerts().length, 0, `${id}: error row removed`);
    assert.equal(page.document.activeElement, field, `${id}: focus stays in the field`);
  }
});

// A sibling tab's update, as the browser delivers it: the other tab has written its state.
function siblingWrites(page, change) {
  const stored = JSON.parse(page.storage.get('forgelm.wizard.state'));
  page.storage.set('forgelm.wizard.state', JSON.stringify({ ...stored, ...change }));
  page.listeners.storage({ key: 'forgelm.wizard.state', newValue: page.storage.get('forgelm.wizard.state') });
}

test('a sibling tab\'s update keeps this tab\'s open sections, flagged errors and focus', () => {
  const page = bootWizard(saved({ step: 6, riskClassification: 'high-risk', openAccordions: { risk_assessment: true } }));
  const errors = () => descendants(page.pane).filter(node => node.getAttribute && node.getAttribute('role') === 'alert').length;
  page.next.events.click();
  const flagged = errors();
  assert.ok(flagged > 0, 'Next flagged the step');
  page.byId('wizard-field-intended-use').focus();
  siblingWrites(page, { trainerType: 'dpo' });
  assert.equal(page.byId('wizard-accordion-risk-assessment').getAttribute('aria-expanded'), 'true', 'the section stays open');
  assert.equal(errors(), flagged, 'the flagged errors stay');
  assert.equal(page.document.activeElement, page.byId('wizard-field-intended-use'), 'focus stays in the field');
  assert.ok(!('attemptedSteps' in page.saved()), 'the flagged steps stay in this tab');
});

test('open sections stay with their tab: another tab\'s state neither closes nor opens them', () => {
  const page = bootWizard(saved({ step: 6, riskClassification: 'minimal-risk' }));
  page.byId('wizard-accordion-eval-gates').events.click();
  // The other tab's own state, not a copy of this one: another answer, and other sections open.
  const other = { ...saved({ step: 6, riskClassification: 'minimal-risk', trainerType: 'kto' }), openAccordions: { risk_assessment: true } };
  page.storage.set('forgelm.wizard.state', JSON.stringify(other));
  page.listeners.storage({ key: 'forgelm.wizard.state', newValue: page.storage.get('forgelm.wizard.state') });
  assert.equal(page.saved().trainerType, 'kto', 'the other tab\'s answer is taken');
  assert.equal(page.byId('wizard-accordion-eval-gates').getAttribute('aria-expanded'), 'true', 'this tab\'s section stays open');
  assert.equal(page.byId('wizard-accordion-risk-assessment').getAttribute('aria-expanded'), 'false', 'the other\'s does not open');
  assert.ok(!('openAccordions' in page.saved()), 'open sections are not saved, so no tab overwrites another\'s');
});

test('a focused section header keeps focus, and a control the update removes leaves it in the dialog', () => {
  const page = bootWizard(saved({ step: 6, riskClassification: 'high-risk' }));
  page.byId('wizard-accordion-eval-gates').focus();
  siblingWrites(page, { trainerType: 'orpo' });
  assert.equal(page.document.activeElement, page.byId('wizard-accordion-eval-gates'));

  page.byId('wizard-field-provider-name').focus();
  siblingWrites(page, { riskClassification: 'minimal-risk' });
  assert.equal(page.byId('wizard-field-provider-name'), null, 'minimal risk drops the provider fields');
  assert.equal(page.document.activeElement, page.dialog, 'focus stays in the dialog');
});

test('reopening the wizard starts from answers another tab saved while it was closed', () => {
  const page = bootWizard(saved({ step: 2, trainerType: 'sft' }));
  page.closeButton.events.click();
  // While this tab's wizard is closed the storage handler ignores updates; another tab moves on.
  siblingWrites(page, { step: 4, trainerType: 'dpo', datasetName: 'org/newer' });
  page.opener.events.click();
  assert.equal(page.counterCurrent.textContent, '5', 'the newer step');
  assert.equal(page.saved().trainerType, 'dpo', 'the newer answers are not overwritten');
  assert.equal(page.saved().datasetName, 'org/newer');
});

test('reopening keeps this tab\'s answers when storage refuses to save them', () => {
  const page = bootWizard(saved({ step: 3, modelPreset: 'custom', model: 'org/first' }));
  page.storage.set = () => { throw new Error('QuotaExceededError'); };
  const field = page.byId('wizard-field-model');
  field.value = 'org/typed';
  field.events.input();
  page.closeButton.events.click();
  page.opener.events.click();
  // The rebuilt input carries its value as an attribute in the DOM shim.
  assert.equal(page.byId('wizard-field-model').getAttribute('value'), 'org/typed', 'the stored copy is older, not newer');
  assert.match(page.yamlOutput.innerHTML, /org\/typed/);
});

// Every field validateStep checks: its step, the state that shows it, a value it rejects and one it accepts.
const VALIDATED = [
  [3, { modelPreset: 'custom', model: '' }, 'model', '', 'org/model'],
  [4, { datasetName: '' }, 'datasetName', '', 'org/data'],
  ...['collectionMethod', 'annotationProcess', 'knownBiases']
    .map(field => [4, { riskClassification: 'high-risk' }, field, '', 'Documented.']),
  ...['providerName', 'systemName', 'intendedPurpose', 'intendedUse', 'foreseeableMisuse', 'mitigationMeasures']
    .map(field => [6, { riskClassification: 'high-risk' }, field, '', 'Documented.']),
  [6, { benchmarkEnabled: true, benchmarkTasks: '' }, 'benchmarkTasks', '', 'arc_easy'],
  [6, { judgeEnabled: true, judgeApiKeyEnv: '' }, 'judgeApiKeyEnv', '', 'OPENAI_API_KEY'],
  [7, { webhookEnabled: true, webhookUrl: '' }, 'webhookUrl', 'env:', 'env:SLACK_WEBHOOK'],
  [7, { monitoringEnabled: true, monitoringEndpoint: 'env:' }, 'monitoringEndpoint', 'env:', 'https://monitor.example.com/p'],
  [7, { monitoringEnabled: true, monitoringCheckIntervalHours: 0 }, 'monitoringCheckIntervalHours', '0', '24'],
  ...['syntheticTeacherModel', 'syntheticSeedFile', 'syntheticApiKeyEnv']
    .map(field => [7, { syntheticEnabled: true, [field]: '' }, field, '', 'value'])
];

test('every validated field drops its error once it validates, and shows it again once it does not (#952)', () => {
  assert.equal(VALIDATED.length, 19);
  for (const [step, overrides, field, invalid, valid] of VALIDATED) {
    const page = bootWizard(saved({ step, ...overrides }));
    page.next.events.click();
    const control = page.byId(idFor(field));
    const row = closest(control, 'wizard-row');
    const errors = () => row.children.filter(node => isElement(node) && hasClass(node, 'wizard-error'));
    const shape = () => row.children.map(node => `${node.tag}.${node.className}`);
    assert.ok(hasClass(control, 'is-invalid') && errors().length === 1, `${field}: flagged by Next`);
    const rendered = shape();

    control.value = valid;
    control.events.input();
    assert.equal(page.byId(idFor(field)), control, `${field}: corrected in place`);
    assert.ok(!hasClass(control, 'is-invalid') && errors().length === 0, `${field}: the error clears`);
    assert.equal(String(page.saved()[field]), valid, `${field}: the value is saved`);

    control.value = invalid;
    control.events.input();
    assert.ok(hasClass(control, 'is-invalid') && errors().length === 1, `${field}: broken again, flagged again`);
    assert.match(errors()[0].textContent, /^wizard\.error\./, `${field}: the error names its check`);
    assert.deepEqual(shape(), rendered, `${field}: the error row comes back where a render puts it`);
    if (control.getAttribute('aria-invalid') !== null) assert.equal(control.getAttribute('aria-invalid'), 'true');
  }
});

test('Next opens the section that hosts each invalid field, and a section header toggles it', () => {
  let sections = 0;
  for (const [step, overrides, field] of VALIDATED) {
    const page = bootWizard(saved({ step, ...overrides }));
    page.next.events.click();
    const section = closest(page.byId(idFor(field)), 'wizard-accordion');
    if (!section) continue;
    sections += 1;
    const header = section.children.find(node => hasClass(node, 'wizard-accordion-header'));
    assert.ok(hasClass(section, 'is-open'), `${field}: its section is open`);
    assert.equal(header.getAttribute('aria-expanded'), 'true', `${field}: its header says so`);
  }
  assert.ok(sections >= 14, `${sections} fields sit in a section`);
  const page = bootWizard(saved({ step: 7 }));
  const header = page.byId('wizard-accordion-webhook');
  for (const open of [true, false]) {
    header.events.click();
    assert.equal(header.getAttribute('aria-expanded'), String(open));
    assert.equal(hasClass(closest(header, 'wizard-accordion'), 'is-open'), open);
  }
});

test('every control saves what it sets', () => {
  const shown = { riskClassification: 'limited-risk', benchmarkEnabled: true, judgeEnabled: true, webhookEnabled: true,
    monitoringEnabled: true, syntheticEnabled: true, modelPreset: 'custom', trainerType: 'grpo', autoRevert: true };
  const fields = Object.keys(defaults.defaultState());
  let checked = 0;
  for (const [index, step] of defaults.STEPS.entries()) {
    const page = bootWizard(saved({ ...shown, step: index }));
    for (const node of descendants(page.pane).filter(isElement)) {
      const id = node.getAttribute('id') || '';
      // A control is `<field id>`, an option card `<field id>-<option>`: the longest field prefix names it.
      const field = fields.filter(name => id === idFor(name) || id.startsWith(idFor(name) + '-'))
        .sort((a, b) => b.length - a.length)[0];
      if (!field || node.disabled) continue;
      const before = JSON.stringify(page.saved()[field]);
      const type = node.getAttribute('type');
      if (node.tag === 'input' && type === 'checkbox') {
        node.checked = !node.checked;
        node.events.change();
      } else if (node.tag === 'select') {
        const option = node.children.filter(isElement).find(item => item.getAttribute('value') !== String(page.saved()[field]));
        node.value = option.getAttribute('value');
        node.events.change();
      } else if (node.tag === 'button' && hasClass(node, 'wizard-card')) {
        if (hasClass(node, 'is-selected')) continue;
        node.events.click();
      } else if (node.tag === 'input' && (type === 'number' || type === 'range')) {
        // A bound the field is not at, so the value changes and stays valid.
        const current = String(page.saved()[field]);
        node.value = [node.getAttribute('max'), node.getAttribute('min')].find(bound => bound !== null && bound !== current);
        (node.events.input || node.events.change)();
      } else if (['input', 'textarea'].includes(node.tag)) {
        node.value = `saved ${field}`;
        (node.events.input || node.events.change)();
      } else {
        continue;
      }
      assert.notEqual(JSON.stringify(page.saved()[field]), before, `${step.id}: ${id} saves what it sets`);
      checked += 1;
    }
  }
  assert.ok(checked > 60, `checked ${checked} controls`);
});

test('a re-render leaves focus outside the step alone, and refocuses a control without scrolling', () => {
  const page = bootWizard(saved({ step: 6, riskClassification: 'high-risk' }));
  page.next.focus();
  siblingWrites(page, { trainerType: 'dpo' });
  assert.equal(page.document.activeElement, page.next, 'focus stays on Next');
  page.byId('wizard-field-provider-name').focus();
  siblingWrites(page, { trainerType: 'orpo' });
  assert.equal(page.document.activeElement, page.byId('wizard-field-provider-name'));
  assert.deepEqual({ ...page.byId('wizard-field-provider-name').focusOptions }, { preventScroll: true });
});

test('another tab resetting the wizard, or clearing storage, resets this one; other keys change nothing', () => {
  for (const key of ['forgelm.wizard.state', null]) {
    const page = bootWizard(saved({ step: 4, trainerType: 'dpo' }));
    page.listeners.storage({ key: 'forgelm.theme', newValue: 'dark' });
    assert.equal(page.counterCurrent.textContent, '5', 'another key changes nothing');
    page.storage.clear();
    page.listeners.storage({ key, newValue: null });
    assert.equal(page.counterCurrent.textContent, '1', `${key}: back to the first step`);
    assert.equal(page.saved().trainerType, defaults.defaultState().trainerType, `${key}: the defaults are saved`);
  }
});

test('a saved step that is no step number starts over instead of leaving the pane blank', () => {
  for (const [step, counter] of [[null, '1'], ['3', '1'], [1.5, '1'], [-3, '1'], [99, '9']]) {
    const page = bootWizard(saved({ step }));
    assert.ok(page.pane.children.length > 0, `${JSON.stringify(step)}: the pane renders`);
    assert.equal(page.counterCurrent.textContent, counter, `${JSON.stringify(step)}: the counter`);
  }
});

test('Reset on the review step starts over and saves the defaults', () => {
  const page = bootWizard(saved({ step: 8, trainerType: 'dpo' }));
  const reset = descendants(page.pane).find(node => node.tag === 'button' &&
    descendants(node).some(child => child.textContent === 'wizard.review.reset'));
  reset.events.click();
  assert.equal(page.counterCurrent.textContent, '1');
  assert.equal(page.saved().trainerType, defaults.defaultState().trainerType);
});
