from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def load_pairs(path: str | Path | None = None) -> list[dict]:
    with open(path or CONFIG_DIR / "pairs.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["pairs"]


def load_strategy(path: str | Path | None = None) -> dict:
    with open(path or CONFIG_DIR / "strategy.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)
