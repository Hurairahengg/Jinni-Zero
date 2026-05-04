"""
JINNI ZERO — Strategy Loader (with lookback validation)
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from backend.strategies.base import BaseStrategy
import backend.strategies as strategies_pkg

logger = logging.getLogger("jinni.loader")


def _iter_strategy_modules():
    for mod in pkgutil.iter_modules(strategies_pkg.__path__):
        if mod.name in {"base"} or mod.name.startswith("_"):
            continue
        yield mod.name


def discover_strategies():
    registry = {}
    for module_name in _iter_strategy_modules():
        try:
            module = importlib.import_module(f"backend.strategies.{module_name}")
        except Exception as e:
            logger.error(f"Failed to import strategy module '{module_name}': {e}")
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseStrategy) or obj is BaseStrategy:
                continue
            try:
                instance = obj()
                strategy_id = instance.strategy_id or module_name
                instance.strategy_id = strategy_id
                registry[strategy_id] = instance
                logger.info(f"Loaded strategy: {strategy_id} (lookback={instance.min_lookback})")
            except Exception as e:
                logger.error(f"Failed to instantiate strategy from {module_name}: {e}")

    return registry


def get_strategy(strategy_id: str):
    registry = discover_strategies()
    if strategy_id not in registry:
        raise KeyError(f"Unknown strategy '{strategy_id}'")
    return registry[strategy_id]


def list_strategy_metadata():
    registry = discover_strategies()
    return [registry[k].get_metadata() for k in sorted(registry.keys())]


def validate_lookback(strategy, bar_count: int, lookback_override: int = 0):
    """Check if enough bars exist for strategy's lookback requirement."""
    required = max(
        getattr(strategy, "min_lookback", 0) or 0,
        lookback_override,
    )
    if bar_count < required:
        raise ValueError(
            f"Strategy '{strategy.strategy_id}' requires {required} bars lookback, "
            f"but only {bar_count} bars available."
        )
    return required