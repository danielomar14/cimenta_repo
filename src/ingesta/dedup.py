"""
CIMENTA · M1 Ingesta · Deduplicación (Etapa 10 del catálogo)

El mismo inmueble se publica repetido: varios brokers en un mismo portal y el
mismo inmueble en varios portales (Inmuebles24/Vivanuncios comparten data Navent;
los brokers cross-postean). Este programa:

  1. Une TODOS los CSVs de data/processed/ en un esquema común.
  2. Construye una FIRMA por propiedad:
       (municipio, colonia, tipo, operación, m² en bucket, recámaras, baños)
     — normalizando texto (mayúsculas/acentos) para que casen entre portales.
  3. Colapsa cada grupo de duplicados a UN registro = el MÁS BARATO,
     guardando cuántos anuncios había, en qué portales y el rango de precio.

Salida: data/processed/_unificado_dedup.csv  (una fila por inmueble único).

Uso:
    python -m src.ingesta.dedup
    python -m src.ingesta.dedup --m2-bucket 10   # tolerancia de m² más laxa
    python -m src.ingesta.dedup --scope intra     # dedup solo dentro de cada portal
"""
from __future__ import annotations

import argparse
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
OUT = PROC / "_unificado_dedup.csv"
# CSVs que NO son de portales (evitar releer la salida)
SKIP = {"_unificado_dedup"}


def _norm(s) -> str:
    """Normaliza texto para casar entre portales: sin acentos, MAYÚS, sin espacios extra."""
    if pd.isna(s):
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


def load_unified() -> pd.DataFrame:
    frames = []
    for csv in sorted(PROC.glob("*.csv")):
        if csv.stem in SKIP:
            continue
        try:
            df = pd.read_csv(csv, on_bad_lines="skip", low_memory=False)
        except Exception:
            continue
        if not len(df):
            continue
        c = df.columns
        out = pd.DataFrame()
        out["portal"] = df["portal"] if "portal" in c else csv.stem
        out["id"] = df.get("id")
        out["tipo"] = df.get("tipo")
        out["operacion"] = df.get("operacion")
        out["municipio"] = df.get("municipio")
        out["colonia"] = df["colonia"] if "colonia" in c else df.get("neighborhood")
        out["name"] = df["name"] if "name" in c else out["colonia"]
        if "precio" in c:
            out["precio"] = pd.to_numeric(df["precio"], errors="coerce")
        elif "priceSale" in c:
            op = df.get("operacion")
            out["precio"] = pd.to_numeric(df["priceSale"].where(op == "venta", df.get("priceRent")), errors="coerce")
        else:
            out["precio"] = pd.NA
        out["surface"] = pd.to_numeric(df["surface"] if "surface" in c else df.get("size_m2"), errors="coerce")
        out["rooms"] = pd.to_numeric(df.get("rooms"), errors="coerce")
        out["bathrooms"] = pd.to_numeric(df.get("bathrooms"), errors="coerce")
        out["lat"] = pd.to_numeric(df.get("lat"), errors="coerce")
        out["lng"] = pd.to_numeric(df.get("lng"), errors="coerce")
        out["url"] = df.get("url")
        frames.append(out)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def build_signature(df: pd.DataFrame, m2_bucket: int, scope: str) -> pd.Series:
    muni = df["municipio"].map(_norm)
    col = df["colonia"].map(_norm)
    tipo = df["tipo"].map(_norm)
    op = df["operacion"].map(_norm)
    surf = df["surface"].apply(lambda x: int(round(x / m2_bucket) * m2_bucket) if pd.notna(x) else -1)
    rooms = df["rooms"].apply(lambda x: int(x) if pd.notna(x) else -1)
    baths = df["bathrooms"].apply(lambda x: int(x) if pd.notna(x) else -1)
    base = muni + "|" + col + "|" + tipo + "|" + op + "|" + surf.astype(str) + "|" + rooms.astype(str) + "|" + baths.astype(str)
    if scope == "intra":          # dedup solo dentro del mismo portal
        base = df["portal"].astype(str) + "||" + base
    return base


