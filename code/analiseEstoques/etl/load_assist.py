# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import unicodedata
import pandas as pd

from analiseEstoques.services.assist_db_service import conectar_assist


SQL_PROCESSAMENTO_ASSIST = """
SELECT
    TB_Estoque.estoque_codigo AS material,
    TB_Estoque.estoque_descricaoProcessamento AS processamento
FROM TB_Estoque WITH (NOLOCK)
WHERE TB_Estoque.estoque_descricaoProcessamento IS NOT NULL;
"""


MAPA_PROCESSAMENTO = {
    "DESCARTE": "DESCARTE",
    "DESCARTE DEFEITUOSO": "DESCARTE DEFEITUOSO",
    "TRATATIVA DIFERENCIADA": "TRATATIVA DIFERENCIADA",
    "TESTE CONSERTO": "TESTE CONSERTO",
    "REPARO": "REPARO",
    "CANIBALIZACAO": "CANIBALIZAÇÃO",
    "TESTE": "TESTE",
}


def _normalizar_material(valor) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip()
    return texto[:-2] if texto.endswith(".0") else texto


def _corrigir_mojibake(texto: str) -> str:
    """
    Corrige textos com codificação quebrada.

    Exemplo real do ASSIST:
    CANIBALIZAÃ‡ÃƒO -> CANIBALIZAÇÃO

    Tenta primeiro CP1252, porque o caractere "‡" não existe em latin-1.
    Depois tenta latin-1 como fallback.
    """
    atual = texto

    for _ in range(3):
        corrigiu = False

        for encoding in ("cp1252", "latin1"):
            try:
                candidato = atual.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue

            if candidato != atual:
                atual = candidato
                corrigiu = True
                break

        if not corrigiu:
            break

    return atual


def _chave_processamento(valor) -> str:
    if valor is None or pd.isna(valor):
        return ""

    texto = _corrigir_mojibake(str(valor).strip())

    texto_sem_acento = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(caractere)
    )

    return " ".join(texto_sem_acento.upper().split())


def normalizar_processamento(valor) -> str | None:
    chave = _chave_processamento(valor)
    if not chave:
        return None
    return MAPA_PROCESSAMENTO.get(chave)


def load_processamento_assist() -> pd.DataFrame:
    inicio = time.perf_counter()

    with conectar_assist() as conn:
        cursor = conn.cursor()
        cursor.execute(SQL_PROCESSAMENTO_ASSIST)
        colunas = [coluna[0] for coluna in cursor.description]
        linhas = cursor.fetchall()

    df = pd.DataFrame.from_records(linhas, columns=colunas)
    df.columns = [str(col).strip().lower() for col in df.columns]

    esperadas = {"material", "processamento"}
    faltantes = esperadas.difference(df.columns)

    if faltantes:
        raise ValueError(
            "Consulta ASSIST retornou layout inesperado. "
            f"Colunas faltantes: {sorted(faltantes)}. "
            f"Colunas encontradas: {list(df.columns)}"
        )

    df["material"] = df["material"].apply(_normalizar_material)
    df["processamento_origem"] = df["processamento"].astype("string").str.strip()

    df = df[df["material"] != ""].copy()
    df = df[df["processamento_origem"].notna()].copy()
    df = df[df["processamento_origem"] != ""].copy()

    df["processamento"] = df["processamento_origem"].apply(normalizar_processamento)

    nao_mapeados = sorted(
        df.loc[df["processamento"].isna(), "processamento_origem"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    if nao_mapeados:
        raise ValueError(
            "ASSIST retornou processamentos ainda não mapeados: "
            f"{nao_mapeados}"
        )

    duplicados = int(df["material"].duplicated(keep=False).sum())

    df = (
        df.drop_duplicates(subset=["material"], keep="last")
        .sort_values("material")
        .reset_index(drop=True)
    )

    tempo = time.perf_counter() - inicio

    print("=" * 64)
    print("ASSIST - PROCESSAMENTO DE MATERIAIS")
    print("=" * 64)
    print(f"Linhas carregadas............. {len(df):,}".replace(",", "."))
    print(f"Materiais únicos.............. {df['material'].nunique():,}".replace(",", "."))
    print(f"Duplicidades encontradas...... {duplicados:,}".replace(",", "."))
    print(f"Categorias normalizadas....... {df['processamento'].nunique():,}".replace(",", "."))
    print(f"Tempo de consulta.............. {tempo:.2f} s".replace(".", ",", 1))
    print("=" * 64)

    return df[["material", "processamento_origem", "processamento"]]


def main() -> None:
    df = load_processamento_assist()

    print()
    print("Distribuição normalizada:")
    print(df["processamento"].value_counts().to_string())

    print()
    print("Amostra:")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
