'use strict';

// Every visible wizard caption must name its control (#950).
//
// Captions were sibling <label> elements without `for`, so assistive
// technology named text inputs after their placeholder and selects, number
// inputs and sliders not at all, and a click on a caption focused nothing.
// These tests render every step through the real renderers and check the
// caption <-> control association, id uniqueness and id stability.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadWizard, state, descendants, textOf, hasClass, isElement, renderStep, scenarios } = require('./wizard_harness.cjs');

// Every captioned control, by the state field it edits, with the i18n key of
// its caption. Outside a browser `tr()` has no table, so captions render as
// their own keys and a swapped `for` shows up as the wrong key.
const CAPTIONS = {
  model: 'wizard.model.custom.label',
  datasetName: 'wizard.dataset.name.label',
  collectionMethod: 'wizard.governance.collection_method.label',
  annotationProcess: 'wizard.governance.annotation_process.label',
  knownBiases: 'wizard.governance.known_biases.label',
  galoreOptim: 'wizard.training.galore_optim.label',
  loraR: 'wizard.training.lora_r.label',
  loraAlpha: 'wizard.training.lora_alpha.label',
  epochs: 'wizard.training.epochs.label',
  learningRate: 'wizard.training.lr.label',
  batchSize: 'wizard.training.batch.label',
  maxLength: 'wizard.training.max_length.label',
  dpoBeta: 'wizard.training.dpo_beta.label',
  orpoBeta: 'wizard.training.orpo_beta.label',
  simpoGamma: 'wizard.training.simpo_gamma.label',
  simpoBeta: 'wizard.training.simpo_beta.label',
  ktoBeta: 'wizard.training.kto_beta.label',
  grpoNumGenerations: 'wizard.training.grpo_num_generations.label',
  grpoMaxCompletionLength: 'wizard.training.grpo_max_completion_length.label',
  grpoRewardModel: 'wizard.training.grpo_reward_model.label',
  riskClassification: 'wizard.risk.label',
  maxAcceptableLoss: 'wizard.eval.max_loss.label',
  providerName: 'wizard.compliance.provider_name.label',
  systemName: 'wizard.compliance.system_name.label',
  intendedPurpose: 'wizard.compliance.intended_purpose.label',
  intendedUse: 'wizard.risk_assessment.intended_use.label',
  foreseeableMisuse: 'wizard.risk_assessment.foreseeable_misuse.label',
  mitigationMeasures: 'wizard.risk_assessment.mitigation_measures.label',
  benchmarkTasks: 'wizard.benchmark.tasks.label',
  benchmarkMinScore: 'wizard.benchmark.min_score.label',
  judgeModel: 'wizard.judge.model.label',
  judgeApiKeyEnv: 'wizard.judge.api_key_env.label',
  judgeMinScore: 'wizard.judge.min_score.label',
  webhookUrl: 'wizard.webhook.url.label',
  monitoringEndpoint: 'wizard.monitoring.endpoint.label',
  monitoringCheckIntervalHours: 'wizard.monitoring.interval.label',
  metricsExport: 'wizard.monitoring.metrics_export.label',
  syntheticTeacherModel: 'wizard.synthetic.teacher_model.label',
  syntheticSeedFile: 'wizard.synthetic.seed_file.label',
  syntheticApiKeyEnv: 'wizard.synthetic.api_key_env.label',
  retentionAuditLog: 'wizard.retention.audit_log.label',
  retentionStaging: 'wizard.retention.staging.label',
  retentionEphemeral: 'wizard.retention.ephemeral.label',
  retentionRawDocuments: 'wizard.retention.raw_documents.label',
  retentionEnforce: 'wizard.retention.enforce.label'
};

// The id scheme, restated rather than imported, so a change to it is visible here.
const idFor = field => 'wizard-field-' + field.replace(/[A-Z]/g, c => '-' + c.toLowerCase());
const FIELD_BY_ID = new Map(Object.keys(CAPTIONS).map(field => [idFor(field), field]));

function ancestors(node) {
  const out = [];
  for (let cur = node.parent; cur; cur = cur.parent) out.push(cur);
  return out;
}

