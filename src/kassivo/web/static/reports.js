// Jahresreport: Diagramme aus /reports/data. Nur textContent und Chart.js-Daten, nie HTML.
(function () {
  'use strict';

  var PALETTE = ['#0072B2', '#E69F00', '#009E73', '#D55E00', '#56B4E9', '#CC79A7', '#F0E442'];
  var OTHER_COLOR = '#999999';
  var MAX_SLICES = 7;
  var MONTHS = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];
  var euro = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' });

  function formatEuro(cents) {
    return euro.format(cents / 100);
  }

  // Höchstens max Scheiben, absteigend; der Rest wird als "Weitere" zusammengefasst.
  function topSlices(items, key, max) {
    var sorted = items
      .filter(function (i) { return i[key] > 0; })
      .sort(function (a, b) { return b[key] - a[key]; });
    var slices = sorted.slice(0, max).map(function (i) { return { name: i.name, cents: i[key] }; });
    if (sorted.length > max) {
      var rest = sorted.slice(max).reduce(function (sum, i) { return sum + i[key]; }, 0);
      slices.push({ name: 'Weitere', cents: rest });
    }
    return slices;
  }

  function cssColors() {
    var cs = getComputedStyle(document.documentElement);
    return { text: cs.getPropertyValue('--fg').trim(), grid: cs.getPropertyValue('--line').trim(),
             bg: cs.getPropertyValue('--bg').trim() };
  }

  function pie(canvas, slices, colors) {
    var total = slices.reduce(function (s, x) { return s + x.cents; }, 0);
    return new Chart(canvas, {
      type: 'pie',
      data: {
        labels: slices.map(function (s) { return s.name + ': ' + formatEuro(s.cents); }),
        datasets: [{
          data: slices.map(function (s) { return s.cents; }),
          backgroundColor: slices.map(function (s, i) {
            return i === MAX_SLICES ? OTHER_COLOR : PALETTE[i % PALETTE.length];
          }),
          borderColor: colors.bg, borderWidth: 2
        }]
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: { position: 'bottom', labels: { color: colors.text } },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var pct = (ctx.parsed / total * 100).toFixed(1).replace('.', ',');
                return slices[ctx.dataIndex].name + ': ' + formatEuro(ctx.parsed) + ' (' + pct + ' %)';
              }
            }
          }
        }
      }
    });
  }

  function bars(canvas, months, colors) {
    return new Chart(canvas, {
      type: 'bar',
      data: {
        labels: MONTHS,
        datasets: [
          { label: 'Einnahmen', backgroundColor: '#0072B2',
            data: months.map(function (m) { return m.income_cents; }) },
          { label: 'Ausgaben', backgroundColor: '#D55E00',
            data: months.map(function (m) { return m.expense_cents; }) }
        ]
      },
      options: {
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: colors.text }, grid: { color: colors.grid } },
          y: { beginAtZero: true, grid: { color: colors.grid },
               ticks: { color: colors.text, callback: function (v) { return formatEuro(v); } } }
        },
        plugins: {
          legend: { labels: { color: colors.text } },
          tooltip: {
            callbacks: {
              label: function (ctx) { return ctx.dataset.label + ': ' + formatEuro(ctx.parsed.y); }
            }
          }
        }
      }
    });
  }

  function init() {
    var box = document.getElementById('charts');
    if (!box) { return; }
    var errorEl = document.getElementById('chart-error');
    var charts = [];
    var data = null;

    function draw() {
      charts.forEach(function (c) { c.destroy(); });
      var colors = cssColors();
      charts = [
        pie(document.getElementById('chart-expense'), topSlices(data.by_category, 'expense_cents', MAX_SLICES), colors),
        pie(document.getElementById('chart-income'), topSlices(data.by_category, 'income_cents', MAX_SLICES), colors),
        bars(document.getElementById('chart-months'), data.by_month, colors)
      ];
    }

    fetch('/reports/data?year=' + encodeURIComponent(box.dataset.year), { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) { throw new Error('http ' + r.status); } return r.json(); })
      .then(function (d) {
        data = d;
        draw();
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
      })
      .catch(function () {
        if (errorEl) { errorEl.classList.remove('hidden'); }
      });
  }

  window.topSlices = topSlices;
  window.formatEuro = formatEuro;
  init();
}());
