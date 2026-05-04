/* ============================================================
   JINNI ZERO — Strategy Loader (Slim / Clean)
   Strategy is king. No engine params.
   Mode switching + strategy run flow + timing.

   Phase 2: Now forwards spread config, ambiguous_bar_mode,
   and mc_runs to backend for Legacy-exact execution.
============================================================ */
(function () {
  var API = {
    list: 'http://localhost:5000/api/strategies',
    detail: function (id) { return 'http://localhost:5000/api/strategy/' + encodeURIComponent(id); },
    run: 'http://localhost:5000/api/backtest/run',
  };

  var STATE = {
    mode: 'manual',
    strategies: [],
    currentStrategyId: null,
    currentMeta: null,
  };

  function $(id) { return document.getElementById(id); }

  function setDisplay(node, val) {
    if (node) node.style.display = val;
  }

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
  // SCHEMA → UI RENDERING (strategy params only)
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
          o.value = opt;
          o.textContent = opt;
          if (val === opt) o.selected = true;
          input.appendChild(o);
        });
      } else if (spec.type === 'boolean') {
        input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = Boolean(val);
        input.style.accentColor = 'var(--accent)';
      } else if (spec.type === 'number') {
        input = document.createElement('input');
        input.className = 'bt-input';
        input.type = 'number';
        input.value = val != null ? val : '';
        if (spec.min != null) input.min = spec.min;
        if (spec.max != null) input.max = spec.max;
        if (spec.step != null) input.step = spec.step;
      } else {
        input = document.createElement('input');
        input.className = 'bt-input';
        input.type = 'text';
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
      if (el.type === 'checkbox') {
        out[key] = el.checked;
      } else if (el.type === 'number') {
        out[key] = el.value === '' ? null : Number(el.value);
      } else {
        out[key] = el.value;
      }
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
        opt.value = item.id;
        opt.textContent = item.name;
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
  // BUILD PAYLOAD  (Phase 2: now includes spread, ambiguous,
  //                 mc_runs for legacy-exact execution)
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
      commission: {
        type: (document.querySelector('input[name="comm_type"]:checked') || {}).value || 'flat',
        amount: parseFloat(($('bt_commission') || {}).value || '0'),
      },
      // ═══ Phase 2: forward missing config for legacy-exact execution ═══
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
  // RUN STRATEGY (WITH TIMING)
  // ==========================================================
  function stageProgress(stepId, pct, label) {
    if (typeof window.btShowRunnerState === 'function') {
      window.btShowRunnerState({ stepId: stepId, pct: pct, label: label });
    }
  }

  async function runStrategyBacktest() {
    var btn = $('bt_strategyRunBtn');
    var payload = buildPayload();

    if (!payload.strategy_id) {
      alert('No strategy selected.');
      return;
    }

    btn.classList.add('running');
    btn.innerHTML = '<span class="bt-run-icon">⟳</span> RUNNING…';

    if (typeof window.clearBacktestMarkers === 'function') {
      window.clearBacktestMarkers();
    }

    var timing = {};
    var totalT0 = performance.now();

    try {
      stageProgress('step_load', 10, 'Preparing strategy…');
      stageProgress('step_run', 35, 'Running strategy engine…');

      // ✅ TEMP FIX: show fake live stats reset
      if (typeof window.btShowRunnerState === 'function') {
        window.btShowRunnerState({
          pct: 35,
          live: {
            equity: 0,
            drawdown: 0,
            open_trade: null,
            last_closed_pnl: null,
          },
        });
      }

      var fetchT0 = performance.now();
      var resp = await fetch(API.run, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      var fetchT1 = performance.now();
      timing.receive_ms = Math.round(fetchT1 - fetchT0);

      var parseT0 = performance.now();
      var data = await resp.json().catch(function () { return {}; });
      // ✅ SAFETY: unwrap if backend accidentally returns {type:"result", data:{...}}
      if (data && data.type === 'result' && data.data) {
        data = data.data;
      }

      // ✅ SAFETY: normalize stats key if backend uses "metrics"
      if (data && !data.stats && data.metrics) {
        data.stats = data.metrics;
      }

      // ✅ SAFETY: normalize curves if backend uses nested "curves"
      if (data && !data.equity_curve && data.curves) {
        data.equity_curve =
          data.curves.equity_downsampled || data.curves.equity_full || [];
      }
      if (data && !data.drawdown_curve && data.curves) {
        data.drawdown_curve =
          data.curves.drawdown_downsampled || data.curves.drawdown_full || [];
      }

      // ✅ HARD FAIL if renderer is missing (prevents silent "stuck" UI)
      if (typeof window.btRenderAnyResult !== 'function') {
        console.error('btRenderAnyResult is missing. backtest.js did not expose it.');
        if (typeof window.btShowRunnerError === 'function') {
          window.btShowRunnerError('btRenderAnyResult missing (backtest.js not loaded or crashed).');
        } else {
          alert('btRenderAnyResult missing (backtest.js not loaded or crashed).');
        }
        return;
      }
      var parseT1 = performance.now();
      timing.parse_ms = Math.round(parseT1 - parseT0);

      if (!resp.ok) {
        throw new Error(data.error || 'Backtest failed (' + resp.status + ')');
      }

      stageProgress('step_stats', 72, 'Computing statistics…');
      stageProgress('step_charts', 88, 'Rendering dashboard…');

      var renderT0 = performance.now();

      if (typeof window.btRenderAnyResult === 'function') {
        window.btRenderAnyResult(data, {
          mode: 'strategy',
          strategy_id: payload.strategy_id,
          range: payload.range,
        });
      }

      var renderT1 = performance.now();
      timing.render_ms = Math.round(renderT1 - renderT0);

      stageProgress('step_done', 100, 'Complete ✓');

      var totalT1 = performance.now();
      timing.total_ms = Math.round(totalT1 - totalT0);

      // ── Log backend performance if included ──
      var backendPerf = data.performance || {};

      console.log(
        '[FRONTEND TIMING] receive=' + timing.receive_ms + 'ms ' +
        'parse=' + timing.parse_ms + 'ms ' +
        'render=' + timing.render_ms + 'ms ' +
        'total=' + timing.total_ms + 'ms'
      );

      if (backendPerf.total_seconds != null) {
        console.log(
          '[BACKEND TIMING] simulation=' + (backendPerf.simulation_seconds || 0) + 's ' +
          'stats=' + (backendPerf.stats_seconds || 0) + 's ' +
          'analytics=' + (backendPerf.analytics_seconds || 0) + 's ' +
          'response_build=' + (backendPerf.response_build_seconds || 0) + 's ' +
          'json=' + (backendPerf.json_seconds || 0) + 's ' +
          'payload=' + (backendPerf.payload_size_kb || 0) + 'KB ' +
          'total=' + (backendPerf.total_seconds || 0) + 's ' +
          'trades=' + (backendPerf.trade_count || 0)
        );
      }

    } catch (err) {
      if (typeof window.btShowRunnerError === 'function') {
        window.btShowRunnerError(err.message || String(err));
      } else {
        console.error(err);
        alert(err.message || String(err));
      }
    } finally {
      btn.classList.remove('running');
      btn.innerHTML = '<span class="bt-run-icon">▶</span> RUN BACKTEST';
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
        if (this.value === 'strategy' && !STATE.strategies.length) {
          await fetchStrategies();
        }
      });
    }

    var strategySelect = $('bt_strategySelect');
    if (strategySelect) {
      strategySelect.addEventListener('change', function () {
        fetchStrategyDetail(this.value);
      });
    }

    var runBtn = $('bt_strategyRunBtn');
    if (runBtn) {
      runBtn.addEventListener('click', function () {
        runStrategyBacktest();
      });
    }

    if ((($('bt_mode') || {}).value || 'manual') === 'strategy') {
      fetchStrategies();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();