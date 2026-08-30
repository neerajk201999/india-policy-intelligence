from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]


def load_config(path: Path) -> Dict[str, Any]:
    """Load JSON-compatible YAML without requiring PyYAML.

    JSON is a strict subset of YAML, so these remain ordinary editable YAML files.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing configuration file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON-compatible YAML in {path}: {exc}") from exc


def sources_config(path: Path = ROOT / "config" / "sources.yaml") -> Dict[str, Any]:
    return load_config(path)


def topics_config(path: Path = ROOT / "config" / "topics.yaml") -> Dict[str, Any]:
    return load_config(path)

