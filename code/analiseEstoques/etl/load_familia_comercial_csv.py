# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd


COLUNAS_ORIGEM = {
    "Cód. Material": "material",
    "Material": "descricao_material",
    "BU": "bu",
    "Diretoria": "diretoria",
    "Segmento": "segmento",
    "Centro Lucro": "centro_lucro",
    "Cód. Centro Lucro": "codigo_centro_lucro",
    "Cód. Centro": "codigo_centro_sap",
}


def _normalizar_material(valor) -> str:
    if valor is None or pd.isna(valor):
        return ""
    texto = str(valor).strip()
    return texto[:-2] if texto.endswith(".0") else texto


def _normalizar_texto(valor) -> str | None:
    if valor is None or pd.isna(valor):
        return None
    texto = " ".join(str(valor).strip().split())
    if not texto or texto == "-":
        return None
    return texto


def load_familia_comercial_csv(
    path: str | Path,
    delimiter: str = ";",
    encoding: str = "utf-8",
) -> pd.DataFrame:
    inicio = time.perf_counter()
    arquivo = Path(path)

    if not arquivo.exists():
        raise FileNotFoundError(
            f"CSV da Base Família Comercial não encontrado: {arquivo}"
        )

    df = pd.read_csv(
        arquivo,
        sep=delimiter,
        encoding=encoding,
        dtype="string",
        keep_default_na=False,
    )

    df.columns = [" ".join(str(col).strip().split()) for col in df.columns]

    faltantes = [c for c in COLUNAS_ORIGEM if c not in df.columns]
    if faltantes:
        raise ValueError(
            "CSV da Base Família Comercial com layout inesperado. "
            f"Colunas faltantes: {faltantes}. "
            f"Colunas encontradas: {list(df.columns)}"
        )

    out = df[list(COLUNAS_ORIGEM)].rename(columns=COLUNAS_ORIGEM).copy()
    out["material"] = out["material"].apply(_normalizar_material)

    for coluna in [
        "descricao_material",
        "bu",
        "diretoria",
        "segmento",
        "centro_lucro",
        "codigo_centro_lucro",
        "codigo_centro_sap",
    ]:
        out[coluna] = out[coluna].apply(_normalizar_texto)

    linhas_lidas = len(out)
    materiais_vazios = int((out["material"] == "").sum())
    out = out[out["material"] != ""].copy()

    duplicados = (
        out.loc[out["material"].duplicated(keep=False), "material"]
        .drop_duplicates()
        .sort_values()
        .tolist()
    )
    if duplicados:
        raise ValueError(
            "CSV da Base Família Comercial contém materiais duplicados. "
            f"Quantidade: {len(duplicados)}. Amostra: {duplicados[:20]}"
        )

    pendente = (
        out["bu"].isna()
        | out["diretoria"].isna()
        | out["segmento"].isna()
    )

    out["status_classificacao"] = "CLASSIFICADO"
    out.loc[pendente, "status_classificacao"] = "PENDENTE_RECLASSIFICACAO"

    out = out.sort_values("material").reset_index(drop=True)

    total = len(out)
    classificados = int((out["status_classificacao"] == "CLASSIFICADO").sum())
    pendentes = int((out["status_classificacao"] == "PENDENTE_RECLASSIFICACAO").sum())
    percentual_pendente = (pendentes / total * 100) if total else 0.0
    tempo = time.perf_counter() - inicio

    print("=" * 72)
    print("BASE FAMÍLIA COMERCIAL - CSV BI")
    print("=" * 72)
    print(f"Arquivo....................... {arquivo.name}")
    print(f"Linhas lidas.................. {linhas_lidas:,}".replace(",", "."))
    print(f"Materiais vazios removidos.... {materiais_vazios:,}".replace(",", "."))
    print(f"Materiais válidos............. {total:,}".replace(",", "."))
    print(f"Materiais únicos.............. {out['material'].nunique():,}".replace(",", "."))
    print(f"Classificados................. {classificados:,}".replace(",", "."))
    print(f"Pendentes de reclassificação.. {pendentes:,}".replace(",", "."))
    print(f"Percentual pendente........... {percentual_pendente:.2f}%")
    print(f"BUs distintas................. {out['bu'].nunique(dropna=True):,}".replace(",", "."))
    print(f"Diretorias distintas.......... {out['diretoria'].nunique(dropna=True):,}".replace(",", "."))
    print(f"Segmentos distintos........... {out['segmento'].nunique(dropna=True):,}".replace(",", "."))
    print(f"Tempo de leitura.............. {tempo:.2f} s")
    print("=" * 72)

    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--delimiter", default=";")
    parser.add_argument("--encoding", default="utf-8")
    args = parser.parse_args()

    df = load_familia_comercial_csv(
        path=args.file,
        delimiter=args.delimiter,
        encoding=args.encoding,
    )

    print()
    print("Hierarquia BU / Diretoria / Segmento:")
    print(
        df.groupby(["bu", "diretoria", "segmento"], dropna=False)
        .size()
        .reset_index(name="materiais")
        .sort_values(
            ["bu", "diretoria", "materiais"],
            ascending=[True, True, False],
        )
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
