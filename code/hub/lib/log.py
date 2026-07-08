from __future__ import annotations

from datetime import datetime


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def print_step(msg: str) -> None:
    print(f"[{ts()}] {msg}")