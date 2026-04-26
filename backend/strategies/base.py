# backend/strategies/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy


class BaseStrategy(ABC):
    strategy_id = ""
    name = "Unnamed Strategy"
    description = ""
    parameters = {}
    indicators_required = []

    # Engine behavior defaults
    allow_stacking = False

    def get_metadata(self):
        return {
            "id": self.strategy_id,
            "name": self.name,
            "description": self.description,
            "parameters": deepcopy(self.parameters),
            "indicators_required": deepcopy(self.indicators_required),
            "allow_stacking": bool(self.allow_stacking),
        }

    def get_default_parameters(self):
        defaults = {}
        for key, spec in self.parameters.items():
            if spec.get("type") == "group":
                continue
            defaults[key] = spec.get("default")
        return defaults

    def validate_parameters(self, raw_params: dict | None):
        raw_params = raw_params or {}
        merged = self.get_default_parameters()

        for key, spec in self.parameters.items():
            if spec.get("type") == "group":
                continue

            value = raw_params.get(key, spec.get("default"))
            ptype = spec.get("type", "string")

            if ptype == "number":
                if value is None or value == "":
                    value = spec.get("default", 0)
                value = float(value)
                if "min" in spec:
                    value = max(float(spec["min"]), value)
                if "max" in spec:
                    value = min(float(spec["max"]), value)
                if spec.get("integer", False):
                    value = int(round(value))

            elif ptype == "boolean":
                value = bool(value)

            elif ptype == "enum":
                options = spec.get("options", [])
                if value not in options:
                    value = spec.get("default", options[0] if options else None)

            else:
                value = "" if value is None else str(value)

            merged[key] = value

        return merged

    def build_indicators_required(self, params: dict):
        resolved = []
        for item in self.indicators_required:
            row = deepcopy(item)
            if "period_param" in row:
                row["period"] = int(params[row["period_param"]])
            resolved.append(row)
        return resolved

    def engine_parameter_overrides(self, params: dict):
        """
        Optional hook.
        A strategy can override engine behavior defaults if needed.
        Example:
            return {
                "exits": {
                    "trailing": {"enabled": True, "mode": "ma", "ma_key": "fast_ema"}
                }
            }
        """
        return {}

    @abstractmethod
    def on_bar(self, i, bar, indicators, state, position, bars, params):
        raise NotImplementedError