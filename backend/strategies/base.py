# backend/strategies/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseStrategy(ABC):
    """
    JINNI ZERO — Strategy Base Class
    --------------------------------
    The STRATEGY is the brain.
    The ENGINE is a dumb broker simulator.

    A strategy:
      • decides WHEN to enter / exit
      • decides SL / TP placement and updates
      • decides position size
      • manages its own state
    """

    # ── REQUIRED METADATA ─────────────────────────────────────
    strategy_id: str = ""
    name: str = ""
    description: str = ""
    version: str = "1.0"

    # ==========================================================
    # METADATA / PARAMETERS
    # ==========================================================
    def get_metadata(self) -> Dict[str, Any]:
        """
        Returns strategy metadata and parameter schema.

        MUST return:
          {
            id: str,
            name: str,
            description: str,
            version: str,
            parameters: dict   # schema for THIS strategy only
          }
        """
        return {
            "id": self.strategy_id,
            "name": self.name or self.strategy_id,
            "description": self.description or "",
            "version": self.version,
            "parameters": self.get_parameter_schema(),
        }

    def get_parameter_schema(self) -> Dict[str, Any]:
        """
        Returns a schema dict defining strategy-specific parameters.

        Schema format is consumed directly by the frontend UI.
        Engine parameters MUST NOT appear here.

        Override in strategy if params are needed.
        """
        return getattr(self, 'parameters', {})

    def get_default_parameters(self) -> Dict[str, Any]:
        """
        Returns default values for strategy parameters.
        Keys must match get_parameter_schema().
        """
        schema = self.get_parameter_schema()
        defaults = {}
        for k, spec in schema.items():
            if isinstance(spec, dict) and 'default' in spec:
                defaults[k] = spec['default']
        return defaults

    def validate_parameters(self, raw_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate / clamp / coerce incoming parameters.

        Must return a CLEAN dict used by the engine.
        Default behavior: merge defaults with raw input.
        """
        params = dict(self.get_default_parameters())
        for k, v in (raw_params or {}).items():
            params[k] = v
        return params

    # ==========================================================
    # INDICATORS
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Return indicator specs to be precomputed by the engine.

        Each spec:
          {
            key: "ema_55",
            kind: "EMA" | "SMA" | "WMA" | "HMA" | "VWAP" | "CHOPPINESS" | etc,
            period: int,
            source: "close" | "open" | "high" | "low"
          }

        Override if indicators are needed.
        """
        return []

    # ==========================================================
    # LIFECYCLE HOOKS
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        """
        Called ONCE before the first bar.
        Strategy may initialize ctx.state here.
        """
        pass

    def on_end(self, ctx: Any) -> None:
        """
        Called ONCE after the final bar.
        """
        pass

    # ==========================================================
    # MAIN STRATEGY LOGIC
    # ==========================================================
    @abstractmethod
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        """
        Called once per bar.

        ctx provides:
          ctx.index
          ctx.bar
          ctx.bars
          ctx.indicators
          ctx.ind_series
          ctx.position
          ctx.balance
          ctx.equity
          ctx.trades
          ctx.params
          ctx.state   ← mutable dict, persists across bars

        Return one of:

        ENTER:
          {
            "enter": "long" | "short",
            "size": float | None,
            "stop_loss": float | None,
            "take_profit": float | None,
            "entry_price": float | None,
            "reason": str | None
          }

        EXIT:
          {
            "exit": True,
            "exit_price": float | None,
            "reason": str | None
          }

        UPDATE:
          {
            "update_sl": float | None,
            "update_tp": float | None
          }

        NOTHING:
          {} or None
        """
        raise NotImplementedError("Strategy must implement on_bar()")
