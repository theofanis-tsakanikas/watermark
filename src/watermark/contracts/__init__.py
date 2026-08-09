"""The contract layer: YAML data, validated on every load, imported by name nowhere.

An entity contract declares what a piece of reference data means, how its history is recorded,
whether it holds personal data and — if it does — the purpose it was collected for. Two of
those are enforced at load time rather than reviewed: see `model.py`.
"""

from __future__ import annotations

from watermark.contracts.decisions import DecisionContract, FallbackRule
from watermark.contracts.features import FeatureContract, Window
from watermark.contracts.loader import ContractError, ContractSet, load
from watermark.contracts.model import EntityContract, Reference, Scd2

__all__ = [
    "ContractError",
    "ContractSet",
    "DecisionContract",
    "EntityContract",
    "FallbackRule",
    "FeatureContract",
    "Reference",
    "Scd2",
    "Window",
    "load",
]
