# CIMENTA

**Inteligencia inmobiliaria cuantitativa para la Zona Metropolitana del Valle de México (ZMVM).**

CIMENTA convierte los anuncios de los principales portales inmobiliarios de México en
valuaciones automáticas, un índice de plusvalía por colonia y oportunidades de inversión
con evidencia. No es un portal de listados ni un valuador aislado: es el analista
cuantitativo que la constructora mediana no puede contratar de tiempo completo.

Principio rector — **honestidad como producto**: cada número lleva su intervalo/contexto, y
el sistema dice cuándo algo es *"demasiado bueno para ser verdad"* (errores de dato, remates,
inundación de un solo publicador). Ver la [bitácora](BITACORA.pdf) para el detalle del avance.

---

## Estado de los módulos

| Módulo | Estado | Qué hace hoy |
|--------|--------|--------------|
| **M1 · Ingesta** | ✅ | 6 portales + geocodificación + deduplicación + señales de confianza |
| **M2 · Valuación (AVM)** | ✅ | Modelo de precio (RF/GBM), MdAPE honesto ~18% (piso de ruido del dato de *pedida*) |
| **M3 · Plusvalía** | ✅ | Índice de momentum por colonia (crimen + comercios + obra nueva) |
| **M4 · Capa urbana** | ◐ | Uso de suelo / valor de suelo (catastro) y transporte como *features* del AVM |
| **M5 · Oportunidades** | ✅ | Buscador de subvaluación con filtro anti-remate y diversificación por publicador |
| **M6 · Portafolio** | ⬜ | Pendiente |

---

## Fuentes de datos

**Portales (M1):**

| Portal | Acceso | Coordenadas |
|--------|--------|-------------|
| Casas y Terrenos | HTTP (`__NEXT_DATA__`) | nativas |
| Lamudi | HTTP (JSON-LD) | nativas |
| Propiedades.com | CloakBrowser (Cloudflare) · Next.js | nativas + fecha + `isAuction` |
| Inmuebles24 | CloakBrowser (Cloudflare) · DOM + JSON-LD | geocode por colonia · publicador/desc/fecha |
| Vivanuncios | CloakBrowser (Cloudflare) · DOM | geocode por colonia · publicador |
| iCasas | HTTP (microdata) | nativas |

**Datos abiertos:**
- **Delincuencia** — Carpetas de investigación FGJ-CDMX (`datos.cdmx.gob.mx`).
- **Comercios** — DENUE, INEGI (entidad 09).
- **Catastro** — Información Catastral CDMX (valor de suelo, año de construcción, obra nueva).
- **Transporte** — Estaciones de Metro y Metrobús (`datos.cdmx.gob.mx`, KMZ).

> El universo de análisis es **venta · precio ≤ 20 MDP · CDMX + ZMVM**.

---

## Estructura del repositorio

```
cimenta_repo/
├── app/
│   └── streamlit_app.py        # visor: Mapa · Modelo · Oportunidades · Plusvalía
├── src/
│   ├── ingesta/                # M1
│   │   ├── casasyterrenos.py  lamudi.py  icasas.py        # portales HTTP
│   │   ├── browser.py          # sesión CloakBrowser (Cloudflare)
│   │   ├── inmuebles24.py  propiedades.py  vivanuncios.py # portales Nivel B
│   │   ├── geocode.py          # geocodificación por colonia (OSM/Nominatim)
│   │   ├── dedup.py            # unifica + deduplica (cheapest-wins) → universo
│   │   ├── senales.py          # publicador / remate / antigüedad por anuncio
│   │   └── fotos_a_drive.py    # fotos → Google Drive (+ borrado local)
│   ├── valuacion/              # M2
│   │   ├── avm.py              # modelo de valuación + oportunidades
│   │   ├── catastro_features.py    # valor de suelo + antigüedad por propiedad
│   │   └── transporte_features.py  # distancia a Metro/Metrobús
│   └── plusvalia/              # M3
│       ├── crimen.py  comercios.py  catastro.py           # señales por colonia
│       └── indice.py          # índice de momentum 0–100
├── docs/
│   ├── diccionario_variables.json  # qué datos hay por propiedad
│   └── context/                    # contexto del proyecto
├── data/                       # crudos/procesados — NO versionado
├── BITACORA.tex / .pdf         # bitácora de avance (LaTeX → tectonic)
├── config.yml                  # configuración (portales, zonas, Drive)
└── requirements.txt
```

`credentials.json` / `token.json` (Google Drive) y `data/` están **gitignored**.

---

## Entorno

**Anaconda · Python 3.12 · `cimenta_env`**

```bash
conda create -n cimenta_env python=3.12 -y
conda activate cimenta_env
pip install -r requirements.txt
```

Los portales tras Cloudflare usan [`cloakbrowser`](https://pypi.org/project/cloakbrowser/)
(Chromium stealth); el resto es HTTP. Sin claves de API.

---

## Cómo correr el pipeline

```bash
conda activate cimenta_env

# 1) Ingesta (cada portal escribe data/processed/<portal>.csv) — ejemplos:
python -m src.ingesta.casasyterrenos          # HTTP, todo CDMX+ZMVM
python -m src.ingesta.lamudi
python -m src.ingesta.icasas
python -m src.ingesta.inmuebles24             # CloakBrowser (lento)
python -m src.ingesta.propiedades
python -m src.ingesta.vivanuncios

# 2) Geocodificar los que no traen coords
python -m src.ingesta.geocode --portal inmuebles24
python -m src.ingesta.geocode --portal vivanuncios

# 3) Señales de confianza + deduplicación (universo venta ≤20 MDP)
python -m src.ingesta.senales
python -m src.ingesta.dedup                   # → _unificado_dedup.csv

# 4) Plusvalía (M3) — datos abiertos por colonia
python -m src.plusvalia.crimen
python -m src.plusvalia.comercios
python -m src.plusvalia.catastro
python -m src.plusvalia.indice                # → indice_plusvalia_colonia.csv

# 5) Valuación (M2) — features espaciales + AVM
python -m src.valuacion.catastro_features
python -m src.valuacion.transporte_features
python -m src.valuacion.avm                   # → avm_predicciones.csv, oportunidades

# 6) Visor
streamlit run app/streamlit_app.py            # http://localhost:8501
```

Todos los scrapers son **reanudables** (checkpoint) y respetan `robots.txt` donde aplica.

---

## Notas honestas

- **AVM ~18% MdAPE**: es el piso de ruido del dato. Los precios son de *pedida* (no de venta)
  y el precio/m² dentro de una colonia varía ±35%. Bajar de ahí requiere precios de venta
  reales (notariales/RPP) o atributos por unidad.
- **Plusvalía**: es un índice de **indicadores líderes**, no un pronóstico supervisado de
  precio (falta histórico por colonia, que se acumula con el tiempo).
- **Oportunidades**: se excluyen remates/subastas y se limita a N por publicador para evitar
  la inundación de una sola empresa.

---

## Flujo de trabajo (ramas)

`feature/dev` (desarrollo) → `dev` (pruebas) → `master` (estable).

---
*Ciudad de México · 2026 · Documento de trabajo.*
