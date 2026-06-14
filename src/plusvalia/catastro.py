"""
CIMENTA · M3 Plusvalía / M4 · Catastro por colonia (Información Catastral CDMX)

Fuente: Portal de Datos Abiertos CDMX — Información Catastral (un CSV por alcaldía).
  https://datos.cdmx.gob.mx/dataset/informacion-catastral-de-la-ciudad-de-mexico

Los permisos de construcción no son descarga abierta (viven en la Ventanilla Única
de SEDUVI, con login). El catastro es el mejor sustituto público: trae, por predio,
el AÑO DE CONSTRUCCIÓN (→ obra nueva), el VALOR OFICIAL DEL SUELO ($/m²) y la
intensidad constructiva. Señal directa de plusvalía + ancla de valuación.

Construye, por colonia × alcaldía:
  · valor_suelo_m2        — valor unitario de suelo mediano (oficial)
  · anio_construccion_med — antigüedad mediana del parque
  · pct_construccion_reciente — % de predios construidos desde 2010 (OBRA NUEVA)
  · intensidad_construccion   — superficie construida / terreno (mediana)
  · n_predios

Salida: data/processed/catastro_por_colonia.csv

Uso:
    python -m src.plusvalia.catastro
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "catastro"
PROC = ROOT / "data" / "processed"
META = "https://datos.cdmx.gob.mx/api/3/action/package_show?id=informacion-catastral-de-la-ciudad-de-mexico"
USECOLS = ["superficie_terreno", "superficie_construccion", "anio_construccion",
           "valor_unitario_suelo", "uso_construccion", "colonia", "alcaldia"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/124 Safari/537.36"


def _norm(s) -> str:
    if pd.isna(s):
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


def resource_urls() -> list[tuple[str, str]]:
    r = requests.get(META, verify=False, timeout=60, headers={"User-Agent": UA})
    res = r.json()["result"]["resources"]
    return [(x["name"], x["url"]) for x in res if str(x.get("format", "")).lower() == "csv"]


def load_all() -> pd.DataFrame:
    RAW.mkdir(parents=True, exist_ok=True)
    parts = []
    for name, url in resource_urls():
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        f = RAW / slug
        if not f.exists() or f.stat().st_size < 100_000:
            print(f"  descargando {slug}...", flush=True)
            with requests.get(url, stream=True, verify=False, timeout=240,
                              headers={"User-Agent": UA}) as resp:
                resp.raise_for_status()
                with f.open("wb") as out:
                    for chunk in resp.iter_content(1 << 20):
                        out.write(chunk)
        try:
            parts.append(pd.read_csv(f, usecols=USECOLS, low_memory=False))
        except Exception as e:
            print(f"    ⚠ {slug}: {e}")
    return pd.concat(parts, ignore_index=True)


def main():
    PROC.mkdir(parents=True, exist_ok=True)
    df = load_all()
    print(f"Predios catastrales: {len(df):,}")
    for c in ["superficie_terreno", "superficie_construccion", "anio_construccion", "valor_unitario_suelo"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["alc"] = df["alcaldia"].map(_norm)
    df["col"] = df["colonia"].map(_norm)
    df = df[(df["alc"] != "") & (df["col"] != "")]
    df["reciente"] = (df["anio_construccion"] >= 2010).astype(int)
    df["intensidad"] = np.where(df["superficie_terreno"] > 0,
                                df["superficie_construccion"] / df["superficie_terreno"], np.nan)
    # años plausibles para la mediana de antigüedad
    anio_ok = df["anio_construccion"].where(df["anio_construccion"].between(1900, 2026))

    g = df.groupby(["alc", "col"])
    out = pd.DataFrame({
        "n_predios": g.size(),
        "valor_suelo_m2": g["valor_unitario_suelo"].median().round(0),
        "anio_construccion_med": anio_ok.groupby([df["alc"], df["col"]]).median().round(0),
        "pct_construccion_reciente": (g["reciente"].mean() * 100).round(1),
        "intensidad_construccion": g["intensidad"].median().round(2),
    }).reset_index().rename(columns={"alc": "alcaldia", "col": "colonia"})
    out = out[out["n_predios"] >= 10]
    out.sort_values("valor_suelo_m2", ascending=False).to_csv(PROC / "catastro_por_colonia.csv", index=False)

    print(f"Colonias con catastro: {len(out):,}")
    print(f"Valor de suelo $/m² mediano (ciudad): ${out['valor_suelo_m2'].median():,.0f}")
    print(f"% construcción reciente (mediana): {out['pct_construccion_reciente'].median():.1f}%")
    print(f"Salida: {PROC/'catastro_por_colonia.csv'}")


if __name__ == "__main__":
    main()
