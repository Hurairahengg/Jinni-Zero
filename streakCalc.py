import json
import argparse
import matplotlib.pyplot as plt
import numpy as np

# =========================================================
# CLI
# =========================================================

parser = argparse.ArgumentParser()

# streak params
for i in range(2, 16):
    parser.add_argument(
        f"--{i}s",
        dest=f"streak_{i}",
        action="store_true"
    )

# tp params
for i in range(1, 4):
    parser.add_argument(
        f"--{i}tp",
        dest=f"tp_{i}",
        action="store_true"
    )

args = parser.parse_args()

# =========================================================
# CONFIG
# =========================================================

FILE_PATH = "data/NQ/8pt.json"

RISK_PER_TRADE = 10.0

FIXED_SL_POINTS = 14.0

COMMISSION_PER_LOT = 0.8

SLIPPAGE_POINTS = 0.3

# =========================================================
# PARAM SELECTION
# =========================================================

selected_streaks = []
selected_tps = []

for i in range(2, 16):
    if getattr(args, f"streak_{i}"):
        selected_streaks.append(i)

for i in range(1, 4):
    if getattr(args, f"tp_{i}"):
        selected_tps.append(i)

# fallback = all
if not selected_streaks:
    selected_streaks = list(range(2, 16))

if not selected_tps:
    selected_tps = [1, 2, 3]

# =========================================================
# LOAD DATA
# =========================================================

with open(FILE_PATH, "r") as f:
    data = json.load(f)

# =========================================================
# HELPERS
# =========================================================

def direction(candle):

    if candle["close"] > candle["open"]:
        return 1

    elif candle["close"] < candle["open"]:
        return -1

    return 0


directions = [direction(c) for c in data]

# =========================================================
# MAIN SIM
# =========================================================

