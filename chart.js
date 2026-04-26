/* ═══════════════════════════════════════════════════════════════════
   chart.js — JINNI ZERO · NQ Range-Bar Chart + Indicators + Signals
   Lightweight Charts v4.2

   Upgrades:
   - Robust chunked virtualization / lazy-loading
   - Stable debounce/throttle around visible range changes
   - Compact "Indicators" dropdown UI + active chips
   - Auto-filled defaults per indicator type
   - Main-chart overlays: EMA / HMA / SMA / WMA / Bollinger Bands
   - Separate panes: RSI / Stoch RSI
   - Cached indicator calculations on loaded window only
   - Efficient O(n) SMA / EMA / WMA / HMA / BB / RSI / Stoch RSI
   - Existing signals + backtest markers preserved
   - Main chart hover sync → oscillator readouts
   - Full-height vertical sync guide
   - Editable oscillator levels/colors after add
═══════════════════════════════════════════════════════════════════ */

/* ──────────────────────────────────────────────────────────────────
   DATA SOURCES
────────────────────────────────────────────────────────────────── */
const RANGE_FILES = {
  2: 'data/2pt.json',
  4: 'data/4pt.json',
  6: 'data/6pt.json',
  8: 'data/8pt.json',
  10: 'data/10pt.json',
  15: 'data/15pt.json',
  20: 'data/20pt.json',
  25: 'data/25pt.json',
  30: 'data/30pt.json',
  35: 'data/35pt.json',
  40: 'data/40pt.json',
  45: 'data/45pt.json',
  50: 'data/50pt.json',
};

/* ──────────────────────────────────────────────────────────────────
   CONFIG
────────────────────────────────────────────────────────────────── */
const CHUNK_SIZE = 1000;
const INITIAL_WINDOW_BARS = 2000;
const BUFFER_PADDING_BARS = 1200;
const FAR_UNLOAD_PADDING_BARS = 2600;
const RANGE_CHANGE_DEBOUNCE_MS = 80;
const INDICATOR_RENDER_DEBOUNCE_MS = 35;
const OSC_PANE_HEIGHT = 142;
const PRICE_SCALE_MIN_WIDTH = 62;

const INDICATOR_CATALOG = {
  EMA: {
    defaults: { length: 55, color: '#00e5ff', source: 'close' },
  },
  HMA: {
    defaults: { length: 55, color: '#ff9800', source: 'close' },
  },
  SMA: {
    defaults: { length: 50, color: '#00e5ff', source: 'close' },
  },
  WMA: {
    defaults: { length: 50, color: '#8bc34a', source: 'close' },
  },
  BB: {
    defaults: { length: 20, stddev: 2, color: '#66bbff', source: 'close' },
  },
  RSI: {
    defaults: {
      length: 14,
      color: '#ffd166',
      source: 'close',
      obLevel: 70,
      osLevel: 30,
      midLevel: 50,
      showMid: true,
      obColor: '#ff3d5a',
      osColor: '#00e676',
      midColor: '#4a6070',
    },
  },
  'Stoch RSI': {
    defaults: {
      length: 14,
      smoothK: 3,
      smoothD: 3,
      color: '#8bc34a',
      source: 'close',
      obLevel: 80,
      osLevel: 20,
      obColor: '#ff3d5a',
      osColor: '#00e676',
    },
  },
};

const INDICATOR_TYPES = Object.keys(INDICATOR_CATALOG);
const PRICE_SOURCES = ['close', 'open', 'high', 'low'];

const DEFAULT_INDICATORS = [
  { type: 'HMA', length: 55, color: '#00e5ff', source: 'close', visible: true },
  { type: 'EMA', length: 55, color: '#ff9800', source: 'close', visible: true },
  { type: 'EMA', length: 200, color: '#e040fb', source: 'close', visible: true },
];

/* ──────────────────────────────────────────────────────────────────
   ROOT / LAYOUT
────────────────────────────────────────────────────────────────── */
const rootContainer = document.getElementById('chartContainer');
rootContainer.innerHTML = '';
rootContainer.style.position = 'relative';
rootContainer.style.overflow = 'hidden';
rootContainer.style.minWidth = '0';
rootContainer.style.minHeight = '0';

const chartsStack = document.createElement('div');
Object.assign(chartsStack.style, {
  position: 'absolute',
  inset: '0',
  display: 'flex',
  flexDirection: 'column',
  minWidth: '0',
  minHeight: '0',
  pointerEvents: 'none',
});
rootContainer.appendChild(chartsStack);

const mainChartHost = document.createElement('div');
Object.assign(mainChartHost.style, {
  position: 'relative',
  flex: '1 1 auto',
  minWidth: '0',
  minHeight: '0',
  pointerEvents: 'auto',
});
chartsStack.appendChild(mainChartHost);

const oscillatorWrap = document.createElement('div');
Object.assign(oscillatorWrap.style, {
  display: 'none',
  flexDirection: 'column',
  gap: '6px',
  paddingBottom: '6px',
  minHeight: '0',
  pointerEvents: 'auto',
});
chartsStack.appendChild(oscillatorWrap);

const overlayUi = document.createElement('div');
Object.assign(overlayUi.style, {
  position: 'absolute',
  top: '10px',
  left: '10px',
  zIndex: '30',
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
  pointerEvents: 'auto',
  maxWidth: '620px',
});
rootContainer.appendChild(overlayUi);

/* full-height vertical sync guide */
const syncGuide = document.createElement('div');
Object.assign(syncGuide.style, {
  position: 'absolute',
  top: '0',
  bottom: '0',
  width: '1px',
  background: 'linear-gradient(to bottom, rgba(0,149,168,0.0), rgba(0,229,255,0.65), rgba(0,149,168,0.0))',
  pointerEvents: 'none',
  zIndex: '12',
  display: 'none',
  boxShadow: '0 0 10px rgba(0,229,255,0.22)',
});
rootContainer.appendChild(syncGuide);

/* ──────────────────────────────────────────────────────────────────
   MAIN CHART
────────────────────────────────────────────────────────────────── */
function createBaseChartOptions() {
  return {
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: '#4a6070',
      fontFamily: "'Space Mono', monospace",
      fontSize: 10,
    },
    grid: {
      vertLines: { visible: false },
      horzLines: { color: '#1e2a38', style: 1 },
    },
    crosshair: {
      mode: 0,
      vertLine: {
        color: '#0095a855',
        labelBackgroundColor: '#0095a8',
      },
      horzLine: {
        color: '#0095a855',
        labelBackgroundColor: '#0095a8',
      },
    },
    rightPriceScale: {
      borderColor: '#1e2a38',
      visible: true,
      minimumWidth: PRICE_SCALE_MIN_WIDTH,
      scaleMargins: { top: 0.08, bottom: 0.18 },
    },
    timeScale: {
      borderColor: '#1e2a38',
      timeVisible: true,
      secondsVisible: false,
      barSpacing: 6,
      minBarSpacing: 2,
      fixLeftEdge: false,
      fixRightEdge: false,
      rightOffset: 0,
    },
    handleScroll: true,
    handleScale: true,
  };
}

const mainChart = LightweightCharts.createChart(mainChartHost, createBaseChartOptions());

const candleSeries = mainChart.addCandlestickSeries({
  upColor: '#00e676',
  downColor: '#ff3d5a',
  borderUpColor: '#00e676',
  borderDownColor: '#ff3d5a',
  wickUpColor: '#00e67688',
  wickDownColor: '#ff3d5a88',
});

const volumeSeries = mainChart.addHistogramSeries({
  priceFormat: { type: 'volume' },
  priceScaleId: 'vol',
  scaleMargins: { top: 0.88, bottom: 0 },
});

mainChart.priceScale('vol').applyOptions({
  borderColor: '#1e2a38',
  minimumWidth: PRICE_SCALE_MIN_WIDTH,
  scaleMargins: { top: 0.88, bottom: 0 },
  visible: true,
});

const buySignalSeries = mainChart.addLineSeries({
  lineVisible: false,
  pointMarkersVisible: false,
  lastValueVisible: false,
  priceLineVisible: false,
  priceScaleId: 'right',
  autoscaleInfoProvider: () => null,
});

const sellSignalSeries = mainChart.addLineSeries({
  lineVisible: false,
  pointMarkersVisible: false,
  lastValueVisible: false,
  priceLineVisible: false,
  priceScaleId: 'right',
  autoscaleInfoProvider: () => null,
});

const _btEntrySeries = mainChart.addLineSeries({
  lineVisible: false,
  pointMarkersVisible: false,
  lastValueVisible: false,
  priceLineVisible: false,
  priceScaleId: 'right',
  autoscaleInfoProvider: () => null,
});

const _btExitSeries = mainChart.addLineSeries({
  lineVisible: false,
  pointMarkersVisible: false,
  lastValueVisible: false,
  priceLineVisible: false,
  priceScaleId: 'right',
  autoscaleInfoProvider: () => null,
});

/* ──────────────────────────────────────────────────────────────────
   STATE
────────────────────────────────────────────────────────────────── */
let currentRange = 2;
let fullData = [];
let datasetVersion = 0;

let loadedWindow = { start: 0, end: 0 };
let loadedData = [];
let lastVisibleRange = null;

let sourceCache = {
  close: [],
  open: [],
  high: [],
  low: [],
};

let suppressMainRangeHandler = false;
let ignoreTimeSync = false;
let rangeChangeTimer = null;
let indicatorRenderTimer = null;

let nextIndicatorId = 1;
let indicators = [];
let signalEnabled = true;
let signalIndicatorId = null;

const indicatorSeriesRegistry = new Map();   // id -> { kind, series... }
const indicatorWindowCache = new Map();      // key -> computed result
let lastRenderedIndicatorRawValues = new Map();
let lastRenderedComputed = new Map();

let fullBacktestTrades = [];
let fullBacktestEntryMarkers = [];
let fullBacktestExitMarkers = [];

const paneState = {
  rsi: null,
  stoch: null,
};

let selectedIndicatorId = null;

/* ──────────────────────────────────────────────────────────────────
   HELPERS
────────────────────────────────────────────────────────────────── */
function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function alignDown(value, chunk) {
  return Math.floor(value / chunk) * chunk;
}

function alignUp(value, chunk) {
  return Math.ceil(value / chunk) * chunk;
}

function withAlpha(hex, alpha) {
  if (typeof hex !== 'string' || !hex.startsWith('#') || hex.length !== 7) return hex;
  const a = clamp(Math.round(alpha * 255), 0, 255).toString(16).padStart(2, '0');
  return `${hex}${a}`;
}

function defaultForType(type) {
  return JSON.parse(JSON.stringify((INDICATOR_CATALOG[type] || INDICATOR_CATALOG.EMA).defaults));
}

function formatIndicatorLabel(ind) {
  if (ind.type === 'BB') return `BB ${ind.length},${ind.stddev}`;
  if (ind.type === 'Stoch RSI') return `Stoch RSI ${ind.length},${ind.smoothK},${ind.smoothD}`;
  return `${ind.type} ${ind.length}`;
}

function sourceArrayFor(source) {
  return sourceCache[source] || sourceCache.close || [];
}

function indicatorWarmup(ind) {
  const len = Math.max(1, Number(ind.length) || 1);
  if (ind.type === 'Stoch RSI') return len * 8 + 20;
  if (ind.type === 'RSI') return len * 5 + 10;
  if (ind.type === 'BB') return len * 4 + 10;
  return len * 4 + 10;
}

function invalidateIndicatorCache() {
  indicatorWindowCache.clear();
}

function normalizeBars(rawBars) {
  const out = [];
  let lastTime = null;

  for (let i = 0; i < rawBars.length; i++) {
    const b = rawBars[i];
    let time = Number(b.time);
    if (!Number.isFinite(time)) continue;

    if (lastTime != null && time <= lastTime) time = lastTime + 1;
    lastTime = time;

    out.push({
      time,
      open: Number(b.open),
      high: Number(b.high),
      low: Number(b.low),
      close: Number(b.close),
      volume: Number(b.volume || 0),
    });
  }

  return out;
}

