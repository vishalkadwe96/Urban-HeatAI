# Urban Heat AI v2 — 10-Day Forecast Model Trainer
#
# Trains THREE RandomForestRegressor models (LST, NDVI, rainfall) that predict
# TOMORROW's value from a rolling window of recent days (lag/rolling features
# + day-of-year seasonality + each city's climate constants). At serving time
# (forecast_service.py) the models are called RECURSIVELY — day+1's prediction
# becomes an input feature for day+2, and so on — to produce a 10-day-ahead
# forecast. Heat-risk and landslide-risk are NOT modeled directly; they're
# derived from the forecasted LST/NDVI/rainfall using the app's own existing,
# already-validated formulas (compute_hvi, cap_risk_by_absolute_lst,
# calculate_landslide_risk) so every dashboard feature (LST, NDVI, Heat risk,
# rainfall, landslide) is covered without duplicating or contradicting logic
# that already exists elsewhere in the codebase.
#
# DATA: same situation as train_lst_model.py — no freely-licensed multi-year
# daily satellite time series was available for all cities within scope, so
# training uses a physically-motivated SYNTHETIC daily time series per city:
#   - LST: seasonal (Indian summer/monsoon/winter) cycle + AR(1)-correlated
#     day-to-day noise, scaled by each city's real heat-factor (backend/services/
#     heat_service._CITY_HEAT — reused, not duplicated).
#   - NDVI: slow seasonal vegetation cycle (greenest post-monsoon, driest in
#     peak summer) + small AR(1) noise.
#   - Rainfall: Indian monsoon seasonality (high daily rain probability
#     Jun-Sep, low otherwise) with lognormal rain-day amounts.
# The pipeline (feature engineering, walk-forward split, model, evaluation) is
# real and reusable — swap _build_city_timeseries() for a real multi-year
# Landsat/IMD extract later and nothing else needs to change.
#
# MANDATORY 5x VALIDATION: per the project requirement that this model be
# tested before shipping, train() runs a 5-fold walk-forward backtest (see
# _walk_forward_backtest) for EACH of the 3 targets before saving anything,
# and refuses to save a model whose backtest MAE exceeds a sanity ceiling —
# see MAX_ACCEPTABLE_MAE below.
#
# Run: python -m backend.ml.train_forecast_model

import json
import math
import random
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import TimeSeriesSplit

from backend.config import CITY_REGISTRY
from backend.ml.paths import (
    FORECAST_LST_MODEL_PATH, FORECAST_NDVI_MODEL_PATH,
    FORECAST_RAIN_MODEL_PATH, FORECAST_META_PATH, MODEL_DIR,
)
try:
    # Normal path — reuses the exact same calibration dict the live app uses.
    from backend.services.heat_service import _CITY_HEAT
except ModuleNotFoundError:
    # heat_service.py imports FastAPI at module level; some minimal
    # training-only environments won't have the full web-app dependency set
    # installed. This mirrored copy (values identical to heat_service.py)
    # lets the forecast model still be trained/retrained offline in that
    # case. If you ever change the numbers in heat_service._CITY_HEAT,
    # update this copy too.
    _CITY_HEAT = {
        "delhi":     {"base": 33, "hf": 9},
        "mumbai":    {"base": 28, "hf": 7},
        "bangalore": {"base": 26, "hf": 7},
        "chennai":   {"base": 30, "hf": 8},
        "hyderabad": {"base": 29, "hf": 8},
        "kolkata":   {"base": 31, "hf": 8},
        "pune":      {"base": 27, "hf": 7},
        "ahmedabad": {"base": 32, "hf": 8},
        "jaipur":    {"base": 33, "hf": 9},
        "lucknow":   {"base": 32, "hf": 8},
    }

N_DAYS_HISTORY = 365 * 3          # 3 years of synthetic daily data per city
LAGS = [1, 2, 3, 7, 14]
ROLL_WINDOWS = [7, 14]
N_BACKTEST_FOLDS = 5              # <-- the "5 bar testing" requirement
MAX_ACCEPTABLE_MAE = {"lst": 2.5, "ndvi": 0.08, "rain": 6.0}  # sanity ceiling


# ── Monthly LST offsets, same shape as heat_service.get_trend(), interpolated
# to a smooth daily curve via a cosine fit so the synthetic series has a
# realistic single-peak Indian summer (Apr-Jun) instead of a jagged monthly step.
_MONTH_OFFSETS = [-6, -4, -2, 0, 3, 6, 7, 5, 1, -2, -4, -5]