// Check one rendered pane; return the fields whose controls it contained.
function auditPane(pane, where) {
  const nodes = descendants(pane).filter(isElement);
  const ids = nodes.map(node => node.getAttribute('id')).filter(id => id !== null);
  assert.deepEqual(ids.filter((id, i) => ids.indexOf(id) !== i), [], `${where}: duplicate ids`);
  const byId = new Map(nodes.filter(node => node.getAttribute('id') !== null).map(node => [node.getAttribute('id'), node]));
  const labels = nodes.filter(node => node.tag === 'label');
  const seen = [];

  for (const control of nodes.filter(node => ['input', 'select', 'textarea'].includes(node.tag))) {
    const type = control.getAttribute('type');
    if (type === 'checkbox' || type === 'radio') {
      // Wrapped labels already name toggles: keep them wrapped, without a competing `for`.
      const wrapper = ancestors(control).find(node => node.tag === 'label');
      assert.ok(wrapper, `${where}: a ${type} must stay inside its <label>`);
      assert.equal(wrapper.getAttribute('for'), null, `${where}: a wrapping label needs no for`);
      assert.equal(descendants(wrapper).filter(node => node.tag === 'input').length, 1, `${where}: one toggle per label`);
      assert.ok(textOf(wrapper).trim(), `${where}: a ${type} label has text`);
      continue;
    }
    const id = control.getAttribute('id');
    assert.ok(FIELD_BY_ID.has(id), `${where}: <${control.tag}> has id ${id}, which no caption names`);
    const field = FIELD_BY_ID.get(id);
    const captions = labels.filter(label => label.getAttribute('for') === id);
    assert.equal(captions.length, 1, `${where}: exactly one caption names ${id}`);
    const caption = captions[0];
    assert.ok(hasClass(caption, 'wizard-row-label'), `${where}: ${id} is named by its visible row caption`);
    // Either attribute would override the <label> in the accessible-name computation.
    for (const attribute of ['aria-label', 'aria-labelledby']) {
      assert.equal(control.getAttribute(attribute), null, `${where}: ${attribute} on ${id} would hide its caption`);
    }
    assert.ok(textOf(caption).startsWith(CAPTIONS[field]), `${where}: ${id} is captioned ${textOf(caption)}`);
    const row = ancestors(control).find(node => hasClass(node, 'wizard-row'));
    assert.ok(row && descendants(row).includes(caption), `${where}: ${id} and its caption share a row`);
    seen.push(field);
  }

  // No orphan captions: every label wraps a control or points at one in this pane.
  for (const label of labels) {
    const target = label.getAttribute('for');
    if (target === null) {
      assert.ok(descendants(label).some(node => ['input', 'select', 'textarea'].includes(node.tag)),
        `${where}: a label without for must wrap a control`);
    } else {
      assert.ok(byId.has(target) && ['input', 'select', 'textarea'].includes(byId.get(target).tag),
        `${where}: label for=${target} points at no control`);
    }
  }
  // A caption that cannot be a <label> (the LoRA method cards are buttons) names its group.
  for (const caption of nodes.filter(node => hasClass(node, 'wizard-row-label') && node.tag !== 'label')) {
    const id = caption.getAttribute('id');
    assert.ok(id, `${where}: a non-label caption needs an id`);
    const groups = nodes.filter(node => node.getAttribute('aria-labelledby') === id);
    assert.equal(groups.length, 1, `${where}: exactly one element is labelled by ${id}`);
    assert.equal(groups[0].getAttribute('role'), 'group', `${where}: ${id} labels a group`);
  }
  return seen;
}

test('every caption names its control on every step and conditional branch', () => {
  const wizard = loadWizard();
  const covered = new Set();
  for (const overrides of scenarios()) {
    for (const step of wizard.STEPS) {
      const pane = renderStep(wizard, state(wizard, overrides), step.id);
      for (const field of auditPane(pane, `${step.id} ${JSON.stringify(overrides)}`)) covered.add(field);
    }
  }
  assert.deepEqual(Object.keys(CAPTIONS).filter(field => !covered.has(field)), [],
    'the scenarios must render every captioned control at least once');
});