function rebuildSourceCache() {
  sourceCache = {
    close: fullData.map(b => b.close),
    open: fullData.map(b => b.open),
    high: fullData.map(b => b.high),
    low: fullData.map(b => b.low),
  };
}

function loadedBounds() {
  if (!loadedData.length) return null;
  return {
    from: loadedData[0].time,
    to: loadedData[loadedData.length - 1].time,
  };
}

function binarySearchAtOrAfter(time) {
  if (!fullData.length) return 0;
  let lo = 0;
  let hi = fullData.length - 1;
  let ans = fullData.length - 1;

  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (fullData[mid].time >= time) {
      ans = mid;
      hi = mid - 1;
    } else {
      lo = mid + 1;
    }
  }
  return ans;
}

function binarySearchAtOrBefore(time) {
  if (!fullData.length) return 0;
  let lo = 0;
  let hi = fullData.length - 1;
  let ans = 0;

  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (fullData[mid].time <= time) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

function visibleIndexRangeFromTimeRange(range) {
  if (!range || range.from == null || range.to == null || !fullData.length) return null;
  const fromIdx = binarySearchAtOrAfter(range.from);
  const toIdx = Math.max(fromIdx, binarySearchAtOrBefore(range.to));
  return { fromIdx, toIdx };
}

function volumeDataForLoadedWindow() {
  return loadedData.map(b => ({
    time: b.time,
    value: b.volume || 0,
    color: b.close >= b.open ? '#00e67633' : '#ff3d5a33',
  }));
}

function updateSidebar(bar) {
  if (!bar) return;
  document.getElementById('statOpen').textContent = bar.open.toFixed(2);
  document.getElementById('statHigh').textContent = bar.high.toFixed(2);
  document.getElementById('statLow').textContent = bar.low.toFixed(2);
  document.getElementById('statClose').textContent = bar.close.toFixed(2);
  document.getElementById('statVolume').textContent = bar.volume ? bar.volume.toFixed(0) : '—';

  const chg = bar.close - bar.open;
  const el = document.getElementById('statChange');
  el.textContent = (chg >= 0 ? '+' : '') + chg.toFixed(2);
  el.className = 'sidebar-value ' + (chg >= 0 ? 'bull' : 'bear');
}

function updateHeader(bar, prev) {
  if (!bar) return;
  document.getElementById('tickerPrice').textContent = bar.close.toFixed(2);

  const el = document.getElementById('tickerChange');
  if (prev) {
    const d = bar.close - prev.close;
    const pct = prev.close ? ((d / prev.close) * 100) : 0;
    el.textContent = (d >= 0 ? '+' : '') + d.toFixed(2) + ' (' + pct.toFixed(2) + '%)';
    el.className = 'ticker-change ' + (d >= 0 ? 'bull' : 'bear');
  } else {
    el.textContent = '—';
    el.className = 'ticker-change';
  }
}

function safeVisibleRangeSet(chart, range) {
  if (!range || range.from == null || range.to == null) return;
  try {
    chart.timeScale().setVisibleRange({ from: range.from, to: range.to });
  } catch (err) {
    // noop
  }
}

function getHostSize(el, fallbackHeight) {
  const rect = el.getBoundingClientRect();
  const width = Math.max(50, Math.floor(el.clientWidth || rect.width || rootContainer.clientWidth || 600));
  const height = Math.max(50, Math.floor(el.clientHeight || rect.height || fallbackHeight || 200));
  return { width, height };
}

function resizeMainChart() {
  const size = getHostSize(mainChartHost, Math.max(220, rootContainer.clientHeight - 20));
  mainChart.applyOptions({ width: size.width, height: size.height });
}

function resizePaneCharts() {
  if (paneState.rsi && paneState.rsi.chart) {
    const size = getHostSize(paneState.rsi.host, OSC_PANE_HEIGHT);
    paneState.rsi.chart.applyOptions({ width: size.width, height: size.height });
  }
  if (paneState.stoch && paneState.stoch.chart) {
    const size = getHostSize(paneState.stoch.host, OSC_PANE_HEIGHT);
    paneState.stoch.chart.applyOptions({ width: size.width, height: size.height });
  }
}

function resizeAllCharts() {
  resizeMainChart();
  resizePaneCharts();
}

function syncPaneRangesFromMain(range) {
  if (!range || range.from == null || range.to == null) return;
  if (ignoreTimeSync) return;

  ignoreTimeSync = true;
  try {
    if (paneState.rsi && paneState.rsi.chart) safeVisibleRangeSet(paneState.rsi.chart, range);
    if (paneState.stoch && paneState.stoch.chart) safeVisibleRangeSet(paneState.stoch.chart, range);
  } finally {
    requestAnimationFrame(() => { ignoreTimeSync = false; });
  }
}

function applyMainRange(range) {
  if (!range || range.from == null || range.to == null) return;

  suppressMainRangeHandler = true;
  safeVisibleRangeSet(mainChart, range);
  requestAnimationFrame(() => { suppressMainRangeHandler = false; });

  syncPaneRangesFromMain(range);
}

function loadedIndexFromTime(time) {
  if (time == null || !loadedData.length) return -1;
  const t = Number(time);
  if (!Number.isFinite(t)) return -1;

  const globalIdx = binarySearchAtOrBefore(t);
  if (globalIdx < loadedWindow.start || globalIdx >= loadedWindow.end) return -1;
  return globalIdx - loadedWindow.start;
}

function updateSyncGuide(param) {
  if (!param || !param.point || param.point.x == null || param.point.x < 0 || param.point.x > rootContainer.clientWidth) {
    syncGuide.style.display = 'none';
    return;
  }
  syncGuide.style.display = 'block';
  syncGuide.style.left = `${Math.round(param.point.x)}px`;
}

function updateOscillatorReadoutsAtTime(time) {
  const idx = loadedIndexFromTime(time);
  if (idx < 0) {
    if (paneState.rsi) paneState.rsi.valuesLabel.textContent = '—';
    if (paneState.stoch) paneState.stoch.valuesLabel.textContent = '—';
    return;
  }

  if (paneState.rsi) {
    const texts = [];
    paneState.rsi.dynamicSeries.forEach((entry, id) => {
      const ind = indicators.find(x => x.id === id);
      const computed = lastRenderedComputed.get(id);
      if (ind && computed && computed.raw && computed.raw[idx] != null) {
        texts.push(`${formatIndicatorLabel(ind)} ${computed.raw[idx].toFixed(2)}`);
      }
    });
    paneState.rsi.valuesLabel.textContent = texts.length ? texts.join(' · ') : '—';
  }

  if (paneState.stoch) {
    const texts = [];
    paneState.stoch.dynamicSeries.forEach((entry, id) => {
      const ind = indicators.find(x => x.id === id);
      const computed = lastRenderedComputed.get(id);
      if (ind && computed) {
        if (computed.kRaw && computed.kRaw[idx] != null) {
          texts.push(`${formatIndicatorLabel(ind)} %K ${computed.kRaw[idx].toFixed(2)}`);
        }
        if (computed.dRaw && computed.dRaw[idx] != null) {
          texts.push(`%D ${computed.dRaw[idx].toFixed(2)}`);
        }
      }
    });
    paneState.stoch.valuesLabel.textContent = texts.length ? texts.join(' · ') : '—';
  }
}

/* ──────────────────────────────────────────────────────────────────
   INDICATOR MATH
────────────────────────────────────────────────────────────────── */
function precomputeSma(values, period) {
  const n = values.length;
  const out = new Array(n).fill(null);
  if (period < 1 || n < period) return out;

  let sum = 0;
  for (let i = 0; i < n; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

function precomputeEma(values, period) {
  const n = values.length;
  const out = new Array(n).fill(null);
  if (period < 1 || n < period) return out;

  const k = 2 / (period + 1);
  let seed = 0;
  for (let i = 0; i < period; i++) seed += values[i];
  let ema = seed / period;
  out[period - 1] = ema;

  for (let i = period; i < n; i++) {
    ema = values[i] * k + ema * (1 - k);
    out[i] = ema;
  }
  return out;
}

function precomputeWma(values, period) {
  const n = values.length;
  const out = new Array(n).fill(null);
  if (period < 1 || n < period) return out;

  const p = period;
  const denom = p * (p + 1) / 2;
  let sum = 0;
  let weighted = 0;

  for (let i = 0; i < p; i++) {
    sum += values[i];
    weighted += values[i] * (i + 1);
  }
  out[p - 1] = weighted / denom;

  for (let i = p; i < n; i++) {
    weighted = weighted + p * values[i] - sum;
    sum = sum + values[i] - values[i - p];
    out[i] = weighted / denom;
  }
  return out;
}

function precomputeHma(values, period) {
  const n = values.length;
  const out = new Array(n).fill(null);

  const p = Math.max(2, period | 0);
  const half = Math.max(1, Math.floor(p / 2));
  const sq = Math.max(1, Math.floor(Math.sqrt(p)));

  const full = precomputeWma(values, p);
  const halfWma = precomputeWma(values, half);

  const diff = new Array(n).fill(null);
  let firstValid = -1;

  for (let i = 0; i < n; i++) {
    if (full[i] != null && halfWma[i] != null) {
      diff[i] = 2 * halfWma[i] - full[i];
      if (firstValid === -1) firstValid = i;
    }
  }
  if (firstValid === -1) return out;

  const compact = [];
  const map = [];
  for (let i = firstValid; i < n; i++) {
    if (diff[i] != null) {
      compact.push(diff[i]);
      map.push(i);
    }
  }
  if (compact.length < sq) return out;

  const final = precomputeWma(compact, sq);
  for (let i = 0; i < final.length; i++) {
    if (final[i] != null) out[map[i]] = final[i];
  }
  return out;
}

function precomputeMa(values, type, period) {
  const t = String(type).toUpperCase();
  if (t === 'SMA') return precomputeSma(values, period);
  if (t === 'EMA') return precomputeEma(values, period);
  if (t === 'WMA') return precomputeWma(values, period);
  if (t === 'HMA') return precomputeHma(values, period);
  return new Array(values.length).fill(null);
}

function precomputeBollinger(values, period, stddevMult) {
  const n = values.length;
  const basis = new Array(n).fill(null);
  const upper = new Array(n).fill(null);
  const lower = new Array(n).fill(null);
  if (period < 1 || n < period) return { basis, upper, lower };

  let sum = 0;
  let sumSq = 0;
  for (let i = 0; i < n; i++) {
    const v = values[i];
    sum += v;
    sumSq += v * v;

    if (i >= period) {
      const old = values[i - period];
      sum -= old;
      sumSq -= old * old;
    }

    if (i >= period - 1) {
      const mean = sum / period;
      const variance = Math.max(0, (sumSq / period) - (mean * mean));
      const sd = Math.sqrt(variance);
      basis[i] = mean;
      upper[i] = mean + sd * stddevMult;
      lower[i] = mean - sd * stddevMult;
    }
  }
  return { basis, upper, lower };
}

function precomputeRsi(values, period) {
  const n = values.length;
  const out = new Array(n).fill(null);
  if (period < 1 || n < period + 1) return out;

  let gains = 0;
  let losses = 0;
  for (let i = 1; i <= period; i++) {
    const delta = values[i] - values[i - 1];
    if (delta >= 0) gains += delta;
    else losses -= delta;
  }

  let avgGain = gains / period;
  let avgLoss = losses / period;
  out[period] = avgLoss === 0 ? 100 : (100 - (100 / (1 + avgGain / avgLoss)));

  for (let i = period + 1; i < n; i++) {
    const delta = values[i] - values[i - 1];
    const gain = delta > 0 ? delta : 0;
    const loss = delta < 0 ? -delta : 0;

    avgGain = ((avgGain * (period - 1)) + gain) / period;
    avgLoss = ((avgLoss * (period - 1)) + loss) / period;

    if (avgLoss === 0) out[i] = 100;
    else {
      const rs = avgGain / avgLoss;
      out[i] = 100 - (100 / (1 + rs));
    }
  }
  return out;
}

function rollingMin(values, period) {
  const out = new Array(values.length).fill(null);
  const dq = [];
  for (let i = 0; i < values.length; i++) {
    while (dq.length && dq[0] <= i - period) dq.shift();
    while (dq.length) {
      const idx = dq[dq.length - 1];
      const prev = values[idx];
      if (prev == null || (values[i] != null && prev >= values[i])) dq.pop();
      else break;
    }
    if (values[i] != null) dq.push(i);
    if (i >= period - 1 && dq.length) out[i] = values[dq[0]];
  }
  return out;
}

function rollingMax(values, period) {
  const out = new Array(values.length).fill(null);
  const dq = [];
  for (let i = 0; i < values.length; i++) {
    while (dq.length && dq[0] <= i - period) dq.shift();
    while (dq.length) {
      const idx = dq[dq.length - 1];
      const prev = values[idx];
      if (prev == null || (values[i] != null && prev <= values[i])) dq.pop();
      else break;
    }
    if (values[i] != null) dq.push(i);
    if (i >= period - 1 && dq.length) out[i] = values[dq[0]];
  }
  return out;
}

function precomputeStochRsi(values, rsiLength, smoothK, smoothD) {
  const rsi = precomputeRsi(values, rsiLength);
  const low = rollingMin(rsi, rsiLength);
  const high = rollingMax(rsi, rsiLength);

  const rawK = new Array(values.length).fill(null);
  for (let i = 0; i < values.length; i++) {
    if (rsi[i] == null || low[i] == null || high[i] == null) continue;
    const denom = high[i] - low[i];
    rawK[i] = denom === 0 ? 0 : ((rsi[i] - low[i]) / denom) * 100;
  }

  const safeRawK = rawK.map(v => (v == null ? 0 : v));
  const k = precomputeSma(safeRawK, Math.max(1, smoothK)).map((v, i) => rawK[i] == null ? null : v);
  const safeK = k.map(v => (v == null ? 0 : v));
  const d = precomputeSma(safeK, Math.max(1, smoothD)).map((v, i) => k[i] == null ? null : v);

  return { rsi, k, d };
}

/* ──────────────────────────────────────────────────────────────────
   PANE MANAGEMENT
────────────────────────────────────────────────────────────────── */
function createPaneHost(titleText) {
  const host = document.createElement('div');
  Object.assign(host.style, {
    position: 'relative',
    height: `${OSC_PANE_HEIGHT}px`,
    minHeight: `${OSC_PANE_HEIGHT}px`,
    borderTop: '1px solid #1e2a38',
  });

  const badge = document.createElement('div');
  Object.assign(badge.style, {
    position: 'absolute',
    top: '6px',
    left: '10px',
    zIndex: '5',
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    background: '#0d1117dd',
    border: '1px solid #1e2a38',
    borderRadius: '4px',
    padding: '4px 8px',
    fontFamily: "'Space Mono', monospace",
    fontSize: '0.56rem',
    fontWeight: '700',
    letterSpacing: '0.06em',
    color: '#8aa4b6',
    pointerEvents: 'none',
    backdropFilter: 'blur(6px)',
  });

  const title = document.createElement('span');
  title.textContent = titleText;
  const values = document.createElement('span');
  values.textContent = '—';

  badge.appendChild(title);
  badge.appendChild(values);
  host.appendChild(badge);
  oscillatorWrap.appendChild(host);

  return { host, valuesLabel: values };
}

function createPaneChart(host) {
  return LightweightCharts.createChart(host, {
    ...createBaseChartOptions(),
    rightPriceScale: {
      borderColor: '#1e2a38',
      visible: true,
      minimumWidth: PRICE_SCALE_MIN_WIDTH,
      scaleMargins: { top: 0.14, bottom: 0.12 },
    },
    timeScale: {
      borderColor: '#1e2a38',
      timeVisible: true,
      secondsVisible: false,
      barSpacing: 6,
      minBarSpacing: 2,
      visible: false,
    },
    handleScroll: true,
    handleScale: true,
  });
}

function updateOscillatorWrapVisibility() {
  oscillatorWrap.style.display = (paneState.rsi || paneState.stoch) ? 'flex' : 'none';

  const hasOsc = oscillatorWrap.style.display !== 'none';
  mainChart.applyOptions({
    rightPriceScale: {
      borderColor: '#1e2a38',
      visible: true,
      minimumWidth: PRICE_SCALE_MIN_WIDTH,
      scaleMargins: hasOsc ? { top: 0.08, bottom: 0.08 } : { top: 0.08, bottom: 0.18 },
    },
  });

  mainChart.priceScale('vol').applyOptions({
    borderColor: '#1e2a38',
    visible: true,
    minimumWidth: PRICE_SCALE_MIN_WIDTH,
    scaleMargins: hasOsc ? { top: 0.90, bottom: 0 } : { top: 0.88, bottom: 0 },
  });

  requestAnimationFrame(() => {
    resizeAllCharts();
    const range = lastVisibleRange || loadedBounds();
    if (range) {
      syncPaneRangesFromMain(range);
      updateOscillatorReadoutsAtTime(range.to);
    }
  });
}

function ensureRsiPane() {
  if (paneState.rsi) return paneState.rsi;

  const pane = createPaneHost('RSI');
  const chart = createPaneChart(pane.host);

  paneState.rsi = {
    host: pane.host,
    valuesLabel: pane.valuesLabel,
    chart,
    dynamicSeries: new Map(),
  };

  chart.subscribeVisibleTimeRangeChange(range => {
    if (ignoreTimeSync || !range || range.from == null || range.to == null) return;
    ignoreTimeSync = true;
    try {
      safeVisibleRangeSet(mainChart, range);
      if (paneState.stoch && paneState.stoch.chart) safeVisibleRangeSet(paneState.stoch.chart, range);
    } finally {
      requestAnimationFrame(() => { ignoreTimeSync = false; });
    }
  });

  chart.subscribeCrosshairMove(param => {
    if (!paneState.rsi) return;
    const texts = [];
    paneState.rsi.dynamicSeries.forEach((entry, id) => {
      const dp = param.seriesData ? param.seriesData.get(entry.line) : null;
      if (dp && dp.value != null) {
        const ind = indicators.find(x => x.id === id);
        if (ind) texts.push(`${formatIndicatorLabel(ind)} ${dp.value.toFixed(2)}`);
      }
    });
    paneState.rsi.valuesLabel.textContent = texts.length ? texts.join(' · ') : '—';
  });

  updateOscillatorWrapVisibility();
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      resizePaneCharts();
      const range = lastVisibleRange || loadedBounds();
      if (range) safeVisibleRangeSet(chart, range);
    });
  });

  return paneState.rsi;
}

