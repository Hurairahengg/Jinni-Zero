from dataclasses import dataclass, field, asdict
from typing import List, Optional

@dataclass
class Config:
    # ══════════════════════════════════════════════════════════
    #   👇 THE ONLY KNOBS YOU TOUCH 👇
    # ══════════════════════════════════════════════════════════
    data_path: str        = "data/NQ/18pt.json"
    min_occurrences: int  = 3_000        # pattern must fire at least this many times
    min_equity_usd: float = 100_000.0    # rule must clear this in net $ across folds
    out_dir: str          = "results"

    # ══════════════════════════════════════════════════════════
    #   defaults below — leave alone unless you know why
    # ══════════════════════════════════════════════════════════

    # data
    brick_size: float     = 18.0
    has_timestamps: bool  = False

    # mining
    L_min: int = 2
    L_max: int = 6
    K_max: int = 6
    min_edge: float = 0.0                # no edge filter — we filter on net $ instead

    # features (kept for future use; don't affect mining)
    use_run_len: bool     = True
    use_brick_speed: bool = False
    use_efficiency: bool  = True
    use_entropy: bool     = True
    use_flips: bool       = True
    feat_window: int      = 10

    # execution model
    slippage_pts: float   = 0.25
    commission_usd: float = 0.35
    dollar_per_pt: float  = 1.0
    allow_overlap: bool   = False

    # SL / exits
    sl_models: List[str]  = field(default_factory=lambda:
        ["fixed", "entry_bar", "swing", "time"])
    sl_fixed_min: float   = 3.0
    sl_fixed_max: float   = 30.0
    sl_iterations: int    = 6
    sl_fixed_pts: float   = 6.0          # fallback
    swing_lookback: int   = 30
    use_trailing: bool    = False
    take_profit_pts: Optional[float] = None

    # optimizer
    objective: str        = "net_profit" # rank by raw $ since that's what we target
    top_n_rules: int      = 50
    score_weights: dict   = field(default_factory=lambda:
        {"expectancy": 0.35, "pf": 0.25, "sharpe": 0.10, "stability": 0.30})

    # validation
    split_mode: str       = "walk_forward"
    train_frac: float     = 0.7
    wf_train_bars: int    = 45_000
    wf_test_bars: int     = 19_000
    wf_step: int          = 19_000
    monte_carlo_runs: int = 500
    mc_block_size: int    = 50

    def sl_grid(self):
        if self.sl_iterations <= 1: return [self.sl_fixed_pts]
        if self.sl_iterations == 2: return [self.sl_fixed_min, self.sl_fixed_max]
        step = (self.sl_fixed_max - self.sl_fixed_min) / (self.sl_iterations - 1)
        return [round(self.sl_fixed_min + i * step, 2)
                for i in range(self.sl_iterations)]

    def to_dict(self):
        return asdict(self)