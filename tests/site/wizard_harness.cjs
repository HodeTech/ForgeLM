'use strict';

// Shared harness for the browser wizard regressions (not a test file: the
// `node --test tests/site/*.test.cjs` glob does not collect it).
//
// It runs the shipped site/js/wizard.js in a VM against a minimal DOM shim
// and exposes the wizard's closure functions through a test-only seam, so the
// tests exercise the real renderers and YAML builder rather than a copy.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');

function element(tag) {
  const node = {
    tag, children: [], attrs: {}, events: {}, className: '',
    appendChild(child) { this.children.push(child); child.parent = this; return child; },
    setAttribute(key, value) { this.attrs[key] = String(value); },
    getAttribute(key) { return Object.hasOwn(this.attrs, key) ? this.attrs[key] : null; },
    addEventListener(key, handler) { this.events[key] = handler; },
    remove() { this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; },
    // The wizard only looks elements up by class (`.wizard-error`, `.wizard-input.is-invalid`).
    querySelector(selector) {
      assert.match(selector, /^(?:\.[\w-]+)+$/, `the DOM shim only supports class selectors, got ${selector}`);
      const wanted = selector.slice(1).split('.');
      return descendants(this).find(child => wanted.every(value => hasClass(child, value))) || null;
    }
  };
  node.classList = {
    add(value) { if (!hasClass(node, value)) node.className = `${node.className} ${value}`.trim(); },
    remove(value) { node.className = classes(node).filter(item => item !== value).join(' '); },
    contains(value) { return hasClass(node, value); },
    toggle(value, force) {
      const on = force === undefined ? !hasClass(node, value) : !!force;
      if (on) node.classList.add(value); else node.classList.remove(value);
      return on;
    }
  };
  return node;
}

function classes(node) {
  return (node.className || '').split(' ').filter(Boolean);
}

// Text nodes (`{ text }`) have no class; an element may have several.
function hasClass(node, value) {
  return classes(node).includes(value);
}

function isElement(node) {
  return !Object.hasOwn(node, 'text');
}

