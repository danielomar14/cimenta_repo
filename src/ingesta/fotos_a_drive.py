"""
CIMENTA · M1 Ingesta · Fotos de propiedades → Google Drive (y borrado local)

Patrón tomado del repo Prueba_fotmob (upload_to_drive / compress_and_upload),
adaptado para FOTOS y con la pieza nueva: BORRAR LOCAL tras subir.

Flujo (rolling, para gastar mínimo disco):
  por cada propiedad scrapeada (de data/raw/{portal}/*.jsonl):
    1. descarga sus imágenes (URLs del campo `images`) a un dir temporal
    2. las empaqueta en un ZIP  {id}.zip
    3. sube el ZIP a Drive:  Mi unidad / CIMENTA / {portal} / fotos /
    4. BORRA las imágenes y el ZIP de la Mac  (--keep-local lo evita)
  → en disco nunca hay más que las fotos de UNA propiedad a la vez.

Reanudable: salta propiedades cuyo {id}.zip ya está en Drive (o en el log).
Idempotente: seguro para Ctrl+C y volver a correr.

Requisitos: credentials.json + token.json en la raíz (gitignored).
Primera vez sin token válido: abre el navegador para autorizar.

Uso:
    python -m src.ingesta.fotos_a_drive                 # todas las propiedades
    python -m src.ingesta.fotos_a_drive --limit 3       # prueba: 3 propiedades
    python -m src.ingesta.fotos_a_drive --keep-local    # no borra (debug)
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import signal
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config.yml"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("fotos")

_shutdown = False


def _sigint(*_):
    global _shutdown
    logger.info("Ctrl+C — termino la propiedad actual y paro limpiamente...")
    _shutdown = True


signal.signal(signal.SIGINT, _sigint)


# ── Config ───────────────────────────────────────────────────────────────────────

def load_cfg() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Google Drive (patrón Prueba_fotmob) ──────────────────────────────────────────

def auth(drive_cfg: dict):
    creds_path = ROOT / drive_cfg["credentials"]
    token_path = ROOT / drive_cfg["token"]
    scopes = drive_cfg["scopes"]
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                logger.error("Falta %s (cliente OAuth Desktop de Google).", creds_path)
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)


def get_or_create_folder(service, name: str, parent_id: str) -> str:
    q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
         f" and '{parent_id}' in parents and trashed=false")
    res = service.files().list(q=q, fields="files(id)").execute()
    fs = res.get("files", [])
    if fs:
        return fs[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    return service.files().create(body=meta, fields="id").execute()["id"]


def resolve_path(service, parts: list[str]) -> str:
    fid = "root"
    for p in parts:
        fid = get_or_create_folder(service, p, fid)
    return fid


def existing_names(service, folder_id: str) -> set[str]:
    names, token = set(), None
    while True:
        res = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(name)", pageSize=1000, pageToken=token,
        ).execute()
        for f in res.get("files", []):
            names.add(f["name"])
        token = res.get("nextPageToken")
        if not token:
            break
    return names


def upload(service, local: Path, folder_id: str) -> bool:
    meta = {"name": local.name, "parents": [folder_id]}
    media = MediaFileUpload(str(local), mimetype="application/zip", resumable=True)
    try:
        service.files().create(body=meta, media_body=media, fields="id").execute()
        return True
    except HttpError as e:
        logger.warning("  ✗ upload %s: %s", local.name, e)
        return False


# ── Propiedades + imágenes ────────────────────────────────────────────────────────

def iter_properties(portal: str):
    """Rinde (id, [image_urls]) desde data/raw/{portal}/*.jsonl, dedup por id."""
    raw_dir = ROOT / "data" / "raw" / portal
    seen = set()
    for jf in sorted(raw_dir.glob("*.jsonl")):
        with jf.open(encoding="utf-8") as f:
            for line in f:
                try:
                    p = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pid = p.get("id")
                if pid in seen:
                    continue
                seen.add(pid)
                imgs = [u for u in (p.get("images") or []) if isinstance(u, str) and u.startswith("http")]
                if imgs:
                    yield str(pid), imgs


def download_images(session: requests.Session, urls: list[str], dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, url in enumerate(urls, 1):
        ext = Path(urlparse(url).path).suffix or ".jpg"
        out = dest / f"{i:02d}{ext}"
        try:
            r = session.get(url, timeout=30, stream=True)
            r.raise_for_status()
            with out.open("wb") as fh:
                for chunk in r.iter_content(8192):
                    fh.write(chunk)
            n += 1
        except requests.RequestException:
            continue
    return n


def zip_dir(src: Path, zip_path: Path) -> None:
    # ZIP_STORED: las imágenes ya están comprimidas (jpg/png); solo se empaquetan.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for f in sorted(src.iterdir()):
            zf.write(f, f.name)


# ── Main ──────────────────────────────────────────────────────────────────────────

def run(args):
    cfg = load_cfg()
    drive_cfg = cfg["drive"]
    portal = args.portal

    tmp_root = ROOT / "data" / "_tmp_fotos"
    tmp_root.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / "data" / "raw" / portal / "_fotos_subidas.json"
    uploaded_log = set(json.loads(log_path.read_text())["uploaded"]) if log_path.exists() else set()

    props = list(iter_properties(portal))
    if args.limit:
        props = props[: args.limit]
    logger.info("Propiedades con fotos: %d", len(props))
    if not props:
        return

    logger.info("Autenticando con Google Drive...")
    service = auth(drive_cfg)
    drive_path = list(drive_cfg["carpeta_base"]) + [portal, "fotos"]
    folder_id = resolve_path(service, drive_path)
    logger.info("Destino Drive: Mi unidad / %s", " / ".join(drive_path))

    in_drive = existing_names(service, folder_id)
    done = in_drive | uploaded_log
    logger.info("Ya en Drive: %d  |  pendientes: %d", len(done), sum(1 for i, _ in props if f"{i}.zip" not in done))

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/124 Safari/537.36"

    ok = skip = fail = 0
    t0 = time.time()
    for idx, (pid, urls) in enumerate(props, 1):
        if _shutdown:
            break
        zip_name = f"{pid}.zip"
        if zip_name in done and not args.force:
            skip += 1
            continue

        work = tmp_root / pid
        zip_path = tmp_root / zip_name
        try:
            n = download_images(session, urls, work)
            if n == 0:
                logger.warning("  [%d] %s: 0 imágenes descargadas, salto", idx, pid)
                fail += 1
                continue
            zip_dir(work, zip_path)
            if upload(service, zip_path, folder_id):
                ok += 1
                uploaded_log.add(zip_name)
                done.add(zip_name)
            else:
                fail += 1
                continue
        finally:
            # BORRADO LOCAL — la pieza nueva vs. el patrón FotMob
            if not args.keep_local:
                shutil.rmtree(work, ignore_errors=True)
                zip_path.unlink(missing_ok=True)

        if ok % 25 == 0 or idx == len(props):
            log_path.write_text(json.dumps({"uploaded": sorted(uploaded_log)}, indent=2))

        rate = idx / (time.time() - t0) if time.time() > t0 else 0
        logger.info("[%d/%d] %s → %d fotos subidas (ok=%d skip=%d fail=%d, %.1f prop/s)",
                    idx, len(props), pid, n, ok, skip, fail, rate)

    log_path.write_text(json.dumps({"uploaded": sorted(uploaded_log)}, indent=2))
    # limpieza del dir temporal raíz si quedó vacío
    if not args.keep_local:
        shutil.rmtree(tmp_root, ignore_errors=True)

    logger.info("=" * 56)
    logger.info("LISTO — subidas:%d  ya estaban:%d  fallos:%d", ok, skip, fail)
    logger.info("Drive: Mi unidad / %s", " / ".join(drive_path))


def main():
    ap = argparse.ArgumentParser(description="CIMENTA · Fotos → Google Drive (+ borrado local)")
    ap.add_argument("--portal", default="casasyterrenos")
    ap.add_argument("--limit", type=int, help="Máximo de propiedades (para pruebas)")
    ap.add_argument("--keep-local", action="store_true", help="No borra archivos locales (debug)")
    ap.add_argument("--force", action="store_true", help="Resube aunque ya esté en Drive")
    args = ap.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        logger.info("Interrumpido. Reanuda con el mismo comando.")
        sys.exit(130)


if __name__ == "__main__":
    main()
