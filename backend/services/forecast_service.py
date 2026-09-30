# Urban Heat AI v2 — 10-Day Forecast Service
#
# Serves the models trained by backend/ml/train_forecast_model.py. Produces a
# day-by-day forecast covering EVERY feature already on the dashboard:
#   - LST            <- ML model (recursive)
#   - NDVI            <- ML model (recursive)
#   - rainfall (mm)   <- ML model (recursive)
#   - heat risk (HVI + level)  <- derived from forecasted lst/ndvi via the
#                                  SAME compute_hvi()/cap_risk_by_absolute_lst()
#                                  functions heat_service.py already uses today
#   - landslide risk  <- derived from forecasted rain/ndvi + today's real
#                         slope via the SAME calculate_landslide_risk()
#                         climate_risk_service.py already uses today
#
# Nothing in heat_service.py / climate_risk_service.py / utils/helpers.py is
# modified — this module only IMPORTS and reuses their existing, already-live
# functions, so today's dashboard numbers are completely unaffected and the
# forecast stays consistent with them (same formulas, projected forward).
#
# joblib/scikit-learn are imported LAZILY inside _load(), same defensive
# pattern as ml_service.py — a broken sklearn install only disables the
# forecast feature, it never crashes the whole backend.

import json
import math
import random
from datetime import date, timedelta

from backend.config import CITY_REGISTRY
from backend.ml.paths import (
    FORECAST_LST_MODEL_PATH, FORECAST_NDVI_MODEL_PATH,
    FORECAST_RAIN_MODEL_PATH, FORECAST_META_PATH,
)
from backend.utils.helpers import compute_hvi, risk_level_from_hvi
from backend.services.heat_service import (
    _CITY_HEAT, _base_lst_for_city, _generate_grid, cap_risk_by_absolute_lst,
)
from backend.services.climate_risk_service import calculate_landslide_risk

LAGS = [1, 2, 3, 7, 14]
ROLL_WINDOWS = [7, 14]
HISTORY_DAYS = 14           # synthetic lag warm-up window ending "today"
MAX_TOTAL_HORIZON = 16      # hard safety cap on how far the recursive AR loop runs
MAX_START_OFFSET = 6        # how far in the future `start_date` may be from today
MAX_REQUEST_DAYS = 10       # default + max days per request

_MONTH_OFFSETS = [-6, -4, -2, 0, 3, 6, 7, 5, 1, -2, -4, -5]

_models = {"lst": None, "ndvi": None, "rain": None}
_meta = None
_load_attempted = False
_load_error = None


def _load():
    global _meta, _load_attempted, _load_error
    if _load_attempted:
        return
    _load_attempted = True
    try:
        import joblib  # noqa: local import — see module docstring
        _models["lst"] = joblib.load(FORECAST_LST_MODEL_PATH)
        _models["ndvi"] = joblib.load(FORECAST_NDVI_MODEL_PATH)
        _models["rain"] = joblib.load(FORECAST_RAIN_MODEL_PATH)
        _meta = json.loads(FORECAST_META_PATH.read_text())
    except Exception as e:
        _models["lst"] = _models["ndvi"] = _models["rain"] = None
        _meta = None
        _load_error = str(e)


def is_ready() -> bool:
    _load()
    return all(_models.values())


def get_forecast_model_info() -> dict:
    _load()
    if _meta is None:
        msg = "Forecast models not trained yet. Run: python -m backend.ml.train_forecast_model"
        if _load_error:
            msg = (f"Forecast ML models could not be loaded ({_load_error}). "
                   "Falling back to the seasonal formula for predictions. "
                   "Run `python -m backend.ml.train_forecast_model` to (re)train.")
        return {"model_loaded": False, "message": msg}
    return {"model_loaded": True, **_meta}


# ── Same seasonal shape functions used at training time (kept in sync) ────────
def _seasonal_lst_offset(doy: int) -> float:
    amplitude = (max(_MONTH_OFFSETS) - min(_MONTH_OFFSETS)) / 2
    mean_off = sum(_MONTH_OFFSETS) / len(_MONTH_OFFSETS)
    peak_doy = 135
    return mean_off + amplitude * math.cos(2 * math.pi * (doy - peak_doy) / 365.25)


def _seasonal_ndvi_offset(doy: int) -> float:
    peak_doy = 288
    return 0.10 * math.cos(2 * math.pi * (doy - peak_doy) / 365.25)


def _monsoon_rain_prob(doy: int) -> float:
    if 152 <= doy <= 273:
        return 0.60
    if 274 <= doy <= 304:
        return 0.25
    return 0.06