function ensureStochPane() {
  if (paneState.stoch) return paneState.stoch;

  const pane = createPaneHost('STOCH RSI');
  const chart = createPaneChart(pane.host);

  paneState.stoch = {
    host: pane.host,
    valuesLabel: pane.valuesLabel,
    chart,
    dynamicSeries: new Map(),
  };

  chart.subscribeVisibleTimeRangeChange(range => {
    if (ignoreTimeSync || !range || range.from == null || range.to == null) return;
    ignoreTimeSync = true;
    try {
      safeVisibleRangeSet(mainChart, range);
      if (paneState.rsi && paneState.rsi.chart) safeVisibleRangeSet(paneState.rsi.chart, range);
    } finally {
      requestAnimationFrame(() => { ignoreTimeSync = false; });
    }
  });

  chart.subscribeCrosshairMove(param => {
    if (!paneState.stoch) return;
    const texts = [];
    paneState.stoch.dynamicSeries.forEach((entry, id) => {
      const dpK = param.seriesData ? param.seriesData.get(entry.k) : null;
      const dpD = param.seriesData ? param.seriesData.get(entry.d) : null;
      const ind = indicators.find(x => x.id === id);
      if (ind) {
        if (dpK && dpK.value != null) texts.push(`${formatIndicatorLabel(ind)} %K ${dpK.value.toFixed(2)}`);
        if (dpD && dpD.value != null) texts.push(`%D ${dpD.value.toFixed(2)}`);
      }
    });
    paneState.stoch.valuesLabel.textContent = texts.length ? texts.join(' · ') : '—';
  });

  updateOscillatorWrapVisibility();
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      resizePaneCharts();
      const range = lastVisibleRange || loadedBounds();
      if (range) safeVisibleRangeSet(chart, range);
    });
  });

  return paneState.stoch;
}

function destroyUnusedPanes() {
  const needRsi = indicators.some(ind => ind.type === 'RSI');
  const needStoch = indicators.some(ind => ind.type === 'Stoch RSI');

  if (!needRsi && paneState.rsi) {
    paneState.rsi.dynamicSeries.forEach(entry => {
      Object.values(entry).forEach(s => {
        try { paneState.rsi.chart.removeSeries(s); } catch (err) {}
      });
    });
    try { paneState.rsi.chart.remove(); } catch (err) {}
    try { oscillatorWrap.removeChild(paneState.rsi.host); } catch (err) {}
    paneState.rsi = null;
  }

  if (!needStoch && paneState.stoch) {
    paneState.stoch.dynamicSeries.forEach(entry => {
      Object.values(entry).forEach(s => {
        try { paneState.stoch.chart.removeSeries(s); } catch (err) {}
      });
    });
    try { paneState.stoch.chart.remove(); } catch (err) {}
    try { oscillatorWrap.removeChild(paneState.stoch.host); } catch (err) {}
    paneState.stoch = null;
  }

  updateOscillatorWrapVisibility();
}

/* ──────────────────────────────────────────────────────────────────
   SERIES REGISTRY
────────────────────────────────────────────────────────────────── */
function makeMainOverlaySeries(color, width = 1.25, lineStyle = LightweightCharts.LineStyle.Solid) {
  return mainChart.addLineSeries({
    color,
    lineWidth: width,
    lineStyle,
    priceLineVisible: false,
    lastValueVisible: true,
    crosshairMarkerVisible: false,
    priceScaleId: 'right',
  });
}

function makePaneLevelSeries(chart, color) {
  return chart.addLineSeries({
    color,
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
    priceScaleId: 'right',
  });
}

function removeIndicatorSeries(indicatorId) {
  const reg = indicatorSeriesRegistry.get(indicatorId);
  if (!reg) return;

  if (reg.kind === 'main') {
    reg.series.forEach(series => {
      try { mainChart.removeSeries(series); } catch (err) {}
    });
  } else if (reg.kind === 'rsi' && paneState.rsi) {
    const entry = paneState.rsi.dynamicSeries.get(indicatorId);
    if (entry) {
      Object.values(entry).forEach(s => {
        try { paneState.rsi.chart.removeSeries(s); } catch (err) {}
      });
      paneState.rsi.dynamicSeries.delete(indicatorId);
    }
  } else if (reg.kind === 'stoch' && paneState.stoch) {
    const entry = paneState.stoch.dynamicSeries.get(indicatorId);
    if (entry) {
      Object.values(entry).forEach(s => {
        try { paneState.stoch.chart.removeSeries(s); } catch (err) {}
      });
      paneState.stoch.dynamicSeries.delete(indicatorId);
    }
  }

  indicatorSeriesRegistry.delete(indicatorId);
}

