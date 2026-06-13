"""
CIMENTA · Visor de propiedades (Casas y Terrenos)

Dashboard minimalista para explorar el CSV scrapeado + mapa OpenStreetMap.

Correr:
    conda activate cimenta_env
    streamlit run app/streamlit_app.py
"""
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data" / "processed" / "casasyterrenos.csv"

# Paleta CIMENTA
AZUL, ACENTO, VERDE, NIEBLA, FONDO = "#16324F", "#2D7DD2", "#2E8B6E", "#AEB6BD", "#F5F7F9"

st.set_page_config(page_title="CIMENTA · Propiedades", page_icon="🏙️", layout="wide")

# ── Estilo minimalista ────────────────────────────────────────────────────────────
st.markdown(
    f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;700&display=swap');
      html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}
      h1, h2, h3 {{ font-family: 'Space Grotesk', sans-serif; color: {AZUL}; }}
      #MainMenu, footer, header {{ visibility: hidden; }}
      .block-container {{ padding-top: 2.2rem; padding-bottom: 1rem; max-width: 1280px; }}
      [data-testid="stMetric"] {{
        background: #fff; border: 1px solid #E3E8EC; border-radius: 10px;
        padding: 14px 18px;
      }}
      [data-testid="stMetricLabel"] {{ color: {NIEBLA}; font-size: 12px; letter-spacing: .4px; }}
      [data-testid="stMetricValue"] {{ font-family: 'Space Grotesk'; color: {AZUL}; }}
      .cim-title {{ font-family:'Space Grotesk'; font-weight:700; font-size:30px; color:{AZUL};
                    letter-spacing:1px; margin-bottom:0; }}
      .cim-sub {{ color:{NIEBLA}; font-size:14px; margin-top:2px; }}
      .leaflet-container {{ border-radius: 12px; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Datos ─────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_data() -> pd.DataFrame:
    df = pd.read_csv(CSV)
    for c in ["priceSale", "priceRent", "surface", "construction", "rooms", "bathrooms", "lat", "lng"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # precio según operación
    df["precio"] = df.apply(
        lambda r: r["priceSale"] if r["operacion"] == "venta" else r["priceRent"], axis=1
    )
    # coordenadas válidas dentro de un bounding box generoso de la ZMVM
    df = df[df["lat"].between(18.9, 20.1) & df["lng"].between(-99.6, -98.6)]
    return df


def money(x) -> str:
    if pd.isna(x) or x == 0:
        return "—"
    if x >= 1_000_000:
        return f"${x/1_000_000:.1f} M"
    if x >= 1_000:
        return f"${x/1_000:.0f} k"
    return f"${x:,.0f}"


if not CSV.exists():
    st.error("No se encontró el CSV. Corre primero el scraper:  `python -m src.ingesta.casasyterrenos`")
    st.stop()

df = load_data()

# ── Encabezado ────────────────────────────────────────────────────────────────────
st.markdown('<div class="cim-title">CIMENTA</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="cim-sub">Propiedades · Casas y Terrenos · CDMX y ZMVM '
    '— inteligencia inmobiliaria cuantitativa</div>',
    unsafe_allow_html=True,
)
st.write("")

# ── Filtros (sidebar) ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"### Filtros")
    ops = sorted(df["operacion"].dropna().unique())
    f_op = st.multiselect("Operación", ops, default=ops)
    tipos = sorted(df["tipo"].dropna().unique())
    f_tipo = st.multiselect("Tipo", tipos, default=tipos)

    munis = sorted(df["municipio"].dropna().unique())
    f_muni = st.multiselect("Municipio (etiqueta del anuncio)", munis, default=[])

    rec_max = int(df["rooms"].fillna(0).max() or 0)
    f_rec = st.slider("Recámaras (mínimo)", 0, max(rec_max, 1), 0)

    tile = st.radio("Mapa base", ["Minimalista", "OpenStreetMap"], horizontal=True)
    st.caption(f"{len(df):,} propiedades con coordenadas válidas.")

# aplicar filtros
m = df["operacion"].isin(f_op) & df["tipo"].isin(f_tipo) & (df["rooms"].fillna(0) >= f_rec)
if f_muni:
    m &= df["municipio"].isin(f_muni)
fdf = df[m]

# ── KPIs ──────────────────────────────────────────────────────────────────────────
venta = fdf[fdf["operacion"] == "venta"]
renta = fdf[fdf["operacion"] == "renta"]
k1, k2, k3, k4 = st.columns(4)
k1.metric("Propiedades", f"{len(fdf):,}")
k2.metric("Venta / Renta", f"{len(venta):,} / {len(renta):,}")
k3.metric("Precio mediano (venta)", money(venta["precio"].median()))
k4.metric("m² mediano (terreno)", f"{fdf['surface'].median():.0f}" if len(fdf) else "—")

st.write("")

# ── Mapa OpenStreetMap ────────────────────────────────────────────────────────────
left, right = st.columns([7, 5], gap="medium")

with left:
    if len(fdf):
        center = [fdf["lat"].median(), fdf["lng"].median()]
        tiles = "CartoDB positron" if tile == "Minimalista" else "OpenStreetMap"
        fmap = folium.Map(location=center, zoom_start=12, tiles=tiles, control_scale=True)
        cluster = MarkerCluster(name="Propiedades").add_to(fmap)

        def txt(x):
            return "" if pd.isna(x) else str(x)

        def num(x):
            return int(x) if pd.notna(x) else "—"

        for _, r in fdf.iterrows():
            color = ACENTO if r["operacion"] == "venta" else VERDE
            url = txt(r["url"])
            link = f"<a href='{url}' target='_blank'>Ver anuncio →</a>" if url else ""
            popup = folium.Popup(
                f"<b>{txt(r['name'])[:60]}</b><br>"
                f"{txt(r['tipo']).capitalize()} · {txt(r['operacion'])}<br>"
                f"<span style='color:{AZUL};font-weight:600'>{money(r['precio'])} {txt(r['currency'])}</span><br>"
                f"{num(r['surface'])} m² · {num(r['rooms'])} rec · {num(r['bathrooms'])} baños<br>"
                f"<i>{txt(r['neighborhood'])}</i><br>"
                f"{link}",
                max_width=260,
            )
            folium.CircleMarker(
                location=[r["lat"], r["lng"]], radius=5, color=color, weight=1,
                fill=True, fill_color=color, fill_opacity=0.75, popup=popup,
            ).add_to(cluster)
        st_folium(fmap, use_container_width=True, height=560, returned_objects=[])
    else:
        st.info("No hay propiedades con los filtros actuales.")
    st.caption("🔵 Venta · 🟢 Renta · tiles © OpenStreetMap / CartoDB")

with right:
    st.markdown("##### Detalle")
    cols = ["name", "tipo", "operacion", "precio", "surface", "rooms", "bathrooms", "neighborhood", "url"]
    show = fdf[cols].rename(columns={
        "name": "Anuncio", "tipo": "Tipo", "operacion": "Op.", "precio": "Precio",
        "surface": "m²", "rooms": "Rec", "bathrooms": "Baños", "neighborhood": "Colonia", "url": "Link",
    })
    st.dataframe(
        show, hide_index=True, height=520, width="stretch",
        column_config={
            "Precio": st.column_config.NumberColumn(format="$ %d"),
            "Link": st.column_config.LinkColumn("Link", display_text="ver →"),
        },
    )
