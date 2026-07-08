from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


def load_config() -> Dict[str, Any]:
    """
    Carrega o config.json do HUB.

    Prioridade:
    1) variável de ambiente HUB_CONFIG (caminho absoluto/relativo)
    2) ./config.json (na raiz do HUB_indicadores)

    Retorna dict do JSON.
    """
    cfg_path_str = (os.getenv("HUB_CONFIG") or "").strip()
    if cfg_path_str:
        cfg_path = Path(cfg_path_str)
    else:
        cfg_path = Path.cwd() / "config.json"

    cfg_path = cfg_path.resolve()
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config não encontrado: {cfg_path}")

    return json.loads(cfg_path.read_text(encoding="utf-8"))


def resolve_path(base_dir: Path, p: str) -> Path:
    """
    Resolve paths do config:
    - se p for relativo: base_dir / p
    - se absoluto/UNC: usa direto
    """
    pp = Path(p)
    return (base_dir / pp).resolve() if not pp.is_absolute() else pp.resolve()


def get_section(cfg: Dict[str, Any], key: str) -> Dict[str, Any]:
    sec = cfg.get(key, {})
    if not isinstance(sec, dict):
        raise ValueError(f"Config inválido: '{key}' deve ser objeto JSON.")
    return sec