function ensureIndicatorSeries(ind) {
  const existing = indicatorSeriesRegistry.get(ind.id);
  if (existing) return existing;

  if (ind.type === 'RSI') {
    const pane = ensureRsiPane();
    const line = pane.chart.addLineSeries({
      color: ind.color,
      lineWidth: 1.4,
      priceLineVisible: true,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
      priceScaleId: 'right',
    });
    const ob = makePaneLevelSeries(pane.chart, withAlpha(ind.obColor || '#ff3d5a', 0.55));
    const os = makePaneLevelSeries(pane.chart, withAlpha(ind.osColor || '#00e676', 0.55));
    const mid = makePaneLevelSeries(pane.chart, withAlpha(ind.midColor || '#4a6070', 0.45));

    pane.dynamicSeries.set(ind.id, { line, ob, os, mid });

    const reg = { kind: 'rsi', series: [line, ob, os, mid] };
    indicatorSeriesRegistry.set(ind.id, reg);
    return reg;
  }

  if (ind.type === 'Stoch RSI') {
    const pane = ensureStochPane();
    const k = pane.chart.addLineSeries({
      color: ind.color,
      lineWidth: 1.3,
      priceLineVisible: true,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
      priceScaleId: 'right',
    });
    const d = pane.chart.addLineSeries({
      color: withAlpha(ind.color, 0.55),
      lineWidth: 1.1,
      priceLineVisible: true,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
      priceScaleId: 'right',
    });
    const ob = makePaneLevelSeries(pane.chart, withAlpha(ind.obColor || '#ff3d5a', 0.55));
    const os = makePaneLevelSeries(pane.chart, withAlpha(ind.osColor || '#00e676', 0.55));

    pane.dynamicSeries.set(ind.id, { k, d, ob, os });

    const reg = { kind: 'stoch', series: [k, d, ob, os] };
    indicatorSeriesRegistry.set(ind.id, reg);
    return reg;
  }

  if (ind.type === 'BB') {
    const upper = makeMainOverlaySeries(withAlpha(ind.color, 0.95), 1.1, LightweightCharts.LineStyle.Dashed);
    const basis = makeMainOverlaySeries(withAlpha(ind.color, 0.65), 1.15, LightweightCharts.LineStyle.Solid);
    const lower = makeMainOverlaySeries(withAlpha(ind.color, 0.95), 1.1, LightweightCharts.LineStyle.Dashed);
    const reg = { kind: 'main', series: [upper, basis, lower] };
    indicatorSeriesRegistry.set(ind.id, reg);
    return reg;
  }

  const ma = makeMainOverlaySeries(ind.color, 1.25, LightweightCharts.LineStyle.Solid);
  const reg = { kind: 'main', series: [ma] };
  indicatorSeriesRegistry.set(ind.id, reg);
  return reg;
}

/* ──────────────────────────────────────────────────────────────────
   UI
────────────────────────────────────────────────────────────────── */
const topControls = document.createElement('div');
Object.assign(topControls.style, {
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
});
overlayUi.appendChild(topControls);

const indicatorsButton = document.createElement('button');
indicatorsButton.textContent = 'Indicators';
Object.assign(indicatorsButton.style, {
  background: '#0d1117ee',
  border: '1px solid #1e2a38',
  color: '#c8d8e8',
  fontFamily: "'Space Mono', monospace",
  fontSize: '0.62rem',
  fontWeight: '700',
  letterSpacing: '0.08em',
  padding: '8px 12px',
  borderRadius: '6px',
  cursor: 'pointer',
  boxShadow: '0 8px 20px rgba(0,0,0,0.24)',
  backdropFilter: 'blur(8px)',
});
topControls.appendChild(indicatorsButton);

const signalControl = document.createElement('div');
Object.assign(signalControl.style, {
  display: 'flex',
  alignItems: 'center',
  gap: '6px',
  background: '#0d1117ee',
  border: '1px solid #1e2a38',
  borderRadius: '6px',
  padding: '6px 8px',
  boxShadow: '0 8px 20px rgba(0,0,0,0.24)',
  backdropFilter: 'blur(8px)',
});
topControls.appendChild(signalControl);

const activeChipsWrap = document.createElement('div');
Object.assign(activeChipsWrap.style, {
  display: 'flex',
  flexWrap: 'wrap',
  gap: '6px',
  maxWidth: '620px',
});
overlayUi.appendChild(activeChipsWrap);

const indicatorPanel = document.createElement('div');
Object.assign(indicatorPanel.style, {
  display: 'none',
  width: '420px',
  maxHeight: 'min(70vh, 720px)',
  background: '#0d1117f4',
  border: '1px solid #1e2a38',
  borderRadius: '8px',
  boxShadow: '0 18px 38px rgba(0,0,0,0.38)',
  backdropFilter: 'blur(10px)',
  overflow: 'hidden',
});
overlayUi.appendChild(indicatorPanel);

const panelScroll = document.createElement('div');
Object.assign(panelScroll.style, {
  maxHeight: 'inherit',
  overflowY: 'auto',
});
indicatorPanel.appendChild(panelScroll);

let panelOpen = false;

function setPanelOpen(open) {
  panelOpen = !!open;
  indicatorPanel.style.display = panelOpen ? 'block' : 'none';
  indicatorsButton.style.borderColor = panelOpen ? '#00e5ff' : '#1e2a38';
  indicatorsButton.style.color = panelOpen ? '#00e5ff' : '#c8d8e8';
  indicatorsButton.style.background = panelOpen ? 'rgba(0,229,255,0.08)' : '#0d1117ee';
}

indicatorsButton.addEventListener('click', e => {
  e.stopPropagation();
  setPanelOpen(!panelOpen);
});
indicatorPanel.addEventListener('click', e => e.stopPropagation());
overlayUi.addEventListener('click', e => e.stopPropagation());
document.addEventListener('click', () => setPanelOpen(false));

function makeUiLabel(text) {
  const el = document.createElement('div');
  el.textContent = text;
  Object.assign(el.style, {
    fontFamily: "'Space Mono', monospace",
    fontSize: '0.54rem',
    fontWeight: '700',
    letterSpacing: '0.10em',
    color: '#6e8798',
    marginBottom: '4px',
  });
  return el;
}

function styleUiInput(el) {
  Object.assign(el.style, {
    width: '100%',
    background: '#111820',
    border: '1px solid #1e2a38',
    color: '#c8d8e8',
    fontFamily: "'Space Mono', monospace",
    fontSize: '0.62rem',
    fontWeight: '700',
    padding: '7px 8px',
    borderRadius: '5px',
    outline: 'none',
  });
  return el;
}

const panelHeader = document.createElement('div');
panelHeader.textContent = 'ADD / EDIT INDICATORS';
Object.assign(panelHeader.style, {
  padding: '10px 12px',
  borderBottom: '1px solid #1e2a38',
  fontFamily: "'Space Mono', monospace",
  fontSize: '0.62rem',
  fontWeight: '700',
  letterSpacing: '0.12em',
  color: '#00e5ff',
});
panelScroll.appendChild(panelHeader);

const panelBody = document.createElement('div');
Object.assign(panelBody.style, {
  padding: '12px',
  display: 'flex',
  flexDirection: 'column',
  gap: '12px',
});
panelScroll.appendChild(panelBody);

const addSection = document.createElement('div');
Object.assign(addSection.style, {
  display: 'flex',
  flexDirection: 'column',
  gap: '10px',
  paddingBottom: '10px',
  borderBottom: '1px solid #1e2a38',
});
panelBody.appendChild(addSection);

const addSectionTitle = document.createElement('div');
addSectionTitle.textContent = 'NEW INDICATOR';
Object.assign(addSectionTitle.style, {
  fontFamily: "'Space Mono', monospace",
  fontSize: '0.56rem',
  fontWeight: '700',
  letterSpacing: '0.12em',
  color: '#8aa4b6',
});
addSection.appendChild(addSectionTitle);

const typeWrap = document.createElement('div');
typeWrap.appendChild(makeUiLabel('TYPE'));
const typeSelect = styleUiInput(document.createElement('select'));
INDICATOR_TYPES.forEach(type => {
  const o = document.createElement('option');
  o.value = type;
  o.textContent = type;
  typeSelect.appendChild(o);
});
typeWrap.appendChild(typeSelect);
addSection.appendChild(typeWrap);

const rowA = document.createElement('div');
Object.assign(rowA.style, {
  display: 'grid',
  gridTemplateColumns: '1fr 1fr',
  gap: '8px',
});
addSection.appendChild(rowA);

const lengthWrap = document.createElement('div');
lengthWrap.appendChild(makeUiLabel('LENGTH'));
const lengthInput = styleUiInput(document.createElement('input'));
lengthInput.type = 'number';
lengthInput.min = '1';
lengthInput.step = '1';
lengthWrap.appendChild(lengthInput);
rowA.appendChild(lengthWrap);

const sourceWrap = document.createElement('div');
sourceWrap.appendChild(makeUiLabel('SOURCE'));
const sourceSelect = styleUiInput(document.createElement('select'));
PRICE_SOURCES.forEach(src => {
  const o = document.createElement('option');
  o.value = src;
  o.textContent = src.toUpperCase();
  sourceSelect.appendChild(o);
});
sourceWrap.appendChild(sourceSelect);
rowA.appendChild(sourceWrap);

const rowB = document.createElement('div');
Object.assign(rowB.style, {
  display: 'grid',
  gridTemplateColumns: '1fr 1fr 1fr',
  gap: '8px',
});
addSection.appendChild(rowB);

const colorWrap = document.createElement('div');
colorWrap.appendChild(makeUiLabel('COLOR'));
const colorInput = document.createElement('input');
colorInput.type = 'color';
Object.assign(colorInput.style, {
  width: '100%',
  height: '34px',
  background: '#111820',
  border: '1px solid #1e2a38',
  borderRadius: '5px',
  padding: '3px',
  cursor: 'pointer',
});
colorWrap.appendChild(colorInput);
rowB.appendChild(colorWrap);

const stddevWrap = document.createElement('div');
stddevWrap.appendChild(makeUiLabel('STDDEV'));
const stddevInput = styleUiInput(document.createElement('input'));
stddevInput.type = 'number';
stddevInput.step = '0.1';
stddevWrap.appendChild(stddevInput);
rowB.appendChild(stddevWrap);

const smoothKWrap = document.createElement('div');
smoothKWrap.appendChild(makeUiLabel('SMOOTH K'));
const smoothKInput = styleUiInput(document.createElement('input'));
smoothKInput.type = 'number';
smoothKInput.step = '1';
smoothKWrap.appendChild(smoothKInput);
rowB.appendChild(smoothKWrap);

const rowC = document.createElement('div');
Object.assign(rowC.style, {
  display: 'grid',
  gridTemplateColumns: '1fr auto',
  gap: '8px',
  alignItems: 'end',
});
addSection.appendChild(rowC);

const smoothDWrap = document.createElement('div');
smoothDWrap.appendChild(makeUiLabel('SMOOTH D'));
const smoothDInput = styleUiInput(document.createElement('input'));
smoothDInput.type = 'number';
smoothDInput.step = '1';
smoothDWrap.appendChild(smoothDInput);
rowC.appendChild(smoothDWrap);

const addIndicatorButton = document.createElement('button');
addIndicatorButton.textContent = '+ ADD';
Object.assign(addIndicatorButton.style, {
  background: 'rgba(0,229,255,0.08)',
  border: '1px solid #00e5ff',
  color: '#00e5ff',
  fontFamily: "'Space Mono', monospace",
  fontSize: '0.62rem',
  fontWeight: '700',
  letterSpacing: '0.10em',
  padding: '9px 14px',
  borderRadius: '5px',
  cursor: 'pointer',
  minWidth: '88px',
});
rowC.appendChild(addIndicatorButton);

const editorSection = document.createElement('div');
Object.assign(editorSection.style, {
  display: 'flex',
  flexDirection: 'column',
  gap: '10px',
});
panelBody.appendChild(editorSection);

