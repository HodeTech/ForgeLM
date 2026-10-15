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
    remove() { this.parent.children = this.parent.children.filter(child => child !== this); },
    // The renderers only look rows up by class (`row.querySelector('.wizard-error')`).
    querySelector(selector) {
      assert.match(selector, /^\.[\w-]+$/, `the DOM shim only supports class selectors, got ${selector}`);
      return descendants(this).find(child => hasClass(child, selector.slice(1))) || null;
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

function hasClass(node, value) {
  return classes(node).includes(value);
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
    '  window.testWizard = {defaultState, buildYaml, validateStep, coerceForRisk, parseEnvRef, STEP_RENDERERS, STEPS};\n' +
    marker), context);
  return { ...context.window.testWizard, translations: context.window.ForgeLMTranslations };
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

// Render one step into a fresh pane, the way the controller's render() does.
function renderStep(wizard, value, stepId) {
  const pane = element('div');
  wizard.coerceForRisk(value);
  wizard.STEP_RENDERERS[stepId](pane, value, () => {}, () => {});
  return pane;
}

module.exports = { root, element, loadWizard, state, descendants, textOf, renderStep };
