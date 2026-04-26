/* ═══════════════════════════════════════════════════════════════════
 strategy_loader.js
 Strategy Loader Frontend
 - Manual Mode / Load Strategy Mode switch
 - Fetch strategy catalog + detail schema
 - Auto-generate strategy parameter editor
 - Auto-generate engine parameter editor
 - Unified professional run flow
 - NO replay mode
 ═══════════════════════════════════════════════════════════════════ */
(function () {
  const API = {
    list: 'http://localhost:5000/api/strategies',
    detail: (id) => `http://localhost:5000/api/strategy/${encodeURIComponent(id)}`,
    run: 'http://localhost:5000/api/backtest',
  };

  const STATE = {
    mode: 'manual',
    strategies: [],
    currentStrategyId: null,
    currentStrategyMeta: null,
    lastResult: null,
  };

  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function setDisplay(node, value) {
    if (node) node.style.display = value;
  }

  function dotGet(obj, path, fallback) {
    const parts = String(path || '').split('.');
    let cur = obj;
    for (let i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== 'object' || !(parts[i] in cur)) return fallback;
      cur = cur[parts[i]];
    }
    return cur;
  }

  function dotSet(obj, path, value) {
    const parts = String(path || '').split('.');
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts[i];
      if (!cur[p] || typeof cur[p] !== 'object') cur[p] = {};
      cur = cur[p];
    }
    cur[parts[parts.length - 1]] = value;
    return obj;
  }

  function setMode(mode) {
    STATE.mode = mode;
    const manualOnly = document.querySelectorAll('.bt-manual-only');
    manualOnly.forEach(node => {
      setDisplay(node, mode === 'manual' ? '' : 'none');
    });
    setDisplay($('bt_strategyPanel'), mode === 'strategy' ? '' : 'none');
  }

  function createFieldLabel(text) {
    return el('label', 'bt-label', text);
  }

  function createHelpText(text) {
    const help = el('div', 'bt-toggle-label', text || '');
    help.style.marginTop = '6px';
    return help;
  }

  function createInputForSpec(key, spec, value, scope) {
    const wrap = el('div', 'bt-field');
    wrap.dataset.scope = scope;
    wrap.dataset.key = key;

    if (spec.type === 'group') return null;

    if (spec.type !== 'boolean') {
      wrap.appendChild(createFieldLabel(spec.label || key));
    }

    let input;

    if (spec.type === 'number') {
      input = document.createElement('input');
      input.type = 'number';
      input.className = 'bt-input';
      input.value = value != null ? value : (spec.default != null ? spec.default : '');
      if (spec.min != null) input.min = spec.min;
      if (spec.max != null) input.max = spec.max;
      if (spec.step != null) input.step = spec.step;
    } else if (spec.type === 'enum') {
      input = document.createElement('select');
      input.className = 'bt-select';
      (spec.options || []).forEach(opt => {
        const o = document.createElement('option');
        o.value = opt;
        o.textContent = opt;
        if ((value != null ? value : spec.default) === opt) o.selected = true;
        input.appendChild(o);
      });
    } else if (spec.type === 'boolean') {
      wrap.appendChild(createFieldLabel(spec.label || key));
      const row = el('div', 'bt-toggle-row');
      const toggle = el('label', 'bt-toggle');

      input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = Boolean(value != null ? value : spec.default);

      const slider = el('span', 'bt-toggle-slider');
      toggle.appendChild(input);
      toggle.appendChild(slider);
      row.appendChild(toggle);

      const hint = el('span', 'bt-toggle-label', spec.help || '');
      row.appendChild(hint);
      wrap.appendChild(row);
    } else {
      input = document.createElement('input');
      input.type = 'text';
      input.className = 'bt-input';
      input.value = value != null ? value : (spec.default != null ? spec.default : '');
    }

    input.dataset.key = key;
    input.dataset.scope = scope;

    if (spec.type !== 'boolean') {
      wrap.appendChild(input);
      if (spec.help) wrap.appendChild(createHelpText(spec.help));
    }

    return wrap;
  }

  function renderSchema(containerId, schema, defaults, scope) {
    const root = $(containerId);
    if (!root) return;
    root.innerHTML = '';

    Object.entries(schema || {}).forEach(([key, spec]) => {
      if (!spec) return;

      if (spec.type === 'group') {
        const title = el('div', 'bt-section-title', spec.label || key);
        title.style.marginTop = '8px';
        title.style.marginBottom = '10px';
        root.appendChild(title);
        return;
      }

      const val = dotGet(defaults || {}, key, spec.default);
      const field = createInputForSpec(key, spec, val, scope);
      if (field) root.appendChild(field);
    });
  }

  function collectSchemaValues(containerId, scope) {
    const root = $(containerId);
    const out = {};
    if (!root) return out;

    root.querySelectorAll(`[data-scope="${scope}"][data-key]`).forEach(node => {
      const key = node.dataset.key;
      let value;

      if (node.type === 'checkbox') {
        value = node.checked;
      } else if (node.type === 'number') {
        value = node.value === '' ? null : Number(node.value);
      } else {
        value = node.value;
      }

      dotSet(out, key, value);
    });

    return out;
  }

  function defaultParamsFromSchema(schema) {
    const out = {};
    Object.entries(schema || {}).forEach(([key, spec]) => {
      if (!spec || spec.type === 'group') return;
      dotSet(out, key, spec.default);
    });
    return out;
  }

  function buildCommonConfig() {
    return {
      range: parseInt(($('bt_range') || {}).value || '10', 10),
      bar_range: parseInt(($('bt_barRange') || {}).value || '1000', 10),
      starting_capital: parseFloat(($('bt_startingCapital') || {}).value || '10000'),
      lot_size: parseFloat(($('bt_lotSize') || {}).value || '1.0'),
      commission: {
        type: document.querySelector('input[name="comm_type"]:checked')?.value || 'flat',
        amount: parseFloat(($('bt_commission') || {}).value || '0'),
      },
      monte_carlo_runs: parseInt(($('bt_mcRuns') || {}).value || '1000', 10) || 0
    };
  }

  function renderStrategyMeta(meta) {
    STATE.currentStrategyMeta = meta || null;
    $('bt_strategyDescription').textContent = (meta && meta.description) ? meta.description : 'No description.';
    renderSchema('bt_strategyParams', (meta && meta.parameters) || {}, (meta && defaultParamsFromSchema(meta.parameters)) || {}, 'strategy');
    renderSchema('bt_engineParams', (meta && meta.engine_schema) || {}, (meta && meta.engine_defaults) || {}, 'engine');
  }

  async function fetchStrategies() {
    const resp = await fetch(API.list);
    if (!resp.ok) throw new Error(`Failed to load strategies (${resp.status})`);
    const list = await resp.json();
    STATE.strategies = Array.isArray(list) ? list : [];

    const select = $('bt_strategySelect');
    if (!select) return;
    select.innerHTML = '';

    STATE.strategies.forEach(item => {
      const opt = document.createElement('option');
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
      $('bt_engineParams').innerHTML = '';
    }
  }

  async function fetchStrategyDetail(id) {
    const resp = await fetch(API.detail(id));
    if (!resp.ok) throw new Error(`Failed to load strategy '${id}'`);
    const meta = await resp.json();
    STATE.currentStrategyId = id;
    renderStrategyMeta(meta);
  }

  function stageProgress(id, pct, label) {
    if (typeof window.btShowRunnerState === 'function') {
      window.btShowRunnerState({
        stepId: id,
        pct,
        label,
      });
      return;
    }
  }

  function buildStrategyPayload() {
    return {
      ...buildCommonConfig(),
      strategy_id: STATE.currentStrategyId,
      parameters: collectSchemaValues('bt_strategyParams', 'strategy'),
      engine: collectSchemaValues('bt_engineParams', 'engine'),
    };
  }

  async function runViaJson(payload) {
    stageProgress('step_load', 10, 'Loading strategy configuration…');
    stageProgress('step_run', 35, 'Running strategy engine…');

    const resp = await fetch(API.run, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.error || `Backtest failed (${resp.status})`);
    }

    stageProgress('step_stats', 72, 'Computing statistics…');
    stageProgress('step_charts', 88, 'Rendering dashboard…');
    stageProgress('step_done', 100, 'Complete ✓');

    return data;
  }

  async function runViaStream(payload) {
    const resp = await fetch(API.run, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const contentType = (resp.headers.get('content-type') || '').toLowerCase();
    if (!contentType.includes('ndjson') && !contentType.includes('stream')) {
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || `Backtest failed (${resp.status})`);
      return data;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalData = null;

    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;

      buffer += decoder.decode(chunk.value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;

        let msg = null;
        try { msg = JSON.parse(line); } catch (_) { msg = null; }
        if (!msg) continue;

        if (msg.type === 'progress') {
          if (typeof window.btShowRunnerState === 'function') {
            window.btShowRunnerState({
              stepId: 'step_run',
              pct: msg.pct != null ? msg.pct : 50,
              label: msg.label || `Bar ${msg.bar || 0}`,
              live: {
                equity: msg.equity,
                drawdown: msg.drawdown,
                open_trade: msg.open_trade,
                last_closed_pnl: msg.last_closed_pnl,
                bar: msg.bar,
                total: msg.total
              }
            });
          }
        } else if (msg.type === 'result') {
          finalData = msg.data;
        }
      }
    }

    if (buffer.trim()) {
      try {
        const tail = JSON.parse(buffer.trim());
        if (tail.type === 'result') finalData = tail.data;
      } catch (_) {}
    }

    if (!finalData) throw new Error('No result received from stream');
    return finalData;
  }

  async function runStrategyBacktest() {
    const btn = $('bt_strategyRunBtn');
    const payload = buildStrategyPayload();

    if (!payload.strategy_id) {
      alert('No strategy selected.');
      return;
    }

    btn.classList.add('running');
    btn.innerHTML = '<span class="bt-run-icon">⟳</span> RUNNING…';

    if (typeof window.clearBacktestMarkers === 'function') {
      window.clearBacktestMarkers();
    }

    try {
      stageProgress('step_load', 5, 'Preparing strategy run…');

      let data;
      try {
        data = await runViaStream(payload);
      } catch (_) {
        data = await runViaJson(payload);
      }

      STATE.lastResult = data;

      if (typeof window.btRenderAnyResult === 'function') {
        window.btRenderAnyResult(data, {
          mode: 'strategy',
          strategy_id: payload.strategy_id,
          range: payload.range,
          engine: payload.engine,
          parameters: payload.parameters,
          monte_carlo_runs: payload.monte_carlo_runs
        });
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

  function wireModeSwitch() {
    const mode = $('bt_mode');
    if (!mode) return;

    mode.addEventListener('change', async function () {
      setMode(this.value);
      if (this.value === 'strategy' && !STATE.strategies.length) {
        try {
          await fetchStrategies();
        } catch (err) {
          $('bt_strategyDescription').textContent = err.message || String(err);
        }
      }
    });
  }

  function wireStrategyEvents() {
    const select = $('bt_strategySelect');
    const runBtn = $('bt_strategyRunBtn');

    if (select) {
      select.addEventListener('change', async function () {
        try {
          await fetchStrategyDetail(this.value);
        } catch (err) {
          $('bt_strategyDescription').textContent = err.message || String(err);
        }
      });
    }

    if (runBtn) {
      runBtn.addEventListener('click', async function () {
        await runStrategyBacktest();
      });
    }
  }

  async function boot() {
    setMode(($('bt_mode') || {}).value || 'manual');
    wireModeSwitch();
    wireStrategyEvents();

    if ((($('bt_mode') || {}).value || 'manual') === 'strategy') {
      try {
        await fetchStrategies();
      } catch (err) {
        $('bt_strategyDescription').textContent = err.message || String(err);
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();