function applyFormDefaults(type) {
  const d = defaultForType(type);
  lengthInput.value = d.length != null ? d.length : 14;
  colorInput.value = d.color || '#00e5ff';
  sourceSelect.value = d.source || 'close';
  stddevInput.value = d.stddev != null ? d.stddev : 2;
  smoothKInput.value = d.smoothK != null ? d.smoothK : 3;
  smoothDInput.value = d.smoothD != null ? d.smoothD : 3;

  stddevWrap.style.display = type === 'BB' ? '' : 'none';
  smoothKWrap.style.display = type === 'Stoch RSI' ? '' : 'none';
  smoothDWrap.style.display = type === 'Stoch RSI' ? '' : 'none';
}
typeSelect.addEventListener('change', () => applyFormDefaults(typeSelect.value));
applyFormDefaults(typeSelect.value);

function renderSignalUi() {
  signalControl.innerHTML = '';

  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = signalEnabled;
  cb.style.accentColor = '#00e5ff';
  cb.addEventListener('change', () => {
    signalEnabled = cb.checked;
    renderSignalUi();
    refreshSignals();
  });

  const label = document.createElement('span');
  label.textContent = 'Signals';
  Object.assign(label.style, {
    fontFamily: "'Space Mono', monospace",
    fontSize: '0.56rem',
    fontWeight: '700',
    letterSpacing: '0.08em',
    color: signalEnabled ? '#00e5ff' : '#6e8798',
  });

  signalControl.appendChild(cb);
  signalControl.appendChild(label);

  const candidates = indicators.filter(ind => ['EMA', 'HMA', 'SMA', 'WMA'].includes(ind.type));
  if (!candidates.length) return;

  if (!signalIndicatorId || !candidates.some(ind => ind.id === signalIndicatorId)) {
    signalIndicatorId = candidates[0].id;
  }

  const select = styleUiInput(document.createElement('select'));
  Object.assign(select.style, {
    width: '148px',
    padding: '5px 7px',
    fontSize: '0.56rem',
  });

  candidates.forEach(ind => {
    const o = document.createElement('option');
    o.value = ind.id;
    o.textContent = formatIndicatorLabel(ind);
    if (ind.id === signalIndicatorId) o.selected = true;
    select.appendChild(o);
  });

  select.addEventListener('change', () => {
    signalIndicatorId = select.value;
    refreshSignals();
  });

  signalControl.appendChild(select);
}

function renderIndicatorChips() {
  activeChipsWrap.innerHTML = '';

  indicators.forEach(ind => {
    const chip = document.createElement('div');
    Object.assign(chip.style, {
      display: 'inline-flex',
      alignItems: 'center',
      gap: '6px',
      background: selectedIndicatorId === ind.id ? 'rgba(0,229,255,0.08)' : '#0d1117ee',
      border: selectedIndicatorId === ind.id ? '1px solid #00e5ff' : '1px solid #1e2a38',
      borderRadius: '999px',
      padding: '5px 8px',
      boxShadow: '0 4px 14px rgba(0,0,0,0.18)',
      backdropFilter: 'blur(8px)',
      cursor: 'pointer',
    });

    chip.addEventListener('click', () => {
      selectedIndicatorId = ind.id;
      renderIndicatorChips();
      renderIndicatorEditors();
      setPanelOpen(true);
    });

    const dot = document.createElement('span');
    Object.assign(dot.style, {
      width: '9px',
      height: '9px',
      borderRadius: '50%',
      background: ind.color,
      boxShadow: `0 0 8px ${withAlpha(ind.color, 0.45)}`,
      flexShrink: '0',
    });

    const text = document.createElement('span');
    text.textContent = formatIndicatorLabel(ind);
    Object.assign(text.style, {
      fontFamily: "'Space Mono', monospace",
      fontSize: '0.56rem',
      fontWeight: '700',
      letterSpacing: '0.04em',
      color: '#c8d8e8',
      whiteSpace: 'nowrap',
    });

    const toggleBtn = document.createElement('button');
    toggleBtn.textContent = ind.visible ? '◉' : '○';
    Object.assign(toggleBtn.style, {
      background: 'transparent',
      border: 'none',
      color: ind.visible ? '#00e5ff' : '#4a6070',
      fontFamily: "'Space Mono', monospace",
      fontSize: '0.68rem',
      cursor: 'pointer',
      padding: '0 2px',
    });
    toggleBtn.addEventListener('click', e => {
      e.stopPropagation();
      updateIndicator(ind.id, { visible: !ind.visible });
    });

    const removeBtn = document.createElement('button');
    removeBtn.textContent = '✕';
    Object.assign(removeBtn.style, {
      background: 'transparent',
      border: 'none',
      color: '#ff3d5a',
      fontFamily: "'Space Mono', monospace",
      fontSize: '0.62rem',
      fontWeight: '700',
      cursor: 'pointer',
      padding: '0 2px',
    });
    removeBtn.addEventListener('click', e => {
      e.stopPropagation();
      if (selectedIndicatorId === ind.id) selectedIndicatorId = null;
      removeIndicator(ind.id);
    });

    chip.appendChild(dot);
    chip.appendChild(text);
    chip.appendChild(toggleBtn);
    chip.appendChild(removeBtn);
    activeChipsWrap.appendChild(chip);
  });
}

function renderIndicatorEditors() {
  editorSection.innerHTML = '';

  const title = document.createElement('div');
  title.textContent = 'ACTIVE INDICATORS';
  Object.assign(title.style, {
    fontFamily: "'Space Mono', monospace",
    fontSize: '0.56rem',
    fontWeight: '700',
    letterSpacing: '0.12em',
    color: '#8aa4b6',
  });
  editorSection.appendChild(title);

  if (!indicators.length) {
    const empty = document.createElement('div');
    empty.textContent = 'No active indicators.';
    Object.assign(empty.style, {
      fontFamily: "'Space Mono', monospace",
      fontSize: '0.58rem',
      color: '#586f7f',
    });
    editorSection.appendChild(empty);
    return;
  }

  indicators.forEach(ind => {
    const card = document.createElement('div');
    Object.assign(card.style, {
      display: 'flex',
      flexDirection: 'column',
      gap: '8px',
      padding: '10px',
      borderRadius: '8px',
      border: selectedIndicatorId === ind.id ? '1px solid #00e5ff' : '1px solid #1e2a38',
      background: selectedIndicatorId === ind.id ? 'rgba(0,229,255,0.04)' : '#0f151c',
    });

    const head = document.createElement('div');
    Object.assign(head.style, {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: '8px',
      cursor: 'pointer',
    });
    head.addEventListener('click', () => {
      selectedIndicatorId = ind.id;
      renderIndicatorEditors();
      renderIndicatorChips();
    });

    const left = document.createElement('div');
    Object.assign(left.style, {
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
    });

    const dot = document.createElement('span');
    Object.assign(dot.style, {
      width: '10px',
      height: '10px',
      borderRadius: '50%',
      background: ind.color,
      flexShrink: '0',
    });

    const label = document.createElement('div');
    label.textContent = formatIndicatorLabel(ind);
    Object.assign(label.style, {
      fontFamily: "'Space Mono', monospace",
      fontSize: '0.60rem',
      fontWeight: '700',
      letterSpacing: '0.05em',
      color: '#c8d8e8',
    });

    left.appendChild(dot);
    left.appendChild(label);

    const actions = document.createElement('div');
    Object.assign(actions.style, {
      display: 'flex',
      alignItems: 'center',
      gap: '6px',
    });

    const vis = document.createElement('button');
    vis.textContent = ind.visible ? 'VISIBLE' : 'HIDDEN';
    Object.assign(vis.style, {
      background: ind.visible ? 'rgba(0,229,255,0.08)' : 'transparent',
      border: '1px solid #1e2a38',
      color: ind.visible ? '#00e5ff' : '#6e8798',
      fontFamily: "'Space Mono', monospace",
      fontSize: '0.52rem',
      fontWeight: '700',
      letterSpacing: '0.08em',
      padding: '5px 8px',
      borderRadius: '4px',
      cursor: 'pointer',
    });
    vis.addEventListener('click', e => {
      e.stopPropagation();
      updateIndicator(ind.id, { visible: !ind.visible });
    });

    const del = document.createElement('button');
    del.textContent = 'REMOVE';
    Object.assign(del.style, {
      background: 'transparent',
      border: '1px solid #3b2028',
      color: '#ff3d5a',
      fontFamily: "'Space Mono', monospace",
      fontSize: '0.52rem',
      fontWeight: '700',
      letterSpacing: '0.08em',
      padding: '5px 8px',
      borderRadius: '4px',
      cursor: 'pointer',
    });
    del.addEventListener('click', e => {
      e.stopPropagation();
      if (selectedIndicatorId === ind.id) selectedIndicatorId = null;
      removeIndicator(ind.id);
    });

    actions.appendChild(vis);
    actions.appendChild(del);
    head.appendChild(left);
    head.appendChild(actions);
    card.appendChild(head);

    const grid = document.createElement('div');
    Object.assign(grid.style, {
      display: 'grid',
      gridTemplateColumns: '1fr 1fr 1fr',
      gap: '8px',
    });
    card.appendChild(grid);

    function addField(parent, labelText, inputEl) {
      const wrap = document.createElement('div');
      wrap.appendChild(makeUiLabel(labelText));
      wrap.appendChild(inputEl);
      parent.appendChild(wrap);
    }

    const typeSel = styleUiInput(document.createElement('select'));
    INDICATOR_TYPES.forEach(t => {
      const o = document.createElement('option');
      o.value = t;
      o.textContent = t;
      if (t === ind.type) o.selected = true;
      typeSel.appendChild(o);
    });
    typeSel.addEventListener('change', () => {
      const defaults = defaultForType(typeSel.value);
      updateIndicator(ind.id, {
        type: typeSel.value,
        length: defaults.length != null ? defaults.length : ind.length,
        source: defaults.source || ind.source,
        color: defaults.color || ind.color,
        stddev: defaults.stddev != null ? defaults.stddev : ind.stddev,
        smoothK: defaults.smoothK != null ? defaults.smoothK : ind.smoothK,
        smoothD: defaults.smoothD != null ? defaults.smoothD : ind.smoothD,
        obLevel: defaults.obLevel != null ? defaults.obLevel : ind.obLevel,
        osLevel: defaults.osLevel != null ? defaults.osLevel : ind.osLevel,
        midLevel: defaults.midLevel != null ? defaults.midLevel : ind.midLevel,
        showMid: defaults.showMid != null ? defaults.showMid : ind.showMid,
        obColor: defaults.obColor || ind.obColor,
        osColor: defaults.osColor || ind.osColor,
        midColor: defaults.midColor || ind.midColor,
      });
    });
    addField(grid, 'TYPE', typeSel);

    const lenInput = styleUiInput(document.createElement('input'));
    lenInput.type = 'number';
    lenInput.min = '1';
    lenInput.step = '1';
    lenInput.value = ind.length;
    lenInput.addEventListener('change', () => updateIndicator(ind.id, { length: Number(lenInput.value || ind.length) }));
    addField(grid, 'LENGTH', lenInput);

    const srcSel = styleUiInput(document.createElement('select'));
    PRICE_SOURCES.forEach(s => {
      const o = document.createElement('option');
      o.value = s;
      o.textContent = s.toUpperCase();
      if (s === ind.source) o.selected = true;
      srcSel.appendChild(o);
    });
    srcSel.addEventListener('change', () => updateIndicator(ind.id, { source: srcSel.value }));
    addField(grid, 'SOURCE', srcSel);

    const grid2 = document.createElement('div');
    Object.assign(grid2.style, {
      display: 'grid',
      gridTemplateColumns: '1fr 1fr 1fr',
      gap: '8px',
    });
    card.appendChild(grid2);

    const colorEl = document.createElement('input');
    colorEl.type = 'color';
    colorEl.value = ind.color;
    Object.assign(colorEl.style, {
      width: '100%',
      height: '34px',
      background: '#111820',
      border: '1px solid #1e2a38',
      borderRadius: '5px',
      padding: '3px',
      cursor: 'pointer',
    });
    colorEl.addEventListener('input', () => updateIndicator(ind.id, { color: colorEl.value }));
    addField(grid2, 'COLOR', colorEl);

    if (ind.type === 'BB') {
      const stdInput = styleUiInput(document.createElement('input'));
      stdInput.type = 'number';
      stdInput.step = '0.1';
      stdInput.value = ind.stddev != null ? ind.stddev : 2;
      stdInput.addEventListener('change', () => updateIndicator(ind.id, { stddev: Number(stdInput.value || 2) }));
      addField(grid2, 'STDDEV', stdInput);
    }

    if (ind.type === 'Stoch RSI') {
      const kInput = styleUiInput(document.createElement('input'));
      kInput.type = 'number';
      kInput.step = '1';
      kInput.value = ind.smoothK != null ? ind.smoothK : 3;
      kInput.addEventListener('change', () => updateIndicator(ind.id, { smoothK: Number(kInput.value || 3) }));
      addField(grid2, 'SMOOTH K', kInput);

      const dInput = styleUiInput(document.createElement('input'));
      dInput.type = 'number';
      dInput.step = '1';
      dInput.value = ind.smoothD != null ? ind.smoothD : 3;
      dInput.addEventListener('change', () => updateIndicator(ind.id, { smoothD: Number(dInput.value || 3) }));
      addField(grid2, 'SMOOTH D', dInput);
    }

    if (ind.type === 'RSI' || ind.type === 'Stoch RSI') {
      const lvlTitle = document.createElement('div');
      lvlTitle.textContent = 'LEVELS';
      Object.assign(lvlTitle.style, {
        fontFamily: "'Space Mono', monospace",
        fontSize: '0.54rem',
        fontWeight: '700',
        letterSpacing: '0.10em',
        color: '#8aa4b6',
        marginTop: '2px',
      });
      card.appendChild(lvlTitle);

      const lvlGrid = document.createElement('div');
      Object.assign(lvlGrid.style, {
        display: 'grid',
        gridTemplateColumns: ind.type === 'RSI' ? '1fr 1fr 1fr 1fr 1fr 1fr' : '1fr 1fr 1fr 1fr',
        gap: '8px',
      });
      card.appendChild(lvlGrid);

      const obVal = styleUiInput(document.createElement('input'));
      obVal.type = 'number';
      obVal.step = '0.1';
      obVal.value = ind.obLevel != null ? ind.obLevel : (ind.type === 'RSI' ? 70 : 80);
      obVal.addEventListener('change', () => updateIndicator(ind.id, { obLevel: Number(obVal.value) }));
      addField(lvlGrid, 'OB', obVal);

      const osVal = styleUiInput(document.createElement('input'));
      osVal.type = 'number';
      osVal.step = '0.1';
      osVal.value = ind.osLevel != null ? ind.osLevel : (ind.type === 'RSI' ? 30 : 20);
      osVal.addEventListener('change', () => updateIndicator(ind.id, { osLevel: Number(osVal.value) }));
      addField(lvlGrid, 'OS', osVal);

      const obColor = document.createElement('input');
      obColor.type = 'color';
      obColor.value = ind.obColor || '#ff3d5a';
      Object.assign(obColor.style, {
        width: '100%',
        height: '34px',
        background: '#111820',
        border: '1px solid #1e2a38',
        borderRadius: '5px',
        padding: '3px',
        cursor: 'pointer',
      });
      obColor.addEventListener('input', () => updateIndicator(ind.id, { obColor: obColor.value }));
      addField(lvlGrid, 'OB COLOR', obColor);

      const osColor = document.createElement('input');
      osColor.type = 'color';
      osColor.value = ind.osColor || '#00e676';
      Object.assign(osColor.style, {
        width: '100%',
        height: '34px',
        background: '#111820',
        border: '1px solid #1e2a38',
        borderRadius: '5px',
        padding: '3px',
        cursor: 'pointer',
      });
      osColor.addEventListener('input', () => updateIndicator(ind.id, { osColor: osColor.value }));
      addField(lvlGrid, 'OS COLOR', osColor);

      if (ind.type === 'RSI') {
        const midVal = styleUiInput(document.createElement('input'));
        midVal.type = 'number';
        midVal.step = '0.1';
        midVal.value = ind.midLevel != null ? ind.midLevel : 50;
        midVal.addEventListener('change', () => updateIndicator(ind.id, { midLevel: Number(midVal.value) }));
        addField(lvlGrid, 'MID', midVal);

        const midColor = document.createElement('input');
        midColor.type = 'color';
        midColor.value = ind.midColor || '#4a6070';
        Object.assign(midColor.style, {
          width: '100%',
          height: '34px',
          background: '#111820',
          border: '1px solid #1e2a38',
          borderRadius: '5px',
          padding: '3px',
          cursor: 'pointer',
        });
        midColor.addEventListener('input', () => updateIndicator(ind.id, { midColor: midColor.value }));
        addField(lvlGrid, 'MID COLOR', midColor);

        const midToggleWrap = document.createElement('div');
        midToggleWrap.appendChild(makeUiLabel('SHOW MID'));
        const midToggle = document.createElement('input');
        midToggle.type = 'checkbox';
        midToggle.checked = ind.showMid !== false;
        midToggle.style.accentColor = '#00e5ff';
        midToggle.addEventListener('change', () => updateIndicator(ind.id, { showMid: midToggle.checked }));
        midToggleWrap.appendChild(midToggle);
        card.appendChild(midToggleWrap);
      }
    }

    editorSection.appendChild(card);
  });
}