test('every caption key is translated in the maintained locales', () => {
  const { translations } = loadWizard();
  for (const locale of ['en', 'tr']) {
    const missing = Object.values(CAPTIONS).filter(key => typeof translations[locale][key] !== 'string');
    assert.deepEqual(missing, [], `${locale}: captions with no translation render as their key`);
  }
});

test('the LoRA method caption names its group of method buttons', () => {
  const wizard = loadWizard();
  const pane = renderStep(wizard, state(wizard, { galoreEnabled: false }), 'training');
  const group = descendants(pane).find(node => isElement(node) && node.getAttribute('role') === 'group');
  const caption = descendants(pane).find(node => isElement(node) && node.getAttribute('id') === group.getAttribute('aria-labelledby'));
  assert.equal(textOf(caption), 'wizard.training.lora_method.label');
  assert.deepEqual(group.children.map(card => card.tag), ['button', 'button', 'button', 'button']);
});

test('every render of a step builds the same ids in the same order', () => {
  const wizard = loadWizard();
  const idsOf = pane => descendants(pane).filter(isElement).map(node => node.getAttribute('id')).filter(Boolean);
  for (const overrides of scenarios()) {
    for (const step of wizard.STEPS) {
      const first = idsOf(renderStep(wizard, state(wizard, overrides), step.id));
      const second = idsOf(renderStep(wizard, state(wizard, overrides), step.id));
      assert.deepEqual(second, first, `${step.id}: same ids in the same order on every render`);
    }
  }
});

test('toggles that re-render a step, and accordion toggles, keep one id per control', () => {
  const wizard = loadWizard();
  const value = state(wizard, { riskClassification: 'high-risk' });
  const pane = renderStep(wizard, value, 'compliance');
  const toggle = labelKey => descendants(pane).find(node => node.tag === 'label' && textOf(node) === labelKey).children[0];
  // Each gate re-renders the whole step with its own new controls.
  const enable = labelKey => {
    const checkbox = toggle(labelKey);
    checkbox.checked = true;
    checkbox.events.change();
  };
  enable('wizard.benchmark.enabled.label');
  assert.deepEqual(auditPane(pane, 'compliance after enabling the benchmark').filter(field =>
    ['benchmarkTasks', 'judgeApiKeyEnv'].includes(field)), ['benchmarkTasks']);
  enable('wizard.judge.enabled.label');
  assert.deepEqual(auditPane(pane, 'compliance after enabling both gates').filter(field =>
    ['benchmarkTasks', 'judgeApiKeyEnv'].includes(field)), ['benchmarkTasks', 'judgeApiKeyEnv']);
  // Collapsing and reopening an accordion flips its class in place; it must not rebuild controls.
  const before = descendants(pane).filter(isElement).map(node => node.getAttribute('id')).filter(Boolean);
  for (const header of descendants(pane).filter(node => hasClass(node, 'wizard-accordion-header'))) {
    header.events.click();
    header.events.click();
  }
  assert.deepEqual(descendants(pane).filter(isElement).map(node => node.getAttribute('id')).filter(Boolean), before);
  auditPane(pane, 'compliance after toggling every accordion twice');
});

test('every control that can re-render a step keeps a stable id, so focus can return to it', () => {
  const wizard = loadWizard();
  for (const overrides of scenarios()) {
    for (const step of wizard.STEPS) {
      const pane = renderStep(wizard, state(wizard, overrides), step.id);
      for (const node of descendants(pane).filter(isElement)) {
        const reRenders = node.getAttribute('type') === 'checkbox' || node.tag === 'select' || hasClass(node, 'wizard-card');
        if (reRenders) assert.match(node.getAttribute('id') || '', /^wizard-field-[a-z0-9-]+$/, `${step.id}: ${node.tag} needs an id`);
      }
    }
  }
});

test('a "required for risk tier" badge stays a separate word in the caption', () => {
  const wizard = loadWizard();
  const pane = renderStep(wizard, state(wizard, { riskClassification: 'high-risk' }), 'compliance');
  const texts = descendants(pane).filter(node => node.tag === 'label').map(textOf);
  for (const caption of ['wizard.compliance.provider_name.label', 'wizard.eval.safety.label', 'wizard.compliance.approval.label']) {
    assert.ok(texts.includes(`${caption} wizard.locked.badge`), `${caption} is followed by a space before its badge`);
  }
});