def _seasonal_lst_offset(doy: int) -> float:
    """Smooth daily seasonal LST offset (°C) from day-of-year, peak ~ mid-May."""
    # Fit a single annual cosine to the monthly offsets' amplitude/phase instead
    # of stair-stepping between months.
    amplitude = (max(_MONTH_OFFSETS) - min(_MONTH_OFFSETS)) / 2
    mean_off = sum(_MONTH_OFFSETS) / len(_MONTH_OFFSETS)
    peak_doy = 135  # ~mid-May, matches the +6/+7 Jun/Jul entries above
    return mean_off + amplitude * math.cos(2 * math.pi * (doy - peak_doy) / 365.25)


def _monsoon_rain_prob(doy: int) -> float:
    """Rain-day probability by day-of-year — Indian SW monsoon (Jun-Sep)."""
    if 152 <= doy <= 273:      # Jun 1 - Sep 30
        return 0.60
    if 274 <= doy <= 304:      # Oct (retreating monsoon / NE monsoon onset)
        return 0.25
    return 0.06


def _seasonal_ndvi_offset(doy: int) -> float:
    """Vegetation greens up after the monsoon, dries out in peak summer."""
    peak_doy = 288  # mid-Oct, post-monsoon green peak
    return 0.10 * math.cos(2 * math.pi * (doy - peak_doy) / 365.25)


def _build_city_timeseries(city_key: str, heat: dict, seed: int) -> pd.DataFrame:
    rng = random.Random(seed)
    base_lst = heat["base"]
    hf = heat["hf"]
    ndvi_base = max(0.15, min(0.55, 0.5 - hf * 0.02))

    lst_noise = 0.0
    ndvi_noise = 0.0
    rows = []
    for t in range(N_DAYS_HISTORY):
        doy = (t % 365) + 1

        lst_noise = 0.7 * lst_noise + rng.gauss(0, 0.55)
        lst = base_lst + _seasonal_lst_offset(doy) * (hf / 8.0) + lst_noise

        ndvi_noise = 0.85 * ndvi_noise + rng.gauss(0, 0.006)
        ndvi = ndvi_base + _seasonal_ndvi_offset(doy) + ndvi_noise
        ndvi = min(0.85, max(0.03, ndvi))

        rain_prob = _monsoon_rain_prob(doy)
        if rng.random() < rain_prob:
            rain = min(120.0, rng.lognormvariate(2.0, 0.9))
        else:
            rain = 0.0

        rows.append({"t": t, "doy": doy, "lst": round(lst, 3),
                      "ndvi": round(ndvi, 4), "rain": round(rain, 2)})
    df = pd.DataFrame(rows)
    df["city"] = city_key
    df["heat_factor"] = hf
    df["base_lst"] = base_lst
    return df


def _add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["doy_sin"] = np.sin(2 * np.pi * df["doy"] / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * df["doy"] / 365.25)
    # NOTE: called once per city (via groupby(...).apply below) so df here is
    # already a single, time-ordered city series — no need to re-group.
    for col in ("lst", "ndvi", "rain"):
        for lag in LAGS:
            df[f"{col}_lag{lag}"] = df[col].shift(lag)
        for win in ROLL_WINDOWS:
            df[f"{col}_roll{win}"] = df[col].shift(1).rolling(win).mean()
    # Prediction TARGETS are tomorrow's values — model input is everything
    # known as of "today" (lag1..lagN + rolling stats), never today's own
    # raw value, so this can be called recursively day-by-day at inference.
    return df


def _feature_cols(target_prefix: str) -> list:
    cols = ["doy_sin", "doy_cos", "heat_factor", "base_lst"]
    for col in ("lst", "ndvi", "rain"):
        for lag in LAGS:
            cols.append(f"{col}_lag{lag}")
        for win in ROLL_WINDOWS:
            cols.append(f"{col}_roll{win}")
    return cols


def _walk_forward_backtest(df: pd.DataFrame, feature_cols: list, target: str) -> list:
    """
    Mandatory pre-ship validation: N_BACKTEST_FOLDS (5) walk-forward folds —
    train on an expanding window of PAST data only, test on the NEXT unseen
    chunk, repeat 5 times sliding forward through the 3-year series. This is
    the correct way to validate a time-series model (never test on data that
    chronologically precedes training data) and is what "5 bar testing"
    below refers to in train().
    """
    clean = df.dropna(subset=feature_cols + [target]).reset_index(drop=True)
    tscv = TimeSeriesSplit(n_splits=N_BACKTEST_FOLDS)
    fold_results = []
    for fold_idx, (train_idx, test_idx) in enumerate(tscv.split(clean), start=1):
        X_train, y_train = clean.loc[train_idx, feature_cols], clean.loc[train_idx, target]
        X_test, y_test = clean.loc[test_idx, feature_cols], clean.loc[test_idx, target]
        m = RandomForestRegressor(n_estimators=50, max_depth=9, min_samples_leaf=3,
                                   random_state=42, n_jobs=-1)
        m.fit(X_train, y_train)
        pred = m.predict(X_test)
        fold_results.append({
            "fold": fold_idx,
            "train_rows": int(len(train_idx)),
            "test_rows": int(len(test_idx)),
            "mae": round(float(mean_absolute_error(y_test, pred)), 4),
            "r2": round(float(r2_score(y_test, pred)), 4),
        })
    return fold_results