/* ──────────────────────────────────────────────────────────────────
   INDICATOR CRUD
────────────────────────────────────────────────────────────────── */
function addIndicator(def) {
  const defaults = defaultForType(def.type);

  const ind = {
    id: `ind_${nextIndicatorId++}`,
    type: def.type,
    length: clamp(parseInt(def.length != null ? def.length : defaults.length, 10) || defaults.length || 14, 1, 2000),
    color: def.color || defaults.color || '#00e5ff',
    source: PRICE_SOURCES.includes(def.source) ? def.source : (defaults.source || 'close'),
    visible: def.visible !== false,
    stddev: def.stddev != null ? Number(def.stddev) : (defaults.stddev != null ? defaults.stddev : 2),
    smoothK: def.smoothK != null ? Math.max(1, Number(def.smoothK)) : (defaults.smoothK != null ? defaults.smoothK : 3),
    smoothD: def.smoothD != null ? Math.max(1, Number(def.smoothD)) : (defaults.smoothD != null ? defaults.smoothD : 3),
    obLevel: def.obLevel != null ? Number(def.obLevel) : defaults.obLevel,
    osLevel: def.osLevel != null ? Number(def.osLevel) : defaults.osLevel,
    midLevel: def.midLevel != null ? Number(def.midLevel) : defaults.midLevel,
    showMid: def.showMid != null ? !!def.showMid : defaults.showMid,
    obColor: def.obColor || defaults.obColor,
    osColor: def.osColor || defaults.osColor,
    midColor: def.midColor || defaults.midColor,
  };

  indicators.push(ind);
  selectedIndicatorId = ind.id;
  ensureIndicatorSeries(ind);

  if (!signalIndicatorId && ['EMA', 'HMA', 'SMA', 'WMA'].includes(ind.type)) {
    signalIndicatorId = ind.id;
  }

  renderIndicatorChips();
  renderSignalUi();
  renderIndicatorEditors();
  invalidateIndicatorCache();

  requestAnimationFrame(() => {
    resizeAllCharts();
    const range = lastVisibleRange || loadedBounds();
    if (range) syncPaneRangesFromMain(range);
    scheduleIndicatorRender();
  });
}

function removeIndicator(indicatorId) {
  indicators = indicators.filter(ind => ind.id !== indicatorId);
  removeIndicatorSeries(indicatorId);
  lastRenderedIndicatorRawValues.delete(indicatorId);
  lastRenderedComputed.delete(indicatorId);

  if (signalIndicatorId === indicatorId) {
    const next = indicators.find(ind => ['EMA', 'HMA', 'SMA', 'WMA'].includes(ind.type));
    signalIndicatorId = next ? next.id : null;
  }

  if (selectedIndicatorId === indicatorId) {
    selectedIndicatorId = indicators.length ? indicators[0].id : null;
  }

  destroyUnusedPanes();
  renderIndicatorChips();
  renderSignalUi();
  renderIndicatorEditors();
  invalidateIndicatorCache();

  requestAnimationFrame(() => {
    resizeAllCharts();
    const range = lastVisibleRange || loadedBounds();
    if (range) syncPaneRangesFromMain(range);
    scheduleIndicatorRender();
  });
}

function updateIndicator(indicatorId, patch) {
  const ind = indicators.find(x => x.id === indicatorId);
  if (!ind) return;

  const oldType = ind.type;
  Object.assign(ind, patch);

  const defaults = defaultForType(ind.type);
  ind.length = clamp(parseInt(ind.length, 10) || defaults.length || 14, 1, 2000);
  ind.source = PRICE_SOURCES.includes(ind.source) ? ind.source : 'close';
  if (ind.type === 'BB') ind.stddev = Number(ind.stddev != null ? ind.stddev : 2);
  if (ind.type === 'Stoch RSI') {
    ind.smoothK = Math.max(1, Number(ind.smoothK != null ? ind.smoothK : 3));
    ind.smoothD = Math.max(1, Number(ind.smoothD != null ? ind.smoothD : 3));
  }

  ind.obLevel = ind.obLevel != null ? Number(ind.obLevel) : defaults.obLevel;
  ind.osLevel = ind.osLevel != null ? Number(ind.osLevel) : defaults.osLevel;
  ind.midLevel = ind.midLevel != null ? Number(ind.midLevel) : defaults.midLevel;
  ind.showMid = ind.showMid != null ? !!ind.showMid : defaults.showMid;
  ind.obColor = ind.obColor || defaults.obColor;
  ind.osColor = ind.osColor || defaults.osColor;
  ind.midColor = ind.midColor || defaults.midColor;

  const typeChanged = oldType !== ind.type;
  if (typeChanged) {
    removeIndicatorSeries(indicatorId);
    destroyUnusedPanes();
    ensureIndicatorSeries(ind);

    if (signalIndicatorId === indicatorId && !['EMA', 'HMA', 'SMA', 'WMA'].includes(ind.type)) {
      const next = indicators.find(x => ['EMA', 'HMA', 'SMA', 'WMA'].includes(x.type));
      signalIndicatorId = next ? next.id : null;
    } else if (!signalIndicatorId && ['EMA', 'HMA', 'SMA', 'WMA'].includes(ind.type)) {
      signalIndicatorId = indicatorId;
    }
  }

  const reg = ensureIndicatorSeries(ind);
  if (ind.type === 'BB') {
    reg.series[0].applyOptions({ color: withAlpha(ind.color, 0.95), visible: ind.visible });
    reg.series[1].applyOptions({ color: withAlpha(ind.color, 0.65), visible: ind.visible });
    reg.series[2].applyOptions({ color: withAlpha(ind.color, 0.95), visible: ind.visible });
  } else if (ind.type === 'Stoch RSI') {
    reg.series[0].applyOptions({ color: ind.color, visible: ind.visible });
    reg.series[1].applyOptions({ color: withAlpha(ind.color, 0.55), visible: ind.visible });
    reg.series[2].applyOptions({ color: withAlpha(ind.obColor || '#ff3d5a', 0.55), visible: ind.visible });
    reg.series[3].applyOptions({ color: withAlpha(ind.osColor || '#00e676', 0.55), visible: ind.visible });
  } else if (ind.type === 'RSI') {
    reg.series[0].applyOptions({ color: ind.color, visible: ind.visible });
    reg.series[1].applyOptions({ color: withAlpha(ind.obColor || '#ff3d5a', 0.55), visible: ind.visible });
    reg.series[2].applyOptions({ color: withAlpha(ind.osColor || '#00e676', 0.55), visible: ind.visible });
    reg.series[3].applyOptions({ color: withAlpha(ind.midColor || '#4a6070', 0.45), visible: ind.visible && ind.showMid !== false });
  } else {
    reg.series[0].applyOptions({ color: ind.color, visible: ind.visible });
  }

  renderIndicatorChips();
  renderSignalUi();
  renderIndicatorEditors();
  invalidateIndicatorCache();

  requestAnimationFrame(() => {
    resizeAllCharts();
    const range = lastVisibleRange || loadedBounds();
    if (range) syncPaneRangesFromMain(range);
    scheduleIndicatorRender();
  });
}

