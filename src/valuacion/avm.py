"""
CIMENTA · M2 Valuación (AVM) + M5 Oportunidades (subvaluación)

Entrena un modelo de valuación automática (AVM) que estima el precio de venta de
cada inmueble a partir de sus características y ubicación, compara varios modelos
(un gradient boosting + clásicos: Random Forest y Ridge), reporta métricas en
train / validation / test, y detecta inmuebles SUBVALUADOS (precio por debajo del
valor estimado).

Entradas: data/processed/_unificado_dedup.csv  (o se construye desde los CSVs).
Salidas (data/processed/):
  · avm_metrics.json        — métricas por modelo y split (MAE, RMSE, R², MAPE, MdAPE)
  · avm_importancias.csv    — importancia de variables del mejor modelo
  · avm_predicciones.csv    — cada inmueble con valor_estimado y subvaluacion_pct (out-of-fold)
  · oportunidades_bj.csv    — 20 casas/deptos de Benito Juárez < 3 MDP, subvaluadas

Uso:
    python -m src.valuacion.avm
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, cross_val_predict, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
SRC = PROC / "_unificado_dedup.csv"

NUM = ["surface", "rooms", "bathrooms", "cat_valor_suelo", "antiguedad",
       "cat_intensidad", "dist_transporte_km", "est_1km"]
CAT = ["municipio", "colonia", "tipo_norm"]
SEED = 42


def norm_tipo(t) -> str:
    t = str(t).lower()
    if "depart" in t or "apart" in t or "loft" in t:
        return "departamento"
    if "casa" in t or "house" in t or "residence" in t or "home" in t:
        return "casa"
    if "terren" in t or "lote" in t or "land" in t:
        return "terreno"
    if "ofic" in t:
        return "oficina"
    if "local" in t or "comerc" in t:
        return "local"
    if "bodega" in t or "nave" in t:
        return "bodega"
    if "edific" in t:
        return "edificio"
    return "otro"


def load() -> pd.DataFrame:
    if SRC.exists():
        df = pd.read_csv(SRC, low_memory=False)
    else:
        from src.ingesta.dedup import load_unified
        df = load_unified()
    # enriquecer con features espaciales precomputadas (join por uid único)
    for fn in ("catastro_features.csv", "transporte_features.csv"):
        fp = PROC / fn
        if fp.exists() and "uid" in df.columns:
            feat = pd.read_csv(fp).drop_duplicates(subset="uid", keep="first")
            df = df.merge(feat, on="uid", how="left")
    for col in ["cat_valor_suelo", "cat_anio", "cat_intensidad", "dist_transporte_km", "est_1km"]:
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    df["antiguedad"] = 2026 - df["cat_anio"]

    df = df[df["operacion"] == "venta"].copy()
    for c in ["precio", "surface", "rooms", "bathrooms"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["tipo_norm"] = df["tipo"].map(norm_tipo)
    # saneamiento: precios y superficies razonables, $/m² creíble para CDMX
    df = df[df["precio"].between(300_000, 20_000_000)]  # universo: venta ≤ 20 MDP
    df = df[df["surface"].between(20, 2000)]
    df = df[df["colonia"].notna() & df["municipio"].notna()]
    ppm = df["precio"] / df["surface"]
    df = df[ppm.between(3_000, 250_000)]
    df = df[df["tipo_norm"].isin(["departamento", "casa", "terreno", "oficina", "local", "edificio"])]
    return df.reset_index(drop=True)


def make_pre() -> ColumnTransformer:
    return ColumnTransformer([
        ("num", SimpleImputer(strategy="median"), NUM),
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=5, sparse_output=False), CAT),
    ])


def models() -> dict:
    return {
        "Gradient Boosting": HistGradientBoostingRegressor(
            max_iter=500, learning_rate=0.06, max_leaf_nodes=63,
            l2_regularization=1.0, random_state=SEED),
        "Random Forest": RandomForestRegressor(
            n_estimators=300, min_samples_leaf=2, n_jobs=-1, random_state=SEED),
        "Ridge (lineal)": Ridge(alpha=2.0),
    }


def build_pipe(name: str, model) -> Pipeline:
    steps = [("pre", make_pre())]
    if "Ridge" in name or "lineal" in name.lower():
        steps.append(("sc", StandardScaler()))  # los lineales necesitan escalado
    steps.append(("m", model))
    return Pipeline(steps)


def score(y_true, y_pred) -> dict:
    """Métricas en escala de PESOS (y viene en log1p)."""
    yt, yp = np.expm1(y_true), np.clip(np.expm1(y_pred), 1e5, 3e7)  # acota a rango sensato
    ape = np.abs((yp - yt) / yt)
    return {
        "MAE": float(mean_absolute_error(yt, yp)),
        "RMSE": float(mean_squared_error(yt, yp) ** 0.5),
        "R2": float(r2_score(yt, yp)),
        "MAPE": float(np.mean(ape) * 100),
        "MdAPE": float(np.median(ape) * 100),
    }


def main():
    df = load()
    print(f"Inmuebles de entrenamiento (venta, saneados): {len(df):,}")
    X = df[NUM + CAT]
    y = np.log1p(df["precio"].to_numpy())

    # split 60 / 20 / 20  (train / validation / test)
    X_tr, X_tmp, y_tr, y_tmp = train_test_split(X, y, test_size=0.40, random_state=SEED)
    X_val, X_te, y_val, y_te = train_test_split(X_tmp, y_tmp, test_size=0.50, random_state=SEED)
    print(f"  train={len(X_tr):,}  val={len(X_val):,}  test={len(X_te):,}")

    metrics, fitted = {}, {}
    for name, model in models().items():
        pipe = build_pipe(name, model)
        pipe.fit(X_tr, y_tr)
        metrics[name] = {
            "train": score(y_tr, pipe.predict(X_tr)),
            "validation": score(y_val, pipe.predict(X_val)),
            "test": score(y_te, pipe.predict(X_te)),
        }
        fitted[name] = pipe
        m = metrics[name]
        print(f"  {name:18} | val MdAPE {m['validation']['MdAPE']:5.1f}%  R² {m['validation']['R2']:.3f}  | test MdAPE {m['test']['MdAPE']:5.1f}%")

    best = min(metrics, key=lambda k: metrics[k]["validation"]["MdAPE"])
    print(f"Mejor modelo (val MdAPE): {best}")
    for name in metrics:
        metrics[name]["es_mejor"] = (name == best)

    PROC.mkdir(parents=True, exist_ok=True)
    (PROC / "avm_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False))

    # ── Valor estimado out-of-fold (cada inmueble valuado por un modelo que NO lo vio) ──
    best_pipe = build_pipe(best, models()[best])
    oof = np.expm1(cross_val_predict(best_pipe, X, y, cv=KFold(5, shuffle=True, random_state=SEED)))
    df["valor_estimado"] = np.clip(oof, 1e5, 3e7).round(0)
    df["subvaluacion_pct"] = ((df["valor_estimado"] - df["precio"]) / df["valor_estimado"] * 100).round(1)

    pred_cols = ["portal", "id", "tipo_norm", "operacion", "municipio", "colonia",
                 "precio", "valor_estimado", "subvaluacion_pct", "surface", "rooms",
                 "bathrooms", "lat", "lng", "url"]
    df[[c for c in pred_cols if c in df.columns]].to_csv(PROC / "avm_predicciones.csv", index=False)

    # ── Importancia de variables (permutación sobre el test, mejor modelo) ──
    best_fitted = fitted[best]
    try:
        imp = permutation_importance(best_fitted, X_te, y_te, n_repeats=5, random_state=SEED, n_jobs=-1)
        pd.DataFrame({"variable": NUM + CAT, "importancia": imp.importances_mean}) \
            .sort_values("importancia", ascending=False) \
            .to_csv(PROC / "avm_importancias.csv", index=False)
    except Exception as e:
        print("  (importancias omitidas:", e, ")")

    # ── M5 · Oportunidades: 20 casas/deptos BJ < 3 MDP subvaluadas ──
    # Banda creíble de subvaluación: 15–45%. Por encima de 45% en zona cara suele
    # ser ERROR DE DATO (precio mal capturado, no oportunidad real) — se excluye.
    bj = df[
        df["municipio"].str.contains("benito", case=False, na=False)
        & df["tipo_norm"].isin(["casa", "departamento"])
        & (df["precio"] < 3_000_000)
        & (df["subvaluacion_pct"].between(15, 45))
    ].copy()
    bj = bj.sort_values("subvaluacion_pct", ascending=False)
    opp = bj.sample(min(20, len(bj)), random_state=SEED) if len(bj) else bj
    opp = opp.sort_values("subvaluacion_pct", ascending=False)
    opp_cols = ["portal", "tipo_norm", "colonia", "precio", "valor_estimado",
                "subvaluacion_pct", "surface", "rooms", "bathrooms", "url"]
    opp[[c for c in opp_cols if c in opp.columns]].to_csv(PROC / "oportunidades_bj.csv", index=False)

    print(f"\nOportunidades BJ (<3 MDP, subvaluadas ≥15%): {len(bj)} candidatas → {len(opp)} seleccionadas")
    print(f"Artefactos en {PROC}/  (avm_metrics.json, avm_predicciones.csv, oportunidades_bj.csv)")


if __name__ == "__main__":
    main()
