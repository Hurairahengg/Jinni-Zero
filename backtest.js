/* ═══════════════════════════════════════════════════════════════════
 backtest.js — Full visualization dashboard (fixed correctness + performance)
 Pure Canvas 2D — zero external chart dependencies

 Fix focus:
 - remove replay mode handling completely
 - isolate Monte Carlo to MC charts only
 - strict point caps / downsampling for large datasets
 - fix blank charts by building fallback analytics from real trades
 - support partial exits / scaling slices in trade log
 - preserve existing UI structure + styling
 ═══════════════════════════════════════════════════════════════════ */
(function () {
  const API = 'http://localhost:5000/api/backtest';
  const API_STREAM = 'http://localhost:5000/api/backtest/stream';

  // ── Design tokens ─────────────────────────────────────────────────
  const T = {
    bg: '#080b0f',
    bg2: '#0d1117',
    bg3: '#111820',
    border: '#1e2a38',
    border2: '#243040',
    text: '#c8d8e8',
    dim: '#4a6070',
    mute: '#2a3a4a',
    accent: '#00e5ff',
    accent2: '#0095a8',
    bull: '#00e676',
    bull2: '#00a352',
    bear: '#ff3d5a',
    bear2: '#b02040',
    mono: "'Space Mono', monospace",
  };

  let _lastRenderData = null;
  let _lastConfig = null;

  // performance caps
  const MAX_LINE_POINTS = 1200;
  const MAX_BAR_POINTS = 300;
  const MAX_SCATTER_POINTS = 900;
  const MAX_MC_PATHS_VISUAL = 60;
  const MAX_MC_PATH_POINTS = 700;

  // ════════════════════════════════════════════════════════════════════
  // GRAPH STEP (downsample for visualization only)
  // ════════════════════════════════════════════════════════════════════
  let GRAPH_STEP = 1;
  const graphStepEl = document.getElementById('bt_graphStep');
  if (graphStepEl) {
    graphStepEl.addEventListener('change', function () {
      GRAPH_STEP = parseInt(this.value, 10) || 1;
      if (_lastRenderData) renderAllCharts(_lastRenderData.data, _lastRenderData.analytics);
    });
  }

  function sampleData(arr) {
    if (!Array.isArray(arr) || GRAPH_STEP <= 1) return arr || [];
    return arr.filter(function (_, i) { return i % GRAPH_STEP === 0; });
  }

  // ════════════════════════════════════════════════════════════════════
  // PER-CANVAS VIEWPORT SYSTEM (zoom + pan)
  // ════════════════════════════════════════════════════════════════════
  var _viewports = {};
  var _chartCache = {};
  var _interactionAttached = {};

  function getVP(id) {
    if (!_viewports[id]) _viewports[id] = { s: 0, e: 1 };
    return _viewports[id];
  }

  function resetAllVPs() {
    _viewports = {};
    _chartCache = {};
  }

  function sliceByVP(data, id) {
    if (!data || !data.length) return data || [];
    var vp = getVP(id);
    var len = data.length;
    var si = Math.floor(vp.s * len);
    var ei = Math.ceil(vp.e * len);
    si = Math.max(0, si);
    ei = Math.min(len, ei);
    if (ei <= si) ei = si + 1;
    return data.slice(si, ei);
  }

  function reRenderSingle(id) {
    var cached = _chartCache[id];
    if (!cached) return;
    var c = cached;
    if (c.type === 'line') drawLineChart(id, c.rawData, c.opts, true);
    else if (c.type === 'bar') drawBarChart(id, c.labels, c.values, c.opts, true);
    else if (c.type === 'hist') drawHistogram(id, c.hist, c.opts, true);
    else if (c.type === 'scatter') drawScatter(id, c.points, c.xKey, c.yKey, c.opts, true);
    else if (c.type === 'mc') drawMcPaths(id, c.paths, true);
  }

  function attachInteraction(id) {
    if (_interactionAttached[id]) return;
    var cv = document.getElementById(id);
    if (!cv) return;

    _interactionAttached[id] = true;
    cv.classList.add('bt-canvas-interactive');

    // Wheel zoom
    cv.addEventListener('wheel', function (e) {
      e.preventDefault();
      var vp = getVP(id);
      var rect = cv.getBoundingClientRect();
      var mx = (e.clientX - rect.left) / rect.width;
      var span = vp.e - vp.s;
      var factor = e.deltaY < 0 ? 0.85 : 1.18;
      var newSpan = Math.min(1, Math.max(0.005, span * factor));
      var center = vp.s + mx * span;
      var ns = center - mx * newSpan;
      var ne = center + (1 - mx) * newSpan;
      if (ns < 0) { ne -= ns; ns = 0; }
      if (ne > 1) { ns -= (ne - 1); ne = 1; }
      vp.s = Math.max(0, ns);
      vp.e = Math.min(1, ne);
      reRenderSingle(id);
    }, { passive: false });

    // Drag pan
    var dragging = false, lastX = 0;
    cv.addEventListener('mousedown', function (e) {
      dragging = true;
      lastX = e.clientX;
      e.preventDefault();
    });

    window.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      var vp = getVP(id);
      var rect = cv.getBoundingClientRect();
      var dx = (e.clientX - lastX) / rect.width;
      var span = vp.e - vp.s;
      var shift = -dx * span;
      var ns = vp.s + shift;
      var ne = vp.e + shift;
      if (ns < 0) { ne -= ns; ns = 0; }
      if (ne > 1) { ns -= (ne - 1); ne = 1; }
      vp.s = Math.max(0, ns);
      vp.e = Math.min(1, ne);
      lastX = e.clientX;
      reRenderSingle(id);
    });

    window.addEventListener('mouseup', function () { dragging = false; });

    // Double click reset
    cv.addEventListener('dblclick', function () {
      _viewports[id] = { s: 0, e: 1 };
      reRenderSingle(id);
    });

    // Touch support
    var touchStartDist = 0, touchStartSpan = 0, touchStartCenter = 0;
    var singleTouchX = 0, isTouching = false;

    cv.addEventListener('touchstart', function (e) {
      if (e.touches.length === 2) {
        e.preventDefault();
        var t0 = e.touches[0], t1 = e.touches[1];
        touchStartDist = Math.abs(t0.clientX - t1.clientX);
        var vp = getVP(id);
        touchStartSpan = vp.e - vp.s;
        touchStartCenter = vp.s + touchStartSpan / 2;
      } else if (e.touches.length === 1) {
        isTouching = true;
        singleTouchX = e.touches[0].clientX;
      }
    }, { passive: false });

    cv.addEventListener('touchmove', function (e) {
      if (e.touches.length === 2) {
        e.preventDefault();
        var t0 = e.touches[0], t1 = e.touches[1];
        var dist = Math.abs(t0.clientX - t1.clientX);
        if (touchStartDist === 0) return;
        var ratio = touchStartDist / dist;
        var newSpan = Math.min(1, Math.max(0.005, touchStartSpan * ratio));
        var vp = getVP(id);
        vp.s = Math.max(0, touchStartCenter - newSpan / 2);
        vp.e = Math.min(1, vp.s + newSpan);
        reRenderSingle(id);
      } else if (e.touches.length === 1 && isTouching) {
        e.preventDefault();
        var vp = getVP(id);
        var rect = cv.getBoundingClientRect();
        var dx = (e.touches[0].clientX - singleTouchX) / rect.width;
        var span = vp.e - vp.s;
        var shift = -dx * span;
        var ns = vp.s + shift;
        var ne = vp.e + shift;
        if (ns < 0) { ne -= ns; ns = 0; }
        if (ne > 1) { ns -= (ne - 1); ne = 1; }
        vp.s = Math.max(0, ns);
        vp.e = Math.min(1, ne);
        singleTouchX = e.touches[0].clientX;
        reRenderSingle(id);
      }
    }, { passive: false });

    cv.addEventListener('touchend', function () { isTouching = false; });
  }

  // ════════════════════════════════════════════════════════════════════
  // TAB SWITCHING
  // ════════════════════════════════════════════════════════════════════
  document.querySelectorAll('.tab-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var t = btn.dataset.tab;
      document.querySelectorAll('.tab-btn').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');

      var isChart = t === 'chart';
      document.getElementById('tabChart').style.display = isChart ? '' : 'none';
      document.getElementById('tabChart').classList.toggle('active', isChart);
      document.getElementById('tabBacktest').style.display = isChart ? 'none' : '';
      document.getElementById('tabBacktest').classList.toggle('active', !isChart);
      document.getElementById('chartHeaderRight').style.display = isChart ? '' : 'none';
      document.getElementById('backtestHeaderRight').style.display = isChart ? 'none' : '';
    });
  });

  // ════════════════════════════════════════════════════════════════════
  // MA ROW MANAGEMENT
  // ════════════════════════════════════════════════════════════════════
  var firstRow = document.getElementById('bt_ma_0');
  var maSection = firstRow ? firstRow.parentElement : null;

  function wireRemove(row) {
    var btn = row.querySelector('.bt-remove-ma');
    if (!btn) return;
    btn.addEventListener('click', function () {
      if (document.querySelectorAll('.bt-ma-row').length > 1) row.remove();
    });
  }

  if (firstRow) wireRemove(firstRow);

  var addMaBtn = document.getElementById('bt_addMa');
  if (addMaBtn && maSection) {
    addMaBtn.addEventListener('click', function () {
      var row = document.createElement('div');
      row.className = 'bt-ma-row';
      row.innerHTML =
        '<select class="bt-select bt-select-sm" data-ma-type>' +
        '<option value="EMA" selected>EMA</option><option value="HMA">HMA</option>' +
        '<option value="SMA">SMA</option><option value="WMA">WMA</option>' +
        '</select>' +
        '<input class="bt-input bt-input-sm" type="number" data-ma-period value="200" min="2" max="500"/>' +
        '<button class="bt-icon-btn bt-remove-ma" title="Remove">✕</button>';
      wireRemove(row);
      maSection.insertBefore(row, addMaBtn);
    });
  }

  // ── SL toggle ─────────────────────────────────────────────────────
  document.querySelectorAll('input[name="sl_mode"]').forEach(function (r) {
    r.addEventListener('change', function () {
      var checked = document.querySelector('input[name="sl_mode"]:checked');
      var v = checked ? checked.value : 'fixed';
      var slFixed = document.getElementById('bt_sl_fixed_wrap');
      var slMa = document.getElementById('bt_sl_ma_wrap');
      if (slFixed) slFixed.style.display = v === 'fixed' ? '' : 'none';
      if (slMa) slMa.style.display = (v === 'ma_cross' || v === 'ma_snapshot') ? '' : 'none';
    });
  });

  // ── TP toggle ─────────────────────────────────────────────────────
  document.querySelectorAll('input[name="tp_mode"]').forEach(function (r) {
    r.addEventListener('change', function () {
      var checked = document.querySelector('input[name="tp_mode"]:checked');
      var v = checked ? checked.value : 'r_multiple';
      var tpR = document.getElementById('bt_tp_r_wrap');
      var tpMa = document.getElementById('bt_tp_ma_wrap');
      if (tpR) tpR.style.display = v === 'r_multiple' ? '' : 'none';
      if (tpMa) tpMa.style.display = v === 'ma_cross' ? '' : 'none';
    });
  });

  // ── Gating toggle ─────────────────────────────────────────────────
  var gatingEnabled = document.getElementById('bt_gatingEnabled');
  if (gatingEnabled) {
    gatingEnabled.addEventListener('change', function () {
      var wrap = document.getElementById('bt_gating_wrap');
      if (wrap) wrap.style.display = this.checked ? '' : 'none';
    });
  }

  // ── R buttons ─────────────────────────────────────────────────────
  var selectedR = 2;
  document.querySelectorAll('.bt-r-btn').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('.bt-r-btn').forEach(function (x) { x.classList.remove('active'); });
      b.classList.add('active');
      selectedR = parseFloat(b.dataset.r || '2');
    });
  });

  // ── Lot size hint ─────────────────────────────────────────────────
  function updateLotHint() {
    var ls = parseFloat((document.getElementById('bt_lotSize') || {}).value || '1') || 1;
    var hint = document.getElementById('bt_lotHint');
    if (hint) hint.textContent = '1 pt = $' + ls.toFixed(ls < 0.1 ? 3 : 2);
  }
  var lotSizeEl = document.getElementById('bt_lotSize');
  if (lotSizeEl) lotSizeEl.addEventListener('input', updateLotHint);
  updateLotHint();

  // ════════════════════════════════════════════════════════════════════
  // CONFIG COLLECTION (legacy/manual mode)
  // ════════════════════════════════════════════════════════════════════
  function collectConfig() {
    var mas = [];
    document.querySelectorAll('.bt-ma-row').forEach(function (row) {
      var typeEl = row.querySelector('[data-ma-type]');
      var periodEl = row.querySelector('[data-ma-period]');
      var type = typeEl ? typeEl.value : 'EMA';
      var p = parseInt(periodEl ? periodEl.value : '0', 10);
      if (!isNaN(p) && p >= 2) mas.push({ type: type, period: p });
    });

    var slMode = document.querySelector('input[name="sl_mode"]:checked');
    var tpMode = document.querySelector('input[name="tp_mode"]:checked');
    var commType = document.querySelector('input[name="comm_type"]:checked');

    return {
      range: parseInt((document.getElementById('bt_range') || {}).value || '10', 10),
      bar_range: parseInt((document.getElementById('bt_barRange') || {}).value || '1000', 10),
      starting_capital: parseFloat((document.getElementById('bt_startingCapital') || {}).value || '10000') || 10000,
      lot_size: parseFloat((document.getElementById('bt_lotSize') || {}).value || '1.0') || 1.0,
      mas: mas,
      entry: (document.getElementById('bt_entry') || {}).value || 'above_all_mas',
      require_candle_confirm: !!((document.getElementById('bt_candleConfirm') || {}).checked),
      sl: {
        mode: slMode ? slMode.value : 'fixed',
        fixed_pts: parseFloat((document.getElementById('bt_sl_fixed') || {}).value || '8') || 8,
        ma_type: (document.getElementById('bt_sl_ma_type') || {}).value || 'EMA',
        ma_length: parseInt((document.getElementById('bt_sl_ma_length') || {}).value || '50', 10) || 50,
      },
      tp: {
        mode: tpMode ? tpMode.value : 'r_multiple',
        r_multiple: selectedR,
        ma_type: (document.getElementById('bt_tp_ma_type') || {}).value || 'EMA',
        ma_length: parseInt((document.getElementById('bt_tp_ma_length') || {}).value || '9', 10) || 9,
      },
      gating: {
        enabled: !!((document.getElementById('bt_gatingEnabled') || {}).checked),
        ma_type: (document.getElementById('bt_gating_ma_type') || {}).value || 'HMA',
        ma_length: parseInt((document.getElementById('bt_gating_ma_length') || {}).value || '21', 10) || 21,
      },
      commission: {
        type: commType ? commType.value : 'flat',
        amount: parseFloat((document.getElementById('bt_commission') || {}).value || '4') || 4,
      },
      ambiguous_bar_mode: (document.getElementById('bt_ambiguousMode') || {}).value || 'conservative',
      monte_carlo_runs: parseInt((document.getElementById('bt_mcRuns') || {}).value || '0', 10) || 0
    };
  }

  // ════════════════════════════════════════════════════════════════════
  // PROGRESS
  // ════════════════════════════════════════════════════════════════════
  var STEPS = ['step_load', 'step_run', 'step_stats', 'step_charts', 'step_done'];
  var STEP_LABELS = {
    step_load: 'Loading data…',
    step_run: 'Running backtest…',
    step_stats: 'Computing statistics…',
    step_charts: 'Building charts…',
    step_done: 'Complete ✓'
  };
  var STEP_PCTS = {
    step_load: 15,
    step_run: 45,
    step_stats: 65,
    step_charts: 85,
    step_done: 100
  };

  function setStep(id) {
    STEPS.forEach(function (s) {
      var el = document.getElementById(s);
      if (!el) return;
      el.classList.remove('active', 'done');
      var si = STEPS.indexOf(s), ci = STEPS.indexOf(id);
      if (si < ci) el.classList.add('done');
      if (si === ci) el.classList.add('active');
    });

    var label = document.getElementById('bt_progressLabel');
    var bar = document.getElementById('bt_progressBar');
    var pct = document.getElementById('bt_progressPct');
    var p = STEP_PCTS[id] || 0;

    if (label) label.textContent = STEP_LABELS[id] || '…';
    if (bar) bar.style.width = p + '%';
    if (pct) pct.textContent = p + '%';
  }

  function showProgress() {
    var wrap = document.getElementById('bt_progressWrap');
    var empty = document.getElementById('bt_empty');
    var dash = document.getElementById('bt_dashboard');
    var ls = document.getElementById('bt_liveStats');
    if (wrap) wrap.style.display = '';
    if (empty) empty.style.display = 'none';
    if (dash) dash.style.display = 'none';
    if (ls) ls.style.display = '';
  }

  function hideProgress() {
    var wrap = document.getElementById('bt_progressWrap');
    var ls = document.getElementById('bt_liveStats');
    if (wrap) wrap.style.display = 'none';
    if (ls) ls.style.display = 'none';
  }

  function delay(ms) {
    return new Promise(function (r) { setTimeout(r, ms); });
  }

  function updateLiveProgress(msg) {
    var pct = msg.pct != null ? msg.pct : 0;
    var barEl = document.getElementById('bt_progressBar');
    var pctEl = document.getElementById('bt_progressPct');
    var lbl = document.getElementById('bt_progressLabel');

    if (barEl) barEl.style.width = pct + '%';
    if (pctEl) pctEl.textContent = Math.round(pct) + '%';
    if (lbl) lbl.textContent = msg.label || ('Bar ' + (msg.bar || 0) + ' / ' + (msg.total || 0));

    var eqEl = document.getElementById('bt_liveEquity');
    var ddEl = document.getElementById('bt_liveDD');
    var otEl = document.getElementById('bt_liveOpen');
    var lpEl = document.getElementById('bt_lastPnl');

    if (eqEl) {
      var eq = Number(msg.equity || 0);
      eqEl.textContent = '$' + eq.toFixed(2);
      eqEl.className = 'bt-live-value' + (eq >= 0 ? ' bull' : ' bear');
    }
    if (ddEl) {
      var dd = Number(msg.drawdown || 0);
      ddEl.textContent = '$' + dd.toFixed(2);
      ddEl.className = 'bt-live-value bear';
    }
    if (otEl) {
      if (msg.open_trade) {
        var ot = msg.open_trade;
        otEl.textContent = String(ot.direction || '').toUpperCase() + ' @ ' + Number(ot.entry_price || ot.entry || 0).toFixed(2);
        otEl.className = 'bt-live-value ' + (ot.direction === 'long' ? 'bull' : 'bear');
      } else {
        otEl.textContent = '—';
        otEl.className = 'bt-live-value';
      }
    }
    if (lpEl) {
      if (msg.last_closed_pnl != null) {
        var v = Number(msg.last_closed_pnl || 0);
        lpEl.textContent = (v >= 0 ? '+' : '') + '$' + v.toFixed(2);
        lpEl.className = 'bt-live-value' + (v >= 0 ? ' bull' : ' bear');
      } else {
        lpEl.textContent = '—';
        lpEl.className = 'bt-live-value';
      }
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // DATA HELPERS
  // ════════════════════════════════════════════════════════════════════
  function arrMin(arr) {
    if (!arr || !arr.length) return 0;
    var m = Infinity;
    for (var i = 0; i < arr.length; i++) {
      var v = arr[i];
      if (v != null && isFinite(v) && v < m) m = v;
    }
    return m === Infinity ? 0 : m;
  }

  function arrMax(arr) {
    if (!arr || !arr.length) return 0;
    var m = -Infinity;
    for (var i = 0; i < arr.length; i++) {
      var v = arr[i];
      if (v != null && isFinite(v) && v > m) m = v;
    }
    return m === -Infinity ? 0 : m;
  }

  function downsampleLineSeries(data, maxPts) {
    if (!data || data.length <= maxPts) return data || [];
    var bucketSize = data.length / maxPts;
    var result = [data[0]];
    for (var b = 1; b < maxPts - 1; b++) {
      var start = Math.floor(b * bucketSize);
      var end = Math.floor((b + 1) * bucketSize);
      var minV = Infinity, maxV = -Infinity, minI = start, maxI = start;
      for (var i = start; i < end && i < data.length; i++) {
        var v = data[i];
        if (v == null || !isFinite(v)) continue;
        if (v < minV) { minV = v; minI = i; }
        if (v > maxV) { maxV = v; maxI = i; }
      }
      if (minV !== Infinity) {
        if (minI < maxI) { result.push(data[minI]); result.push(data[maxI]); }
        else { result.push(data[maxI]); result.push(data[minI]); }
      }
    }
    result.push(data[data.length - 1]);
    return result;
  }

  function limitBarSeries(labels, values, maxPts) {
    labels = labels || [];
    values = values || [];
    if (labels.length <= maxPts) return { labels: labels, values: values };
    var step = Math.ceil(labels.length / maxPts);
    var outL = [], outV = [];
    for (var i = 0; i < labels.length; i += step) {
      outL.push(labels[i]);
      outV.push(values[i]);
    }
    return { labels: outL, values: outV };
  }

  function limitScatter(points, maxPts) {
    points = points || [];
    if (points.length <= maxPts) return points;
    var step = Math.ceil(points.length / maxPts);
    var out = [];
    for (var i = 0; i < points.length; i += step) out.push(points[i]);
    return out;
  }

  function getCanvas(id) {
    var cv = document.getElementById(id);
    if (!cv) return null;
    var dpr = window.devicePixelRatio || 1;
    var w = cv.parentElement ? (cv.parentElement.clientWidth - 2) : 0;
    if (w < 50) {
      var rect = cv.getBoundingClientRect();
      w = rect.width > 50 ? rect.width - 2 : 400;
    }
    w = Math.max(w, 50);

    var h = cv.clientHeight;
    if (h < 30) {
      var rect2 = cv.getBoundingClientRect();
      h = rect2.height > 30 ? rect2.height : (cv.classList.contains('bt-canvas-sm') ? 180 : 260);
    }
    h = Math.max(h, 30);

    cv.width = w * dpr;
    cv.height = h * dpr;
    cv.style.width = w + 'px';
    cv.style.height = h + 'px';

    var ctx = cv.getContext('2d');
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.fillStyle = T.bg2;
    ctx.fillRect(0, 0, w, h);
    return { ctx: ctx, w: w, h: h };
  }

  function labelFont(size) {
    size = size || 9;
    return '700 ' + size + 'px ' + T.mono;
  }

  // ════════════════════════════════════════════════════════════════════
  // DRAWERS
  // ════════════════════════════════════════════════════════════════════
  function drawLineChart(id, rawData, opts, isRerender) {
    rawData = Array.isArray(rawData) ? rawData.filter(function (v) { return v != null && isFinite(v); }) : [];
    if (!rawData.length) return;

    opts = opts || {};
    if (!isRerender) _chartCache[id] = { type: 'line', rawData: rawData, opts: opts };

    var sampled = sampleData(downsampleLineSeries(rawData, MAX_LINE_POINTS));
    var totalLen = sampled.length;
    var data = sliceByVP(sampled, id);
    if (!data.length) return;

    var g = getCanvas(id);
    if (!g) return;
    var ctx = g.ctx, w = g.w, h = g.h;
    var pad = { t: 20, r: 16, b: 32, l: 68 };
    var pw = w - pad.l - pad.r;
    var ph = h - pad.t - pad.b;
    if (pw < 10 || ph < 10) return;

    var mn = arrMin(data), mx = arrMax(data);
    if (mn === mx) { mn -= 1; mx += 1; }
    var range = mx - mn;
    var lineCol = opts.color || T.bull;

    var vp = getVP(id);
    var vpStartIdx = Math.floor(vp.s * totalLen);

    if (opts.refVal != null && isFinite(opts.refVal) && opts.refVal >= mn && opts.refVal <= mx) {
      var ry = pad.t + ph * (1 - (opts.refVal - mn) / range);
      ctx.strokeStyle = T.border2;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 5]);
      ctx.beginPath();
      ctx.moveTo(pad.l, ry);
      ctx.lineTo(w - pad.r, ry);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = labelFont(7);
      ctx.fillStyle = T.dim;
      ctx.textAlign = 'left';
      ctx.fillText(opts.refLabel || String(opts.refVal.toFixed(0)), pad.l + 4, ry - 3);
    }

    if (opts.zeroLine && mn < 0 && mx > 0) {
      var zy = pad.t + ph * (1 - (0 - mn) / range);
      ctx.strokeStyle = T.border2;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(pad.l, zy);
      ctx.lineTo(w - pad.r, zy);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.strokeStyle = T.border;
    ctx.lineWidth = 0.5;
    for (var i = 0; i <= 4; i++) {
      var gy = pad.t + ph * (i / 4);
      ctx.beginPath();
      ctx.moveTo(pad.l, gy);
      ctx.lineTo(w - pad.r, gy);
      ctx.stroke();

      var gv = mx - range * (i / 4);
      ctx.font = labelFont(8);
      ctx.fillStyle = T.dim;
      ctx.textAlign = 'right';
      var glabel = Math.abs(gv) >= 1000 ? (gv / 1000).toFixed(1) + 'k' : gv.toFixed(1);
      ctx.fillText(glabel, pad.l - 5, gy + 3);
    }

    var gradient = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
    gradient.addColorStop(0, lineCol + '44');
    gradient.addColorStop(1, lineCol + '00');

    ctx.beginPath();
    var first = true;
    data.forEach(function (v, i) {
      var x = pad.l + (i / Math.max(1, data.length - 1)) * pw;
      var y = pad.t + ph * (1 - (v - mn) / range);
      if (first) { ctx.moveTo(x, y); first = false; }
      else ctx.lineTo(x, y);
    });
    ctx.lineTo(pad.l + pw, pad.t + ph);
    ctx.lineTo(pad.l, pad.t + ph);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.beginPath();
    first = true;
    data.forEach(function (v, i) {
      var x = pad.l + (i / Math.max(1, data.length - 1)) * pw;
      var y = pad.t + ph * (1 - (v - mn) / range);
      if (first) { ctx.moveTo(x, y); first = false; }
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = lineCol;
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.font = labelFont(8);
    ctx.fillStyle = T.dim;
    ctx.textAlign = 'center';
    [0, 0.25, 0.5, 0.75, 1].forEach(function (t) {
      var idx = vpStartIdx + Math.floor(t * Math.max(0, data.length - 1));
      ctx.fillText(String(idx), pad.l + t * pw, h - pad.b + 14);
    });

    attachInteraction(id);
  }

  function drawBarChart(id, labels, values, opts, isRerender) {
    labels = Array.isArray(labels) ? labels : [];
    values = Array.isArray(values) ? values : [];
    if (!labels.length || !values.length) return;

    var limited = limitBarSeries(labels, values, MAX_BAR_POINTS);
    labels = limited.labels;
    values = limited.values;

    opts = opts || {};
    if (!isRerender) _chartCache[id] = { type: 'bar', labels: labels, values: values, opts: opts };

    var vp = getVP(id);
    var len = labels.length;
    var si = Math.floor(vp.s * len);
    var ei = Math.ceil(vp.e * len);
    si = Math.max(0, si);
    ei = Math.min(len, ei);
    if (ei <= si) ei = si + 1;

    var vLabels = labels.slice(si, ei);
    var vValues = values.slice(si, ei);
    if (!vLabels.length || !vValues.length) return;

    var g = getCanvas(id);
    if (!g) return;
    var ctx = g.ctx, w = g.w, h = g.h;
    var pad = { t: 16, r: 12, b: 44, l: 58 };
    var pw = w - pad.l - pad.r;
    var ph = h - pad.t - pad.b;
    if (pw < 10 || ph < 10) return;

    var mn = 0, mx = 0;
    for (var vi = 0; vi < vValues.length; vi++) {
      var vv = Number(vValues[vi] || 0);
      if (vv < mn) mn = vv;
      if (vv > mx) mx = vv;
    }
    if (mn === mx) { mn -= 1; mx += 1; }
    var range = mx - mn;
    var slotW = pw / Math.max(1, vLabels.length);
    var barW = slotW * 0.7;
    var barOff = slotW * 0.15;

    ctx.strokeStyle = T.border;
    ctx.lineWidth = 0.5;
    for (var i = 0; i <= 4; i++) {
      var y = pad.t + ph * (i / 4);
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(w - pad.r, y);
      ctx.stroke();

      var v = mx - range * (i / 4);
      ctx.font = labelFont(8);
      ctx.fillStyle = T.dim;
      ctx.textAlign = 'right';
      ctx.fillText(Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(1), pad.l - 4, y + 3);
    }

    var zy = pad.t + ph * (1 - (0 - mn) / range);
    ctx.strokeStyle = T.border2;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.l, zy);
    ctx.lineTo(w - pad.r, zy);
    ctx.stroke();

    vLabels.forEach(function (lbl, i) {
      var v = Number(vValues[i] || 0);
      var x = pad.l + i * slotW + barOff;
      var barH = Math.abs(v / range) * ph;
      var y = v >= 0 ? zy - barH : zy;
      var col = opts.colorFn ? opts.colorFn(v, i + si) : (v >= 0 ? T.bull : T.bear);

      ctx.fillStyle = col + 'bb';
      ctx.fillRect(x, y, barW, Math.max(barH, 1));
      ctx.strokeStyle = col;
      ctx.lineWidth = 0.5;
      ctx.strokeRect(x, y, barW, Math.max(barH, 1));

      ctx.font = labelFont(7);
      ctx.fillStyle = T.dim;
      ctx.textAlign = 'center';
      ctx.fillText(String(lbl).substring(0, 8), x + barW / 2, h - pad.b + 13);
    });

    attachInteraction(id);
  }

  function drawHistogram(id, hist, opts, isRerender) {
    hist = hist || {};
    var counts = Array.isArray(hist.counts) ? hist.counts : [];
    var edges = Array.isArray(hist.edges) ? hist.edges : [];
    if (!counts.length) return;

    opts = opts || {};
    if (!isRerender) _chartCache[id] = { type: 'hist', hist: hist, opts: opts };

    var vp = getVP(id);
    var len = counts.length;
    var si = Math.floor(vp.s * len);
    var ei = Math.ceil(vp.e * len);
    si = Math.max(0, si);
    ei = Math.min(len, ei);
    if (ei <= si) ei = si + 1;

    var vCounts = counts.slice(si, ei);
    var vEdges = edges.length ? edges.slice(si, ei + 1) : [];
    if (!vCounts.length) return;

    var g = getCanvas(id);
    if (!g) return;
    var ctx = g.ctx, w = g.w, h = g.h;
    var maxV = 0;
    for (var ci = 0; ci < vCounts.length; ci++) maxV = Math.max(maxV, Number(vCounts[ci] || 0));
    maxV = maxV || 1;

    var pad = { t: 16, r: 12, b: 36, l: 44 };
    var pw = w - pad.l - pad.r;
    var ph = h - pad.t - pad.b;
    if (pw < 10 || ph < 10) return;

    var bw = pw / Math.max(1, vCounts.length);

    ctx.strokeStyle = T.border;
    ctx.lineWidth = 0.5;
    for (var i = 0; i <= 4; i++) {
      var y = pad.t + ph * (i / 4);
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(w - pad.r, y);
      ctx.stroke();

      ctx.font = labelFont(8);
      ctx.fillStyle = T.dim;
      ctx.textAlign = 'right';
      ctx.fillText(String(Math.round(maxV * (1 - i / 4))), pad.l - 4, y + 3);
    }

    vCounts.forEach(function (c, i) {
      var x = pad.l + i * bw;
      var barH = (Number(c || 0) / maxV) * ph;
      var edgeVal = vEdges.length ? Number(vEdges[i] || 0) : i;
      var col = opts.colorFn ? opts.colorFn(edgeVal) : (edgeVal >= 0 ? T.bull + '99' : T.bear + '99');
      ctx.fillStyle = col;
      ctx.fillRect(x + 1, pad.t + ph - barH, Math.max(1, bw - 2), Math.max(barH, 1));
    });

    [0, Math.floor(vCounts.length / 2), vCounts.length - 1].forEach(function (i) {
      if (i >= vCounts.length) return;
      var x = pad.l + i * bw + bw / 2;
      ctx.font = labelFont(7);
      ctx.fillStyle = T.dim;
      ctx.textAlign = 'center';
      var edge = vEdges.length ? Number(vEdges[i] || 0).toFixed(1) : String(i);
      ctx.fillText(edge, x, h - pad.b + 13);
    });

    attachInteraction(id);
  }

  function drawScatter(id, points, xKey, yKey, opts, isRerender) {
    points = Array.isArray(points) ? points.filter(function (p) {
      return p && isFinite(Number(p[xKey])) && isFinite(Number(p[yKey]));
    }) : [];
    if (!points.length) return;

    points = limitScatter(points, MAX_SCATTER_POINTS);

    opts = opts || {};
    if (!isRerender) _chartCache[id] = { type: 'scatter', points: points, xKey: xKey, yKey: yKey, opts: opts };

    var sorted = points.slice().sort(function (a, b) { return Number(a[xKey]) - Number(b[xKey]); });

    var vp = getVP(id);
    var len = sorted.length;
    var si = Math.floor(vp.s * len);
    var ei = Math.ceil(vp.e * len);
    si = Math.max(0, si);
    ei = Math.min(len, ei);
    if (ei <= si) ei = si + 1;

    var vPoints = sorted.slice(si, ei);
    if (!vPoints.length) return;

    var g = getCanvas(id);
    if (!g) return;
    var ctx = g.ctx, w = g.w, h = g.h;
    var pad = { t: 20, r: 20, b: 36, l: 52 };
    var pw = w - pad.l - pad.r;
    var ph = h - pad.t - pad.b;
    if (pw < 10 || ph < 10) return;

    var xmn = Infinity, xmx = -Infinity, ymn = Infinity, ymx = -Infinity;
    for (var pi = 0; pi < vPoints.length; pi++) {
      var p = vPoints[pi];
      var x = Number(p[xKey]), y = Number(p[yKey]);
      if (x < xmn) xmn = x;
      if (x > xmx) xmx = x;
      if (y < ymn) ymn = y;
      if (y > ymx) ymx = y;
    }

    if (xmn === xmx) { xmn -= 1; xmx += 1; }
    if (ymn === ymx) { ymn -= 1; ymx += 1; }

    var xr = xmx - xmn;
    var yr = ymx - ymn;

    ctx.strokeStyle = T.border;
    ctx.lineWidth = 0.5;
    for (var i = 0; i <= 4; i++) {
      var yy = pad.t + ph * (i / 4);
      var xx = pad.l + pw * (i / 4);
      ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(xx, pad.t); ctx.lineTo(xx, h - pad.b); ctx.stroke();
    }

    ctx.font = labelFont(7);
    ctx.fillStyle = T.dim;
    ctx.textAlign = 'right';
    for (var yi = 0; yi <= 4; yi++) {
      ctx.fillText((ymx - yr * (yi / 4)).toFixed(1), pad.l - 4, pad.t + ph * (yi / 4) + 3);
    }

    ctx.textAlign = 'center';
    for (var xi = 0; xi <= 4; xi++) {
      ctx.fillText((xmn + xr * (xi / 4)).toFixed(1), pad.l + pw * (xi / 4), h - pad.b + 13);
    }

    vPoints.forEach(function (p) {
      var x = pad.l + ((Number(p[xKey]) - xmn) / xr) * pw;
      var y = pad.t + (1 - ((Number(p[yKey]) - ymn) / yr)) * ph;
      var col = opts.colorFn ? opts.colorFn(p) : (p.win ? T.bull : T.bear);
      ctx.beginPath();
      ctx.arc(x, y, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = col + 'aa';
      ctx.fill();
      ctx.strokeStyle = col;
      ctx.lineWidth = 0.5;
      ctx.stroke();
    });

    attachInteraction(id);
  }

  function drawMcPaths(id, paths, isRerender) {
    paths = Array.isArray(paths) ? paths.filter(function (p) { return Array.isArray(p) && p.length; }) : [];
    if (!paths.length) return;

    // isolate MC visual load
    paths = paths.slice(0, MAX_MC_PATHS_VISUAL).map(function (p) {
      return downsampleLineSeries(p.filter(function (v) { return v != null && isFinite(v); }), MAX_MC_PATH_POINTS);
    }).filter(function (p) { return p.length > 1; });

    if (!paths.length) return;
    if (!isRerender) _chartCache[id] = { type: 'mc', paths: paths };

    var g = getCanvas(id);
    if (!g) return;
    var ctx = g.ctx, w = g.w, h = g.h;
    var pad = { t: 20, r: 16, b: 32, l: 68 };
    var pw = w - pad.l - pad.r;
    var ph = h - pad.t - pad.b;
    if (pw < 10 || ph < 10) return;

    var processedPaths = [];
    var mn = Infinity, mx = -Infinity;

    for (var pi = 0; pi < paths.length; pi++) {
      var sampled = sampleData(paths[pi]);
      var sliced = sliceByVP(sampled, id);
      if (!sliced.length) continue;
      processedPaths.push(sliced);
      for (var vi = 0; vi < sliced.length; vi++) {
        if (sliced[vi] < mn) mn = sliced[vi];
        if (sliced[vi] > mx) mx = sliced[vi];
      }
    }

    if (!processedPaths.length) return;
    if (mn === Infinity || mx === -Infinity) return;
    if (mn === mx) { mn -= 1; mx += 1; }
    var range = mx - mn;

    ctx.strokeStyle = T.border;
    ctx.lineWidth = 0.5;
    for (var i = 0; i <= 4; i++) {
      var gy = pad.t + ph * (i / 4);
      ctx.beginPath();
      ctx.moveTo(pad.l, gy);
      ctx.lineTo(w - pad.r, gy);
      ctx.stroke();

      var gv = mx - range * (i / 4);
      ctx.font = labelFont(8);
      ctx.fillStyle = T.dim;
      ctx.textAlign = 'right';
      ctx.fillText(Math.abs(gv) >= 1000 ? (gv / 1000).toFixed(1) + 'k' : gv.toFixed(0), pad.l - 4, gy + 3);
    }

    processedPaths.forEach(function (path) {
      var dsN = path.length;
      if (dsN < 2) return;
      var firstVal = Number(path[0] || 0);
      var lastVal = Number(path[dsN - 1] || 0);
      var col = lastVal >= firstVal ? T.bull : T.bear;

      ctx.beginPath();
      path.forEach(function (v, i) {
        var x = pad.l + (i / Math.max(1, dsN - 1)) * pw;
        var y = pad.t + ph * (1 - ((Number(v) - mn) / range));
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = col + '55';
      ctx.lineWidth = 0.8;
      ctx.stroke();
    });

    attachInteraction(id);
  }

  // ════════════════════════════════════════════════════════════════════
  // KPI HELPERS
  // ════════════════════════════════════════════════════════════════════
  function kpiCard(label, value, colorClass, subtext) {
    colorClass = colorClass || '';
    subtext = subtext || '';
    return '<div class="bt-kpi"><div class="bt-kpi-label">' + label + '</div><div class="bt-kpi-value ' + colorClass + '">' + value + '</div>' + (subtext ? '<div class="bt-kpi-sub">' + subtext + '</div>' : '') + '</div>';
  }

  function colorClass(v, goodPos) {
    if (goodPos === undefined) goodPos = true;
    if (v == null || v === 0 || !isFinite(v)) return '';
    return goodPos ? (v > 0 ? 'bull' : 'bear') : (v > 0 ? 'bear' : 'bull');
  }

  function fmt(v, prefix, dec) {
    prefix = prefix == null ? '$' : prefix;
    dec = dec != null ? dec : 2;
    if (v == null || !isFinite(Number(v))) return '—';
    return prefix + Number(v).toFixed(dec);
  }

  function fmtPct(v, dec) {
    dec = dec != null ? dec : 1;
    if (v == null || !isFinite(Number(v))) return '—';
    return Number(v).toFixed(dec) + '%';
  }

  function fillKpiGrid(id, cards) {
    var el = document.getElementById(id);
    if (el) el.innerHTML = (cards || []).join('');
  }

  // ════════════════════════════════════════════════════════════════════
  // FALLBACK ANALYTICS BUILDERS
  // ════════════════════════════════════════════════════════════════════
  function toNumber(v, fallback) {
    var n = Number(v);
    return isFinite(n) ? n : (fallback != null ? fallback : 0);
  }

  function sanitizeNumericArray(arr) {
    if (!Array.isArray(arr)) return [];
    return arr.map(function (v) { return Number(v); }).filter(function (v) { return isFinite(v); });
  }

  function normalizeEpochSeconds(ts) {
    var n = Number(ts);
    if (!isFinite(n) || n <= 0) return null;
    if (n > 1000000000000) return Math.floor(n / 1000);
    return Math.floor(n);
  }

  function safeDateFromTs(ts) {
    var s = normalizeEpochSeconds(ts);
    if (s == null) return null;
    var d = new Date(s * 1000);
    if (isNaN(d.getTime())) return null;
    return d;
  }

  function histogram(data, bins) {
    data = sanitizeNumericArray(data);
    bins = bins || 20;
    if (!data.length) return { edges: [], counts: [] };
    var mn = Math.min.apply(null, data);
    var mx = Math.max.apply(null, data);
    if (mn === mx) return { edges: [mn, mx], counts: [data.length] };
    var w = (mx - mn) / bins;
    var counts = new Array(bins).fill(0);
    var edges = [];
    for (var i = 0; i <= bins; i++) edges.push(Number((mn + i * w).toFixed(4)));
    data.forEach(function (x) {
      var idx = Math.floor((x - mn) / w);
      idx = Math.max(0, Math.min(bins - 1, idx));
      counts[idx] += 1;
    });
    return { edges: edges, counts: counts };
  }

  function rollingMetric(trades, winSize, calcFn) {
    if (!Array.isArray(trades) || !trades.length) return [];
    var out = [];
    for (var i = 0; i < trades.length; i++) {
      var start = Math.max(0, i - winSize + 1);
      var slice = trades.slice(start, i + 1);
      out.push(calcFn(slice, i));
    }
    return out;
  }

  function computeRollingAnalytics(trades) {
    var winSize = 20;
    return {
      win_rate: rollingMetric(trades, winSize, function (slice) {
        if (!slice.length) return 0;
        var wins = slice.filter(function (t) { return toNumber(t.net_pnl, 0) > 0; }).length;
        return (wins / slice.length) * 100;
      }),
      expectancy: rollingMetric(trades, winSize, function (slice) {
        if (!slice.length) return 0;
        var sum = 0;
        slice.forEach(function (t) { sum += toNumber(t.net_pnl, 0); });
        return sum / slice.length;
      }),
      profit_factor: rollingMetric(trades, winSize, function (slice) {
        var gp = 0, gl = 0;
        slice.forEach(function (t) {
          var v = toNumber(t.net_pnl, 0);
          if (v > 0) gp += v;
          else gl += Math.abs(v);
        });
        if (gl <= 0) return gp > 0 ? gp : 0;
        return gp / gl;
      }),
      sharpe: rollingMetric(trades, winSize, function (slice) {
        var vals = slice.map(function (t) { return toNumber(t.net_pnl, 0); });
        if (vals.length < 2) return 0;
        var mean = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
        var variance = vals.reduce(function (a, b) { return a + Math.pow(b - mean, 2); }, 0) / Math.max(1, vals.length - 1);
        var std = Math.sqrt(variance);
        return std > 0 ? (mean / std) : 0;
      })
    };
  }

  function computeTimeBreakdown(trades) {
    var byHour = {};
    var byDOW = {};
    var byMonth = {};
    var DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    trades.forEach(function (t) {
      var dt = safeDateFromTs(t.exit_time || t.entry_time);
      if (!dt) return;
      var h = String(dt.getHours());
      var dow = DOW[dt.getDay()];
      var mon = MONTHS[dt.getMonth()];
      var pnl = toNumber(t.net_pnl, 0);

      if (!byHour[h]) byHour[h] = { net: 0, trades: 0 };
      if (!byDOW[dow]) byDOW[dow] = { net: 0, trades: 0 };
      if (!byMonth[mon]) byMonth[mon] = { net: 0, trades: 0 };

      byHour[h].net += pnl; byHour[h].trades += 1;
      byDOW[dow].net += pnl; byDOW[dow].trades += 1;
      byMonth[mon].net += pnl; byMonth[mon].trades += 1;
    });

    return {
      time_of_day: byHour,
      day_of_week: byDOW,
      by_month: byMonth
    };
  }

  function computeCommissionSummary(trades) {
    var total = 0, gross = 0, net = 0;
    trades.forEach(function (t) {
      total += toNumber(t.commission, 0);
      gross += toNumber(t.gross_pnl, 0);
      net += toNumber(t.net_pnl, 0);
    });
    return {
      total: total,
      per_trade: trades.length ? total / trades.length : 0,
      net_without_comm: gross,
      net_with_comm: net,
      pct_of_gross: gross !== 0 ? (total / Math.abs(gross)) * 100 : 0
    };
  }

  function computeScatterData(trades) {
    var maeMfe = [];
    var retScatter = [];

    trades.forEach(function (t, i) {
      var mae = toNumber(t.mae_dollar, NaN);
      var mfe = toNumber(t.mfe_dollar, NaN);

      if (isFinite(mae) && isFinite(mfe)) {
        maeMfe.push({
          mae: mae,
          mfe: mfe,
          win: toNumber(t.net_pnl, 0) > 0
        });
      }

      var rv = t.net_pnl_r != null && isFinite(Number(t.net_pnl_r))
        ? Number(t.net_pnl_r)
        : toNumber(t.net_pnl, 0);

      retScatter.push({
        x: i + 1,
        y: rv,
        win: toNumber(t.net_pnl, 0) > 0
      });
    });

    return {
      mae_mfe: maeMfe,
      return_scatter: retScatter
    };
  }

  function generateMonteCarlo(trades, startingCapital, runs) {
    runs = Math.max(0, parseInt(runs || 0, 10) || 0);
    if (!Array.isArray(trades) || !trades.length || runs <= 0) {
      return {
        final_equity: {},
        max_drawdown: {},
        paths_sample: [],
        final_dist: { edges: [], counts: [] },
        dd_dist: { edges: [], counts: [] },
        prob_profitable: null,
        prob_dd_10: null,
        prob_dd_20: null,
        prob_dd_30: null
      };
    }

    var pnlSeries = trades.map(function (t) { return toNumber(t.net_pnl, 0); });
    var finals = [];
    var dds = [];
    var pathsSample = [];

    function percentile(arr, p) {
      if (!arr.length) return null;
      var sorted = arr.slice().sort(function (a, b) { return a - b; });
      var idx = (p / 100) * (sorted.length - 1);
      var lo = Math.floor(idx);
      var hi = Math.ceil(idx);
      if (lo === hi) return sorted[lo];
      var w = idx - lo;
      return sorted[lo] * (1 - w) + sorted[hi] * w;
    }

    for (var r = 0; r < runs; r++) {
      var eq = startingCapital;
      var peak = startingCapital;
      var maxDD = 0;
      var path = [startingCapital];

      for (var i = 0; i < pnlSeries.length; i++) {
        var pick = pnlSeries[Math.floor(Math.random() * pnlSeries.length)];
        eq += pick;
        peak = Math.max(peak, eq);
        maxDD = Math.max(maxDD, peak - eq);
        path.push(eq);
      }

      finals.push(eq);
      dds.push(maxDD);
      if (pathsSample.length < MAX_MC_PATHS_VISUAL) pathsSample.push(path);
    }

    var profitable = finals.filter(function (v) { return v > startingCapital; }).length;
    var dd10 = dds.filter(function (v) { return v > (startingCapital * 0.10); }).length;
    var dd20 = dds.filter(function (v) { return v > (startingCapital * 0.20); }).length;
    var dd30 = dds.filter(function (v) { return v > (startingCapital * 0.30); }).length;

    return {
      final_equity: {
        p5: percentile(finals, 5),
        p25: percentile(finals, 25),
        p50: percentile(finals, 50),
        p95: percentile(finals, 95)
      },
      max_drawdown: {
        p50: percentile(dds, 50),
        p95: percentile(dds, 95)
      },
      paths_sample: pathsSample,
      final_dist: histogram(finals, 24),
      dd_dist: histogram(dds, 24),
      prob_profitable: runs ? (profitable / runs) * 100 : null,
      prob_dd_10: runs ? (dd10 / runs) * 100 : null,
      prob_dd_20: runs ? (dd20 / runs) * 100 : null,
      prob_dd_30: runs ? (dd30 / runs) * 100 : null
    };
  }

  function ensureAnalyticsShape(a) {
    a = a || {};
    if (!a.rolling) a.rolling = {};
    if (!a.r_histogram) a.r_histogram = { edges: [], counts: [] };
    if (!a.duration_histogram) a.duration_histogram = { edges: [], counts: [] };
    if (!a.mae_mfe) a.mae_mfe = [];
    if (!a.return_scatter) a.return_scatter = [];
    if (!a.time_of_day) a.time_of_day = {};
    if (!a.day_of_week) a.day_of_week = {};
    if (!a.by_month) a.by_month = {};
    if (!a.regime) a.regime = { volatility: {}, choppiness: {} };
    if (!a.monte_carlo) {
      a.monte_carlo = {
        final_equity: {},
        max_drawdown: {},
        paths_sample: [],
        final_dist: { edges: [], counts: [] },
        dd_dist: { edges: [], counts: [] },
        prob_profitable: null,
        prob_dd_10: null,
        prob_dd_20: null,
        prob_dd_30: null
      };
    }
    if (!a.commission) a.commission = { total: 0, per_trade: 0, net_without_comm: 0, net_with_comm: 0, pct_of_gross: 0 };
    if (!Array.isArray(a.rolling.win_rate)) a.rolling.win_rate = [];
    if (!Array.isArray(a.rolling.expectancy)) a.rolling.expectancy = [];
    if (!Array.isArray(a.rolling.profit_factor)) a.rolling.profit_factor = [];
    if (!Array.isArray(a.rolling.sharpe)) a.rolling.sharpe = [];
    return a;
  }

  function buildFallbackAnalytics(data, config) {
    var trades = Array.isArray(data.trades) ? data.trades : [];
    var startingCapital =
      toNumber((data.stats || {}).starting_capital, NaN) ||
      toNumber((config || {}).starting_capital, NaN) ||
      10000;

    var mcRuns = toNumber((config || {}).monte_carlo_runs, 0) || 0;
    var rolling = computeRollingAnalytics(trades);
    var rSeries = trades.map(function (t) {
      return t.net_pnl_r != null && isFinite(Number(t.net_pnl_r))
        ? Number(t.net_pnl_r)
        : toNumber(t.net_pnl, 0);
    });
    var durationSeries = trades.map(function (t) { return toNumber(t.bars_held, 0); });
    var time = computeTimeBreakdown(trades);
    var scat = computeScatterData(trades);
    var comm = computeCommissionSummary(trades);
    var mc = generateMonteCarlo(trades, startingCapital, mcRuns);

    return ensureAnalyticsShape({
      rolling: rolling,
      r_histogram: histogram(rSeries, 20),
      duration_histogram: histogram(durationSeries, 20),
      mae_mfe: scat.mae_mfe,
      return_scatter: scat.return_scatter,
      time_of_day: time.time_of_day,
      day_of_week: time.day_of_week,
      by_month: time.by_month,
      regime: { volatility: {}, choppiness: {} },
      monte_carlo: mc,
      commission: comm
    });
  }

  // ════════════════════════════════════════════════════════════════════
  // TRADE LOG
  // ════════════════════════════════════════════════════════════════════
  function buildTradeLog(trades) {
    var el = document.getElementById('bt_tradeLog');
    if (!el) return;

    if (!trades || !trades.length) {
      el.innerHTML = '<div class="bt-trade-log-empty">No trades</div>';
      return;
    }

    var cols = ['#', 'POS', 'DIR', 'ENTRY', 'EXIT', 'REASON', 'SL LVL', 'TP LVL', 'R', 'SIZE', 'GROSS', 'COMM', 'NET', 'BARS'];
    var header = '<div class="bt-trade-row bt-trade-header" style="grid-template-columns: 30px 44px 42px 66px 66px 84px 58px 58px 50px 52px 60px 48px 60px 36px;">' +
      cols.map(function (c) { return '<div class="bt-trade-cell">' + c + '</div>'; }).join('') +
      '</div>';

    var rows = trades.map(function (t, i) {
      var rVal = t.net_pnl_r != null && isFinite(Number(t.net_pnl_r)) ? Number(t.net_pnl_r) : null;
      var r = rVal != null ? (rVal >= 0 ? '+' : '') + rVal.toFixed(2) + 'R' : '—';
      var reas = String(t.exit_reason || '').toUpperCase();
      var slLvl = t.stop_loss != null && isFinite(Number(t.stop_loss)) ? Number(t.stop_loss).toFixed(2) : '—';
      var tpLvl = t.take_profit != null && isFinite(Number(t.take_profit)) ? Number(t.take_profit).toFixed(2) : '—';
      var grossCls = toNumber(t.gross_pnl, 0) >= 0 ? 'bull' : 'bear';
      var netCls = toNumber(t.net_pnl, 0) >= 0 ? 'bull' : 'bear';
      var dirCls = t.direction === 'long' ? 'bull' : 'bear';
      var posId = t.position_id != null ? t.position_id : '—';
      var size = t.size != null && isFinite(Number(t.size)) ? Number(t.size).toFixed(2) : '—';

      return '<div class="bt-trade-row" style="grid-template-columns: 30px 44px 42px 66px 66px 84px 58px 58px 50px 52px 60px 48px 60px 36px;">' +
        '<div class="bt-trade-cell">' + (i + 1) + '</div>' +
        '<div class="bt-trade-cell">' + posId + '</div>' +
        '<div class="bt-trade-cell ' + dirCls + '">' + String(t.direction || '').toUpperCase() + '</div>' +
        '<div class="bt-trade-cell">' + (t.entry_price != null ? Number(t.entry_price).toFixed(2) : '—') + '</div>' +
        '<div class="bt-trade-cell">' + (t.exit_price != null ? Number(t.exit_price).toFixed(2) : '—') + '</div>' +
        '<div class="bt-trade-cell">' + reas + '</div>' +
        '<div class="bt-trade-cell">' + slLvl + '</div>' +
        '<div class="bt-trade-cell">' + tpLvl + '</div>' +
        '<div class="bt-trade-cell ' + netCls + '">' + r + '</div>' +
        '<div class="bt-trade-cell">' + size + '</div>' +
        '<div class="bt-trade-cell ' + grossCls + '">' + (t.gross_pnl != null ? ((toNumber(t.gross_pnl, 0) >= 0 ? '+' : '') + toNumber(t.gross_pnl, 0).toFixed(2)) : '—') + '</div>' +
        '<div class="bt-trade-cell">' + (t.commission != null ? toNumber(t.commission, 0).toFixed(2) : '—') + '</div>' +
        '<div class="bt-trade-cell ' + netCls + '">' + (t.net_pnl != null ? ((toNumber(t.net_pnl, 0) >= 0 ? '+' : '') + toNumber(t.net_pnl, 0).toFixed(2)) : '—') + '</div>' +
        '<div class="bt-trade-cell">' + (t.bars_held != null ? t.bars_held : '—') + '</div>' +
        '</div>';
    }).join('');

    el.innerHTML = header + rows;
  }

  // ════════════════════════════════════════════════════════════════════
  // NORMALIZATION
  // ════════════════════════════════════════════════════════════════════
  function normalizeResultPayload(raw, configOverride) {
    if (!raw) return null;

    var data = {
      trades: Array.isArray(raw.trades) ? raw.trades : [],
      stats: raw.stats || raw.metrics || {},
      analytics: raw.analytics || null,
      equity_curve: raw.equity_curve || (raw.curves ? (raw.curves.equity_full || raw.curves.equity_downsampled || []) : []) || [],
      drawdown_curve: raw.drawdown_curve || (raw.curves ? (raw.curves.drawdown_full || raw.curves.drawdown_downsampled || []) : []) || []
    };

    var config = configOverride || raw.config || {};

    if (raw.metrics && !raw.stats) data.stats = raw.metrics;
    if (data.stats.starting_capital == null && config.starting_capital != null) data.stats.starting_capital = config.starting_capital;
    if (data.stats.lot_size == null && config.lot_size != null) data.stats.lot_size = config.lot_size;
    if (data.stats.total_bars_used == null && config.bar_range != null) data.stats.total_bars_used = config.bar_range;
    if (data.stats.total_trades == null) data.stats.total_trades = data.trades.length;

    data.equity_curve = sanitizeNumericArray(data.equity_curve);
    data.drawdown_curve = sanitizeNumericArray(data.drawdown_curve);

    var analytics = ensureAnalyticsShape(data.analytics || {});
    var fallback = buildFallbackAnalytics(data, config);

    if (!analytics.rolling.win_rate.length) analytics.rolling.win_rate = fallback.rolling.win_rate;
    if (!analytics.rolling.expectancy.length) analytics.rolling.expectancy = fallback.rolling.expectancy;
    if (!analytics.rolling.profit_factor.length) analytics.rolling.profit_factor = fallback.rolling.profit_factor;
    if (!analytics.rolling.sharpe.length) analytics.rolling.sharpe = fallback.rolling.sharpe;

    if (!analytics.r_histogram.counts.length) analytics.r_histogram = fallback.r_histogram;
    if (!analytics.duration_histogram.counts.length) analytics.duration_histogram = fallback.duration_histogram;
    if (!analytics.mae_mfe.length) analytics.mae_mfe = fallback.mae_mfe;
    if (!analytics.return_scatter.length) analytics.return_scatter = fallback.return_scatter;

    if (!Object.keys(analytics.time_of_day || {}).length) analytics.time_of_day = fallback.time_of_day;
    if (!Object.keys(analytics.day_of_week || {}).length) analytics.day_of_week = fallback.day_of_week;
    if (!Object.keys(analytics.by_month || {}).length) analytics.by_month = fallback.by_month;

    if (!analytics.monte_carlo || !analytics.monte_carlo.paths_sample || !analytics.monte_carlo.paths_sample.length) {
      analytics.monte_carlo = fallback.monte_carlo;
    }

    if (!analytics.commission || (!analytics.commission.total && fallback.commission.total)) {
      analytics.commission = fallback.commission;
    }

    data.analytics = analytics;
    return { data: data, config: config };
  }

  // ════════════════════════════════════════════════════════════════════
  // POPULATE DASHBOARD
  // ════════════════════════════════════════════════════════════════════
  function populateDashboard(data, config) {
    resetAllVPs();

    var s = data.stats;
    var a = data.analytics || {};
    _lastRenderData = { data: data, analytics: a };
    _lastConfig = config;

    var maStr = (config && Array.isArray(config.mas) && config.mas.length)
      ? config.mas.map(function (m) { return m.type + m.period; }).join('+')
      : ((config && config.strategy_id) ? String(config.strategy_id).replace(/_/g, ' ').toUpperCase() : 'PLUGIN STRATEGY');

    var entryLabel = (config && config.entry)
      ? String(config.entry).replace(/_/g, ' ').toUpperCase()
      : 'PLUGIN MODE';

    document.getElementById('bd_strategy').textContent =
      maStr + ' · ' + ((config && config.range != null) ? config.range : '—') + 'pt · ' + entryLabel;

    var extraMeta = '';
    if (config && config.engine && config.engine.position_sizing && config.engine.position_sizing.mode) {
      extraMeta += ' · sizing ' + String(config.engine.position_sizing.mode).replace(/_/g, ' ');
    }
    if (config && config.engine && config.engine.scaling_in && config.engine.scaling_in.enabled) {
      extraMeta += ' · scaling-in enabled';
    }

    document.getElementById('bd_meta').textContent =
      (s.total_bars_used || 0) +
      ' bars · lot ' + (s.lot_size != null ? s.lot_size : 1) +
      ' · cap $' + (s.starting_capital != null ? s.starting_capital : 10000) +
      ' · ' + (s.date_range || '') +
      extraMeta;

    fillKpiGrid('bd_coreKpis', [
      kpiCard('STARTING CAPITAL', fmt(s.starting_capital, '$', 0)),
      kpiCard('FINAL BALANCE', fmt(s.final_balance, '$', 2), colorClass((toNumber(s.final_balance, 0) - toNumber(s.starting_capital, 0)))),
      kpiCard('LOT SIZE', (s.lot_size != null ? s.lot_size : 1) + ' (1pt=$' + (s.lot_size != null ? s.lot_size : 1) + ')'),
      kpiCard('NET P&L', fmt(s.net_pnl), colorClass(s.net_pnl)),
      kpiCard('NET PROFIT %', fmtPct(s.net_profit_pct), colorClass(s.net_profit_pct)),
      kpiCard('PROFIT FACTOR', s.profit_factor != null ? Number(s.profit_factor).toFixed(2) : '—', s.profit_factor > 1.5 ? 'bull' : s.profit_factor < 1 ? 'bear' : ''),
      kpiCard('WIN RATE', fmtPct(toNumber(s.win_rate, 0) * 100), toNumber(s.win_rate, 0) > 0.5 ? 'bull' : 'bear'),
      kpiCard('TOTAL TRADES', s.total_trades || 0),
      kpiCard('WINS / LOSSES', (s.winning_trades || 0) + ' / ' + (s.losing_trades || 0)),
      kpiCard('AVG WIN', fmt(s.avg_win), 'bull'),
      kpiCard('AVG LOSS', fmt(s.avg_loss), 'bear'),
      kpiCard('LARGEST WIN', fmt(s.largest_win), 'bull'),
      kpiCard('LARGEST LOSS', fmt(s.largest_loss), 'bear'),
      kpiCard('EXPECTANCY $', fmt(s.expectancy), colorClass(s.expectancy)),
      kpiCard('MAX CONSEC WINS', s.max_consec_wins || 0),
      kpiCard('MAX CONSEC LOSS', s.max_consec_losses || 0),
    ]);

    fillKpiGrid('bd_riskKpis', [
      kpiCard('MAX DRAWDOWN $', fmt(s.max_drawdown), 'bear'),
      kpiCard('MAX DRAWDOWN %', fmtPct(s.max_drawdown_pct), 'bear'),
      kpiCard('FINAL EQUITY', fmt(s.final_equity), colorClass((toNumber(s.final_equity, 0) - toNumber(s.starting_capital, 0)))),
      kpiCard('TOTAL BARS', s.total_bars_used || 0)
    ]);

    fillKpiGrid('bd_ratioKpis', [
      kpiCard('SHARPE', s.sharpe != null ? Number(s.sharpe).toFixed(3) : '—', colorClass(s.sharpe)),
      kpiCard('SQN', s.sqn != null ? Number(s.sqn).toFixed(3) : '—', colorClass(s.sqn)),
      kpiCard('OMEGA', s.omega != null ? Number(s.omega).toFixed(3) : '—', s.omega > 1 ? 'bull' : s.omega < 1 ? 'bear' : ''),
      kpiCard('PROFIT FACTOR', s.profit_factor != null ? Number(s.profit_factor).toFixed(3) : '—', colorClass(s.profit_factor))
    ]);

    var comm = a.commission || {};
    fillKpiGrid('bd_commKpis', [
      kpiCard('TOTAL COMMISSION', fmt(comm.total), 'bear'),
      kpiCard('COMMISSION/TRADE', fmt(comm.per_trade)),
      kpiCard('GROSS P&L', fmt(comm.net_without_comm)),
      kpiCard('NET P&L (after comm)', fmt(comm.net_with_comm), colorClass(comm.net_with_comm)),
      kpiCard('COMM % OF GROSS', fmtPct(comm.pct_of_gross), 'bear'),
    ]);

    var reg = a.regime || {};
    var regCards = [];
    Object.entries(reg.volatility || {}).forEach(function (entry) {
      var k = entry[0], v = entry[1];
      regCards.push(kpiCard('VOL: ' + String(k).toUpperCase().replace('_', ' '), fmt(v.net) + ' (' + v.trades + 't)', colorClass(v.net), 'WR ' + v.wr + '%'));
    });
    Object.entries(reg.choppiness || {}).forEach(function (entry) {
      var k = entry[0], v = entry[1];
      regCards.push(kpiCard('REGIME: ' + String(k).toUpperCase(), fmt(v.net) + ' (' + v.trades + 't)', colorClass(v.net), 'WR ' + v.wr + '%'));
    });
    if (!regCards.length) regCards.push(kpiCard('REGIME', 'No regime breakdown', '', 'No regime analytics provided'));
    fillKpiGrid('bd_regimeKpis', regCards);

    var mc = a.monte_carlo || {};
    var fe = mc.final_equity || {};
    var dd = mc.max_drawdown || {};
    fillKpiGrid('bd_mcKpis', [
      kpiCard('FINAL EQ P5', fmt(fe.p5), colorClass(fe.p5)),
      kpiCard('FINAL EQ P25', fmt(fe.p25), colorClass(fe.p25)),
      kpiCard('FINAL EQ P50', fmt(fe.p50), colorClass(fe.p50)),
      kpiCard('FINAL EQ P95', fmt(fe.p95), colorClass(fe.p95)),
      kpiCard('DD P50', fmt(dd.p50), 'bear'),
      kpiCard('DD P95 (WORST)', fmt(dd.p95), 'bear'),
    ]);

    var probEl = document.getElementById('bd_mcProb');
    if (probEl) {
      probEl.innerHTML = [
        ['Prob profitable', fmtPct(mc.prob_profitable)],
        ['Prob DD > 10% of capital', fmtPct(mc.prob_dd_10)],
        ['Prob DD > 20% of capital', fmtPct(mc.prob_dd_20)],
        ['Prob DD > 30% of capital', fmtPct(mc.prob_dd_30)],
      ].map(function (row) {
        return '<div class="bt-prob-row"><span>' + row[0] + '</span><span>' + row[1] + '</span></div>';
      }).join('');
    }

    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        renderAllChartsWithRetry(data, a, 0);
      });
    });
  }

  function renderAllChartsWithRetry(data, a, attempt) {
    var testCv = document.getElementById('cv_equity');
    if (testCv) {
      var pw = testCv.parentElement ? testCv.parentElement.clientWidth : 0;
      if (pw < 50 && attempt < 10) {
        requestAnimationFrame(function () { renderAllChartsWithRetry(data, a, attempt + 1); });
        return;
      }
    }
    renderAllCharts(data, a);
  }

  function renderAllCharts(data, a) {
    a = ensureAnalyticsShape(a || {});
    var eq = sanitizeNumericArray(data.equity_curve || []);
    var dd = sanitizeNumericArray(data.drawdown_curve || []);
    var roll = a.rolling || {};

    // ONLY these charts get single line datasets
    if (eq.length) drawLineChart('cv_equity', eq, {
      color: T.bull,
      zeroLine: false,
      refVal: (data.stats || {}).starting_capital,
      refLabel: 'Capital'
    });

    if (dd.length) drawLineChart('cv_drawdown', dd, {
      color: T.bear,
      zeroLine: true
    });

    if ((roll.win_rate || []).length) drawLineChart('cv_rollWr', roll.win_rate, { color: T.accent });
    if ((roll.expectancy || []).length) drawLineChart('cv_rollExp', roll.expectancy, { color: T.accent2, zeroLine: true });
    if ((roll.profit_factor || []).length) drawLineChart('cv_rollPf', roll.profit_factor, { color: '#ff9800' });
    if ((roll.sharpe || []).length) drawLineChart('cv_rollSharpe', roll.sharpe, { color: '#e040fb', zeroLine: true });

    if (a.r_histogram && (a.r_histogram.counts || []).length) {
      drawHistogram('cv_rHist', a.r_histogram, {
        colorFn: function (v) { return v >= 0 ? T.bull + '99' : T.bear + '99'; }
      });
    }

    if (a.duration_histogram && (a.duration_histogram.counts || []).length) {
      drawHistogram('cv_durHist', a.duration_histogram, {
        colorFn: function () { return T.accent + '88'; }
      });
    }

    if ((a.mae_mfe || []).length) {
      drawScatter('cv_maemfe', a.mae_mfe, 'mae', 'mfe', {
        colorFn: function (p) { return p.win ? T.bull : T.bear; }
      });
    }

    if ((a.return_scatter || []).length) {
      drawScatter('cv_retScatter', a.return_scatter, 'x', 'y', {
        colorFn: function (p) { return p.win ? T.bull : T.bear; }
      });
    }

    var hourKeys = [];
    for (var hi = 0; hi < 24; hi++) hourKeys.push(hi);
    var hourLabels = hourKeys.map(function (h) { return h + 'h'; });
    var hourVals = hourKeys.map(function (h) {
      var row = (a.time_of_day || {})[String(h)];
      return row ? toNumber(row.net, 0) : 0;
    });
    if (hourVals.some(function (v) { return v !== 0; })) {
      drawBarChart('cv_hour', hourLabels, hourVals, {
        colorFn: function (v) { return v >= 0 ? T.bull : T.bear; }
      });
    }

    var DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    var dowVals = DOW.map(function (d) {
      var row = (a.day_of_week || {})[d];
      return row ? toNumber(row.net, 0) : 0;
    });
    if (dowVals.some(function (v) { return v !== 0; })) {
      drawBarChart('cv_dow', DOW, dowVals, {
        colorFn: function (v) { return v >= 0 ? T.bull : T.bear; }
      });
    }

    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var monthVals = MONTHS.map(function (m) {
      var row = (a.by_month || {})[m];
      return row ? toNumber(row.net, 0) : 0;
    });
    if (monthVals.some(function (v) { return v !== 0; })) {
      drawBarChart('cv_month', MONTHS, monthVals, {
        colorFn: function (v) { return v >= 0 ? T.bull : T.bear; }
      });
    }

    // MONTE CARLO ISOLATED HERE ONLY
    if (a.monte_carlo && (a.monte_carlo.paths_sample || []).length) {
      drawMcPaths('cv_mcPaths', a.monte_carlo.paths_sample);
    }
    if (a.monte_carlo && a.monte_carlo.final_dist && (a.monte_carlo.final_dist.counts || []).length) {
      drawHistogram('cv_mcFinal', a.monte_carlo.final_dist, {
        colorFn: function (v) { return v >= 0 ? T.bull + '88' : T.bear + '88'; }
      });
    }
    if (a.monte_carlo && a.monte_carlo.dd_dist && (a.monte_carlo.dd_dist.counts || []).length) {
      drawHistogram('cv_mcDd', a.monte_carlo.dd_dist, {
        colorFn: function () { return T.bear + '88'; }
      });
    }
  }

  // ResizeObserver
  (function () {
    var rt = null;
    var p = document.getElementById('bt_resultsPanel');
    if (p) {
      new ResizeObserver(function () {
        if (rt) clearTimeout(rt);
        rt = setTimeout(function () {
          if (_lastRenderData) renderAllCharts(_lastRenderData.data, _lastRenderData.analytics);
        }, 200);
      }).observe(p);
    }
  })();

  // ════════════════════════════════════════════════════════════════════
  // RUN BUTTON (legacy/manual mode)
  // ════════════════════════════════════════════════════════════════════
  var runBtn = document.getElementById('bt_runBtn');
  if (runBtn) {
    runBtn.addEventListener('click', async function () {
      var config = collectConfig();
      if (!config.mas.length) {
        alert('Add at least one Moving Average.');
        return;
      }

      var btn = document.getElementById('bt_runBtn');
      btn.classList.add('running');
      btn.innerHTML = '<span class="bt-run-icon">⟳</span> RUNNING…';

      showProgress();
      setStep('step_load');

      if (typeof window.clearBacktestMarkers === 'function') window.clearBacktestMarkers();

      var streamed = false;

      try {
        setStep('step_run');

        var resp = await fetch(API_STREAM, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(config)
        });

        if (!resp.ok) throw new Error('Stream ' + resp.status);
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
            try { msg = JSON.parse(trimmed); } catch (e) { msg = null; }
            if (!msg) continue;

            if (msg.type === 'progress') updateLiveProgress(msg);
            else if (msg.type === 'result') {
              finalData = msg.data;
              setStep('step_stats');
            }
          }
        }

        if (buffer.trim()) {
          try {
            var msg2 = JSON.parse(buffer.trim());
            if (msg2.type === 'result') finalData = msg2.data;
          } catch (e) {}
        }

        if (!finalData) throw new Error('No result from stream');

        setStep('step_charts');
        await delay(80);
        setStep('step_done');
        await delay(300);

        window.btRenderAnyResult(finalData, config);

      } catch (streamErr) {
        if (!streamed || streamErr.message === 'No result from stream') {
          try {
            setStep('step_run');

            var resp2 = await fetch(API, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(config)
            });

            setStep('step_stats');
            await delay(120);

            if (!resp2.ok) {
              var eText = await resp2.text();
              throw new Error('Server ' + resp2.status + ': ' + eText);
            }

            var data = await resp2.json();

            setStep('step_charts');
            await delay(80);
            setStep('step_done');
            await delay(300);

            window.btRenderAnyResult(data, config);

          } catch (fallbackErr) {
            console.error(fallbackErr);
            window.btShowRunnerError(fallbackErr.message);
          }
        } else {
          console.error(streamErr);
          window.btShowRunnerError(streamErr.message);
        }
      } finally {
        btn.classList.remove('running');
        btn.innerHTML = '<span class="bt-run-icon">▶</span> START BACKTEST';
      }
    });
  }

  // ════════════════════════════════════════════════════════════════════
  // EXPOSED RENDER / PROGRESS API FOR STRATEGY MODE
  // ════════════════════════════════════════════════════════════════════
  window.btShowRunnerState = function (state) {
    try {
      showProgress();

      if (state && state.stepId) setStep(state.stepId);

      var pct = (state && state.pct != null) ? state.pct : 0;
      var bar = document.getElementById('bt_progressBar');
      var pctEl = document.getElementById('bt_progressPct');
      var label = document.getElementById('bt_progressLabel');

      if (bar) bar.style.width = pct + '%';
      if (pctEl) pctEl.textContent = Math.round(pct) + '%';
      if (label && state && state.label) label.textContent = state.label;

      if (state && state.live) {
        updateLiveProgress({
          pct: pct,
          bar: state.live.bar || 0,
          total: state.live.total || 0,
          equity: state.live.equity,
          drawdown: state.live.drawdown,
          open_trade: state.live.open_trade,
          last_closed_pnl: state.live.last_closed_pnl
        });
      }
    } catch (err) {
      console.error(err);
    }
  };

  window.btShowRunnerError = function (message) {
    hideProgress();
    var dash = document.getElementById('bt_dashboard');
    var empty = document.getElementById('bt_empty');
    if (dash) dash.style.display = 'none';
    if (empty) {
      empty.style.display = '';
      empty.innerHTML =
        '<div class="bt-empty-icon" style="color:var(--bear)">✕</div>' +
        '<div class="bt-empty-title" style="color:var(--bear)">Error</div>' +
        '<div class="bt-empty-sub">' + String(message || 'Unknown error') + '</div>';
    }
  };

  window.btRenderAnyResult = function (raw, configOverride) {
    try {
      var normalized = normalizeResultPayload(raw, configOverride);
      if (!normalized) throw new Error('No result payload');

      hideProgress();
      var empty = document.getElementById('bt_empty');
      var dash = document.getElementById('bt_dashboard');
      if (empty) empty.style.display = 'none';
      if (dash) dash.style.display = '';

      buildTradeLog(normalized.data.trades || []);
      populateDashboard(normalized.data, normalized.config || {});

      if (typeof window.plotBacktestMarkers === 'function') {
        window.plotBacktestMarkers(normalized.data.trades || []);
      }
    } catch (err) {
      window.btShowRunnerError(err.message || String(err));
    }
  };
})();
``