addIndicatorButton.addEventListener('click', () => {
  const type = typeSelect.value;
  const def = {
    type,
    length: Number(lengthInput.value),
    color: colorInput.value,
    source: sourceSelect.value,
    visible: true,
  };

  if (type === 'BB') def.stddev = Number(stddevInput.value || 2);
  if (type === 'Stoch RSI') {
    def.smoothK = Number(smoothKInput.value || 3);
    def.smoothD = Number(smoothDInput.value || 3);
  }

  addIndicator(def);
  setPanelOpen(false);
});

/* ──────────────────────────────────────────────────────────────────
   INDICATOR CALC ON LOADED WINDOW
────────────────────────────────────────────────────────────────── */
function computeIndicatorForLoadedWindow(ind) {
  if (!loadedData.length) return null;

  const warmup = indicatorWarmup(ind);
  const calcStart = Math.max(0, loadedWindow.start - warmup);
  const calcEnd = loadedWindow.end;

  const cacheKey = [
    datasetVersion,
    currentRange,
    ind.id,
    ind.type,
    ind.length,
    ind.source,
    ind.stddev,
    ind.smoothK,
    ind.smoothD,
    calcStart,
    calcEnd,
  ].join('|');

  if (indicatorWindowCache.has(cacheKey)) {
    return indicatorWindowCache.get(cacheKey);
  }

  const values = sourceArrayFor(ind.source).slice(calcStart, calcEnd);
  const times = fullData.slice(calcStart, calcEnd).map(b => b.time);
  const offset = loadedWindow.start - calcStart;
  const visibleLen = loadedWindow.end - loadedWindow.start;

  let result = null;

  if (ind.type === 'BB') {
    const bb = precomputeBollinger(values, ind.length, Number(ind.stddev || 2));
    const basisRaw = bb.basis.slice(offset, offset + visibleLen);
    const upperRaw = bb.upper.slice(offset, offset + visibleLen);
    const lowerRaw = bb.lower.slice(offset, offset + visibleLen);

    const basis = [];
    const upper = [];
    const lower = [];
    for (let i = 0; i < visibleLen; i++) {
      const t = times[offset + i];
      if (upperRaw[i] != null) upper.push({ time: t, value: upperRaw[i] });
      if (basisRaw[i] != null) basis.push({ time: t, value: basisRaw[i] });
      if (lowerRaw[i] != null) lower.push({ time: t, value: lowerRaw[i] });
    }

    result = { kind: 'bb', basisRaw, upperRaw, lowerRaw, basis, upper, lower };
  } else if (ind.type === 'RSI') {
    const raw = precomputeRsi(values, ind.length).slice(offset, offset + visibleLen);
    const line = [];
    for (let i = 0; i < visibleLen; i++) {
      if (raw[i] != null) line.push({ time: times[offset + i], value: raw[i] });
    }
    result = { kind: 'rsi', raw, line };
  } else if (ind.type === 'Stoch RSI') {
    const stoch = precomputeStochRsi(values, ind.length, ind.smoothK || 3, ind.smoothD || 3);
    const kRaw = stoch.k.slice(offset, offset + visibleLen);
    const dRaw = stoch.d.slice(offset, offset + visibleLen);

    const kLine = [];
    const dLine = [];
    for (let i = 0; i < visibleLen; i++) {
      const t = times[offset + i];
      if (kRaw[i] != null) kLine.push({ time: t, value: kRaw[i] });
      if (dRaw[i] != null) dLine.push({ time: t, value: dRaw[i] });
    }
    result = { kind: 'stoch', kRaw, dRaw, kLine, dLine };
  } else {
    const raw = precomputeMa(values, ind.type, ind.length).slice(offset, offset + visibleLen);
    const line = [];
    for (let i = 0; i < visibleLen; i++) {
      if (raw[i] != null) line.push({ time: times[offset + i], value: raw[i] });
    }
    result = { kind: 'ma', raw, line };
  }

  indicatorWindowCache.set(cacheKey, result);
  return result;
}

function renderLevelLines() {
  if (!loadedData.length) return;

  const start = loadedData[0].time;
  const end = loadedData[loadedData.length - 1].time;

  indicators.forEach(ind => {
    const reg = indicatorSeriesRegistry.get(ind.id);
    if (!reg) return;

    if (ind.type === 'RSI' && paneState.rsi) {
      const entry = paneState.rsi.dynamicSeries.get(ind.id);
      if (!entry) return;

      entry.ob.setData(ind.visible ? [{ time: start, value: ind.obLevel }, { time: end, value: ind.obLevel }] : []);
      entry.os.setData(ind.visible ? [{ time: start, value: ind.osLevel }, { time: end, value: ind.osLevel }] : []);
      entry.mid.setData(ind.visible && ind.showMid !== false
        ? [{ time: start, value: ind.midLevel }, { time: end, value: ind.midLevel }]
        : []);
    }

    if (ind.type === 'Stoch RSI' && paneState.stoch) {
      const entry = paneState.stoch.dynamicSeries.get(ind.id);
      if (!entry) return;

      entry.ob.setData(ind.visible ? [{ time: start, value: ind.obLevel }, { time: end, value: ind.obLevel }] : []);
      entry.os.setData(ind.visible ? [{ time: start, value: ind.osLevel }, { time: end, value: ind.osLevel }] : []);
    }
  });
}

function renderIndicatorsNow() {
  if (!loadedData.length) return;

  lastRenderedIndicatorRawValues = new Map();
  lastRenderedComputed = new Map();

  indicators.forEach(ind => {
    const reg = ensureIndicatorSeries(ind);
    const computed = computeIndicatorForLoadedWindow(ind);
    if (!computed) return;

    lastRenderedComputed.set(ind.id, computed);

    if (ind.type === 'BB') {
      reg.series[0].setData(ind.visible ? computed.upper : []);
      reg.series[1].setData(ind.visible ? computed.basis : []);
      reg.series[2].setData(ind.visible ? computed.lower : []);
      reg.series.forEach(s => s.applyOptions({ visible: ind.visible }));
      lastRenderedIndicatorRawValues.set(ind.id, computed.basisRaw);
    } else if (ind.type === 'RSI') {
      reg.series[0].setData(ind.visible ? computed.line : []);
      reg.series[0].applyOptions({ visible: ind.visible });
      reg.series[1].applyOptions({ visible: ind.visible });
      reg.series[2].applyOptions({ visible: ind.visible });
      reg.series[3].applyOptions({ visible: ind.visible && ind.showMid !== false });
      lastRenderedIndicatorRawValues.set(ind.id, computed.raw);
    } else if (ind.type === 'Stoch RSI') {
      reg.series[0].setData(ind.visible ? computed.kLine : []);
      reg.series[1].setData(ind.visible ? computed.dLine : []);
      reg.series[0].applyOptions({ visible: ind.visible });
      reg.series[1].applyOptions({ visible: ind.visible });
      reg.series[2].applyOptions({ visible: ind.visible });
      reg.series[3].applyOptions({ visible: ind.visible });
      lastRenderedIndicatorRawValues.set(ind.id, computed.kRaw);
    } else {
      reg.series[0].setData(ind.visible ? computed.line : []);
      reg.series[0].applyOptions({ visible: ind.visible });
      lastRenderedIndicatorRawValues.set(ind.id, computed.raw);
    }
  });

  renderLevelLines();
  updateOscillatorReadoutsAtTime((lastVisibleRange && lastVisibleRange.to) || (loadedData.length ? loadedData[loadedData.length - 1].time : null));
  refreshSignals();
}

function scheduleIndicatorRender() {
  if (indicatorRenderTimer) clearTimeout(indicatorRenderTimer);
  indicatorRenderTimer = setTimeout(() => {
    indicatorRenderTimer = null;
    renderIndicatorsNow();
  }, INDICATOR_RENDER_DEBOUNCE_MS);
}

/* ──────────────────────────────────────────────────────────────────
   SIGNALS
────────────────────────────────────────────────────────────────── */
function computeSignals(data, maValues) {
  if (!signalEnabled || !data || !data.length || !maValues || !maValues.length) {
    return { buy: [], sell: [] };
  }

  const buy = [];
  const sell = [];
  let bullCount = 0;
  let bearCount = 0;

  for (let i = 1; i < data.length; i++) {
    const bar = data[i];
    const mv = maValues[i];
    if (mv == null) {
      bullCount = 0;
      bearCount = 0;
      continue;
    }

    const c = bar.close;
    const o = bar.open;
    const above = c > mv;
    const below = c < mv;
    const bull = c >= o;
    const bear = c < o;

    if (above && bull) bullCount++;
    else bullCount = 0;

    if (below && bear) bearCount++;
    else bearCount = 0;

    if (bullCount >= 2) {
      buy.push({
        time: bar.time,
        position: 'belowBar',
        color: '#00e676',
        shape: 'arrowUp',
        text: 'BUY',
        size: 1,
      });
      bullCount = 0;
    }

    if (bearCount >= 2) {
      sell.push({
        time: bar.time,
        position: 'aboveBar',
        color: '#ff3d5a',
        shape: 'arrowDown',
        text: 'SELL',
        size: 1,
      });
      bearCount = 0;
    }
  }

  return { buy, sell };
}

function refreshSignals() {
  if (!signalEnabled || !signalIndicatorId) {
    buySignalSeries.setMarkers([]);
    sellSignalSeries.setMarkers([]);
    return;
  }

  const ind = indicators.find(x => x.id === signalIndicatorId && x.visible);
  if (!ind || !['EMA', 'HMA', 'SMA', 'WMA'].includes(ind.type)) {
    buySignalSeries.setMarkers([]);
    sellSignalSeries.setMarkers([]);
    return;
  }

  const raw = lastRenderedIndicatorRawValues.get(ind.id);
  if (!raw || !loadedData.length) {
    buySignalSeries.setMarkers([]);
    sellSignalSeries.setMarkers([]);
    return;
  }

  const sigs = computeSignals(loadedData, raw);
  buySignalSeries.setMarkers(sigs.buy);
  sellSignalSeries.setMarkers(sigs.sell);
}

/* ──────────────────────────────────────────────────────────────────
   BACKTEST MARKERS
────────────────────────────────────────────────────────────────── */
function snapTimeToDataset(time) {
  if (!fullData.length) return time;

  let lo = 0;
  let hi = fullData.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (fullData[mid].time < time) lo = mid + 1;
    else hi = mid;
  }

  const idx = lo;
  if (idx > 0) {
    const dCur = Math.abs(fullData[idx].time - time);
    const dPrev = Math.abs(fullData[idx - 1].time - time);
    if (dPrev < dCur) return fullData[idx - 1].time;
  }
  return fullData[idx] ? fullData[idx].time : fullData[fullData.length - 1].time;
}

function exitColor(reason) {
  if (!reason) return '#ffffff';
  const r = String(reason).toUpperCase();
  if (r === 'TP_R') return '#00e676';
  if (r === 'SL_HIT') return '#ff3d5a';
  if (r === 'MA_EXIT') return '#ffab00';
  if (r === 'END_OF_DATA') return '#ffffff';
  return '#aaaaaa';
}

