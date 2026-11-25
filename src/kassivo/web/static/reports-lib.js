// Reine Hilfsfunktionen des Jahresreports (ohne DOM). Im Browser als window.ReportsLib,
// unter Node per require() testbar (tests/js/reports.test.mjs).
(function (root, factory) {
  'use strict';
  var lib = factory();
  if (typeof module === 'object' && module.exports) { module.exports = lib; }
  else { root.ReportsLib = lib; }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var euro = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' });

  function formatEuro(cents) {
    return euro.format(cents / 100);
  }

  // Höchstens max Scheiben, absteigend; der Rest wird als "Weitere" (other: true) zusammengefasst.
  function topSlices(items, key, max) {
    var sorted = items
      .filter(function (i) { return i[key] > 0; })
      .sort(function (a, b) { return b[key] - a[key]; });
    var slices = sorted.slice(0, max).map(function (i) { return { name: i.name, cents: i[key] }; });
    if (sorted.length > max) {
      var rest = sorted.slice(max).reduce(function (sum, i) { return sum + i[key]; }, 0);
      slices.push({ name: 'Weitere', cents: rest, other: true });
    }
    return slices;
  }

  return { formatEuro: formatEuro, topSlices: topSlices };
}));
