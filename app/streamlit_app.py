"""
CIMENTA · Visor de propiedades (multi-portal)

Lee TODOS los CSVs de data/processed/ (casasyterrenos, lamudi, inmuebles24,
propiedades, vivanuncios…), los normaliza a un esquema común y los muestra
juntos en un mapa OpenStreetMap + tabla.

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
PROC = ROOT / "data" / "processed"

AZUL, ACENTO, VERDE, NIEBLA = "#16324F", "#2D7DD2", "#2E8B6E", "#AEB6BD"
PORTAL_COLOR = {
    "casasyterrenos": "#2D7DD2",  # azul
    "lamudi": "#2E8B6E",          # verde
    "inmuebles24": "#E07A3F",     # naranja
    "propiedades": "#8E6FC7",     # morado
    "vivanuncios": "#C7506B",     # rosa
}

st.set_page_config(page_title="CIMENTA · Propiedades", page_icon="🏙️", layout="wide")
st.markdown(
    f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;700&display=swap');
      html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}
      h1, h2, h3 {{ font-family: 'Space Grotesk', sans-serif; color: {AZUL}; }}
      #MainMenu, footer, header {{ visibility: hidden; }}
      .block-container {{ padding-top: 2.2rem; padding-bottom: 1rem; max-width: 1320px; }}
      [data-testid="stMetric"] {{ background:#fff; border:1px solid #E3E8EC; border-radius:10px; padding:14px 18px; }}
      [data-testid="stMetricLabel"] {{ color:{NIEBLA}; font-size:12px; letter-spacing:.4px; }}
      [data-testid="stMetricValue"] {{ font-family:'Space Grotesk'; color:{AZUL}; }}
      .cim-title {{ font-family:'Space Grotesk'; font-weight:700; font-size:30px; color:{AZUL}; letter-spacing:1px; margin-bottom:0; }}
      .cim-sub {{ color:{NIEBLA}; font-size:14px; margin-top:2px; }}
      .leaflet-container {{ border-radius:12px; }}
    </style>
    """,
    unsafe_allow_html=True,
)


def _money(x) -> str:
    if pd.isna(x) or x == 0:
        return "—"
    if x >= 1_000_000:
        return f"${x/1_000_000:.1f} M"
    if x >= 1_000:
        return f"${x/1_000:.0f} k"
    return f"${x:,.0f}"


def _normalize(df: pd.DataFrame, portal: str) -> pd.DataFrame:
    c = df.columns
    out = pd.DataFrame()
    out["portal"] = df["portal"] if "portal" in c else portal
    out["tipo"] = df.get("tipo")
    out["operacion"] = df.get("operacion")
    out["municipio"] = df.get("municipio")
    out["colonia"] = df["colonia"] if "colonia" in c else df.get("neighborhood")
    out["name"] = df["name"] if "name" in c else out["colonia"]
    # precio unificado
    if "precio" in c:
        out["precio"] = pd.to_numeric(df["precio"], errors="coerce")
    elif "priceSale" in c:
        op = df.get("operacion")
        out["precio"] = pd.to_numeric(
            df["priceSale"].where(op == "venta", df.get("priceRent")), errors="coerce")
    else:
        out["precio"] = pd.NA
    out["surface"] = pd.to_numeric(df["surface"] if "surface" in c else df.get("size_m2"), errors="coerce")
    out["rooms"] = pd.to_numeric(df.get("rooms"), errors="coerce")
    out["bathrooms"] = pd.to_numeric(df.get("bathrooms"), errors="coerce")
    out["lat"] = pd.to_numeric(df.get("lat"), errors="coerce")
    out["lng"] = pd.to_numeric(df.get("lng"), errors="coerce")
    out["url"] = df.get("url")
    return out


@st.cache_data(ttl=120, show_spinner=False)
def load_all() -> pd.DataFrame:
    frames = []
    for csv in sorted(PROC.glob("*.csv")):
        try:
            df = pd.read_csv(csv, on_bad_lines="skip", low_memory=False)
        except Exception:
            continue
        if len(df):
            frames.append(_normalize(df, csv.stem))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df[df["lat"].between(18.9, 20.1) & df["lng"].between(-99.6, -98.6)]
    return df


