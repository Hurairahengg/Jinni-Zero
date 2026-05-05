/* ============================================================
   JINNI ZERO — Strategy Loader
   Now with REAL streaming progress (matches legacy mode).
============================================================ */
(function () {
  var API = {
    list: 'http://localhost:5000/api/strategies',
    detail: function (id) { return 'http://localhost:5000/api/strategy/' + encodeURIComponent(id); },
    run: 'http://localhost:5000/api/backtest/run',
    runStream: 'http://localhost:5000/api/backtest/run/stream',
  };

  var STATE = {
    mode: 'manual',
    strategies: [],
    currentStrategyId: null,
    currentMeta: null,
  };

  function $(id) { return document.getElementById(id); }
  function setDisplay(node, val) { if (node) node.style.display = val; }

  // ==========================================================
  // MODE SWITCHING
  // ==========================================================
  function setMode(mode) {
    STATE.mode = mode;
    document.querySelectorAll('.bt-manual-only').forEach(function (node) {
      setDisplay(node, mode === 'manual' ? '' : 'none');
    });
    setDisplay($('bt_strategyPanel'), mode === 'strategy' ? '' : 'none');
  }

  // ==========================================================
  // SCHEMA → UI
  // ==========================================================
  function renderStrategyParams(schema, defaults) {
    var root = $('bt_strategyParams');
    if (!root) return;
    root.innerHTML = '';

    if (!schema || !Object.keys(schema).length) {
      var empty = document.createElement('div');
      empty.className = 'bt-toggle-label';
      empty.textContent = 'This strategy has no configurable parameters.';
      root.appendChild(empty);
      return;
    }

    Object.entries(schema).forEach(function (entry) {
      var key = entry[0], spec = entry[1];
      if (!spec || spec.type === 'group') return;

      var row = document.createElement('div');
      row.className = 'bt-field';

      var label = document.createElement('label');
      label.className = 'bt-label';
      label.textContent = spec.label || key;
      row.appendChild(label);

      var input;
      var val = defaults[key] != null ? defaults[key] : spec.default;

      if (spec.type === 'enum') {
        input = document.createElement('select');
        input.className = 'bt-select';
        (spec.options || []).forEach(function (opt) {
          var o = document.createElement('option');
          o.value = opt; o.textContent = opt;
          if (val === opt) o.selected = true;
          input.appendChild(o);
        });
      } else if (spec.type === 'boolean') {
        input = document.createElement('input');
        input.type = 'checkbox'; input.checked = Boolean(val);
        input.style.accentColor = 'var(--accent)';
      } else if (spec.type === 'number') {
        input = document.createElement('input');
        input.className = 'bt-input'; input.type = 'number';
        input.value = val != null ? val : '';
        if (spec.min != null) input.min = spec.min;
        if (spec.max != null) input.max = spec.max;
        if (spec.step != null) input.step = spec.step;
      } else {
        input = document.createElement('input');
        input.className = 'bt-input'; input.type = 'text';
        input.value = val != null ? val : '';
      }

      input.dataset.key = key;
      row.appendChild(input);

      if (spec.help) {
        var help = document.createElement('div');
        help.className = 'bt-toggle-label';
        help.textContent = spec.help;
        help.style.marginTop = '4px';
        row.appendChild(help);
      }

      root.appendChild(row);
    });
  }

  function collectStrategyParams() {
    var out = {};
    var root = $('bt_strategyParams');
    if (!root) return out;
    root.querySelectorAll('[data-key]').forEach(function (el) {
      var key = el.dataset.key;
      if (el.type === 'checkbox') out[key] = el.checked;
      else if (el.type === 'number') out[key] = el.value === '' ? null : Number(el.value);
      else out[key] = el.value;
    });
    return out;
  }

  // ==========================================================
  // STRATEGY LOADING
  // ==========================================================
  function renderStrategyMeta(meta) {
    STATE.currentMeta = meta || null;
    $('bt_strategyDescription').textContent =
      (meta && meta.description) ? meta.description : 'No description.';
    var schema = (meta && meta.parameters) || {};
    var defaults = {};
    Object.entries(schema).forEach(function (entry) {
      var k = entry[0], v = entry[1];
      if (v && v.default != null) defaults[k] = v.default;
    });
    renderStrategyParams(schema, defaults);
  }

  async function fetchStrategies() {
    try {
      var resp = await fetch(API.list);
      if (!resp.ok) throw new Error('Failed (' + resp.status + ')');
      var list = await resp.json();
      STATE.strategies = Array.isArray(list) ? list : [];
      var select = $('bt_strategySelect');
      if (!select) return;
      select.innerHTML = '';
      STATE.strategies.forEach(function (item) {
        var opt = document.createElement('option');
        opt.value = item.id; opt.textContent = item.name;
        select.appendChild(opt);
      });
      if (STATE.strategies.length) {
        STATE.currentStrategyId = STATE.strategies[0].id;
        select.value = STATE.currentStrategyId;
        await fetchStrategyDetail(STATE.currentStrategyId);
      } else {
        $('bt_strategyDescription').textContent = 'No strategy plugins found.';
        $('bt_strategyParams').innerHTML = '';
      }
    } catch (err) {
      $('bt_strategyDescription').textContent = 'Error loading strategies: ' + err.message;
    }
  }

  async function fetchStrategyDetail(id) {
    try {
      var resp = await fetch(API.detail(id));
      if (!resp.ok) throw new Error('Failed (' + resp.status + ')');
      var meta = await resp.json();
      STATE.currentStrategyId = id;
      renderStrategyMeta(meta);
    } catch (err) {
      $('bt_strategyDescription').textContent = 'Error: ' + err.message;
    }
  }

  // ==========================================================
  // BUILD PAYLOAD
  // ==========================================================
  function buildPayload() {
    var sliceMode = ($('bt_sliceMode') || {}).value || 'bar_count';

    var payload = {
      strategy_id: STATE.currentStrategyId,
      parameters: collectStrategyParams(),
      range: parseInt(($('bt_range') || {}).value || '10', 10),
      bar_range: parseInt(($('bt_barRange') || {}).value || '1000', 10),
      starting_capital: parseFloat(($('bt_startingCapital') || {}).value || '10000'),
      lot_size: parseFloat(($('bt_lotSize') || {}).value || '1.0'),
      point_value: parseFloat((document.getElementById('bt_pointValue') || {}).value || '1') || 1.0,
      sizing_mode: ($('bt_sizingMode') || {}).value || 'fixed',
      risk_pct: parseFloat(($('bt_riskPct') || {}).value || '1.0') || 1.0,
      commission: {
        type: (document.querySelector('input[name="comm_type"]:checked') || {}).value || 'flat',
        amount: parseFloat(($('bt_commission') || {}).value || '0'),
      },
      ambiguous_bar_mode: ($('bt_ambiguousMode') || {}).value || 'conservative',
      spread: {
        enabled: ($('bt_spreadEnabled') || {}).checked || false,
        min: parseFloat(($('bt_spreadMin') || {}).value || '0'),
        max: parseFloat(($('bt_spreadMax') || {}).value || '0'),
        seed: parseInt(($('bt_spreadSeed') || {}).value || '0', 10),
      },
      mc_runs: parseInt(($('bt_mcRuns') || {}).value || '1000', 10),
    };

    if (sliceMode === 'date_range') {
      payload.start_date = ($('bt_startDate') || {}).value || '';
      payload.end_date = ($('bt_endDate') || {}).value || '';
      payload.bar_range = 0;
    }
    return payload;
  }

  // ==========================================================
  // RUN — REAL STREAMING PROGRESS
  // ==========================================================
  async function runStrategyBacktest() {
    var btn = $('bt_strategyRunBtn');
    var payload = buildPayload();

    if (!payload.strategy_id) { alert('No strategy selected.'); return; }

    btn.classList.add('running');
    btn.innerHTML = '<span class="bt-run-icon">⟳</span> RUNNING…';

    if (typeof window.clearBacktestMarkers === 'function') window.clearBacktestMarkers();

    var timing = {};
    var totalT0 = performance.now();
    var streamed = false;

    try {
      // Reset progress UI
      if (typeof window.btShowRunnerState === 'function') {
        window.btShowRunnerState({ stepId: 'step_load', pct: 5, label: 'Preparing strategy…' });
      }

      // ══ TRY STREAMING FIRST ══════════════════════════════
      var fetchT0 = performance.now();
      var resp = await fetch(API.runStream, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!resp.ok) throw new Error('Stream failed: ' + resp.status);
      var ct = (resp.headers.get('content-type') || '').toLowerCase();
      if (!ct.includes('ndjson') && !ct.includes('stream')) throw new Error('Not a stream response');

      streamed = true;

      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';
      var finalData = null;

      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;

        buffer += decoder.decode(chunk.value, { stream: true });
        var lines = buffer.split('\n');
        buffer = lines.pop();

        for (var li = 0; li < lines.length; li++) {
          var trimmed = lines[li].trim();
          if (!trimmed) continue;
          var msg = null;
          try { msg = JSON.parse(trimmed); } catch (e) { continue; }

          if (msg.type === 'progress') {
            // Real progress from backend
            if (typeof window.btShowRunnerState === 'function') {
              window.btShowRunnerState({
                pct: msg.pct || 0,
                label: 'Bar ' + (msg.bar || 0).toLocaleString() + ' / ' + (msg.total || 0).toLocaleString(),
                live: {
                  bar: msg.bar,
                  total: msg.total,
                  equity: msg.equity,
                  drawdown: msg.drawdown,
                  open_trade: msg.open_trade,
                  last_closed_pnl: msg.last_closed_pnl,
                },
              });
            }
          } else if (msg.type === 'result') {
            finalData = msg.data;
          } else if (msg.type === 'error') {
            throw new Error(msg.error || 'Backend error during stream');
          }
        }
      }

      // Check leftover buffer
      if (buffer.trim()) {
        try {
          var lastMsg = JSON.parse(buffer.trim());
          if (lastMsg.type === 'result') finalData = lastMsg.data;
          if (lastMsg.type === 'error') throw new Error(lastMsg.error || 'Backend error');
        } catch (e) { /* ignore parse errors on leftover */ }
      }

      if (!finalData) throw new Error('No result from stream');

      var fetchT1 = performance.now();
      timing.receive_ms = Math.round(fetchT1 - fetchT0);

      // ══ RENDER ═══════════════════════════════════════════
      if (typeof window.btShowRunnerState === 'function') {
        window.btShowRunnerState({ stepId: 'step_stats', pct: 92, label: 'Computing statistics…' });
      }

      if (typeof window.btRenderAnyResult !== 'function') {
        throw new Error('btRenderAnyResult missing (backtest.js not loaded).');
      }

      // Normalize
      if (finalData && finalData.type === 'result' && finalData.data) finalData = finalData.data;
      if (finalData && !finalData.stats && finalData.metrics) finalData.stats = finalData.metrics;
      if (finalData && !finalData.equity_curve && finalData.curves) {
        finalData.equity_curve = finalData.curves.equity_downsampled || finalData.curves.equity_full || [];
      }
      if (finalData && !finalData.drawdown_curve && finalData.curves) {
        finalData.drawdown_curve = finalData.curves.drawdown_downsampled || finalData.curves.drawdown_full || [];
      }

      if (typeof window.btShowRunnerState === 'function') {
        window.btShowRunnerState({ stepId: 'step_charts', pct: 96, label: 'Rendering dashboard…' });
      }

      var renderT0 = performance.now();
      window.btRenderAnyResult(finalData, {
        mode: 'strategy',
        strategy_id: payload.strategy_id,
        range: payload.range,
      });
      var renderT1 = performance.now();
      timing.render_ms = Math.round(renderT1 - renderT0);

      if (typeof window.btShowRunnerState === 'function') {
        window.btShowRunnerState({ stepId: 'step_done', pct: 100, label: 'Complete ✓' });
      }

    } catch (streamErr) {
      // ══ FALLBACK: non-streaming ══════════════════════════
      if (!streamed || streamErr.message.includes('No result')) {
        try {
          if (typeof window.btShowRunnerState === 'function') {
            window.btShowRunnerState({ stepId: 'step_run', pct: 30, label: 'Running (non-streaming)…' });
          }
          var resp2 = await fetch(API.run, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          if (!resp2.ok) {
            var errText = await resp2.text();
            throw new Error('Server ' + resp2.status + ': ' + errText);
          }

          if (typeof window.btShowRunnerState === 'function') {
            window.btShowRunnerState({ stepId: 'step_stats', pct: 80, label: 'Parsing response…' });
          }

          var data = await resp2.json().catch(function () { return {}; });
          if (data && data.type === 'result' && data.data) data = data.data;
          if (data && !data.stats && data.metrics) data.stats = data.metrics;

          if (typeof window.btRenderAnyResult === 'function') {
            window.btRenderAnyResult(data, {
              mode: 'strategy',
              strategy_id: payload.strategy_id,
              range: payload.range,
            });
          }

          if (typeof window.btShowRunnerState === 'function') {
            window.btShowRunnerState({ stepId: 'step_done', pct: 100, label: 'Complete ✓' });
          }

        } catch (fallbackErr) {
          console.error(fallbackErr);
          if (typeof window.btShowRunnerError === 'function') {
            window.btShowRunnerError(fallbackErr.message || String(fallbackErr));
          } else {
            alert(fallbackErr.message || String(fallbackErr));
          }
        }
      } else {
        console.error(streamErr);
        if (typeof window.btShowRunnerError === 'function') {
          window.btShowRunnerError(streamErr.message || String(streamErr));
        } else {
          alert(streamErr.message || String(streamErr));
        }
      }
    } finally {
      btn.classList.remove('running');
      btn.innerHTML = '<span class="bt-run-icon">▶</span> RUN BACKTEST';

      var totalT1 = performance.now();
      timing.total_ms = Math.round(totalT1 - totalT0);
      console.log('[STRATEGY TIMING] receive=' + (timing.receive_ms || '?') + 'ms '
        + 'render=' + (timing.render_ms || '?') + 'ms '
        + 'total=' + timing.total_ms + 'ms');
    }
  }

  // ==========================================================
  // WIRING
  // ==========================================================
  function boot() {
    setMode(($('bt_mode') || {}).value || 'manual');

    var modeSelect = $('bt_mode');
    if (modeSelect) {
      modeSelect.addEventListener('change', async function () {
        setMode(this.value);
        if (this.value === 'strategy' && !STATE.strategies.length) await fetchStrategies();
      });
    }

    var strategySelect = $('bt_strategySelect');
    if (strategySelect) {
      strategySelect.addEventListener('change', function () { fetchStrategyDetail(this.value); });
    }

    var runBtn = $('bt_strategyRunBtn');
    if (runBtn) {
      runBtn.addEventListener('click', function () { runStrategyBacktest(); });
    }

    if ((($('bt_mode') || {}).value || 'manual') === 'strategy') fetchStrategies();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();