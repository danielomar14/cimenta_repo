"""
CIMENTA · M2/M4 · Features de transporte por propiedad (Metro + Metrobús)

Fuente: Portal de Datos Abiertos CDMX — estaciones de STC Metro y Metrobús (KMZ).
  Líneas y Estaciones del Metro / Metrobús (KMZ = KML zippeado → se parsea con
  la librería estándar, sin geopandas).

Por cada propiedad calcula:
  · dist_transporte_km — distancia a la estación (Metro/Metrobús) más cercana
  · est_1km            — estaciones dentro de 1 km (conectividad)
"El mercado no paga kilómetros: paga minutos" → la cercanía a transporte sube precio.

Salida: data/processed/transporte_features.csv  (id → features)

Uso:
    python -m src.valuacion.transporte_features
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd
import requests
import urllib3
from sklearn.neighbors import BallTree

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "transporte"
PROC = ROOT / "data" / "processed"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/124 Safari/537.36"
KML_NS = "{http://www.opengis.net/kml/2.2}"
KMZ = {
    "metro": "https://datos.cdmx.gob.mx/dataset/1b014317-ddb1-46c7-ac79-7330c652abe3/resource/b9b61c06-8325-4df1-85c6-10c3aa7af1ac/download/stcmetro_kmz.zip",
    "metrobus": "https://datos.cdmx.gob.mx/dataset/63a08f93-e959-430f-8dc4-bf07182401e6/resource/3679bef3-3919-478a-959d-4fbf8ef7cd0a/download/mb_kmz.zip",
}


def _iter_kml(zbytes: bytes):
    """Rinde el texto de cada .kml dentro de un KMZ (posiblemente anidado)."""
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    for n in z.namelist():
        low = n.lower()
        if low.endswith(".kml"):
            yield z.read(n)
        elif low.endswith(".kmz"):
            yield from _iter_kml(z.read(n))


def _points(kml_bytes: bytes) -> list[tuple[float, float]]:
    """Coordenadas de <Point> (namespace-agnóstico)."""
    pts = []
    for el in ET.fromstring(kml_bytes).iter():
        if el.tag.endswith("Point"):
            for ch in el.iter():
                if ch.tag.endswith("coordinates") and ch.text and ch.text.strip():
                    p = ch.text.strip().split(",")
                    try:
                        pts.append((float(p[1]), float(p[0])))  # (lat, lng)
                    except (ValueError, IndexError):
                        pass
    return pts


def stations_from_kmz(name: str, url: str) -> list[tuple[float, float]]:
    RAW.mkdir(parents=True, exist_ok=True)
    f = RAW / f"{name}.kmz"
    if not f.exists() or f.stat().st_size < 1000:
        r = requests.get(url, verify=False, timeout=180, headers={"User-Agent": UA})
        r.raise_for_status()
        f.write_bytes(r.content)
    pts = []
    for kml in _iter_kml(f.read_bytes()):
        pts += _points(kml)
    return pts


def main():
    stations = []
    for name, url in KMZ.items():
        s = stations_from_kmz(name, url)
        print(f"  {name}: {len(s)} estaciones")
        stations += s
    st = np.array(stations)
    tree = BallTree(np.radians(st), metric="haversine")

    props = pd.read_csv(PROC / "_unificado_dedup.csv", low_memory=False)
    props["lat"] = pd.to_numeric(props["lat"], errors="coerce")
    props["lng"] = pd.to_numeric(props["lng"], errors="coerce")
    p = props.dropna(subset=["lat", "lng"])
    p = p[p["lat"].between(18.9, 20.1) & p["lng"].between(-99.6, -98.6)]

    rad = np.radians(p[["lat", "lng"]].to_numpy())
    dist, _ = tree.query(rad, k=1)
    dist_km = dist[:, 0] * 6371
    est_1km = tree.query_radius(rad, r=1.0 / 6371, count_only=True)

    feat = pd.DataFrame({
        "id": p["id"].to_numpy(),
        "dist_transporte_km": np.round(dist_km, 3),
        "est_1km": est_1km,
    })
    feat.to_csv(PROC / "transporte_features.csv", index=False)
    print(f"\nPropiedades: {len(feat):,}")
    print(f"Distancia mediana a estación: {feat['dist_transporte_km'].median()*1000:.0f} m")
    print(f"% a ≤500 m de una estación: {(feat['dist_transporte_km'] <= 0.5).mean()*100:.0f}%")
    print(f"Salida: {PROC/'transporte_features.csv'}")


if __name__ == "__main__":
    main()