st.markdown('<div class="cim-title">CIMENTA</div>', unsafe_allow_html=True)
st.markdown('<div class="cim-sub">Propiedades · CDMX y ZMVM · multi-portal — inteligencia inmobiliaria cuantitativa</div>', unsafe_allow_html=True)

df = load_all()
if df.empty:
    st.warning("Aún no hay datos geolocalizados. Corre los scrapers en src/ingesta/ (y geocode para Inmuebles24).")
    st.stop()

with st.sidebar:
    st.markdown("### Filtros")
    if st.button("🔄 Refrescar datos"):
        st.cache_data.clear(); st.rerun()
    portales = sorted(df["portal"].dropna().unique())
    f_port = st.multiselect("Portal", portales, default=portales)
    ops = sorted(df["operacion"].dropna().unique())
    f_op = st.multiselect("Operación", ops, default=ops)
    rec_max = int(df["rooms"].fillna(0).max() or 0)
    f_rec = st.slider("Recámaras (mínimo)", 0, max(rec_max, 1), 0)
    tile = st.radio("Mapa base", ["Minimalista", "OpenStreetMap"], horizontal=True)
    st.caption(f"{len(df):,} propiedades geolocalizadas.")

m = df["portal"].isin(f_port) & df["operacion"].isin(f_op) & (df["rooms"].fillna(0) >= f_rec)
fdf = df[m]

# KPIs
k = st.columns(2 + len(portales))
k[0].metric("Propiedades", f"{len(fdf):,}")
venta = fdf[fdf["operacion"] == "venta"]
k[1].metric("Precio mediano (venta)", _money(venta["precio"].median()))
counts = fdf["portal"].value_counts()
for i, p in enumerate(portales):
    k[2 + i].metric(p[:12], f"{int(counts.get(p, 0)):,}")

st.write("")
left, right = st.columns([7, 5], gap="medium")

with left:
    if len(fdf):
        fmap = folium.Map(location=[fdf["lat"].median(), fdf["lng"].median()],
                          zoom_start=11,
                          tiles="CartoDB positron" if tile == "Minimalista" else "OpenStreetMap",
                          control_scale=True)
        cluster = MarkerCluster().add_to(fmap)
        for _, r in fdf.iterrows():
            color = PORTAL_COLOR.get(r["portal"], NIEBLA)
            nm = "" if pd.isna(r["name"]) else str(r["name"])[:60]
            col = "" if pd.isna(r["colonia"]) else str(r["colonia"])
            url = "" if pd.isna(r["url"]) else str(r["url"])
            link = f"<a href='{url}' target='_blank'>Ver →</a>" if url else ""
            popup = folium.Popup(
                f"<b>{nm}</b><br>"
                f"<span style='color:{color};font-weight:600'>{r['portal']}</span> · {r.get('operacion','')}<br>"
                f"<span style='color:{AZUL};font-weight:600'>{_money(r['precio'])}</span> · "
                f"{int(r['surface']) if pd.notna(r['surface']) else '—'} m² · "
                f"{int(r['rooms']) if pd.notna(r['rooms']) else '—'} rec<br>"
                f"<i>{col}</i><br>{link}",
                max_width=250,
            )
            folium.CircleMarker([r["lat"], r["lng"]], radius=4, color=color, weight=1,
                                fill=True, fill_color=color, fill_opacity=0.7, popup=popup).add_to(cluster)
        st_folium(fmap, use_container_width=True, height=580, returned_objects=[])
    leg = " · ".join(f"<span style='color:{PORTAL_COLOR.get(p, NIEBLA)}'>●</span> {p}" for p in portales)
    st.markdown(f"<div style='font-size:13px'>{leg}</div>", unsafe_allow_html=True)
    st.caption("tiles © OpenStreetMap / CartoDB")

with right:
    st.markdown("##### Detalle")
    show = fdf[["name", "portal", "tipo", "operacion", "precio", "surface", "rooms", "colonia", "url"]].rename(
        columns={"name": "Anuncio", "portal": "Portal", "tipo": "Tipo", "operacion": "Op.",
                 "precio": "Precio", "surface": "m²", "rooms": "Rec", "colonia": "Colonia", "url": "Link"})
    st.dataframe(show, hide_index=True, height=560, width="stretch",
                 column_config={"Precio": st.column_config.NumberColumn(format="$ %d"),
                                "Link": st.column_config.LinkColumn("Link", display_text="ver →")})
