"""
CIMENTA · M3 Plusvalía · Comercios por colonia (DENUE — INEGI)

Fuente: Directorio Estadístico Nacional de Unidades Económicas (DENUE), INEGI.
  https://www.inegi.org.mx/app/mapa/denue/  (CDMX = entidad 09)
  Descarga masiva: https://www.inegi.org.mx/contenidos/masiva/denue/denue_09_csv.zip

Tesis (M3): si en una colonia abren más comercios, su economía mejora → plusvalía.
Construye, por colonia × alcaldía:
  · comercios_total      — densidad de unidades económicas
  · comercios_consumo    — comercio al menudeo + alimentos/bebidas (SCIAN 46 y 72),
                           el tipo que señala gentrificación (cafés, restaurantes, tiendas)
  · comercios_nuevos     — altas 2025-2026 (registros nuevos tras el Censo 2024)
  · comercios_nuevos_pct — % de comercios recién dados de alta

Nota: para crecimiento interanual fino conviene diferenciar dos snapshots DENUE
(p.ej. 2021 vs 2025); aquí se usa la última versión + altas recientes como proxy.

Salida: data/processed/comercios_por_colonia.csv

Uso:
    python -m src.plusvalia.comercios
"""
from __future__ import annotations

import unicodedata
import zipfile
from pathlib import Path

import pandas as pd
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "denue"
PROC = ROOT / "data" / "processed"
URL = "https://www.inegi.org.mx/contenidos/masiva/denue/denue_09_csv.zip"
USECOLS = ["nomb_asent", "tipo_asent", "municipio", "fecha_alta", "codigo_act"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/124 Safari/537.36"


def _norm(s) -> str:
    if pd.isna(s):
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


def load_denue() -> pd.DataFrame:
    RAW.mkdir(parents=True, exist_ok=True)
    zf = RAW / "denue_09_csv.zip"
    if not zf.exists() or zf.stat().st_size < 10_000_000:
        print("  descargando DENUE 09 (~45 MB)...", flush=True)
        with requests.get(URL, stream=True, verify=False, timeout=300, headers={"User-Agent": UA}) as r:
            r.raise_for_status()
            with zf.open("wb") as out:
                for chunk in r.iter_content(1 << 20):
                    out.write(chunk)
    with zipfile.ZipFile(zf) as z:
        name = next(n for n in z.namelist() if "conjunto_de_datos" in n and n.endswith(".csv"))
        with z.open(name) as f:
            return pd.read_csv(f, usecols=USECOLS, encoding="latin-1", low_memory=False)


def main():
    PROC.mkdir(parents=True, exist_ok=True)
    df = load_denue()
    print(f"Unidades económicas (DENUE CDMX): {len(df):,}")
    df["alc"] = df["municipio"].map(_norm)
    df["col"] = df["nomb_asent"].map(_norm)
    df = df[(df["alc"] != "") & (df["col"] != "")]
    cod = df["codigo_act"].astype(str)
    anio = pd.to_numeric(df["fecha_alta"].astype(str).str[:4], errors="coerce")
    df["_consumo"] = cod.str.startswith(("46", "72")).astype(int)   # menudeo + alimentos
    df["_nuevo"] = (anio >= 2025).astype(int)

    g = df.groupby(["alc", "col"])
    out = g.agg(
        comercios_total=("_consumo", "size"),
        comercios_consumo=("_consumo", "sum"),
        comercios_nuevos=("_nuevo", "sum"),
    ).reset_index().rename(columns={"alc": "alcaldia", "col": "colonia"})
    out["comercios_nuevos_pct"] = (out["comercios_nuevos"] / out["comercios_total"] * 100).round(1)
    out = out[out["comercios_total"] >= 5]  # quita colonias con muestra mínima
    out = out.sort_values("comercios_total", ascending=False)
    out.to_csv(PROC / "comercios_por_colonia.csv", index=False)

    print(f"Colonias con comercios: {len(out):,}")
    print(f"% nuevos (mediana): {out['comercios_nuevos_pct'].median():.1f}%")
    print(f"Salida: {PROC/'comercios_por_colonia.csv'}")


if __name__ == "__main__":
    main()
