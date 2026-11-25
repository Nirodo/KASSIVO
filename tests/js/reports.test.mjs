// Reine Funktionen des Jahresreports. Lauf: node --test tests/js/reports.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { topSlices, formatEuro } = require('../../src/kassivo/web/static/reports-lib.js');

const items = (...pairs) => pairs.map(([name, c]) => ({ name, expense_cents: c }));
const NB = ' '; // geschütztes Leerzeichen, wie Intl es ausgibt

test('sortiert absteigend', () => {
  const r = topSlices(items(['a', 100], ['b', 300], ['c', 200]), 'expense_cents', 7);
  assert.deepEqual(r.map(s => s.name), ['b', 'c', 'a']);
  assert.deepEqual(r.map(s => s.cents), [300, 200, 100]);
  assert.ok(r.every(s => !s.other));
});

test('höchstens 7 plus Weitere mit Flag', () => {
  const many = items(...Array.from({ length: 10 }, (_, i) => ['k' + i, 100 - i]));
  const r = topSlices(many, 'expense_cents', 7);
  assert.equal(r.length, 8);
  const last = r[7];
  assert.equal(last.name, 'Weitere');
  assert.equal(last.other, true);
  assert.equal(last.cents, 93 + 92 + 91);
  assert.ok(r.slice(0, 7).every(s => !s.other));
});

test('genau 7 ergibt kein Weitere', () => {
  const r = topSlices(items(...Array.from({ length: 7 }, (_, i) => ['k' + i, 10 + i])), 'expense_cents', 7);
  assert.equal(r.length, 7);
  assert.ok(r.every(s => !s.other));
});

test('Gleichstand bleibt in Eingabereihenfolge', () => {
  const r = topSlices(items(['x', 5], ['y', 5], ['z', 5]), 'expense_cents', 7);
  assert.deepEqual(r.map(s => s.name), ['x', 'y', 'z']);
});

test('null, negativ und NaN fallen weg', () => {
  const r = topSlices(items(['n', 0], ['m', -50], ['q', NaN], ['ok', 10]), 'expense_cents', 7);
  assert.deepEqual(r.map(s => s.name), ['ok']);
});

test('leere Eingabe; Eingabe bleibt unverändert', () => {
  assert.deepEqual(topSlices([], 'expense_cents', 7), []);
  const input = items(['a', 1], ['b', 2]);
  topSlices(input, 'expense_cents', 7);
  assert.deepEqual(input.map(i => i.name), ['a', 'b']);
});

test('formatEuro rundet und formatiert negativ', () => {
  assert.equal(formatEuro(0), `0,00${NB}€`);
  assert.equal(formatEuro(1700), `17,00${NB}€`);
  assert.equal(formatEuro(5), `0,05${NB}€`);
  assert.equal(formatEuro(-3300), `-33,00${NB}€`);
  assert.equal(formatEuro(1999), `19,99${NB}€`);
});

test('formatEuro große Werte', () => {
  assert.equal(formatEuro(123456789), `1.234.567,89${NB}€`);
  assert.equal(formatEuro(100000000000), `1.000.000.000,00${NB}€`);
});

test('formatEuro: -0 wird 0, Nicht-Ganzzahlen werden weg von null gerundet', () => {
  assert.equal(formatEuro(-0), `0,00${NB}€`);
  assert.equal(formatEuro(0.5), `0,01${NB}€`);
  assert.equal(formatEuro(-0.5), `-0,01${NB}€`);
  assert.equal(formatEuro(1.4), `0,01${NB}€`);
});

test('topSlices: nur endliche Zahlen größer 0', () => {
  const r = topSlices(items(['s', '50'], ['u', undefined], ['i', Infinity], ['n', null], ['ok', 3]),
    'expense_cents', 7);
  assert.deepEqual(r.map(s => s.name), ['ok']);
  assert.deepEqual(topSlices(items(['a', 0], ['b', 0]), 'expense_cents', 7), []);
});
