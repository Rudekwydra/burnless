"""coreconfig — single source of truth for burnless tiers.

Public API. (Named `coreconfig`, not `config`, to avoid colliding with the
legacy config.py module; renamed to `config` once config.py is retired.)
"""
from __future__ import annotations

from .schema import DEFAULT_TIERS, TierDefinition
from .resolver import (
    default_config,
    load,
    normalize_model,
    resolve_keywords,
    resolve_model,
    resolve_priority,
    route,
)

__all__ = [
    "DEFAULT_TIERS",
    "TierDefinition",
    "default_config",
    "load",
    "normalize_model",
    "resolve_keywords",
    "resolve_model",
    "resolve_priority",
    "route",
]
