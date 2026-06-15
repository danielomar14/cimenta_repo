"""
CIMENTA · M1 · Señales de confianza por anuncio (publicador / remate / antigüedad)

Resuelve el problema de "las oportunidades son todas de una empresa de remates,
nueva y sin calificar". Lee la data CRUDA (data/raw/*/*.jsonl) y extrae, por
anuncio, las señales para poder filtrar:

  · publicador        — empresa/agente que publica (CyT.broker; Inmuebles24/
                        Vivanuncios.publicador tras re-scrape)
  · antiguedad_pub    — antigüedad del publicador en el portal ("X meses en …")
  · rating_pub        — calificación del publicador (si la hay)
  · es_subasta        — bandera de subasta/remate del portal (Propiedades.isAuction)
  · es_remate         — remate detectado por texto (remate/adjudicado/judicial/…)
  · verificado        — anunciante verificado (Propiedades.verifiedUserWhatsapp)
  · fecha_pub         — fecha de publicación / última actualización

Salida: data/processed/senales_anuncio.csv  (clave: portal, id)

Uso:
    python -m src.ingesta.senales
"""
from __future__ import annotations

import csv
import glob
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
FIELDS = ["portal", "id", "publicador", "antiguedad_pub", "rating_pub",
          "es_subasta", "es_remate", "verificado", "fecha_pub"]
REMATE_RE = re.compile(r"remat|adjudicad|recuperad|judicial|cartera vencida|dacion", re.I)


def _norm(s) -> str:
    if not s:
        return ""
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()


def extract(portal: str, r: dict) -> dict:
    s = {k: "" for k in FIELDS}
    s["portal"], s["id"] = portal, str(r.get("id", ""))
    name = _norm(r.get("name"))
    desc = _norm(r.get("description"))
    text = f"{name} {desc}"

    if portal == "casasyterrenos":
        s["publicador"] = (r.get("broker") or {}).get("name") if isinstance(r.get("broker"), dict) else ""
        s["fecha_pub"] = r.get("lastUpdate") or ""
    elif portal == "propiedades":
        s["es_subasta"] = bool(r.get("isAuction"))
        s["verificado"] = bool(r.get("verifiedUserWhatsapp"))
        s["fecha_pub"] = r.get("published") or ""
        s["antiguedad_pub"] = r.get("days_ago") or ""   # "22 días en Propiedades.com"
        text = _norm(r.get("short_address"))
    elif portal in ("inmuebles24", "vivanuncios"):
        # publicador derivado del logo: ".../logo_<slug>_<num>.jpg"
        logo = r.get("publicador_logo") or ""
        mm = re.search(r"logo_([a-z0-9-]+?)_\d", logo, re.I)
        s["publicador"] = mm.group(1) if mm else ""
        s["fecha_pub"] = r.get("fecha_posted") or ""        # datePosted del JSON-LD
        text = _norm(r.get("descripcion")) or f"{_norm(r.get('address'))} {_norm(r.get('colonia'))}"
    elif portal == "lamudi":
        s["fecha_pub"] = ""  # no expone fecha
    # remate por texto (o por bandera de subasta)
    s["es_remate"] = bool(REMATE_RE.search(text)) or bool(s["es_subasta"])
    return s


def main():
    PROC.mkdir(parents=True, exist_ok=True)
    bykey = {}  # (portal,id) → mejor registro (más señal); maneja re-scrapes
    for jf in sorted(glob.glob(str(ROOT / "data" / "raw" / "*" / "*.jsonl"))):
        portal = jf.split("/")[-2]
        for line in open(jf, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            s = extract(portal, r)
            if not s["id"]:
                continue
            key = (s["portal"], s["id"])
            score = (1 if s["publicador"] else 0) + (1 if s["fecha_pub"] else 0) + (1 if s["es_remate"] else 0)
            old = bykey.get(key)
            if old is None or score > old["_score"]:
                s["_score"] = score
                bykey[key] = s
    rows = list(bykey.values())
    for r in rows:
        r.pop("_score", None)
    with (PROC / "senales_anuncio.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader(); w.writerows(rows)

    n = len(rows)
    rem = sum(1 for r in rows if r["es_remate"])
    pub = sum(1 for r in rows if r["publicador"])
    print(f"Anuncios con señales: {n:,}")
    print(f"  con publicador: {pub:,}  | remates/subastas: {rem:,}")
    print(f"Salida: {PROC/'senales_anuncio.csv'}")


if __name__ == "__main__":
    main()