for streak_size in selected_streaks:

    for tp_close_after in selected_tps:

        print("\n================================================")
        print(f"STREAK = {streak_size} | TP AFTER = {tp_close_after}")
        print("================================================")

        equity = [0]

        trade_returns = []

        stats = {

            "trades": 0,

            "wins": 0,
            "losses": 0,

            "gross_profit": 0.0,
            "gross_loss": 0.0,

            "gross_pnl_before_costs": 0.0,

            "net_profit": 0.0,

            "largest_win": 0.0,
            "largest_loss": 0.0,

            "total_commission": 0.0,
            "total_slippage_cost": 0.0,

            "total_lots": 0.0,

            "avg_trade": 0.0,
            "avg_lot_size": 0.0,

            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
        }

        i = streak_size

        while i < len(data) - (tp_close_after + 2):

            # =====================================================
            # STREAK CHECK
            # =====================================================

            streak_dir = directions[i - streak_size]

            if streak_dir == 0:
                i += 1
                continue

            valid = True

            for j in range(i - streak_size, i):

                if directions[j] != streak_dir:
                    valid = False
                    break

            if not valid:
                i += 1
                continue

            # =====================================================
            # REVERSAL CHECK
            # =====================================================

            reversal_dir = directions[i]

            if reversal_dir != -streak_dir:
                i += 1
                continue

            # =====================================================
            # ENTRY
            # =====================================================

            entry_candle = data[i + 1]

            raw_entry = entry_candle["open"]

            # entry slippage
            if reversal_dir == 1:
                entry_price = raw_entry + SLIPPAGE_POINTS
            else:
                entry_price = raw_entry - SLIPPAGE_POINTS

            # =====================================================
            # FIXED STOP
            # =====================================================

            if reversal_dir == 1:
                sl_price = entry_price - FIXED_SL_POINTS
            else:
                sl_price = entry_price + FIXED_SL_POINTS

            # =====================================================
            # LOT SIZE
            # =====================================================

            lots = RISK_PER_TRADE / FIXED_SL_POINTS

            # =====================================================
            # TAKE PROFIT
            # =====================================================

            tp_candle = data[i + 1 + tp_close_after]

            raw_tp = tp_candle["close"]

            # exit slippage
            if reversal_dir == 1:
                tp_price = raw_tp - SLIPPAGE_POINTS
            else:
                tp_price = raw_tp + SLIPPAGE_POINTS

            # =====================================================
            # STOP CHECK USING OPEN PRICES ONLY
            # =====================================================

            hit_sl = False

            for k in range(i + 2, i + 2 + tp_close_after):

                future_open = data[k]["open"]

                if reversal_dir == 1:

                    if future_open <= sl_price:
                        exit_price = sl_price
                        hit_sl = True
                        break

                else:

                    if future_open >= sl_price:
                        exit_price = sl_price
                        hit_sl = True
                        break

            if not hit_sl:
                exit_price = tp_price

            # =====================================================
            # PNL
            # =====================================================

            if reversal_dir == 1:
                pnl_points = exit_price - entry_price
            else:
                pnl_points = entry_price - exit_price

            gross_pnl = pnl_points * lots

            # =====================================================
            # COSTS
            # =====================================================

            commission = lots * COMMISSION_PER_LOT

            # approximate full slippage cost
            slippage_cost = (
                SLIPPAGE_POINTS * 2
            ) * lots

            # =====================================================
            # NET PNL
            # =====================================================

            # IMPORTANT:
            # NET PNL IS AFTER ALL COSTS
            net_pnl = (
                gross_pnl
                - commission
            )

            # =====================================================
            # EQUITY
            # =====================================================

            equity.append(
                equity[-1] + net_pnl
            )

            trade_returns.append(net_pnl)

            # =====================================================
            # STATS
            # =====================================================

            stats["trades"] += 1

            stats["gross_pnl_before_costs"] += gross_pnl

            stats["total_commission"] += commission
            stats["total_slippage_cost"] += slippage_cost

            stats["total_lots"] += lots

            stats["net_profit"] += net_pnl

            if net_pnl >= 0:

                stats["wins"] += 1

                stats["gross_profit"] += net_pnl

                stats["largest_win"] = max(
                    stats["largest_win"],
                    net_pnl
                )

            else:

                stats["losses"] += 1

                stats["gross_loss"] += abs(net_pnl)

                stats["largest_loss"] = min(
                    stats["largest_loss"],
                    net_pnl
                )

            i += 1

        # =====================================================
        # BASIC METRICS
        # =====================================================

        trades = stats["trades"]

        if trades > 0:

            winrate = (
                stats["wins"] / trades
            ) * 100

            expectancy = (
                stats["net_profit"] / trades
            )

            stats["avg_trade"] = (
                stats["net_profit"] / trades
            )

            stats["avg_lot_size"] = (
                stats["total_lots"] / trades
            )

        else:

            winrate = 0
            expectancy = 0

        # =====================================================
        # PROFIT FACTOR
        # =====================================================

        if stats["gross_loss"] > 0:

            pf = (
                stats["gross_profit"]
                / stats["gross_loss"]
            )

        else:
            pf = 0

        # =====================================================
        # DRAWDOWN
        # =====================================================

        peak = equity[0]

        max_dd = 0

        for val in equity:

            if val > peak:
                peak = val

            dd = peak - val

            if dd > max_dd:
                max_dd = dd

        # =====================================================
        # SHARPE-LIKE
        # =====================================================

        if len(trade_returns) > 1:

            mean_ret = np.mean(trade_returns)

            std_ret = np.std(trade_returns)

            if std_ret != 0:
                sharpe = mean_ret / std_ret
            else:
                sharpe = 0

        else:
            sharpe = 0

        # =====================================================
        # WIN / LOSS STREAKS
        # =====================================================

        max_wins = 0
        max_losses = 0

        current_wins = 0
        current_losses = 0

        for ret in trade_returns:

            if ret >= 0:

                current_wins += 1
                current_losses = 0

            else:

                current_losses += 1
                current_wins = 0

            max_wins = max(
                max_wins,
                current_wins
            )

            max_losses = max(
                max_losses,
                current_losses
            )

        stats["max_consecutive_wins"] = max_wins
        stats["max_consecutive_losses"] = max_losses

        # =====================================================
        # PRINT
        # =====================================================

        print(f"Trades              : {trades}")

        print(f"Wins                : {stats['wins']}")
        print(f"Losses              : {stats['losses']}")

        print(f"Winrate             : {winrate:.2f}%")

        print(f"PF                  : {pf:.2f}")

        print(f"Expectancy          : ${expectancy:.2f}")

        print(
            f"Gross PnL Before Fees : "
            f"${stats['gross_pnl_before_costs']:.2f}"
        )

        print(
            f"Total Commission    : "
            f"${stats['total_commission']:.2f}"
        )

        print(
            f"Total Slippage Cost : "
            f"${stats['total_slippage_cost']:.2f}"
        )

        print(
            f"Net Profit          : "
            f"${stats['net_profit']:.2f}"
        )

        print(
            f"Avg Trade           : "
            f"${stats['avg_trade']:.2f}"
        )

        print(
            f"Avg Lot Size        : "
            f"{stats['avg_lot_size']:.4f}"
        )

        print(
            f"Total Lots          : "
            f"{stats['total_lots']:.2f}"
        )

        print(
            f"Max Drawdown        : "
            f"${max_dd:.2f}"
        )

        print(
            f"Sharpe-like         : "
            f"{sharpe:.3f}"
        )

        print(
            f"Largest Win         : "
            f"${stats['largest_win']:.2f}"
        )

        print(
            f"Largest Loss        : "
            f"${stats['largest_loss']:.2f}"
        )

        print(
            f"Max Win Streak      : "
            f"{stats['max_consecutive_wins']}"
        )

        print(
            f"Max Loss Streak     : "
            f"{stats['max_consecutive_losses']}"
        )

        # =====================================================
        # EQUITY CURVE
        # =====================================================

        plt.figure(figsize=(12, 6))

        plt.plot(equity)

        plt.title(
            f"Equity Curve | "
            f"Streak={streak_size} | "
            f"TP={tp_close_after}"
        )

        plt.xlabel("Trades")

        plt.ylabel("Equity ($)")

        filename = (
            f"equity_s{streak_size}"
            f"_tp{tp_close_after}.png"
        )

        plt.savefig(filename)

        plt.close()

        print(f"\nSaved equity curve: {filename}")