// Minimal DOM stand-in so the HUD page script can run under plain node (no browser, no jsdom).
// usage: node hud_dom_harness.js <page.html> <steps.json>
// steps: [{"model": {...}} | {"click": "<text of a button/row/tab>"} ...]; prints one JSON result.
const fs = require('fs');

class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.style = {}; this.listeners = {}; this._text = ''; this.className = ''; this.id = ''; this.scrollTop = 0; }
  appendChild(c) { this.children.push(c); c.parent = this; return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set type(v) { this.attrs.type = v; }
}
const byId = {};
function make(tag) { return new Node(tag); }
function findId(node, id) {
  if (node.id === id) return node;
  for (const child of node.children) { const found = findId(child, id); if (found) return found; }
  return null;
}
const doc = {
  createElement: make, createElementNS: (ns, tag) => make(tag), createTextNode: t => { const n = make('#text'); n._text = t; return n; },
  getElementById: id => {
    if (byId[id]) return byId[id];
    for (const node of Object.values(byId)) { const found = findId(node, id); if (found) return found; }
    return (byId[id] = Object.assign(make('div'), { id }));
  },
  addEventListener() {},
};
const posted = [];
const win = { addEventListener() {}, __post: m => posted.push(m) };
global.document = doc; global.window = win;
global.setTimeout = () => 0; global.clearTimeout = () => {};
global.setInterval = () => 0;

const html = fs.readFileSync(process.argv[2], 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
for (const id of ['statusbar', 'titlebar', 'scroll', 'toast', 'tabs', 'veil', 'dlg', 'dlg-title', 'dlg-body', 'dlg-ok', 'dlg-cancel']) doc.getElementById(id);
new Function('window', 'document', script)(win, doc);

function walk(n, f) { f(n); n.children.forEach(c => walk(c, f)); }
function find(text) {
  if (text === '@cancel') return byId['dlg-cancel'];
  if (text === '@ok') return byId['dlg-ok'];
  let hit = null;
  walk(byId.statusbar, n => { if (!hit && n.listeners.click && n.textContent === text) hit = n; });
  for (const root of [byId.titlebar, byId.scroll, byId.tabs, byId.dlg]) walk(root, n => { if (!hit && n.listeners.click && n.textContent.includes(text)) hit = n; });
  return hit;
}
const out = [];
for (const step of JSON.parse(fs.readFileSync(process.argv[3], 'utf8'))) {
  if (step.model) {
    win.render(step.model);
    const rows = []; walk(byId.scroll, n => { if (/(^| )r( |$)/.test(n.className)) rows.push({ text: n.textContent, role: n.attrs.role || null, checked: n.attrs['aria-checked'] || null, clickable: !!n.listeners.click }); });
    const tabs = []; walk(byId.tabs, n => { if (n.tag === 'button') tabs.push({ text: n.textContent, current: n.attrs['aria-current'] === 'page' }); });
    out.push({ status: byId.statusbar.textContent, title: byId.titlebar.children[0].textContent,
      updated: doc.getElementById('model-updated').textContent, rows, tabs, scroll: byId.scroll.textContent,
      pre: (() => { let t = null; walk(byId.scroll, n => { if (n.className === 'code') t = n.textContent; }); return t; })(),
      dialog: byId.veil.style.display === 'flex' ? byId['dlg-title'].textContent + '|' + byId['dlg-body'].textContent : null,
      toast: byId.toast.style.display === 'block' ? byId.toast.textContent : null, dim: byId.scroll.className });
  } else if (step.click) {
    const n = find(step.click);
    if (!n) { out.push({ error: 'no clickable: ' + step.click }); continue; }
    n.listeners.click.forEach(f => f({}));
    out.push({ posted: posted.slice(-1)[0] });
  }
}
console.log(JSON.stringify(out));
