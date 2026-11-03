'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const { test } = require('node:test');
const { root } = require('./wizard_harness.cjs');

const localPython = path.join(root, '.venv', 'bin', 'python');
const python = process.env.FORGELM_TEST_PYTHON || (fs.existsSync(localPython) ? localPython : 'python3');
const compiled = JSON.parse(execFileSync(python, ['-c', `
import json
from html.parser import HTMLParser
from pathlib import Path
from tools.build_usermanuals import parse_front_matter, render_markdown

class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a' and 'href' in attrs:
            self.links.append(attrs['href'])

out = {}
for lang in ('en', 'tr'):
    src = Path(f'docs/usermanuals/{lang}/reference/json-output.md').read_text(encoding='utf-8')
    meta, body = parse_front_matter(src)
    html, headings = render_markdown(body)
    elements = Elements()
    elements.feed(html)
    out[lang] = dict(title=meta['title'], html=html, headings=headings, ids=elements.ids, links=elements.links)
print(json.dumps(out))
`], { cwd: root, encoding: 'utf8' }));

function loadGuide(lang, hash) {
  const page = compiled[lang];
  const links = page.links.filter(href => href.startsWith('#/')).map(href => ({
    href, events: {},
    getAttribute(name) { assert.equal(name, 'href'); return href; },
    addEventListener(name, handler) { this.events[name] = handler; }
  }));
  const content = {
    html: '', links,
    querySelectorAll(selector) { assert.equal(selector, 'a[href^="#/"]'); return links; }
  };
  const window = {
    location: { hash },
    ForgeLMUserManualsIndex: { sections: [] },
    ForgeLMUserManuals: { [lang]: { 'reference/json-output': page } },
    scrollTo() { window.scrolled = null; }
  };
  const ids = new Map(page.ids.map(id => [id, { scrollIntoView() { window.scrolled = id; } }]));
  const document = {
    addEventListener() {},
    getElementById(id) { return id === 'guide-content' ? content : ids.get(id) || null; },
    querySelectorAll() { return []; }
  };
  const history = {
    replaceState(_state, _title, url) { window.location.hash = url; },
    pushState(_state, _title, url) { window.location.hash = url; }
  };
  let source = fs.readFileSync(path.join(root, 'site/js/guide.js'), 'utf8');
  const end = source.lastIndexOf('})();');
  assert.ok(end > 0, 'the guide is an isolated closure');
  source = source.slice(0, end) + `
    setHtml = function (element, html) { element.html = html; };
    window.guideUnderTest = { state: state, navigate: navigate, parseHash: parseHash };
  ` + source.slice(end);
  const context = vm.createContext({ window, document, history, console, setTimeout() {} });
  vm.runInContext(source, context, { filename: 'guide.js' });
  const guide = window.guideUnderTest;
  guide.state.lang = lang;
  return { window, guide, content };
}

for (const lang of ['en', 'tr']) {
  test(`${lang}: compiled section links retain their page and scroll target after navigation and reload`, async () => {
    const page = compiled[lang];
    const links = page.links.filter(href => href.startsWith('#/reference/json-output#'));
    assert.equal(links.length, 1, 'section links must include the SPA page route');
    const href = links[0];
    const anchor = href.split('#')[2];
    assert.ok(page.ids.includes(anchor), `the compiled HTML contains ${anchor}`);

    const first = loadGuide(lang, '#/reference/json-output');
    first.guide.navigate(first.guide.parseHash(first.window.location.hash), true);
    await new Promise(setImmediate);
    const link = first.content.links.find(link => link.href === href);
    for (const click of [
      { button: 1 }, { button: 2 },
      { button: 0, ctrlKey: true }, { button: 0, metaKey: true },
      { button: 0, shiftKey: true }, { button: 0, altKey: true }
    ]) {
      let prevented = false;
      link.events.click({ ...click, preventDefault() { prevented = true; } });
      await new Promise(setImmediate);
      assert.equal(prevented, false, `preserve native behavior for ${JSON.stringify(click)}`);
      assert.equal(first.window.location.hash, '#/reference/json-output');
      assert.equal(first.guide.state.route, 'reference/json-output');
      assert.equal(first.window.scrolled, null);
    }
    let prevented = false;
    link.events.click({ button: 0, preventDefault() { prevented = true; } });
    await new Promise(setImmediate);
    assert.ok(prevented, 'the real content click handler owns SPA navigation');
    assert.equal(first.guide.state.route, 'reference/json-output');
    assert.ok(first.content.html.includes(page.html), 'the real renderer receives the page, not a missing-page result');
    assert.equal(first.window.scrolled, anchor);
    assert.equal(first.window.location.hash, href);

    first.window.scrolled = null;
    link.events.click({ button: 0, preventDefault() {} });
    await new Promise(setImmediate);
    assert.equal(first.window.scrolled, anchor, 'clicking the current fragment again still scrolls');

    const reloaded = loadGuide(lang, first.window.location.hash);
    reloaded.guide.navigate(reloaded.guide.parseHash(reloaded.window.location.hash), true);
    await new Promise(setImmediate);
    assert.equal(reloaded.guide.state.route, 'reference/json-output');
    assert.ok(reloaded.content.html.includes(page.html));
    assert.equal(reloaded.window.scrolled, anchor);

    reloaded.guide.navigate('reference/json-output', false);
    await new Promise(setImmediate);
    assert.equal(reloaded.window.location.hash, '#/reference/json-output');
    assert.equal(reloaded.window.scrolled, null, 'a route without a heading clears the scroll target');
  });
}
