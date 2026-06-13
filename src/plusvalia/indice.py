"""
CIMENTA · M3 Plusvalía · Índice de momentum por colonia

Combina las señales líderes de plusvalía en un índice 0–100 por colonia:
  · Seguridad (tendencia de delincuencia a la baja)   — crimen_por_colonia.csv
  · Crecimiento de comercio (% de comercios nuevos)   — comercios_por_colonia.csv
  · Vitalidad comercial (comercio de consumo)         — comercios_por_colonia.csv

Como los nombres de colonia difieren entre fuentes (DENUE: "DEL VALLE" vs FGJ:
"DEL VALLE CENTRO"), se unen por COLONIA BASE (sin sufijos NORTE/SUR/CENTRO…).

Honestidad: esto es un índice de INDICADORES LÍDERES, no un modelo supervisado de
apreciación (para eso falta histórico de precios por colonia, que se acumula con el
tiempo). Responde "¿esta colonia tiene momentum al alza?" con datos públicos.

Salida: data/processed/indice_plusvalia_colonia.csv

Uso:
    python -m src.plusvalia.indice
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
SUFIJOS = {"NORTE", "SUR", "ORIENTE", "PONIENTE", "CENTRO"}
W = {"seguridad": 0.40, "crecimiento": 0.35, "vitalidad": 0.25}


def colonia_base(col: str) -> str:
    words = str(col).split()
    while words and words[-1] in SUFIJOS:
        words.pop()
    return " ".join(words)


def _pct_rank(s: pd.Series) -> pd.Series:
    """Percentil 0–1; NaN → 0.5 (neutral)."""
    return s.rank(pct=True).fillna(0.5)


def main():
    crimen = pd.read_csv(PROC / "crimen_por_colonia.csv")
    com = pd.read_csv(PROC / "comercios_por_colonia.csv")
    for d in (crimen, com):
        d["base"] = d["colonia"].map(colonia_base)

    # re-agregar a (alcaldia, colonia base)
    cr = crimen.groupby(["alcaldia", "base"]).agg(
        c2022=("c2022", "sum"), c2024=("c2024", "sum")).reset_index()
    with np.errstate(divide="ignore", invalid="ignore"):
        cr["crimen_tendencia_pct"] = np.where(
            cr["c2022"] > 0, (cr["c2024"] - cr["c2022"]) / cr["c2022"] * 100, np.nan)

    co = com.groupby(["alcaldia", "base"]).agg(
        comercios_total=("comercios_total", "sum"),
        comercios_consumo=("comercios_consumo", "sum"),
        comercios_nuevos=("comercios_nuevos", "sum")).reset_index()
    co["comercios_nuevos_pct"] = (co["comercios_nuevos"] / co["comercios_total"] * 100).round(1)

    df = cr.merge(co, on=["alcaldia", "base"], how="outer")

    # señales (mayor = mejor para plusvalía)
    seguridad = _pct_rank(-df["crimen_tendencia_pct"])         # crimen cayendo → alto
    crecimiento = _pct_rank(df["comercios_nuevos_pct"])         # más comercio nuevo → alto
    vitalidad = _pct_rank(df["comercios_consumo"])             # más comercio de consumo → alto
    df["indice_momentum"] = (
        100 * (W["seguridad"] * seguridad + W["crecimiento"] * crecimiento + W["vitalidad"] * vitalidad)
    ).round(1)
    df = df.rename(columns={"base": "colonia"})

    # precio mediano por m² de la colonia (contexto, desde el set deduplicado)
    dd = PROC / "_unificado_dedup.csv"
    if dd.exists():
        p = pd.read_csv(dd, low_memory=False)
        p = p[p["operacion"] == "venta"].copy()
        p["precio"] = pd.to_numeric(p["precio"], errors="coerce")
        p["surface"] = pd.to_numeric(p["surface"], errors="coerce")
        p = p[(p["surface"] > 15) & (p["precio"] > 0)]
        p["ppm2"] = p["precio"] / p["surface"]
        p["alcaldia"] = p["municipio"].astype(str).str.normalize("NFKD").str.encode("ascii", "ignore").str.decode("ascii").str.upper().str.strip()
        p["colonia"] = p["colonia"].astype(str).str.normalize("NFKD").str.encode("ascii", "ignore").str.decode("ascii").str.upper().str.strip().map(colonia_base)
        pm = p.groupby(["alcaldia", "colonia"])["ppm2"].median().round(0).reset_index().rename(columns={"ppm2": "precio_m2_mediano"})
        df = df.merge(pm, on=["alcaldia", "colonia"], how="left")

    cols = ["alcaldia", "colonia", "indice_momentum", "crimen_tendencia_pct",
            "comercios_total", "comercios_consumo", "comercios_nuevos_pct"]
    if "precio_m2_mediano" in df.columns:
        cols.append("precio_m2_mediano")
    out = df[cols].sort_values("indice_momentum", ascending=False)
    out.to_csv(PROC / "indice_plusvalia_colonia.csv", index=False)

    print(f"Colonias con índice: {len(out):,}")
    print("\nTop 8 momentum (toda la ciudad):")
    print(out.head(8)[["alcaldia", "colonia", "indice_momentum", "crimen_tendencia_pct", "comercios_nuevos_pct"]].to_string(index=False))
    print("\nBenito Juárez (top 6):")
    print(out[out["alcaldia"] == "BENITO JUAREZ"].head(6)[["colonia", "indice_momentum", "crimen_tendencia_pct", "comercios_nuevos_pct", "precio_m2_mediano"]].to_string(index=False))
    print(f"\nSalida: {PROC/'indice_plusvalia_colonia.csv'}")


if __name__ == "__main__":
    main()