def run(args):
    df = load_unified()
    if df.empty:
        print("No hay CSVs en data/processed/. Corre primero los scrapers.")
        return
    # ── Filtro de universo: solo venta y precio ≤ tope (def 20 MDP) ──
    df["precio"] = pd.to_numeric(df.get("precio"), errors="coerce")
    n_total = len(df)
    if not args.incluir_renta:
        df = df[df["operacion"] == "venta"]
    df = df[df["precio"].between(1, args.precio_max)].reset_index(drop=True)
    print(f"Universo: {'venta+renta' if args.incluir_renta else 'solo venta'}, "
          f"precio ≤ ${args.precio_max/1e6:.0f} MDP  ({n_total:,} → {len(df):,} anuncios)")
    n_raw = len(df)

    # filas sin ubicación útil no se pueden dedupear con confianza → quedan como únicas
    has_key = (df["colonia"].map(_norm) != "") | (df["municipio"].map(_norm) != "")
    df["sig"] = build_signature(df, args.m2_bucket, args.scope)
    # las filas sin clave reciben firma única (su id) para no fusionarse a ciegas
    df.loc[~has_key, "sig"] = "NOKEY|" + df.loc[~has_key, "portal"].astype(str) + "|" + df.loc[~has_key, "id"].astype(str)

    # Sub-clustering por precio dentro de cada firma: dos anuncios son el MISMO
    # inmueble solo si su precio está dentro de la tolerancia del más barato del
    # cluster (evita fusionar p.ej. un $2.3M con un $26M en la misma colonia/m²).
    df_sorted = df.sort_values(["sig", "precio"], na_position="last").reset_index(drop=True)
    clusters, cur_sig, cur_min, idx = [], None, None, 0
    tol = args.tol
    for sig, p in zip(df_sorted["sig"], df_sorted["precio"]):
        if sig != cur_sig:
            cur_sig, cur_min, idx = sig, p, 0
        elif pd.isna(p) or pd.isna(cur_min) or p > cur_min * (1 + tol):
            idx += 1
            cur_min = p
        clusters.append(f"{sig}#{idx}")
    df_sorted["cluster"] = clusters
    g = df_sorted.groupby("cluster", sort=False)

    kept = g.first().reset_index()
    kept["n_anuncios"] = g.size().values
    kept["portales"] = g["portal"].apply(lambda s: ",".join(sorted({str(x) for x in s if pd.notna(x)}))).values
    kept["precio_min"] = g["precio"].min().values
    kept["precio_max"] = g["precio"].max().values
    kept["ahorro"] = (kept["precio_max"] - kept["precio_min"])  # cuánto se evita pagando el más barato

    cols = ["portal", "id", "tipo", "operacion", "municipio", "colonia", "name",
            "precio", "precio_min", "precio_max", "ahorro", "surface", "rooms",
            "bathrooms", "lat", "lng", "url", "n_anuncios", "portales"]
    kept = kept[cols].drop(columns=["sig"], errors="ignore")
    kept.to_csv(OUT, index=False)

    n_unique = len(kept)
    dups = kept[kept["n_anuncios"] > 1]
    multi_portal = dups[dups["portales"].str.contains(",")]
    print("=" * 60)
    print(f"DEDUPLICACIÓN ({args.scope}, bucket m² = {args.m2_bucket})")
    print(f"  Anuncios crudos:        {n_raw:,}")
    print(f"  Inmuebles únicos:       {n_unique:,}")
    print(f"  Duplicados eliminados:  {n_raw - n_unique:,}  ({(n_raw-n_unique)/n_raw*100:.1f}%)")
    print(f"  Grupos con duplicados:  {len(dups):,}")
    print(f"    · multi-portal:       {len(multi_portal):,}")
    print(f"  Ahorro mediano por dup: ${dups['ahorro'].median():,.0f}" if len(dups) else "")
    print(f"\n  Por portal (anuncios crudos):")
    for p, n in df['portal'].value_counts().items():
        print(f"    {p:16} {n:,}")
    print(f"\n  Salida: {OUT}")


def main():
    ap = argparse.ArgumentParser(description="CIMENTA · Deduplicación de propiedades (cheapest-wins)")
    ap.add_argument("--m2-bucket", type=int, default=5, help="tolerancia de m² para agrupar (def 5)")
    ap.add_argument("--scope", choices=["global", "intra"], default="global",
                    help="global = dentro y entre portales; intra = solo dentro de cada portal")
    ap.add_argument("--tol", type=float, default=0.30,
                    help="tolerancia de precio para considerar dos anuncios el mismo inmueble (def 0.30 = ±30%%)")
    ap.add_argument("--precio-max", type=float, default=20_000_000,
                    help="precio máximo del universo (def 20 MDP)")
    ap.add_argument("--incluir-renta", action="store_true",
                    help="incluye propiedades en renta (def: solo venta)")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
