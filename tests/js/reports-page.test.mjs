// reports.js in einer minimalen Fake-Umgebung (window, document, Chart, fetch).
// Lauf: node --test tests/js
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const lib = require('../../src/kassivo/web/static/reports-lib.js');
const SRC = fs.readFileSync(new URL('../../src/kassivo/web/static/reports.js', import.meta.url), 'utf8');

const IDS = ['charts', 'chart-error-load', 'chart-error-draw', 'chart-expense-box', 'chart-expense',
  'chart-expense-empty', 'chart-income-box', 'chart-income', 'chart-income-empty', 'chart-months'];

const DATA = {
  by_category: [{ name: 'Miete', income_cents: 0, expense_cents: 3000 },
                { name: 'Beiträge', income_cents: 1700, expense_cents: 0 }],
  by_month: Array.from({ length: 12 }, () => ({ income_cents: 0, expense_cents: 0 })),
};

function setup({ withLib = true, failOn = null, data = DATA } = {}) {
  const els = {};
  for (const id of IDS) {
    const classes = new Set(id.startsWith('chart-error') || id.endsWith('-empty') ? ['hidden'] : []);
    els[id] = { id, dataset: { year: '2026' }, classList: {
      toggle: (c, on) => { on ? classes.add(c) : classes.delete(c); },
      contains: c => classes.has(c) } };
  }
  const log = { created: [], destroyed: [], errors: [], listeners: [] };
  const win = {
    matchMedia: () => ({ addEventListener: (_, fn) => log.listeners.push(fn) }),
  };
  if (withLib) { win.ReportsLib = lib; }
  const ctx = {
    window: win, console: { error: (...a) => log.errors.push(a) },
    document: { getElementById: id => els[id] || null,
                documentElement: {} },
    getComputedStyle: () => ({ getPropertyValue: () => '#000' }),
    encodeURIComponent,
    fetch: async () => ({ ok: true, json: async () => data }),
    Chart: class {
      constructor(canvas, cfg) {
        if (failOn && failOn.value === cfg.type + ':' + canvas.id) { throw new Error('boom'); }
        if (failOn && failOn.value === cfg.type && canvas.id === 'chart-months') { throw new Error('boom'); }
        this.canvas = canvas; log.created.push(this);
      }
      destroy() { log.destroyed.push(this); }
    },
  };
  vm.runInNewContext(SRC, ctx);
  const hidden = id => els[id].classList.contains('hidden');
  return { log, hidden };
}

const tick = () => new Promise(r => setTimeout(r, 0));

test('fehlende reports-lib.js zeigt Fehler und loggt', () => {
  const { log, hidden } = setup({ withLib: false });
  assert.equal(hidden('chart-error-draw'), false);
  assert.equal(log.errors.length, 1);
});

test('normaler Lauf zeichnet drei Diagramme ohne Fehler', async () => {
  const { log, hidden } = setup();
  await tick(); await tick();
  assert.equal(log.created.length, 3);
  assert.equal(hidden('chart-error-draw'), true);
  assert.equal(hidden('chart-error-load'), true);
});

test('Jahr ohne Einnahmen: Hinweis statt Diagramm', async () => {
  const data = { ...DATA, by_category: [{ name: 'Miete', income_cents: 0, expense_cents: 3000 }] };
  const { log, hidden } = setup({ data });
  await tick(); await tick();
  assert.equal(hidden('chart-income-box'), true);
  assert.equal(hidden('chart-income-empty'), false);
  assert.equal(hidden('chart-expense-box'), false);
  assert.equal(log.created.length, 2);
});

test('Zeichenfehler im dritten Diagramm zerstört die früheren', async () => {
  const { log, hidden } = setup({ failOn: { value: 'bar' } });
  await tick(); await tick();
  assert.equal(log.created.length, 2);
  assert.equal(log.destroyed.length, 2);
  assert.equal(hidden('chart-error-draw'), false);
  assert.ok(log.errors.length >= 1);
});

test('späteres erfolgreiches draw blendet die Meldung aus', async () => {
  const failOn = { value: 'bar' };
  const { log, hidden } = setup({ failOn });
  await tick(); await tick();
  assert.equal(hidden('chart-error-draw'), false);
  failOn.value = null;
  log.listeners[0]();
  assert.equal(hidden('chart-error-draw'), true);
  assert.equal(log.created.length, 5);
});
