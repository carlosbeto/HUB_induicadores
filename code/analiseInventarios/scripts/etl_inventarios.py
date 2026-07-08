# code/analiseInventarios/scripts/etl_inventarios.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_step(step_name: str, script_path: Path) -> None:
    print()
    print("=" * 70)
    print(step_name)
    print("=" * 70)

    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.stdout:
        print(result.stdout.strip())

    if result.stderr:
        print(result.stderr.strip())

    if result.returncode != 0:
        raise SystemExit(
            f"[ERRO] Falha na etapa: {step_name} | retorno={result.returncode}"
        )


def main() -> None:
    scripts_dir = Path(__file__).resolve().parent

    steps = [
        ("[1/4] Carregando contagens MM/EWM (counts)...", scripts_dir / "01_load_excels.py"),
        ("[2/4] Carregando snapshot MM (mm_snapshot)...", scripts_dir / "02_load_mm_snapshot.py"),
        ("[3/4] Carregando baseline mensal (baseline_items)...", scripts_dir / "load_baseline_snapshot.py"),
        ("[4/4] Gerando KPIs / outputs...", scripts_dir / "03_build_kpis.py"),
    ]

    print("=" * 70)
    print("ETL INVENTARIOS - ORQUESTRADOR")
    print("=" * 70)

    for step_name, script_path in steps:
        if not script_path.exists():
            raise FileNotFoundError(f"Script não encontrado: {script_path}")
        run_step(step_name, script_path)

    print()
    print("=" * 70)
    print("ETL INVENTARIOS FINALIZADO COM SUCESSO")
    print("=" * 70)


if __name__ == "__main__":
    main()