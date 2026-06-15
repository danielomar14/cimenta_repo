"""
CIMENTA · App (multi-portal)

Pestañas:
  🗺️ Mapa          — todas las propiedades geolocalizadas (OpenStreetMap)
  🤖 Modelo (AVM)  — valuación: train/validation/test, métricas y performance
  💎 Oportunidades — buscador interactivo de subvaluación (filtros anti-remate)
  📈 Plusvalía      — índice de momentum por colonia (crimen FGJ + comercios DENUE)
  🏢 Publicadores   — reputación por publicador (% remate, subvaluación) — detecta sospechosos

Correr:
    conda activate cimenta_env
    streamlit run app/streamlit_app.py
"""
import json
from pathlib import Path

import altair as alt
import folium
import pandas as pd
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"

AZUL, ACENTO, VERDE, NIEBLA, EXITO = "#16324F", "#2D7DD2", "#2E8B6E", "#AEB6BD", "#2E8B6E"
PORTAL_COLOR = {
    "casasyterrenos": "#2D7DD2", "lamudi": "#2E8B6E", "inmuebles24": "#E07A3F",
    "propiedades": "#8E6FC7", "vivanuncios": "#C7506B",
}

st.set_page_config(page_title="CIMENTA", page_icon="🏙️", layout="wide")
st.markdown(
    f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;700&display=swap');
      html, body, [class*="css"] {{ font-family:'Inter',sans-serif; }}
      h1,h2,h3 {{ font-family:'Space Grotesk',sans-serif; color:{AZUL}; }}
      #MainMenu, footer, header {{ visibility:hidden; }}
      .block-container {{ padding-top:2rem; padding-bottom:1rem; max-width:1320px; }}
      [data-testid="stMetric"] {{ background:#fff; border:1px solid #E3E8EC; border-radius:10px; padding:14px 18px; }}
      [data-testid="stMetricLabel"] {{ color:{NIEBLA}; font-size:12px; letter-spacing:.4px; }}
      [data-testid="stMetricValue"] {{ font-family:'Space Grotesk'; color:{AZUL}; }}
      .cim-title {{ font-family:'Space Grotesk'; font-weight:700; font-size:30px; color:{AZUL}; letter-spacing:1px; }}
      .cim-sub {{ color:{NIEBLA}; font-size:14px; margin-top:2px; }}
      .leaflet-container {{ border-radius:12px; }}
    </style>
    """, unsafe_allow_html=True)


def _money(x) -> str:
    if pd.isna(x) or x == 0:
        return "—"
    if x >= 1_000_000:
        return f"${x/1_000_000:.2f} M"
    if x >= 1_000:
        return f"${x/1_000:.0f} k"
    return f"${x:,.0f}"


# ── Carga de datos (mapa) ─────────────────────────────────────────────────────────
def _normalize(df: pd.DataFrame, portal: str) -> pd.DataFrame:
    c = df.columns
    out = pd.DataFrame()
    out["portal"] = df["portal"] if "portal" in c else portal
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
    return out


@st.cache_data(ttl=120, show_spinner=False)
def load_map() -> pd.DataFrame:
    # Universo canónico: set deduplicado (solo venta ≤ 20 MDP). Fallback: CSVs crudos.
    dd = PROC / "_unificado_dedup.csv"
    if dd.exists():
        try:
            d = pd.read_csv(dd, on_bad_lines="skip", low_memory=False)
            df = _normalize(d, "dedup")
            return df[df["lat"].between(18.9, 20.1) & df["lng"].between(-99.6, -98.6)]
        except Exception:
            pass
    skip = ("_", "avm", "oportunidades", "crimen", "comercios", "indice")
    frames = []
    for csv in sorted(PROC.glob("*.csv")):
        if csv.stem.startswith(skip):
            continue
        try:
            d = pd.read_csv(csv, on_bad_lines="skip", low_memory=False)
        except Exception:
            continue
        if len(d):
            frames.append(_normalize(d, csv.stem))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df[df["lat"].between(18.9, 20.1) & df["lng"].between(-99.6, -98.6)]


@st.cache_data(ttl=120, show_spinner=False)
def load_avm():
    def _read_csv(p): return pd.read_csv(p) if p.exists() else None
    metrics = json.loads((PROC / "avm_metrics.json").read_text()) if (PROC / "avm_metrics.json").exists() else None
    return metrics, _read_csv(PROC / "avm_predicciones.csv"), _read_csv(PROC / "avm_importancias.csv"), _read_csv(PROC / "oportunidades_bj.csv")


@st.cache_data(ttl=120, show_spinner=False)
def load_plusvalia():
    p = PROC / "indice_plusvalia_colonia.csv"
    return pd.read_csv(p) if p.exists() else None


@st.cache_data(ttl=120, show_spinner=False)
def load_oportunidades():
    p = PROC / "avm_predicciones.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p)
    df["id"] = df["id"].astype(str)
    sp = PROC / "senales_anuncio.csv"
    if sp.exists():
        sen = pd.read_csv(sp, dtype={"id": str})[["portal", "id", "publicador", "es_remate", "fecha_pub"]].drop_duplicates(["portal", "id"])
        df = df.merge(sen, on=["portal", "id"], how="left")
    for c in ["publicador", "es_remate", "fecha_pub"]:
        if c not in df.columns:
            df[c] = pd.NA
    return df


@st.cache_data(ttl=120, show_spinner=False)
def load_publicadores():
    sp = PROC / "senales_anuncio.csv"
    if not sp.exists():
        return None
    sen = pd.read_csv(sp, dtype={"id": str})
    sen = sen[sen["publicador"].notna() & (sen["publicador"].astype(str).str.strip() != "")].copy()
    if not len(sen):
        return None
    sen["remate"] = sen["es_remate"].astype(str).str.lower().isin(["true", "1"]).astype(int)
    # subvaluación desde las predicciones del AVM
    pp = PROC / "avm_predicciones.csv"
    if pp.exists():
        pr = pd.read_csv(pp, dtype={"id": str})[["portal", "id", "subvaluacion_pct", "municipio"]]
        sen = sen.merge(pr, on=["portal", "id"], how="left")
    g = sen.groupby("publicador")
    out = pd.DataFrame({
        "anuncios": g.size(),
        "portal": g["portal"].agg(lambda s: s.mode().iat[0] if len(s.mode()) else ""),
        "pct_remate": (g["remate"].mean() * 100).round(0),
        "subval_med": g["subvaluacion_pct"].median().round(0) if "subvaluacion_pct" in sen else 0,
        "alcaldias": g["municipio"].nunique() if "municipio" in sen else 0,
    }).reset_index()
    out["sospechoso"] = (out["pct_remate"] >= 30) | (out["subval_med"] >= 35)
    return out.sort_values("anuncios", ascending=False)


st.markdown('<div class="cim-title">CIMENTA</div>', unsafe_allow_html=True)
st.markdown('<div class="cim-sub">Inteligencia inmobiliaria cuantitativa · CDMX y ZMVM</div>', unsafe_allow_html=True)
with st.sidebar:
    if st.button("🔄 Refrescar datos"):
        st.cache_data.clear(); st.rerun()

tab_mapa, tab_modelo, tab_opp, tab_plus, tab_pub = st.tabs(
    ["🗺️ Mapa", "🤖 Modelo (AVM)", "💎 Oportunidades", "📈 Plusvalía", "🏢 Publicadores"])

# ══════════════════════════════ MAPA ══════════════════════════════
with tab_mapa:
    df = load_map()
    if df.empty:
        st.warning("Aún no hay datos geolocalizados. Corre los scrapers (y geocode para Inmuebles24).")
    else:
        portales = sorted(df["portal"].dropna().unique())
        c1, c2, c3 = st.columns([2, 2, 1])
        f_port = c1.multiselect("Portal", portales, default=portales)
        ops = sorted(df["operacion"].dropna().unique())
        f_op = c2.multiselect("Operación", ops, default=ops)
        tile = c3.radio("Mapa", ["Minimalista", "OSM"], horizontal=True, label_visibility="collapsed")
        fdf = df[df["portal"].isin(f_port) & df["operacion"].isin(f_op)]

        k = st.columns(2 + len(portales))
        k[0].metric("Geolocalizadas", f"{len(fdf):,}")
        k[1].metric("Precio mediano venta", _money(fdf[fdf["operacion"] == "venta"]["precio"].median()))
        vc = fdf["portal"].value_counts()
        for i, p in enumerate(portales):
            k[2 + i].metric(p[:11], f"{int(vc.get(p, 0)):,}")

        L, R = st.columns([7, 5], gap="medium")
        with L:
            if len(fdf):
                fmap = folium.Map(location=[fdf["lat"].median(), fdf["lng"].median()], zoom_start=11,
                                  tiles="CartoDB positron" if tile == "Minimalista" else "OpenStreetMap")
                cl = MarkerCluster().add_to(fmap)
                for _, r in fdf.iterrows():
                    color = PORTAL_COLOR.get(r["portal"], NIEBLA)
                    nm = "" if pd.isna(r["name"]) else str(r["name"])[:60]
                    url = "" if pd.isna(r["url"]) else str(r["url"])
                    link = f"<a href='{url}' target='_blank'>Ver →</a>" if url else ""
                    folium.CircleMarker([r["lat"], r["lng"]], radius=4, color=color, weight=1, fill=True,
                                        fill_color=color, fill_opacity=0.7,
                                        popup=folium.Popup(f"<b>{nm}</b><br><span style='color:{color}'>{r['portal']}</span> · {r.get('operacion','')}<br><b>{_money(r['precio'])}</b> · {int(r['surface']) if pd.notna(r['surface']) else '—'} m²<br>{link}", max_width=240)).add_to(cl)
                st_folium(fmap, use_container_width=True, height=560, returned_objects=[])
            st.markdown("<div style='font-size:13px'>" + " · ".join(f"<span style='color:{PORTAL_COLOR.get(p, NIEBLA)}'>●</span> {p}" for p in portales) + "</div>", unsafe_allow_html=True)
        with R:
            show = fdf[["name", "portal", "operacion", "precio", "surface", "rooms", "colonia", "url"]].rename(
                columns={"name": "Anuncio", "portal": "Portal", "operacion": "Op.", "precio": "Precio", "surface": "m²", "rooms": "Rec", "colonia": "Colonia", "url": "Link"})
            st.dataframe(show, hide_index=True, height=560, width="stretch",
                         column_config={"Precio": st.column_config.NumberColumn(format="$ %d"),
                                        "Link": st.column_config.LinkColumn("Link", display_text="ver →")})

# ══════════════════════════════ MODELO ══════════════════════════════
with tab_modelo:
    metrics, preds, imp, _ = load_avm()
    if not metrics:
        st.info("Aún no hay modelo entrenado. Corre:  `python -m src.valuacion.avm`")
    else:
        best = next((m for m, d in metrics.items() if d.get("es_mejor")), list(metrics)[0])
        st.markdown(f"### Valuación automática (AVM)")
        st.caption("Estima el precio de venta justo de cada inmueble por sus características y ubicación. "
                   "**MdAPE** = error porcentual mediano (lo que más importa en valuación).")
        bm = metrics[best]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Mejor modelo", best)
        m2.metric("MdAPE (test)", f"{bm['test']['MdAPE']:.1f}%")
        m3.metric("R² (test)", f"{bm['test']['R2']:.3f}")
        m4.metric("MAE (test)", _money(bm['test']['MAE']))

        st.markdown("##### Comparación de modelos")
        rows = []
        for name, d in metrics.items():
            rows.append({"Modelo": ("★ " if d.get("es_mejor") else "") + name,
                         "MdAPE val": round(d["validation"]["MdAPE"], 1), "R² val": round(d["validation"]["R2"], 3),
                         "MdAPE test": round(d["test"]["MdAPE"], 1), "MAPE test": round(d["test"]["MAPE"], 1),
                         "R² test": round(d["test"]["R2"], 3), "MAE test": round(d["test"]["MAE"])})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"MAE test": st.column_config.NumberColumn(format="$ %d")})
        with st.expander("Métricas completas (train / validation / test)"):
            for name, d in metrics.items():
                st.markdown(f"**{name}**")
                st.dataframe(pd.DataFrame(d["train"] | {}, index=["train"]).join(
                    [pd.DataFrame(d["validation"], index=["validation"]), pd.DataFrame(d["test"], index=["test"])], how="outer")
                    if False else pd.DataFrame({k: d[k] for k in ["train", "validation", "test"]}).T.round(2),
                    width="stretch")

        cL, cR = st.columns([3, 2], gap="large")
        with cL:
            st.markdown("##### Valor estimado vs. precio real (out-of-fold)")
            if preds is not None and len(preds):
                s = preds.sample(min(2500, len(preds)), random_state=1)
                base = alt.Chart(s).mark_circle(opacity=0.25, size=16, color=ACENTO).encode(
                    x=alt.X("precio:Q", title="Precio real (MXN)", scale=alt.Scale(type="log")),
                    y=alt.Y("valor_estimado:Q", title="Valor estimado (MXN)", scale=alt.Scale(type="log")))
                ref = alt.Chart(pd.DataFrame({"x": [3e5, 1.5e8]})).mark_line(color=NIEBLA, strokeDash=[5, 5]).encode(x="x:Q", y="x:Q")
                st.altair_chart(base + ref, use_container_width=True)
        with cR:
            st.markdown("##### Importancia de variables")
            if imp is not None and len(imp):
                st.bar_chart(imp.set_index("variable")["importancia"])

# ══════════════════════════════ OPORTUNIDADES ══════════════════════════════
with tab_opp:
    df = load_oportunidades()
    st.markdown("### Buscador de oportunidades")
    st.caption("Propiedades **por debajo de su valor estimado** (AVM). Elige zona/precio/tipo; "
               "el sistema **excluye remates/subastas** y **diversifica por publicador** "
               "(evita la inundación de una sola empresa). Banda 15–45% = oportunidad creíble "
               "(arriba suele ser error de dato).")
    if df is None or not len(df):
        st.info("Aún no hay valuaciones. Corre:  `python -m src.valuacion.avm`")
    else:
        c1, c2, c3, c4 = st.columns(4)
        alc = ["(todas)"] + sorted(df["municipio"].dropna().unique())
        f_alc = c1.selectbox("Alcaldía", alc, index=alc.index("Benito Juárez") if "Benito Juárez" in alc else 0)
        tipos = sorted(df["tipo_norm"].dropna().unique())
        defa = [t for t in ["casa", "departamento"] if t in tipos] or tipos
        f_tipo = c2.multiselect("Tipo", tipos, default=defa)
        precio_max = c3.slider("Precio máx (MDP)", 0.5, 20.0, 3.0, 0.5)
        subval = c4.slider("Subvaluación %", 0, 60, (15, 45))
        cA, cB = st.columns([1, 2])
        excl = cA.checkbox("Excluir remates", True)
        max_pub = cB.slider("Máx por publicador", 1, 10, 3)

        v = df[df["tipo_norm"].isin(f_tipo) & (df["precio"] < precio_max * 1e6)
               & df["subvaluacion_pct"].between(subval[0], subval[1])].copy()
        if f_alc != "(todas)":
            v = v[v["municipio"] == f_alc]
        if excl:
            v = v[~v["es_remate"].astype(str).str.lower().isin(["true", "1"])]
        v = v.sort_values("subvaluacion_pct", ascending=False)
        v["_pub"] = v["publicador"].fillna("").replace("", "desconocido")
        v = v.groupby("_pub", sort=False, group_keys=False).head(max_pub)

        m1, m2, m3 = st.columns(3)
        m1.metric("Oportunidades", f"{len(v):,}")
        m2.metric("Subvaluación mediana", f"{v['subvaluacion_pct'].median():.0f}%" if len(v) else "—")
        m3.metric("Descuento mediano", _money((v["valor_estimado"] - v["precio"]).median()) if len(v) else "—")

        disp = v.rename(columns={"tipo_norm": "Tipo", "municipio": "Alcaldía", "colonia": "Colonia",
                                 "precio": "Precio", "valor_estimado": "Valor estimado",
                                 "subvaluacion_pct": "Subval %", "surface": "m²", "rooms": "Rec",
                                 "url": "Link", "portal": "Portal", "publicador": "Publicador"})
        base = ["Tipo", "Colonia", "Precio", "Valor estimado", "Subval %", "m²", "Rec", "Publicador", "Portal", "Link"]
        if f_alc == "(todas)":
            base.insert(1, "Alcaldía")
        cols = [c for c in base if c in disp.columns]
        st.dataframe(disp[cols], hide_index=True, width="stretch", height=520,
                     column_config={"Precio": st.column_config.NumberColumn(format="$ %d"),
                                    "Valor estimado": st.column_config.NumberColumn(format="$ %d"),
                                    "Subval %": st.column_config.NumberColumn(format="%.0f%%"),
                                    "Link": st.column_config.LinkColumn("Link", display_text="ver →")})

# ══════════════════════════════ PLUSVALÍA ══════════════════════════════
with tab_plus:
    idx = load_plusvalia()
    st.markdown("### Momentum de plusvalía por colonia")
    st.caption("Índice 0–100 de indicadores **líderes** (datos públicos): seguridad "
               "(tendencia de delincuencia FGJ), crecimiento de comercio y vitalidad "
               "comercial (DENUE-INEGI). Responde *¿esta colonia va al alza?* — no es "
               "un pronóstico supervisado de precio (eso requiere histórico, que se acumula).")
    if idx is None or not len(idx):
        st.info("Aún no hay índice. Corre:  `python -m src.plusvalia.crimen && "
                "python -m src.plusvalia.comercios && python -m src.plusvalia.indice`")
    else:
        alcaldias = ["(todas)"] + sorted(idx["alcaldia"].dropna().unique())
        f_alc = st.selectbox("Alcaldía", alcaldias, index=alcaldias.index("BENITO JUAREZ") if "BENITO JUAREZ" in alcaldias else 0)
        v = idx if f_alc == "(todas)" else idx[idx["alcaldia"] == f_alc]
        v = v.sort_values("indice_momentum", ascending=False)

        a, b, c = st.columns(3)
        a.metric("Colonias", f"{len(v):,}")
        b.metric("Momentum mediano", f"{v['indice_momentum'].median():.0f}/100")
        b2 = v["crimen_tendencia_pct"].median()
        c.metric("Tendencia crimen (mediana)", f"{b2:+.0f}%")

        L, R = st.columns([3, 4], gap="large")
        with L:
            st.markdown("##### Top colonias por momentum")
            top = v.head(15).set_index("colonia")["indice_momentum"]
            st.bar_chart(top, horizontal=True, color=VERDE)
        with R:
            st.markdown("##### Detalle")
            disp = v.rename(columns={"colonia": "Colonia", "alcaldia": "Alcaldía",
                                     "indice_momentum": "Momentum", "crimen_tendencia_pct": "Crimen Δ%",
                                     "comercios_nuevos_pct": "Com. nuevos %",
                                     "pct_construccion_reciente": "Obra nueva %",
                                     "valor_suelo_m2": "Suelo $/m²", "precio_m2_mediano": "$/m²"})
            cols = [c for c in ["Colonia", "Alcaldía", "Momentum", "Crimen Δ%", "Com. nuevos %",
                                "Obra nueva %", "$/m²"] if c in disp.columns]
            if f_alc != "(todas)":
                cols = [c for c in cols if c != "Alcaldía"]
            st.dataframe(disp[cols], hide_index=True, height=520, width="stretch",
                         column_config={"Momentum": st.column_config.ProgressColumn("Momentum", min_value=0, max_value=100, format="%.0f"),
                                        "$/m²": st.column_config.NumberColumn(format="$ %d"),
                                        "Crimen Δ%": st.column_config.NumberColumn(format="%.0f%%"),
                                        "Com. nuevos %": st.column_config.NumberColumn(format="%.1f%%")})
        st.caption("Fuentes: Carpetas de investigación FGJ-CDMX · DENUE (INEGI) · precios CIMENTA.")

# ══════════════════════════════ PUBLICADORES ══════════════════════════════
with tab_pub:
    pub = load_publicadores()
    st.markdown("### Reputación de publicadores")
    st.caption("Quién publica cada anuncio y su perfil. Útil para detectar al **publicador "
               "que inunda con remates**: muchos anuncios + alto **% de remates** o alta "
               "**subvaluación** = sospechoso (marcado 🚩).")
    if pub is None or not len(pub):
        st.info("Aún no hay señales de publicador. Corre:  `python -m src.ingesta.senales`  "
                "(requiere portales con publicador: CyT, Inmuebles24, Vivanuncios).")
    else:
        c1, c2, c3 = st.columns(3)
        min_anuncios = c1.slider("Mín. anuncios", 1, 50, 5)
        solo_sosp = c2.checkbox("Solo sospechosos 🚩", False)
        v = pub[pub["anuncios"] >= min_anuncios].copy()
        if solo_sosp:
            v = v[v["sospechoso"]]
        c3.metric("Publicadores", f"{len(v):,}")

        v["🚩"] = v["sospechoso"].map({True: "🚩", False: ""})
        disp = v.rename(columns={"publicador": "Publicador", "anuncios": "Anuncios",
                                 "portal": "Portal", "pct_remate": "% Remate",
                                 "subval_med": "Subval. mediana %", "alcaldias": "Alcaldías"})
        cols = [c for c in ["🚩", "Publicador", "Portal", "Anuncios", "% Remate",
                            "Subval. mediana %", "Alcaldías"] if c in disp.columns]
        st.dataframe(disp[cols], hide_index=True, width="stretch", height=540,
                     column_config={"% Remate": st.column_config.NumberColumn(format="%.0f%%"),
                                    "Subval. mediana %": st.column_config.NumberColumn(format="%.0f%%"),
                                    "Anuncios": st.column_config.NumberColumn(format="%d")})
        st.caption("🚩 = ≥30% de sus anuncios son remates, o subvaluación mediana ≥35% "
                   "(precios muy por debajo del valor → revisar antes de confiar).")
