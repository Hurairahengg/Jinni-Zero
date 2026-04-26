# backend/strategy_loader.py
from __future__ import annotations

import importlib
import inspect
import pkgutil

from backend.strategies.base import BaseStrategy
import backend.strategies as strategies_pkg


def _iter_strategy_modules():
    for mod in pkgutil.iter_modules(strategies_pkg.__path__):
        if mod.name in {"base"} or mod.name.startswith("_"):
            continue
        yield mod.name


def discover_strategies():
    registry = {}

    for module_name in _iter_strategy_modules():
        module = importlib.import_module(f"backend.strategies.{module_name}")

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseStrategy) or obj is BaseStrategy:
                continue

            instance = obj()
            strategy_id = instance.strategy_id or module_name
            instance.strategy_id = strategy_id
            registry[strategy_id] = instance

    return registry


def get_strategy(strategy_id: str):
    registry = discover_strategies()
    if strategy_id not in registry:
        raise KeyError(f"Unknown strategy '{strategy_id}'")
    return registry[strategy_id]


def list_strategy_metadata():
    registry = discover_strategies()
    return [registry[k].get_metadata() for k in sorted(registry.keys())]