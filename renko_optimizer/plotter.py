"""
Renko Optimizer — Results Visualizer
Reads results/ directory produced by main.py and renders professional charts.

Usage:
    python plotter.py
    python plotter.py --results results --top 10
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from matplotlib.widgets import Button

# ════════════════════════════ THEME ════════════════════════════
BG      = "#0d1117"
PANEL   = "#161b22"
PANEL2  = "#1c2128"
BORDER  = "#30363d"
TEXT    = "#e6edf3"
MUTED   = "#7d8590"
ACCENT  = "#58a6ff"
GREEN   = "#3fb950"
RED     = "#f85149"
YELLOW  = "#d29922"
PURPLE  = "#bc8cff"
CYAN    = "#39c5cf"

plt.rcParams.update({
    "figure.facecolor":  BG,
    "axes.facecolor":    PANEL,
    "axes.edgecolor":    BORDER,
    "axes.labelcolor":   TEXT,
    "axes.titlecolor":   TEXT,
    "axes.titleweight":  "bold",
    "axes.titlesize":    11,
    "xtick.color":       MUTED,
    "ytick.color":       MUTED,
    "grid.color":        BORDER,
    "grid.alpha":        0.35,
    "grid.linewidth":    0.4,
    "font.family":       "DejaVu Sans",
    "font.size":         9,
    "legend.facecolor":  PANEL2,
    "legend.edgecolor":  BORDER,
    "legend.labelcolor": TEXT,
    "text.color":        TEXT,
    "savefig.facecolor": BG,
    "savefig.dpi":       120,
})

# ════════════════════════════ LOADERS ════════════════════════════
def load_leaderboard(results_dir: Path):
    fp = results_dir / "leaderboard.csv"
    if not fp.exists(): return []
    with open(fp) as f:
        return list(csv.DictReader(f))

def load_heatmap(results_dir: Path):
    fp = results_dir / "heatmap_L_vs_K.csv"
    if not fp.exists(): return None, None, None
    rows = list(csv.reader(open(fp)))
    if not rows: return None, None, None
    Ks = [int(x) for x in rows[0][1:]]
    Ls, vals = [], []
    for r in rows[1:]:
        Ls.append(int(r[0]))
        vals.append([float(x) if x else np.nan for x in r[1:]])
    return np.array(Ls), np.array(Ks), np.array(vals)

def load_trades(trades_dir: Path):
    """Return dict[rule_name] -> list of trade dicts."""
    out = {}
    if not trades_dir.exists(): return out
    for fp in sorted(trades_dir.glob("*.csv")):
        with open(fp) as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            for k in ("gross_pts","net_usd","mae","mfe","entry_px","exit_px"):
                if k in r and r[k] != "": r[k] = float(r[k])
            for k in ("entry_idx","exit_idx","fold"):
                if k in r and r[k] != "": r[k] = int(r[k])
        out[fp.stem] = rows
    return out

def load_rules(rules_dir: Path):
    out = {}
    if not rules_dir.exists(): return out
    for fp in sorted(rules_dir.glob("*.json")):
        out[fp.stem] = json.loads(fp.read_text())
    return out

# ════════════════════════════ UTILS ════════════════════════════
def pattern_str(p):
    # accepts list[int] like [1,-1,1]  OR  string like "+-+"
    if isinstance(p, str):
        return "".join("▲" if ch == "+" else "▼" for ch in p)
    return "".join("▲" if int(x) == 1 else "▼" for x in p)

def fmt_money(x):
    try: x = float(x)
    except: return str(x)
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"

def color_for_value(v, good_thresh=0):
    try: v = float(v)
    except: return TEXT
    return GREEN if v > good_thresh else (RED if v < good_thresh else MUTED)

def style_ax(ax, title=None, xlabel=None, ylabel=None):
    if title:  ax.set_title(title, loc="left", pad=8)
    if xlabel: ax.set_xlabel(xlabel, color=MUTED, fontsize=8)
    if ylabel: ax.set_ylabel(ylabel, color=MUTED, fontsize=8)
    ax.grid(True, alpha=0.3)
    for s in ax.spines.values(): s.set_color(BORDER)
    ax.tick_params(labelsize=8)

def add_watermark(fig):
    fig.text(0.995, 0.005, "JINNI · Renko Optimizer",
             ha="right", va="bottom", color=MUTED, fontsize=7, alpha=0.6)

# ════════════════════════════ VIEW 1: OVERVIEW ════════════════════════════
def view_overview(leaderboard, heatmap, results_dir):
    if not leaderboard:
        print("  ! no leaderboard found"); return

    fig = plt.figure(figsize=(15, 9))
    fig.suptitle("📊  RENKO OPTIMIZER — OVERVIEW", color=TEXT,
                 fontsize=15, fontweight="bold", x=0.02, ha="left", y=0.97)
    gs = GridSpec(3, 4, figure=fig, hspace=0.55, wspace=0.35,
                  left=0.05, right=0.97, top=0.90, bottom=0.06)

    # KPI strip from #1 rule + aggregates
    top = leaderboard[0]
    n_rules     = len(leaderboard)
    total_trades = sum(int(r["trades"]) for r in leaderboard)
    avg_exp     = np.mean([float(r["expectancy"]) for r in leaderboard])
    avg_pf      = np.mean([float(r["profit_factor"]) for r in leaderboard
                           if r["profit_factor"] not in ("", "inf")])
    best_score  = float(top["score"])
    best_pat    = pattern_str(top["pattern"])

    kpis = [
        ("RULES",            f"{n_rules}",                ACCENT),
        ("TOTAL TRADES",     f"{total_trades:,}",         TEXT),
        ("AVG EXPECTANCY",   fmt_money(avg_exp),          color_for_value(avg_exp)),
        ("AVG PROFIT FACT",  f"{avg_pf:.2f}",             color_for_value(avg_pf, 1.0)),
        ("TOP SCORE",        f"{best_score:.3f}",         PURPLE),
        ("TOP PATTERN",      f"{best_pat} {top['side']}", CYAN),
    ]
    for i, (label, val, color) in enumerate(kpis):
        ax = fig.add_subplot(gs[0, i % 4]) if i < 4 else None
        if ax is None: continue
        ax.axis("off")
        ax.add_patch(mpatches.FancyBboxPatch(
            (0.02, 0.05), 0.96, 0.9, boxstyle="round,pad=0.02",
            facecolor=PANEL, edgecolor=BORDER, transform=ax.transAxes))
        ax.text(0.06, 0.68, label, color=MUTED, fontsize=8,
                fontweight="bold", transform=ax.transAxes)
        ax.text(0.06, 0.25, val, color=color, fontsize=15,
                fontweight="bold", transform=ax.transAxes)

    # top-10 score bar
    ax_bar = fig.add_subplot(gs[1, :2])
    top10 = leaderboard[:10]
    names = [f"{pattern_str(r['pattern'])} {r['side'][0]}|L{r['L']}K{r['K']}"
             for r in top10]
    scores = [float(r["score"]) for r in top10]
    cols   = [GREEN if s>0 else RED for s in scores]
    y = np.arange(len(top10))
    ax_bar.barh(y, scores, color=cols, edgecolor=PANEL)
    ax_bar.set_yticks(y); ax_bar.set_yticklabels(names, fontsize=8)
    ax_bar.invert_yaxis()
    style_ax(ax_bar, "TOP-10 RULES BY SCORE", xlabel="score")
    ax_bar.axvline(0, color=MUTED, linewidth=0.6)

    # expectancy vs PF scatter
    ax_sc = fig.add_subplot(gs[1, 2:])
    xs = [float(r["expectancy"])    for r in leaderboard]
    ys = [float(r["profit_factor"]) if r["profit_factor"] not in ("","inf")
          else 0 for r in leaderboard]
    sz = [max(20, int(r["trades"])/3) for r in leaderboard]
    cl = [GREEN if float(r["score"])>0 else RED for r in leaderboard]
    ax_sc.scatter(xs, ys, s=sz, c=cl, alpha=0.65, edgecolor=BORDER, linewidth=0.5)
    ax_sc.axhline(1, color=YELLOW, linestyle="--", linewidth=0.7, alpha=0.7)
    ax_sc.axvline(0, color=MUTED, linewidth=0.6)
    style_ax(ax_sc, "EXPECTANCY × PROFIT FACTOR  (bubble = trades)",
             xlabel="expectancy", ylabel="profit factor")

    # heatmap
    ax_hm = fig.add_subplot(gs[2, :2])
    Ls, Ks, V = heatmap
    if V is not None and V.size > 0:
        vmax = np.nanmax(np.abs(V))
        im = ax_hm.imshow(V, cmap="RdYlGn", aspect="auto",
                          vmin=-vmax, vmax=vmax, origin="lower")
        ax_hm.set_xticks(range(len(Ks))); ax_hm.set_xticklabels(Ks)
        ax_hm.set_yticks(range(len(Ls))); ax_hm.set_yticklabels(Ls)
        for i in range(V.shape[0]):
            for j in range(V.shape[1]):
                v = V[i, j]
                if not np.isnan(v):
                    ax_hm.text(j, i, f"{v:.2f}",
                               ha="center", va="center",
                               color=TEXT if abs(v)<vmax*0.6 else "white",
                               fontsize=7)
        cb = plt.colorbar(im, ax=ax_hm, fraction=0.035)
        cb.ax.tick_params(colors=MUTED, labelsize=7)
        cb.outline.set_edgecolor(BORDER)
    style_ax(ax_hm, "EXPECTANCY HEATMAP   L (rows) × K (cols)",
             xlabel="K (hold)", ylabel="L (pattern len)")

    # win-rate distribution
    ax_wr = fig.add_subplot(gs[2, 2])
    wrs = []
    for r in leaderboard:
        try:
            n = int(r["trades"]); 
            if n == 0: continue
            # win-rate not directly in leaderboard; derive from sign of expectancy not ideal
            # we just plot expectancy distribution here instead
            wrs.append(float(r["expectancy"]))
        except: pass
    if wrs:
        ax_wr.hist(wrs, bins=20, color=ACCENT, alpha=0.75, edgecolor=PANEL)
        ax_wr.axvline(0, color=MUTED, linewidth=0.6)
    style_ax(ax_wr, "EXPECTANCY DISTRIBUTION", xlabel="expectancy")

    # side distribution (long vs short)
    ax_sd = fig.add_subplot(gs[2, 3])
    longs  = sum(1 for r in leaderboard if r["side"]=="LONG")
    shorts = sum(1 for r in leaderboard if r["side"]=="SHORT")
    ax_sd.bar(["LONG","SHORT"], [longs, shorts],
              color=[GREEN, RED], edgecolor=PANEL)
    for i, v in enumerate([longs, shorts]):
        ax_sd.text(i, v, str(v), ha="center", va="bottom", fontsize=9, color=TEXT)
    style_ax(ax_sd, "LONG vs SHORT RULES")

    add_watermark(fig)
    out = results_dir / "plots" / "_overview.png"
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out); print(f"  ✓ {out}")
    return fig

# ════════════════════════════ VIEW 2: RULE DEEP-DIVE ════════════════════════════
def view_rule(rule_row, trades, rules_meta, results_dir, idx=0):
    name = rule_name_from_row(rule_row)
    rule_trades = trades.get(name, [])
    if not rule_trades:
        print(f"  ! no trades for {name}"); return None

    fig = plt.figure(figsize=(15, 9))
    title = (f"🎯  {pattern_str(rule_row['pattern'])} {rule_row['side']}   "
             f"L={rule_row['L']}  K={rule_row['K']}  SL={rule_row['sl_model']}")
    fig.suptitle(title, color=TEXT, fontsize=14, fontweight="bold",
                 x=0.02, ha="left", y=0.97)
    fig.text(0.02, 0.935,
             f"  trades={rule_row['trades']}  expectancy={fmt_money(rule_row['expectancy'])}  "
             f"PF={rule_row['profit_factor']}  sharpe={rule_row['sharpe']}  "
             f"net={fmt_money(rule_row['net_usd'])}",
             color=MUTED, fontsize=10, ha="left")

    gs = GridSpec(3, 3, figure=fig, hspace=0.55, wspace=0.35,
                  left=0.05, right=0.97, top=0.88, bottom=0.06)

    # equity curve (cumulative net USD)
    pnls = [t["net_usd"] for t in rule_trades]
    pts  = [t["gross_pts"] for t in rule_trades]
    eq_usd = np.cumsum(pnls)
    eq_pts = np.cumsum(pts)

    ax_eq = fig.add_subplot(gs[0, :])
    ax_eq.plot(eq_usd, color=ACCENT, linewidth=1.5)
    ax_eq.fill_between(range(len(eq_usd)), 0, eq_usd,
                       where=eq_usd>=0, color=ACCENT, alpha=0.18)
    ax_eq.fill_between(range(len(eq_usd)), 0, eq_usd,
                       where=eq_usd<0, color=RED, alpha=0.18)
    ax_eq.axhline(0, color=MUTED, linewidth=0.6, linestyle="--")
    style_ax(ax_eq, "EQUITY CURVE  (net USD, cumulative)",
             xlabel="trade #", ylabel="$")

    # drawdown
    ax_dd = fig.add_subplot(gs[1, :2])
    peak = np.maximum.accumulate(eq_usd)
    dd   = eq_usd - peak
    ax_dd.fill_between(range(len(dd)), 0, dd, color=RED, alpha=0.4)
    ax_dd.plot(dd, color=RED, linewidth=0.8)
    style_ax(ax_dd, "DRAWDOWN  ($ from peak)", xlabel="trade #", ylabel="$")

    # exit reasons pie
    ax_ex = fig.add_subplot(gs[1, 2])
    reasons = [t["reason"] for t in rule_trades]
    counts  = {r: reasons.count(r) for r in set(reasons)}
    colors_map = {"SL": RED, "TP": GREEN, "TIME": YELLOW,
                  "TRAIL": PURPLE, "OPP": CYAN}
    cs = [colors_map.get(k, MUTED) for k in counts.keys()]
    wedges, _, texts = ax_ex.pie(counts.values(), labels=counts.keys(),
                                 colors=cs, autopct="%1.0f%%",
                                 textprops={"color": TEXT, "fontsize": 9},
                                 wedgeprops={"edgecolor": PANEL, "linewidth": 1.5})
    ax_ex.set_title("EXIT REASONS", loc="left", pad=8)

    # PnL distribution
    ax_pn = fig.add_subplot(gs[2, 0])
    ax_pn.hist(pnls, bins=30, color=ACCENT, alpha=0.75, edgecolor=PANEL)
    ax_pn.axvline(0, color=MUTED, linewidth=0.6)
    ax_pn.axvline(np.mean(pnls), color=YELLOW, linewidth=1, linestyle="--",
                  label=f"μ={np.mean(pnls):.2f}")
    ax_pn.legend(loc="upper right", fontsize=8)
    style_ax(ax_pn, "PnL DISTRIBUTION  ($)", xlabel="net $")

    # MAE vs MFE scatter
    ax_mm = fig.add_subplot(gs[2, 1])
    maes = [t["mae"] for t in rule_trades]
    mfes = [t["mfe"] for t in rule_trades]
    cols = [GREEN if p>0 else RED for p in pnls]
    ax_mm.scatter(maes, mfes, c=cols, s=14, alpha=0.6, edgecolor="none")
    ax_mm.axhline(0, color=MUTED, linewidth=0.5)
    ax_mm.axvline(0, color=MUTED, linewidth=0.5)
    style_ax(ax_mm, "MAE × MFE  (per trade)", xlabel="MAE (pts)", ylabel="MFE (pts)")

    # rolling win-rate (window=20)
    ax_wr = fig.add_subplot(gs[2, 2])
    wins = np.array([1 if p>0 else 0 for p in pnls], dtype=float)
    w = min(20, max(5, len(wins)//5))
    if len(wins) >= w:
        roll = np.convolve(wins, np.ones(w)/w, mode="valid") * 100
        ax_wr.plot(roll, color=PURPLE, linewidth=1.2)
        ax_wr.axhline(50, color=MUTED, linewidth=0.6, linestyle="--")
        ax_wr.fill_between(range(len(roll)), 50, roll,
                           where=roll>=50, color=GREEN, alpha=0.15)
        ax_wr.fill_between(range(len(roll)), 50, roll,
                           where=roll<50, color=RED, alpha=0.15)
    style_ax(ax_wr, f"ROLLING WIN-RATE  (window={w})",
             xlabel="trade #", ylabel="%")

    add_watermark(fig)
    out = results_dir / "plots" / f"_deepdive_{idx+1:02d}_{name}.png"
    fig.savefig(out); print(f"  ✓ {out}")
    return fig

def rule_name_from_row(r):
    sl = r["sl_model"]
    if r.get("sl_param") not in (None, "", "None"):
        try:    sl = f"{sl}{float(r['sl_param']):g}"
        except: pass
    return f"L{r['L']}_K{r['K']}_{r['side']}_{sl}"

# ════════════════════════════ VIEW 3: LEADERBOARD TABLE ════════════════════════════
def view_leaderboard_table(leaderboard, results_dir, top=25):
    rows = leaderboard[:top]
    if not rows: return None
    fig = plt.figure(figsize=(15, max(5, 0.5 + 0.32 * len(rows))))
    fig.suptitle("📋  LEADERBOARD — TOP RULES", color=TEXT, fontsize=14,
                 fontweight="bold", x=0.02, ha="left", y=0.97)
    ax = fig.add_subplot(111); ax.axis("off")

    cols = ["#", "Pattern", "Side", "L", "K", "SL", "Trades",
            "Expectancy", "PF", "Sharpe", "Net $", "Stability", "Score"]
    cell_text, cell_colors = [], []
    for i, r in enumerate(rows):
        exp = float(r["expectancy"]); pf = r["profit_factor"]
        sc  = float(r["score"]); net = float(r["net_usd"])
        cell_text.append([
            f"{i+1}",
            pattern_str(r["pattern"]),
            r["side"], r["L"], r["K"], r["sl_model"],
            r["trades"],
            f"{exp:+.3f}",
            f"{pf}",
            r["sharpe"],
            fmt_money(net),
            r["stability"],
            f"{sc:+.3f}",
        ])
        row_color = (PANEL if i % 2 == 0 else PANEL2)
        cell_colors.append([row_color]*len(cols))

    table = ax.table(cellText=cell_text, colLabels=cols,
                     cellColours=cell_colors, loc="center",
                     cellLoc="center", colLoc="center")
    table.auto_set_font_size(False); table.set_fontsize(9)
    table.scale(1, 1.45)
    for (r_, c_), cell in table.get_celld().items():
        cell.set_edgecolor(BORDER); cell.set_linewidth(0.4)
        if r_ == 0:
            cell.set_facecolor(BORDER); cell.set_height(0.05)
            cell.set_text_props(color=TEXT, weight="bold", fontsize=9)
        else:
            cell.set_text_props(color=TEXT)
            # color expectancy / net / score columns
            if c_ in (7, 10, 12):
                val = cell.get_text().get_text().replace("$","").replace(",","")
                try:
                    v = float(val.replace("+",""))
                    cell.set_text_props(color=GREEN if v>0 else RED, weight="bold")
                except: pass

    add_watermark(fig)
    out = results_dir / "plots" / "_leaderboard.png"
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out); print(f"  ✓ {out}")
    return fig

# ════════════════════════════ VIEW 4: COMPARISON ════════════════════════════
def view_comparison(leaderboard, trades, results_dir, top=5):
    rows = leaderboard[:top]
    fig = plt.figure(figsize=(15, 8))
    fig.suptitle(f"⚖️  TOP-{top} EQUITY COMPARISON", color=TEXT,
                 fontsize=14, fontweight="bold", x=0.02, ha="left", y=0.97)
    gs = GridSpec(2, 2, figure=fig, hspace=0.45, wspace=0.3,
                  left=0.06, right=0.97, top=0.91, bottom=0.08)

    palette = [ACCENT, GREEN, YELLOW, PURPLE, CYAN, RED, "#ff7b72"]

    # equity curves
    ax_eq = fig.add_subplot(gs[0, :])
    for i, r in enumerate(rows):
        name = rule_name_from_row(r)
        ts = trades.get(name, [])
        if not ts: continue
        eq = np.cumsum([t["net_usd"] for t in ts])
        ax_eq.plot(eq, color=palette[i % len(palette)], linewidth=1.4,
                   label=f"{pattern_str(r['pattern'])} {r['side'][0]} L{r['L']}K{r['K']}")
    ax_eq.axhline(0, color=MUTED, linewidth=0.6, linestyle="--")
    ax_eq.legend(loc="upper left", fontsize=8, ncol=2)
    style_ax(ax_eq, "EQUITY CURVES OVERLAY  (net $)",
             xlabel="trade #", ylabel="$")

    # bar: net USD
    ax_b1 = fig.add_subplot(gs[1, 0])
    labels = [f"{pattern_str(r['pattern'])} {r['side'][0]}" for r in rows]
    nets   = [float(r["net_usd"]) for r in rows]
    cols   = [GREEN if v>=0 else RED for v in nets]
    ax_b1.bar(range(len(rows)), nets, color=cols, edgecolor=PANEL)
    ax_b1.set_xticks(range(len(rows)))
    ax_b1.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    for i, v in enumerate(nets):
        ax_b1.text(i, v, fmt_money(v), ha="center",
                   va="bottom" if v>=0 else "top", fontsize=8, color=TEXT)
    ax_b1.axhline(0, color=MUTED, linewidth=0.6)
    style_ax(ax_b1, "NET $ PER RULE")

    # bar: profit factor
    ax_b2 = fig.add_subplot(gs[1, 1])
    pfs = []
    for r in rows:
        try: pfs.append(float(r["profit_factor"]))
        except: pfs.append(0.0)
    ax_b2.bar(range(len(rows)), pfs,
              color=[GREEN if v>=1 else RED for v in pfs], edgecolor=PANEL)
    ax_b2.axhline(1, color=YELLOW, linewidth=0.6, linestyle="--")
    ax_b2.set_xticks(range(len(rows)))
    ax_b2.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    for i, v in enumerate(pfs):
        ax_b2.text(i, v, f"{v:.2f}", ha="center", va="bottom",
                   fontsize=8, color=TEXT)
    style_ax(ax_b2, "PROFIT FACTOR PER RULE")

    add_watermark(fig)
    out = results_dir / "plots" / "_comparison.png"
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out); print(f"  ✓ {out}")
    return fig

# ════════════════════════════ MAIN ════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results", help="results directory")
    ap.add_argument("--top", type=int, default=5,
                    help="number of rules to deep-dive / compare")
    ap.add_argument("--no-show", action="store_true",
                    help="save only, don't open windows")
    args = ap.parse_args()

    rdir = Path(args.results)
    if not rdir.exists():
        print(f"  ! results dir not found: {rdir}"); return

    print(f"\n══════ RENKO RESULTS VISUALIZER ══════")
    print(f"  reading: {rdir.resolve()}")

    leaderboard = load_leaderboard(rdir)
    heatmap     = load_heatmap(rdir)
    trades      = load_trades(rdir / "trades")
    rules_meta  = load_rules(rdir / "rules")

    print(f"  rules in leaderboard : {len(leaderboard)}")
    print(f"  rules with trade logs: {len(trades)}")
    print(f"  rule JSON definitions: {len(rules_meta)}")
    print()

    figs = []
    print("[plot] overview …")
    f = view_overview(leaderboard, heatmap, rdir);  figs.append(f) if f else None
    print("[plot] leaderboard table …")
    f = view_leaderboard_table(leaderboard, rdir, top=min(25, len(leaderboard)))
    figs.append(f) if f else None
    print("[plot] comparison …")
    f = view_comparison(leaderboard, trades, rdir,
                        top=min(args.top, len(leaderboard)))
    figs.append(f) if f else None

    print(f"[plot] deep-dives  (top {args.top}) …")
    for i, r in enumerate(leaderboard[:args.top]):
        f = view_rule(r, trades, rules_meta, rdir, idx=i)
        if f: figs.append(f)

    if args.no_show:
        for f in figs: plt.close(f)
        print(f"\n✓ saved → {rdir / 'plots'}")
    else:
        print(f"\n✓ saved → {rdir / 'plots'}")
        print("  showing windows — close all to exit.")
        plt.show()

if __name__ == "__main__":
    main()