def _synthetic_history(city_key: str, heat: dict, seed: int, today: date) -> dict:
    """
    Builds HISTORY_DAYS of synthetic-but-seasonally-correct lst/ndvi/rain
    ending exactly on `today`, using the same generative shape as training,
    then anchors (shifts) the whole window so TODAY's value matches reality
    — see _anchor_today() below. Deterministic per city+date (rng seeded on
    city seed + today's ordinal day) so repeated calls on the same day are
    stable, but the run before/after midnight naturally rolls forward.
    """
    rng = random.Random(seed * 100003 + today.toordinal())
    base_lst, hf = heat["base"], heat["hf"]
    ndvi_base = max(0.15, min(0.55, 0.5 - hf * 0.02))

    lst_noise = ndvi_noise = 0.0
    lst_hist, ndvi_hist, rain_hist, doy_hist = [], [], [], []
    start = today - timedelta(days=HISTORY_DAYS - 1)
    for k in range(HISTORY_DAYS):
        d = start + timedelta(days=k)
        doy = d.timetuple().tm_yday

        lst_noise = 0.7 * lst_noise + rng.gauss(0, 0.55)
        lst = base_lst + _seasonal_lst_offset(doy) * (hf / 8.0) + lst_noise

        ndvi_noise = 0.85 * ndvi_noise + rng.gauss(0, 0.006)
        ndvi = min(0.85, max(0.03, ndvi_base + _seasonal_ndvi_offset(doy) + ndvi_noise))

        rain = 0.0
        if rng.random() < _monsoon_rain_prob(doy):
            rain = min(120.0, rng.lognormvariate(2.0, 0.9))

        lst_hist.append(lst); ndvi_hist.append(ndvi); rain_hist.append(rain); doy_hist.append(doy)

    return {"lst": lst_hist, "ndvi": ndvi_hist, "rain": rain_hist, "doy": doy_hist}


def _anchor_today(hist: dict, anchor_lst: float, anchor_ndvi: float, anchor_rain: float):
    """
    Shifts the whole synthetic LST/NDVI history by a constant so the LAST
    entry (today) equals the real, live-calibrated value the dashboard is
    already showing (from heat_service._base_lst_for_city / the cached
    grid's average NDVI) — keeps day-to-day shape+noise from the synthetic
    generator, but the level is tied to today's real reading, not a guess.
    Rainfall is bursty (a flat shift makes no physical sense), so its last
    value is simply overridden with today's real observed estimate instead.
    """
    lst_shift = anchor_lst - hist["lst"][-1]
    ndvi_shift = anchor_ndvi - hist["ndvi"][-1]
    hist["lst"] = [v + lst_shift for v in hist["lst"]]
    hist["ndvi"] = [min(0.9, max(0.02, v + ndvi_shift)) for v in hist["ndvi"]]
    hist["rain"][-1] = max(0.0, anchor_rain)
    return hist


def _features_for_next_day(hist: dict, next_doy: int, heat_factor: float, base_lst: float) -> list:
    """Builds ONE feature row (matching train_forecast_model.py's feature_columns
    order exactly) to predict the day AFTER the current end of `hist`."""
    def lag(series, n):
        return series[-n] if len(series) >= n else series[0]

    def roll(series, n):
        window = series[-n:] if len(series) >= n else series
        return sum(window) / len(window)

    row = {
        "doy_sin": math.sin(2 * math.pi * next_doy / 365.25),
        "doy_cos": math.cos(2 * math.pi * next_doy / 365.25),
        "heat_factor": heat_factor,
        "base_lst": base_lst,
    }
    for col in ("lst", "ndvi", "rain"):
        series = hist[col]
        for l in LAGS:
            row[f"{col}_lag{l}"] = lag(series, l)
        for w in ROLL_WINDOWS:
            row[f"{col}_roll{w}"] = roll(series, w)
    return row


_FEATURE_ORDER = None


def _feature_order() -> list:
    global _FEATURE_ORDER
    if _FEATURE_ORDER is None:
        _load()
        _FEATURE_ORDER = (_meta or {}).get("feature_columns") or (
            ["doy_sin", "doy_cos", "heat_factor", "base_lst"] +
            [f"{c}_lag{l}" for c in ("lst", "ndvi", "rain") for l in LAGS] +
            [f"{c}_roll{w}" for c in ("lst", "ndvi", "rain") for w in ROLL_WINDOWS]
        )
    return _FEATURE_ORDER


