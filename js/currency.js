/* ═══════════════════════════════════════════════════════════════════
   JINNI ZERO — Frontend Currency Conversion (Display-Only)
   
   DOES NOT affect backend, trade logic, equity curves, or stored values.
   All conversion is purely visual — raw USD values are preserved
   as data-raw-usd attributes on DOM elements.
   
   Auto-hooks into btRenderAnyResult to tag dollar elements after
   every backtest render. When user changes conversion settings,
   ALL tagged elements auto-update.
   
   Load AFTER backtest.js.
═══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ════════════════════════════════════════════════════════════════
  //  STATE
  // ════════════════════════════════════════════════════════════════
  var state = {
    enabled: false,
    multiplier: 1,
    symbol: '$',
    decimals: 2,
  };

  var SYMBOL_DECIMALS = {
    '$': 2, '¥': 0, '€': 2, '£': 2, '₹': 2,
    '৳': 2, 'kr': 2, 'R$': 2, '₩': 0, 'CHF': 2,
  };

  var SYMBOL_LIST = Object.keys(SYMBOL_DECIMALS);

  // ════════════════════════════════════════════════════════════════
  //  FORMATTING
  // ════════════════════════════════════════════════════════════════

  function _addCommas(str) {
    var parts = str.split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return parts.join('.');
  }

  function format(rawUsd, opts) {
    if (rawUsd == null || rawUsd === '' || rawUsd === '—') return '—';
    opts = opts || {};

    var num = typeof rawUsd === 'string' ? parseFloat(rawUsd) : rawUsd;
    if (!Number.isFinite(num)) return '—';

    var converted = state.enabled ? num * state.multiplier : num;
    var sym = opts.noSymbol ? '' : state.symbol;
    var dec = opts.decimals != null ? opts.decimals : state.decimals;
    var isNeg = converted < 0;
    var abs = Math.abs(converted);

    // Compact mode for large numbers
    if (opts.compact && abs >= 100000) {
      if (abs >= 1000000) {
        var mStr = _addCommas((abs / 1000000).toFixed(Math.min(dec, 2)));
        return (isNeg ? '-' : (opts.forceSign && num > 0 ? '+' : '')) + sym + mStr + 'M';
      }
      var kStr = _addCommas((abs / 1000).toFixed(Math.min(dec, 1)));
      return (isNeg ? '-' : (opts.forceSign && num > 0 ? '+' : '')) + sym + kStr + 'K';
    }

    var formatted = _addCommas(abs.toFixed(dec));
    var sign = isNeg ? '-' : (opts.forceSign && num > 0 ? '+' : '');
    return sign + sym + formatted;
  }

  function formatPct(val, dec) {
    if (val == null || val === '') return '—';
    var num = typeof val === 'string' ? parseFloat(val) : val;
    if (!Number.isFinite(num)) return '—';
    return (num > 0 ? '+' : '') + num.toFixed(dec != null ? dec : 2) + '%';
  }

  function formatR(val, dec) {
    if (val == null || val === '') return '—';
    var num = typeof val === 'string' ? parseFloat(val) : val;
    if (!Number.isFinite(num)) return '—';
    return (num > 0 ? '+' : '') + num.toFixed(dec != null ? dec : 2) + 'R';
  }

  function formatNum(val, dec) {
    if (val == null || val === '') return '—';
    var num = typeof val === 'string' ? parseFloat(val) : val;
    if (!Number.isFinite(num)) return '—';
    return (num < 0 ? '-' : '') + _addCommas(Math.abs(num).toFixed(dec != null ? dec : 2));
  }

  // ════════════════════════════════════════════════════════════════
  //  DOM TAGGING (stores raw USD, auto-refreshes on change)
  // ════════════════════════════════════════════════════════════════
  var DATA_ATTR = 'data-raw-usd';
  var OPTS_ATTR = 'data-currency-opts';

  function tag(el, rawUsd, opts) {
    if (!el) return;
    if (rawUsd == null || rawUsd === '') {
      el.textContent = '—';
      el.removeAttribute(DATA_ATTR);
      el.removeAttribute(OPTS_ATTR);
      return;
    }
    el.setAttribute(DATA_ATTR, String(rawUsd));
    if (opts) {
      el.setAttribute(OPTS_ATTR, JSON.stringify(opts));
    } else {
      el.removeAttribute(OPTS_ATTR);
    }
    el.textContent = format(rawUsd, opts);
  }

  function refreshAll() {
    var els = document.querySelectorAll('[' + DATA_ATTR + ']');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var raw = parseFloat(el.getAttribute(DATA_ATTR));
      var optsStr = el.getAttribute(OPTS_ATTR);
      var opts = null;
      if (optsStr) { try { opts = JSON.parse(optsStr); } catch (e) {} }
      el.textContent = format(raw, opts);
    }
  }

  // ════════════════════════════════════════════════════════════════
  //  SETTINGS
  // ════════════════════════════════════════════════════════════════

  function setEnabled(on) {
    state.enabled = !!on;
    if (!state.enabled) {
      state.multiplier = 1;
      state.symbol = '$';
      state.decimals = 2;
    }
    _syncUi();
    refreshAll();
  }

  function setMultiplier(val) {
    var n = parseFloat(val);
    state.multiplier = Number.isFinite(n) && n > 0 ? n : 1;
    refreshAll();
  }

  function setSymbol(sym) {
    state.symbol = sym || '$';
    state.decimals = SYMBOL_DECIMALS[state.symbol] != null
      ? SYMBOL_DECIMALS[state.symbol] : 2;
    refreshAll();
  }

  function _syncUi() {
    var toggle = document.getElementById('cc_enabled');
    var panel  = document.getElementById('cc_panel');
    var mult   = document.getElementById('cc_multiplier');
    var sym    = document.getElementById('cc_symbol');
    if (toggle) toggle.checked = state.enabled;
    if (panel)  panel.style.display = state.enabled ? 'flex' : 'none';
    if (mult)   mult.value = state.multiplier;
    if (sym)    sym.value = state.symbol;
  }

  // ════════════════════════════════════════════════════════════════
  //  STAT ID → FORMAT OPTIONS MAP
  //
  //  Every dashboard element that shows a dollar value.
  //  true = default opts, object = custom opts.
  // ════════════════════════════════════════════════════════════════
  

  // ════════════════════════════════════════════════════════════════
  //  AUTO-TAG AFTER BACKTEST RENDER
  // ════════════════════════════════════════════════════════════════

  

  // ════════════════════════════════════════════════════════════════
  //  HOOK INTO btRenderAnyResult
  // ════════════════════════════════════════════════════════════════

  function installHook() {
    var check = setInterval(function () {
      if (typeof window.btRenderAnyResult !== 'function') return;
      clearInterval(check);

      var original = window.btRenderAnyResult;
      window.btRenderAnyResult = function (data, cfg) {
        original(data, cfg);
        // backtest.js already formats via fmt() → CurrencyDisplay.format()
        // and stamps data-raw-usd on elements. This just ensures a clean
        // re-tag pass in case anything was missed.
        requestAnimationFrame(function () { refreshAll(); });
      };
    }, 200);

    setTimeout(function () { clearInterval(check); }, 10000);
  }

  // ════════════════════════════════════════════════════════════════
  //  UI WIRING
  // ════════════════════════════════════════════════════════════════

  function wireControls() {
    var toggle = document.getElementById('cc_enabled');
    var mult   = document.getElementById('cc_multiplier');
    var sym    = document.getElementById('cc_symbol');

    if (toggle) {
      toggle.addEventListener('change', function () { setEnabled(this.checked); });
    }
    if (mult) {
      mult.addEventListener('input', function () { setMultiplier(this.value); });
      mult.addEventListener('change', function () { setMultiplier(this.value); });
    }
    if (sym) {
      sym.addEventListener('change', function () { setSymbol(this.value); });
    }
    _syncUi();
  }

  // ════════════════════════════════════════════════════════════════
  //  BOOT
  // ════════════════════════════════════════════════════════════════

  function boot() {
    wireControls();
    installHook();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // ════════════════════════════════════════════════════════════════
  //  PUBLIC API
  // ════════════════════════════════════════════════════════════════

  window.CurrencyDisplay = {
    format: format,
    formatPct: formatPct,
    formatR: formatR,
    formatNum: formatNum,
    tag: tag,
    refreshAll: refreshAll,
    setEnabled: setEnabled,
    setMultiplier: setMultiplier,
    setSymbol: setSymbol,
    getState: function () { return state; },
    SYMBOLS: SYMBOL_LIST,
  };

})();