function rebuildBacktestMarkerCache(trades) {
  fullBacktestTrades = Array.isArray(trades) ? trades.slice() : [];
  fullBacktestEntryMarkers = [];
  fullBacktestExitMarkers = [];

  if (!fullBacktestTrades.length || !fullData.length) return;

  const seenEntry = {};
  const seenExit = {};

  for (let i = 0; i < fullBacktestTrades.length; i++) {
    const t = fullBacktestTrades[i];
    const isLong = t.direction === 'long';

    const entryTime = snapTimeToDataset(t.entry_time);
    const entryKey = `entry_${entryTime}_${t.id}`;
    if (!seenEntry[entryKey]) {
      seenEntry[entryKey] = true;
      fullBacktestEntryMarkers.push({
        time: entryTime,
        position: isLong ? 'belowBar' : 'aboveBar',
        color: isLong ? '#00e676' : '#ff3d5a',
        shape: isLong ? 'arrowUp' : 'arrowDown',
        text: isLong ? 'BUY' : 'SELL',
        size: 1,
      });
    }

    if (t.exit_time != null) {
      let exitTime = snapTimeToDataset(t.exit_time);
      if (exitTime === entryTime) {
        const idx = binarySearchAtOrAfter(entryTime);
        if (idx + 1 < fullData.length) exitTime = fullData[idx + 1].time;
      }

      const exitKey = `exit_${exitTime}_${t.id}`;
      if (!seenExit[exitKey]) {
        seenExit[exitKey] = true;
        fullBacktestExitMarkers.push({
          time: exitTime,
          position: isLong ? 'aboveBar' : 'belowBar',
          color: exitColor(t.exit_reason),
          shape: 'circle',
          text: (t.exit_reason || 'EXIT').toUpperCase(),
          size: 1,
        });
      }
    }
  }

  fullBacktestEntryMarkers.sort((a, b) => a.time - b.time);
  fullBacktestExitMarkers.sort((a, b) => a.time - b.time);
}

function markerIsInLoadedWindow(marker) {
  if (!loadedData.length) return false;
  const from = loadedData[0].time;
  const to = loadedData[loadedData.length - 1].time;
  return marker.time >= from && marker.time <= to;
}

function refreshBacktestMarkers() {
  _btEntrySeries.setMarkers(fullBacktestEntryMarkers.filter(markerIsInLoadedWindow));
  _btExitSeries.setMarkers(fullBacktestExitMarkers.filter(markerIsInLoadedWindow));
}

window.plotBacktestMarkers = function (trades) {
  buySignalSeries.setMarkers([]);
  sellSignalSeries.setMarkers([]);
  rebuildBacktestMarkerCache(trades);
  refreshBacktestMarkers();
};

window.clearBacktestMarkers = function () {
  fullBacktestTrades = [];
  fullBacktestEntryMarkers = [];
  fullBacktestExitMarkers = [];
  _btEntrySeries.setMarkers([]);
  _btExitSeries.setMarkers([]);
  refreshSignals();
};

/* ──────────────────────────────────────────────────────────────────
   RENDER LOADED WINDOW
────────────────────────────────────────────────────────────────── */
function renderLoadedWindow(preserveRange = null, fit = false) {
  loadedData = fullData.slice(loadedWindow.start, loadedWindow.end);

  candleSeries.setData(loadedData);
  volumeSeries.setData(volumeDataForLoadedWindow());

  scheduleIndicatorRender();
  refreshBacktestMarkers();

  const lastLoaded = loadedData[loadedData.length - 1] || fullData[fullData.length - 1];
  updateSidebar(lastLoaded);

  const last = fullData[fullData.length - 1] || null;
  const prev = fullData.length > 1 ? fullData[fullData.length - 2] : null;
  updateHeader(last, prev);

  const candlesEl = document.getElementById('statCandles');
  if (candlesEl) candlesEl.textContent = fullData.length.toLocaleString();

  requestAnimationFrame(() => {
    resizeAllCharts();

    if (fit) {
      suppressMainRangeHandler = true;
      mainChart.timeScale().fitContent();
      requestAnimationFrame(() => {
        suppressMainRangeHandler = false;
        const rng = loadedBounds();
        if (rng) {
          syncPaneRangesFromMain(rng);
          updateOscillatorReadoutsAtTime(rng.to);
        }
      });
    } else if (preserveRange) {
      applyMainRange(preserveRange);
      updateOscillatorReadoutsAtTime(preserveRange.to);
    } else {
      const rng = loadedBounds();
      if (rng) {
        syncPaneRangesFromMain(rng);
        updateOscillatorReadoutsAtTime(rng.to);
      }
    }
  });
}

function setLoadedWindow(start, end, preserveRange = null, fit = false) {
  const total = fullData.length;
  if (!total) return;

  let s = clamp(start, 0, total - 1);
  let e = clamp(end, s + 1, total);

  if (e <= s) e = Math.min(total, s + CHUNK_SIZE);
  if (s === loadedWindow.start && e === loadedWindow.end) return;

  loadedWindow = { start: s, end: e };
  renderLoadedWindow(preserveRange, fit);
}

/* ──────────────────────────────────────────────────────────────────
   WINDOWING / LAZY LOAD
────────────────────────────────────────────────────────────────── */
function initialWindowForDataset() {
  const total = fullData.length;
  const end = total;
  const start = Math.max(0, end - Math.min(INITIAL_WINDOW_BARS, total));
  return {
    start: alignDown(start, CHUNK_SIZE),
    end: total,
  };
}

function targetLoadedWindowFromVisibleRange(range) {
  if (!range || range.from == null || range.to == null || !fullData.length) return null;

  const visible = visibleIndexRangeFromTimeRange(range);
  if (!visible) return null;

  const visStart = visible.fromIdx;
  const visEndExclusive = visible.toIdx + 1;

  let desiredStart = Math.max(0, visStart - BUFFER_PADDING_BARS);
  let desiredEnd = Math.min(fullData.length, visEndExclusive + BUFFER_PADDING_BARS);

  desiredStart = alignDown(desiredStart, CHUNK_SIZE);
  desiredEnd = alignUp(desiredEnd, CHUNK_SIZE);
  if (desiredEnd > fullData.length) desiredEnd = fullData.length;
  if (desiredEnd <= desiredStart) desiredEnd = Math.min(fullData.length, desiredStart + CHUNK_SIZE);

  if (visStart < desiredStart) desiredStart = alignDown(visStart, CHUNK_SIZE);
  if (visEndExclusive > desiredEnd) {
    desiredEnd = alignUp(visEndExclusive, CHUNK_SIZE);
    if (desiredEnd > fullData.length) desiredEnd = fullData.length;
  }

  const loadedContainsVisible =
    visStart >= loadedWindow.start &&
    visEndExclusive <= loadedWindow.end;

  const enoughLeftBuffer = (visStart - loadedWindow.start) >= Math.floor(BUFFER_PADDING_BARS * 0.35);
  const enoughRightBuffer = (loadedWindow.end - visEndExclusive) >= Math.floor(BUFFER_PADDING_BARS * 0.35);

  const tooMuchFarLeft = (visStart - loadedWindow.start) > FAR_UNLOAD_PADDING_BARS;
  const tooMuchFarRight = (loadedWindow.end - visEndExclusive) > FAR_UNLOAD_PADDING_BARS;

  const needsShift =
    !loadedContainsVisible ||
    !enoughLeftBuffer ||
    !enoughRightBuffer ||
    tooMuchFarLeft ||
    tooMuchFarRight;

  if (!needsShift) return null;
  return { start: desiredStart, end: desiredEnd };
}

function handleMainVisibleRangeChange(range) {
  if (suppressMainRangeHandler || !range || range.from == null || range.to == null || !fullData.length) return;

  lastVisibleRange = range;
  syncPaneRangesFromMain(range);
  updateOscillatorReadoutsAtTime(range.to);

  if (rangeChangeTimer) clearTimeout(rangeChangeTimer);
  rangeChangeTimer = setTimeout(() => {
    rangeChangeTimer = null;
    const latestRange = lastVisibleRange;
    const target = targetLoadedWindowFromVisibleRange(latestRange);
    if (!target) return;
    setLoadedWindow(target.start, target.end, latestRange, false);
  }, RANGE_CHANGE_DEBOUNCE_MS);
}

mainChart.timeScale().subscribeVisibleTimeRangeChange(handleMainVisibleRangeChange);

/* ──────────────────────────────────────────────────────────────────
   LOAD DATASET
────────────────────────────────────────────────────────────────── */
async function loadRange(rangePt) {
  currentRange = rangePt;
  const url = RANGE_FILES[rangePt];
  if (!url) return;

  try {
    const resp = await fetch(url);
    const rawBars = await resp.json();

    fullData = normalizeBars(rawBars);
    datasetVersion += 1;
    rebuildSourceCache();
    invalidateIndicatorCache();

    const init = initialWindowForDataset();
    loadedWindow = { start: init.start, end: init.end };
    loadedData = fullData.slice(loadedWindow.start, loadedWindow.end);

    const lastBar = fullData[fullData.length - 1];
    const prevBar = fullData.length > 1 ? fullData[fullData.length - 2] : null;

    updateHeader(lastBar, prevBar);
    updateSidebar(lastBar);
    renderLoadedWindow(null, true);

    renderIndicatorChips();
    renderSignalUi();
    renderIndicatorEditors();
  } catch (err) {
    console.error('Load error:', err);
  }
}

/* ──────────────────────────────────────────────────────────────────
   MAIN CHART HOVER / CROSSHAIR
────────────────────────────────────────────────────────────────── */
mainChart.subscribeCrosshairMove(param => {
  const dp = param.seriesData ? param.seriesData.get(candleSeries) : null;
  if (dp && dp.open != null) updateSidebar(dp);

  updateSyncGuide(param);

  if (param && param.time != null) {
    updateOscillatorReadoutsAtTime(param.time);
  } else {
    updateOscillatorReadoutsAtTime(null);
  }
});

/* ──────────────────────────────────────────────────────────────────
   RANGE BUTTONS
────────────────────────────────────────────────────────────────── */
document.querySelectorAll('.interval-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.interval-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const pt = parseInt(btn.textContent, 10);
    loadRange(pt);
  });
});

/* ──────────────────────────────────────────────────────────────────
   RESIZE
────────────────────────────────────────────────────────────────── */
new ResizeObserver(() => {
  resizeAllCharts();
  const range = lastVisibleRange || loadedBounds();
  if (range) syncPaneRangesFromMain(range);
}).observe(rootContainer);

/* ──────────────────────────────────────────────────────────────────
   LIVE APPEND STUB
────────────────────────────────────────────────────────────────── */
function appendNewBar(bar) {
  const normalized = normalizeBars([
    ...(fullData.length ? [fullData[fullData.length - 1]] : []),
    bar,
  ]);
  const nextBar = normalized[normalized.length - 1];
  if (!nextBar) return;

  fullData.push(nextBar);
  sourceCache.close.push(nextBar.close);
  sourceCache.open.push(nextBar.open);
  sourceCache.high.push(nextBar.high);
  sourceCache.low.push(nextBar.low);

  datasetVersion += 1;
  invalidateIndicatorCache();

  const visible = lastVisibleRange || loadedBounds();
  const nearRightEdge = visible
    ? binarySearchAtOrBefore(visible.to) >= fullData.length - 10
    : true;

  if (nearRightEdge) {
    const init = initialWindowForDataset();
    setLoadedWindow(init.start, init.end, visible, false);
  } else {
    const last = fullData[fullData.length - 1];
    const prev = fullData.length > 1 ? fullData[fullData.length - 2] : null;
    updateHeader(last, prev);
  }
}

/* ──────────────────────────────────────────────────────────────────
   INIT
────────────────────────────────────────────────────────────────── */
renderSignalUi();
renderIndicatorEditors();
DEFAULT_IND