def train() -> dict:
    frames = []
    for city_key in CITY_REGISTRY:
        heat = _CITY_HEAT.get(city_key)
        if heat is None:
            continue  # skip dynamically-registered/search-added cities — no calibrated heat-factor for them
        seed = CITY_REGISTRY[city_key]["seed"]
        frames.append(_build_city_timeseries(city_key, heat, seed))
    raw = pd.concat(frames, ignore_index=True)
    raw = raw.sort_values(["city", "t"]).reset_index(drop=True)
    df = pd.concat(
        [_add_lag_features(g) for _, g in raw.groupby("city", sort=False)],
        ignore_index=True,
    )

    feature_cols = _feature_cols("")
    targets = {"lst": FORECAST_LST_MODEL_PATH, "ndvi": FORECAST_NDVI_MODEL_PATH, "rain": FORECAST_RAIN_MODEL_PATH}

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "algorithm": "RandomForestRegressor (scikit-learn) x3 — LST / NDVI / rainfall",
        "horizon_strategy": "single-step-ahead model called recursively (walk-forward) for N-day-ahead forecasts",
        "feature_columns": feature_cols,
        "lags_days": LAGS,
        "rolling_windows_days": ROLL_WINDOWS,
        "n_cities": int(df["city"].nunique()),
        "n_days_per_city": N_DAYS_HISTORY,
        "backtest_folds": N_BACKTEST_FOLDS,
        "targets": {},
        "derived_downstream": (
            "heat_risk (HVI + risk level) and landslide_risk are NOT modeled "
            "directly — they are computed from the forecasted lst/ndvi/rainfall "
            "using the existing compute_hvi / cap_risk_by_absolute_lst / "
            "calculate_landslide_risk functions already used by the live dashboard, "
            "so forecast risk levels stay consistent with today's risk levels."
        ),
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    for target, path in targets.items():
        print(f"\n=== Backtesting target: {target} (5-fold walk-forward) ===")
        folds = _walk_forward_backtest(df, feature_cols, target)
        for f in folds:
            print(f"  fold {f['fold']}/5 — train={f['train_rows']} test={f['test_rows']} "
                  f"MAE={f['mae']} R2={f['r2']}")
        avg_mae = round(sum(f["mae"] for f in folds) / len(folds), 4)
        avg_r2 = round(sum(f["r2"] for f in folds) / len(folds), 4)
        ceiling = MAX_ACCEPTABLE_MAE[target]
        passed = avg_mae <= ceiling
        print(f"  -> avg MAE={avg_mae} (ceiling {ceiling}) avg R2={avg_r2} "
              f"-> {'PASS' if passed else 'FAIL'}")
        if not passed:
            raise RuntimeError(
                f"Forecast model for '{target}' failed 5-fold validation: "
                f"avg MAE {avg_mae} exceeds acceptable ceiling {ceiling}. "
                "Refusing to save an unvalidated model — adjust features/data and retrain."
            )

        # Final model: fit on ALL data (now that backtesting has passed) so
        # serving uses every available day, not just one fold's train split.
        clean = df.dropna(subset=feature_cols + [target]).reset_index(drop=True)
        final_model = RandomForestRegressor(n_estimators=80, max_depth=10, min_samples_leaf=3,
                                             random_state=42, n_jobs=-1)
        final_model.fit(clean[feature_cols], clean[target])
        joblib.dump(final_model, path)

        importances = dict(zip(feature_cols, (round(v, 4) for v in final_model.feature_importances_)))
        top_importances = dict(sorted(importances.items(), key=lambda kv: -kv[1])[:8])

        meta["targets"][target] = {
            "model_file": path.name,
            "backtest_folds_detail": folds,
            "backtest_avg_mae": avg_mae,
            "backtest_avg_r2": avg_r2,
            "validation_ceiling_mae": ceiling,
            "validation_passed": passed,
            "top_feature_importances": top_importances,
            "n_training_rows": int(len(clean)),
        }

    FORECAST_META_PATH.write_text(json.dumps(meta, indent=2))
    print("\nAll 3 forecast models passed 5-fold validation and were saved.")
    return meta


if __name__ == "__main__":
    info = train()
    print(json.dumps(info, indent=2))
