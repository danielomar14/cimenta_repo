"""
CIMENTA · M2 · Features catastrales por propiedad (join espacial)

Para cada propiedad (lat/lng) busca los predios catastrales más cercanos y toma
el valor oficial del suelo, el año de construcción y la intensidad constructiva
locales. Da features per-propiedad (no solo por colonia) para enriquecer el AVM:
  · cat_valor_suelo  — valor unitario de suelo $/m² (mediana de los 5 más cercanos)
  · cat_anio         — año de construcción (mediana local)
  · cat_intensidad   — superficie construida / terreno (mediana local)
  · cat_dist_km      — distancia al predio más cercano (control de calidad)

Entrada: data/raw/catastro/*.csv  (descargado por src/plusvalia/catastro.py)
         data/processed/_unificado_dedup.csv  (universo de propiedades)
Salida:  data/processed/catastro_features.csv  (id → features)

Uso:
    python -m src.valuacion.catastro_features
"""
from __future__ import annotations

import glob
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "catastro"
PROC = ROOT / "data" / "processed"
COLS = ["latitud", "longitud", "valor_unitario_suelo", "anio_construccion",
        "superficie_construccion", "superficie_terreno"]
K = 5


def load_catastro() -> pd.DataFrame:
    parts = []
    for f in sorted(glob.glob(str(RAW / "*.csv"))):
        parts.append(pd.read_csv(f, usecols=COLS, low_memory=False))
    c = pd.concat(parts, ignore_index=True)
    for col in COLS:
        c[col] = pd.to_numeric(c[col], errors="coerce")
    c = c.dropna(subset=["latitud", "longitud", "valor_unitario_suelo"])
    c = c[c["latitud"].between(18.9, 20.1) & c["longitud"].between(-99.6, -98.6)]
    return c.reset_index(drop=True)


def main():
    c = load_catastro()
    print(f"Predios con coords: {len(c):,}")
    tree = BallTree(np.radians(c[["latitud", "longitud"]].to_numpy()), metric="haversine")

    props = pd.read_csv(PROC / "_unificado_dedup.csv", low_memory=False)
    props["lat"] = pd.to_numeric(props["lat"], errors="coerce")
    props["lng"] = pd.to_numeric(props["lng"], errors="coerce")
    p = props.dropna(subset=["lat", "lng"])
    p = p[p["lat"].between(18.9, 20.1) & p["lng"].between(-99.6, -98.6)]
    print(f"Propiedades a enriquecer: {len(p):,}")

    dist, idx = tree.query(np.radians(p[["lat", "lng"]].to_numpy()), k=K)
    valor = c["valor_unitario_suelo"].to_numpy()
    anio = c["anio_construccion"].to_numpy()
    anio = np.where((anio >= 1900) & (anio <= 2026), anio, np.nan)
    st = c["superficie_terreno"].to_numpy()
    sc = c["superficie_construccion"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        inten = np.where(st > 0, sc / st, np.nan)

    feat = pd.DataFrame({
        "id": p["id"].to_numpy(),
        "cat_valor_suelo": np.nanmedian(valor[idx], axis=1).round(0),
        "cat_anio": np.nanmedian(anio[idx], axis=1).round(0),
        "cat_intensidad": np.round(np.nanmedian(inten[idx], axis=1), 2),
        "cat_dist_km": (dist[:, 0] * 6371).round(3),
    })
    feat.to_csv(PROC / "catastro_features.csv", index=False)
    print(f"valor suelo $/m² (mediana): ${np.nanmedian(feat['cat_valor_suelo']):,.0f}")
    print(f"año construcción (mediana): {np.nanmedian(feat['cat_anio']):.0f}")
    print(f"dist al predio más cercano (mediana): {feat['cat_dist_km'].median()*1000:.0f} m")
    print(f"Salida: {PROC/'catastro_features.csv'}")


if __name__ == "__main__":
    main()