def _predict_next(hist: dict, next_doy: int, heat_factor: float, base_lst: float):
    _load()
    row = _features_for_next_day(hist, next_doy, heat_factor, base_lst)
    order = _feature_order()

    if is_ready():
        import pandas as pd  # local import, mirrors the lazy-load pattern above
        X = pd.DataFrame([[row[c] for c in order]], columns=order)
        lst_next = float(_models["lst"].predict(X)[0])
        ndvi_next = float(_models["ndvi"].predict(X)[0])
        rain_next = float(_models["rain"].predict(X)[0])
        source = "ml_model"
    else:
        # Formula fallback (keeps the feature usable even if sklearn/model
        # files aren't available in this environment) — same seasonal shape
        # as the trainer, anchored to the last known value instead of noise.
        lst_next = base_lst + _seasonal_lst_offset(next_doy) * (heat_factor / 8.0)
        ndvi_next = hist["ndvi"][-1]
        rain_next = hist["rain"][-1] * 0.5
        source = "seasonal_formula_fallback"

    lst_next = max(10.0, min(55.0, lst_next))
    ndvi_next = max(0.02, min(0.9, ndvi_next))
    rain_next = max(0.0, min(150.0, rain_next))
    return lst_next, ndvi_next, rain_next, source


def predict_forecast(city_key: str, start_date: str | None = None, days: int = 10) -> dict:
    cfg = CITY_REGISTRY.get(city_key)
    if cfg is None:
        return {"error": f"City '{city_key}' not found — search and select a city first."}

    days = max(1, min(MAX_REQUEST_DAYS, int(days or MAX_REQUEST_DAYS)))
    today = date.today()

    start = today
    if start_date:
        try:
            y, m, d = (int(x) for x in start_date.split("-"))
            start = date(y, m, d)
        except Exception:
            return {"error": "start_date must be in YYYY-MM-DD format."}
    if start < today:
        return {"error": "start_date cannot be in the past."}
    max_start = today + timedelta(days=MAX_START_OFFSET)
    if start > max_start:
        return {"error": f"start_date can be at most {max_start.isoformat()} "
                          f"({MAX_START_OFFSET} days from today) for reliable accuracy."}

    total_horizon = min(MAX_TOTAL_HORIZON, (start - today).days + days)

    heat = _CITY_HEAT.get(city_key, {"base": 32, "hf": 8})
    base_lst_today, calib_meta = _base_lst_for_city(city_key, heat)

    zones = _generate_grid(city_key)
    n = max(1, len(zones))
    avg_ndvi_today = sum(z["ndvi"] for z in zones) / n
    avg_rain_today = (sum(z.get("rainfall_48h_mm", 0.0) for z in zones) / n) / 2.0
    avg_slope = sum(z.get("slope_deg", 0.0) for z in zones) / n
    avg_pop = sum(z["population_density"] for z in zones) / n

    hist = _synthetic_history(city_key, heat, cfg["seed"], today)
    hist = _anchor_today(hist, base_lst_today, avg_ndvi_today, avg_rain_today)

    records = []
    cursor = today
    for offset in range(1, total_horizon + 1):
        cursor = cursor + timedelta(days=1)
        next_doy = cursor.timetuple().tm_yday
        lst_next, ndvi_next, rain_next, source = _predict_next(hist, next_doy, heat["hf"], base_lst_today)

        # Extend history for the NEXT recursive step
        hist["lst"].append(lst_next)
        hist["ndvi"].append(ndvi_next)
        hist["rain"].append(rain_next)
        hist["doy"].append(next_doy)

        # Derive heat risk exactly like heat_service.py does today
        uhi_est = round(lst_next - (base_lst_today + 4), 2)
        hvi = compute_hvi(lst_next, ndvi_next, avg_pop, uhi_est, base_lst=base_lst_today, hf=heat["hf"])
        risk = cap_risk_by_absolute_lst(risk_level_from_hvi(hvi), lst_next)

        # Derive landslide risk exactly like climate_risk_service.py does today
        landslide = calculate_landslide_risk(rain_next, avg_slope, ndvi_next)

        if cursor >= start:
            records.append({
                "date": cursor.isoformat(),
                "day_offset": offset,
                "lst_c": round(lst_next, 2),
                "ndvi": round(ndvi_next, 3),
                "rainfall_mm": round(rain_next, 2),
                "hvi": hvi,
                "heat_risk_level": risk,
                "landslide_risk_score": landslide["risk_score"],
                "landslide_risk_level": landslide["risk_level"],
                "source": source,
            })
        if len(records) >= days:
            break

    return {
        "city": city_key,
        "city_name": cfg["name"],
        "generated_from": {
            "today": today.isoformat(),
            "today_lst_c": round(base_lst_today, 2),
            "today_ndvi": round(avg_ndvi_today, 3),
            "today_rainfall_est_mm": round(avg_rain_today, 2),
            "calibration_source": calib_meta.get("source"),
        },
        "start_date": start.isoformat(),
        "days": len(records),
        "model_ready": is_ready(),
        "forecast": records,
    }
