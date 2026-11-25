// Jahresreport: Diagramme aus /reports/data. Nur textContent und Chart.js-Daten, nie HTML.
(function () {
  'use strict';

  var PALETTE = ['#0072B2', '#E69F00', '#009E73', '#D55E00', '#56B4E9', '#CC79A7', '#F0E442'];
  var OTHER_COLOR = '#999999';
  var MAX_SLICES = 7;
  var MONTHS = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];
  var topSlices, formatEuro;

  function show(id, on) {
    var el = document.getElementById(id);
    if (el) { el.classList.toggle('hidden', !on); }
  }

  function cssColors() {
    var cs = getComputedStyle(document.documentElement);
    return { text: cs.getPropertyValue('--fg').trim(), grid: cs.getPropertyValue('--line').trim(),
             bg: cs.getPropertyValue('--bg').trim() };
  }

  function pie(canvas, slices, colors) {
    return new Chart(canvas, {
      type: 'pie',
      data: {
        labels: slices.map(function (s) { return s.name + ': ' + formatEuro(s.cents); }),
        datasets: [{
          data: slices.map(function (s) { return s.cents; }),
          backgroundColor: slices.map(function (s, i) {
            return s.other ? OTHER_COLOR : PALETTE[i % PALETTE.length];
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
              // Prozent beziehen sich auf die SICHTBAREN Scheiben (ausgeblendete Legendeneintraege
              // zaehlen nicht mit); die Legende selbst zeigt weiter die Eurobetraege.
              label: function (ctx) {
                var visible = slices.reduce(function (sum, x, i) {
                  return ctx.chart.getDataVisibility(i) ? sum + x.cents : sum;
                }, 0);
                var pct = visible > 0 ? (ctx.parsed / visible * 100).toFixed(1).replace('.', ',') : '0,0';
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

  // Ohne Daten: Diagramm ausblenden, Hinweis zeigen.
  function pieOrNote(kind, slices, colors) {
    var empty = slices.length === 0;
    show('chart-' + kind + '-box', !empty);
    show('chart-' + kind + '-empty', empty);
    return empty ? null : pie(document.getElementById('chart-' + kind), slices, colors);
  }

  function init() {
    var box = document.getElementById('charts');
    if (!box) { return; }
    if (!window.ReportsLib) {
      console.error('reports-lib.js nicht geladen');
      show('chart-error-draw', true);
      return;
    }
    topSlices = window.ReportsLib.topSlices;
    formatEuro = window.ReportsLib.formatEuro;
    var charts = [];
    var data = null;

    function destroyAll(list) {
      list.forEach(function (c) { try { c.destroy(); } catch (e) { console.error(e); } });
    }

    function draw() {
      destroyAll(charts);
      charts = [];
      show('chart-error-draw', false);
      var made = [];
      try {
        var colors = cssColors();
        var add = function (c) { if (c) { made.push(c); } };
        add(pieOrNote('expense', topSlices(data.by_category, 'expense_cents', MAX_SLICES), colors));
        add(pieOrNote('income', topSlices(data.by_category, 'income_cents', MAX_SLICES), colors));
        add(bars(document.getElementById('chart-months'), data.by_month, colors));
      } catch (err) {
        console.error('Report: Diagramm konnte nicht gezeichnet werden:', err);
        destroyAll(made);
        show('chart-error-draw', true);
        return;
      }
      charts = made;
    }

    fetch('/reports/data?year=' + encodeURIComponent(box.dataset.year), { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) { throw new Error('http ' + r.status); } return r.json(); })
      .then(function (d) {
        data = d;
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
        draw();
      }, function (err) {
        console.error('Report: Daten konnten nicht geladen werden:', err);
        show('chart-error-load', true);
      })
      .catch(function (err) {
        console.error('Report: Fehler nach dem Laden:', err);
        show('chart-error-draw', true);
      });
  }

  init();
}());
