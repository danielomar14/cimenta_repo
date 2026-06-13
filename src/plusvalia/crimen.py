"""
CIMENTA · M3 Plusvalía · Delincuencia por colonia (Carpetas de investigación FGJ CDMX)

Fuente: Portal de Datos Abiertos CDMX — Fiscalía General de Justicia.
  https://datos.cdmx.gob.mx/dataset/carpetas-de-investigacion-fgj-de-la-ciudad-de-mexico
  CSV por año: https://archivo.datos.cdmx.gob.mx/FGJ/carpetas/carpetasFGJ_<año>.csv
  (el host tiene el certificado SSL expirado → se descarga con verify=False)

Construye, por colonia × alcaldía:
  · conteo de carpetas por año (excluyendo "HECHO NO DELICTIVO")
  · tasa anualizada (normaliza años parciales por meses presentes)
  · tendencia de delincuencia (% cambio entre el primer y último año)
Señal líder de plusvalía: menos delincuencia y/o tendencia a la baja → colonia mejora.

Salida: data/processed/crimen_por_colonia.csv

Uso:
    python -m src.plusvalia.crimen
"""
from __future__ import annotations

import unicodedata
from functools import reduce
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "crimen"
PROC = ROOT / "data" / "processed"
URL = "https://archivo.datos.cdmx.gob.mx/FGJ/carpetas/carpetasFGJ_{}.csv"
YEARS = [2022, 2023, 2024]
USECOLS = ["anio_hecho", "mes_hecho", "categoria_delito", "colonia_catalogo", "alcaldia_catalogo"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/124 Safari/537.36"
# delitos de alto impacto (subconjunto para una señal más fina)
ALTO_IMPACTO = ("ROBO", "VIOLAC", "HOMICIDIO", "LESIONES", "SECUESTRO", "EXTORSION")


def _norm(s) -> str:
    if pd.isna(s):
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


def download(year: int) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    f = RAW / f"carpetasFGJ_{year}.csv"
    if f.exists() and f.stat().st_size > 1_000_000:
        return f
    print(f"  descargando {year}...", flush=True)
    with requests.get(URL.format(year), stream=True, verify=False, timeout=180,
                      headers={"User-Agent": UA}) as r:
        r.raise_for_status()
        with f.open("wb") as out:
            for chunk in r.iter_content(1 << 20):
                out.write(chunk)
    print(f"    {f.stat().st_size/1e6:.0f} MB", flush=True)
    return f


def aggregate() -> pd.DataFrame:
    parts, months = [], {}
    for y in YEARS:
        f = download(y)
        df = pd.read_csv(f, usecols=USECOLS, low_memory=False)
        cat = df["categoria_delito"].astype(str).str.upper()
        df = df[cat != "HECHO NO DELICTIVO"].copy()
        df["cat"] = cat[cat != "HECHO NO DELICTIVO"]
        mser = pd.to_numeric(df["mes_hecho"].map(_mes_num), errors="coerce")
        months[y] = int(mser.max()) if mser.notna().any() else 12
        df["alc"] = df["alcaldia_catalogo"].map(_norm)
        df["col"] = df["colonia_catalogo"].map(_norm)
        df = df[(df["col"] != "") & (df["alc"] != "")]
        tot = df.groupby(["alc", "col"]).size().rename(f"c{y}")
        hi = df[df["cat"].str.contains("|".join(ALTO_IMPACTO), na=False)] \
            .groupby(["alc", "col"]).size().rename(f"hi{y}")
        parts.append(pd.concat([tot, hi], axis=1).reset_index())
    m = reduce(lambda a, b: a.merge(b, on=["alc", "col"], how="outer"), parts).fillna(0)

    # tasa anualizada (corrige años parciales)
    for y in YEARS:
        m[f"r{y}"] = (m[f"c{y}"] / months[y] * 12).round(1)
    first, last = YEARS[0], YEARS[-1]
    rf, rl = m[f"r{first}"].to_numpy(float), m[f"r{last}"].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        trend = np.where(rf > 0, (rl - rf) / rf * 100, np.nan)
    m["crimen_anual"] = m[f"r{last}"]
    m["crimen_alto_impacto"] = m[f"hi{last}"]
    m["crimen_tendencia_pct"] = np.round(trend, 1)
    print(f"\n  meses por año (para anualizar): {months}")
    return m


def main():
    PROC.mkdir(parents=True, exist_ok=True)
    m = aggregate().rename(columns={"alc": "alcaldia", "col": "colonia"})
    out_cols = ["alcaldia", "colonia"] + [f"c{y}" for y in YEARS] + \
        ["crimen_anual", "crimen_alto_impacto", "crimen_tendencia_pct"]
    out = PROC / "crimen_por_colonia.csv"
    m[out_cols].sort_values("crimen_anual", ascending=False).to_csv(out, index=False)
    print(f"\nColonias con datos de crimen: {len(m):,}")
    print(f"Tendencia mediana de delincuencia {YEARS[0]}→{YEARS[-1]}: {m['crimen_tendencia_pct'].median():.1f}%")
    print(f"Salida: {out}")


def _mes_num(m):
    meses = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
             "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
    return meses.get(str(m).strip().lower())


if __name__ == "__main__":
    main()
