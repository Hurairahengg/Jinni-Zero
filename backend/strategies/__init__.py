# backend/strategy_loader.py
from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from typing import Dict, List

from backend.strategies.base import BaseStrategy
import backend.strategies as strategies_pkg


def _iter_strategy_module_names():
    """
    Iterate over python modules inside backend/strategies/
    while skipping base.py, __init__.py, and private files.
    """
    for mod in pkgutil.iter_modules(strategies_pkg.__path__):
        name = mod.name
        if name in {"base", "__init__"}:
            continue
        if name.startswith("_"):
            continue
        yield name


def _load_strategy_module(module_name: str):
    """
    Import (or reload) a strategy module.
    Reloading is helpful during development so edits are picked up.
    """
    full_name = f"backend.strategies.{module_name}"
    importlib.invalidate_caches()

    if full_name in sys.modules:
        return importlib.reload(sys.modules[full_name])

    return importlib.import_module(full_name)


def _extract_strategy_instances(module) -> List[BaseStrategy]:
    """
    Find all BaseStrategy subclasses inside a loaded module
    and return instantiated strategy objects.
    """
    found = []

    for _, obj in inspect.getmembers(module, inspect.isclass):
        # must be a real subclass of BaseStrategy, not BaseStrategy itself
        if not issubclass(obj, BaseStrategy):
            continue
        if obj is BaseStrategy:
            continue

        # avoid importing classes re-exported from other modules
        if obj.__module__ != module.__name__:
            continue

        instance = obj()

        if not getattr(instance, "strategy_id", ""):
            # fallback to module/class-derived id if missing
            instance.strategy_id = obj.__name__.replace("Strategy", "").lower()

        found.append(instance)

    return found


def discover_strategies() -> Dict[str, BaseStrategy]:
    """
    Discover all strategy plugins under backend/strategies/
    and return a registry: {strategy_id: strategy_instance}
    """
    registry: Dict[str, BaseStrategy] = {}

    for module_name in _iter_strategy_module_names():
        module = _load_strategy_module(module_name)
        instances = _extract_strategy_instances(module)

        for instance in instances:
            strategy_id = str(instance.strategy_id).strip()

            if not strategy_id:
                raise ValueError(
                    f"Strategy in module '{module_name}' is missing a valid strategy_id"
                )

            if strategy_id in registry:
                raise ValueError(
                    f"Duplicate strategy_id detected: '{strategy_id}' "
                    f"(module '{module_name}')"
                )

            registry[strategy_id] = instance

    return registry


def get_strategy(strategy_id: str) -> BaseStrategy:
    """
    Return one strategy instance by ID.
    """
    registry = discover_strategies()

    if strategy_id not in registry:
        available = ", ".join(sorted(registry.keys())) if registry else "(none found)"
        raise KeyError(
            f"Unknown strategy '{strategy_id}'. Available strategies: {available}"
        )

    return registry[strategy_id]


def list_strategy_metadata() -> List[dict]:
    """
    Return metadata for all discovered strategies.
    """
    registry = discover_strategies()

    out = []
    for strategy_id in sorted(registry.keys()):
        strategy = registry[strategy_id]
        meta = strategy.get_metadata()

        # ensure id is always consistent
        meta["id"] = strategy.strategy_id
        out.append(meta)

    return out