// Run the site scripts in *context* and return the wizard's closure functions.
function runWizard(context) {
  for (const file of ['wizard_defaults.js', 'translations.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, 'site/js', file), 'utf8'), context);
  }
  const marker = '  /* ── Boot ─';
  const source = fs.readFileSync(path.join(root, 'site/js/wizard.js'), 'utf8');
  assert.equal(source.split(marker).length, 2, 'test seam must identify the boot boundary exactly once');
  // Expose closure functions only in the VM; production has no test-only API. With
  // readyState 'loading' the boot code only registers a listener, so nothing runs yet.
  vm.runInContext(source.replace(marker,
    '  window.testWizard = {defaultState, buildYaml, validateStep, coerceForRisk, parseEnvRef, STEP_RENDERERS, STEPS,' +
    ' initWizard};\n' + marker), context);
  return { ...context.window.testWizard, translations: context.window.ForgeLMTranslations };
}

function loadWizard() {
  const document = {
    readyState: 'loading', addEventListener() {}, querySelector() { return null; }, createElement: element,
    createElementNS: (_, tag) => element(tag), createTextNode: text => ({ text })
  };
  return runWizard(vm.createContext({ window: {}, document, URL }));
}

// The controller: the real initWizard() against the modal skeleton it looks up, with
// focus, getElementById and an in-memory localStorage, so render()'s bookkeeping and
// focus restoration, goNext() and the cross-tab `storage` handler run as on the page.
function bootWizard(saved) {
  let active = null;
  const make = tag => {
    const node = element(tag);
    node.focus = () => { active = node; };
    node.setSelectionRange = (start, end) => { node.selectionStart = start; node.selectionEnd = end; };
    node.contains = other => { for (let cur = other; cur; cur = cur.parent) if (cur === node) return true; return false; };
    Object.defineProperty(node, 'id', { get: () => node.getAttribute('id') || '' });
    return node;
  };
  const selectors = {
    dialog: '.wizard-dialog', pane: '[data-wizard-pane]', yamlOutput: '[data-wizard-yaml-output]',
    yamlPane: '[data-wizard-yaml-pane]', yamlToggle: '[data-wizard-yaml-toggle]', yamlCopy: '[data-wizard-yaml-copy]',
    progress: '[data-wizard-progress]', counterCurrent: '[data-wizard-counter-current]',
    counterTotal: '[data-wizard-counter-total]', stepName: '[data-wizard-step-name]',
    prev: '[data-wizard-prev]', next: '[data-wizard-next]'
  };
  const modal = make('div');
  const parts = {};
  for (const name of Object.keys(selectors)) parts[name] = modal.appendChild(make('div'));
  parts.progress.style = {};
  const nextLabel = parts.next.appendChild(make('span'));
  parts.next.querySelector = () => nextLabel;
  Object.defineProperty(parts.pane, 'innerHTML', {
    set(value) {
      assert.equal(value, '');
      for (const child of parts.pane.children) child.parent = null;  // detached, as in the DOM
      parts.pane.children = [];
    }
  });
  modal.querySelector = selector => parts[Object.keys(selectors).find(name => selectors[name] === selector)] || null;
  Object.assign(modal, { querySelectorAll: () => [], hasAttribute: () => false, removeAttribute() {}, setAttribute() {} });
  modal.dataset = {};
  const body = make('body');
  body.style = {};
  const opener = make('button');
  const document = {
    readyState: 'loading', addEventListener() {}, body, createTextNode: text => ({ text }),
    createElement: make, createElementNS: (_, tag) => make(tag),
    get activeElement() { return active || body; },
    getElementById: id => descendants(modal).find(node => isElement(node) && node.getAttribute('id') === id) || null,
    querySelector: selector => (selector === '[data-wizard-modal]' ? modal
      : selector === '[data-wizard-yaml-output]' ? parts.yamlOutput : null),
    querySelectorAll: selector => (selector === '[data-wizard-open]' ? [opener] : [])
  };
  const storage = new Map(saved ? [['forgelm.wizard.state', JSON.stringify(saved)]] : []);
  const listeners = {};
  const window = {
    localStorage: {
      getItem: key => (storage.has(key) ? storage.get(key) : null), setItem: (key, value) => storage.set(key, String(value)),
      removeItem: key => storage.delete(key)
    },
    location: { search: '', pathname: '/quickstart.html' }, history: { replaceState() {} },
    addEventListener: (type, handler) => { listeners[type] = handler; }
  };
  const wizard = runWizard(vm.createContext({ window, document, URL, URLSearchParams, setTimeout: () => 0 }));
  wizard.initWizard();
  opener.events.click();
  const byId = id => document.getElementById(id);
  return { ...parts, wizard, document, storage, listeners, byId, saved: () => JSON.parse(storage.get('forgelm.wizard.state')) };
}

function state(wizard, overrides = {}) {
  return Object.assign(wizard.defaultState(), { model: 'org/model', datasetName: 'org/dataset' }, overrides);
}

function descendants(node) {
  return [node, ...(node.children || []).flatMap(descendants)];
}

// Visible text of a node: text nodes, `text:` attributes and nested children.
function textOf(node) {
  if (Object.hasOwn(node, 'text')) return node.text;
  return (node.textContent || '') + (node.children || []).map(textOf).join('');
}

// Render one step into a fresh pane the way the controller's render() does.
// The rerender callback rebuilds the same pane, so a toggle that re-renders
// the step (benchmark, judge, webhook, ...) behaves as it does in the page.
function renderStep(wizard, value, stepId) {
  const pane = element('div');
  const rerender = () => {
    for (const child of pane.children) child.parent = null;
    pane.children = [];
    wizard.coerceForRisk(value);
    wizard.STEP_RENDERERS[stepId](pane, value, rerender, () => {});
  };
  rerender();
  return pane;
}

const RISK_TIERS = ['unknown', 'minimal-risk', 'limited-risk', 'high-risk', 'unacceptable'];

// State overrides that jointly take every conditional branch of every
// renderer: optional gates on and off, both detail modes, the custom-model
// and GaLore paths, and all six trainers.
function scenarios() {
  const out = [];
  for (const riskClassification of RISK_TIERS) {
    for (const detailsVisible of [true, false]) {
      for (const on of [false, true]) {
        out.push({
          riskClassification, detailsVisible, autoRevert: on, benchmarkEnabled: on, judgeEnabled: on,
          webhookEnabled: on, monitoringEnabled: on, syntheticEnabled: on, galoreEnabled: on,
          modelPreset: on ? 'custom' : 'llama3-8b', trainerType: on ? 'grpo' : 'sft'
        });
      }
    }
  }
  for (const trainerType of ['sft', 'dpo', 'orpo', 'simpo', 'kto', 'grpo']) out.push({ trainerType });
  // After a failed Next: every validated field is invalid, so the error rows render too.
  for (const riskClassification of ['limited-risk', 'high-risk']) {
    out.push({
      riskClassification, attemptedSteps: ['model', 'dataset', 'compliance', 'operations'],
      modelPreset: 'custom', model: '', datasetName: '', benchmarkEnabled: true, benchmarkTasks: '',
      judgeEnabled: true, judgeApiKeyEnv: '', webhookEnabled: true, webhookUrl: '', monitoringEnabled: true,
      monitoringEndpoint: 'env:', monitoringCheckIntervalHours: 0, syntheticEnabled: true,
      syntheticTeacherModel: '', syntheticSeedFile: '', syntheticApiKeyEnv: ''
    });
  }
  return out;
}

module.exports = {
  root, element, loadWizard, bootWizard, state, descendants, textOf, hasClass, isElement, renderStep, RISK_TIERS,
  scenarios
};
