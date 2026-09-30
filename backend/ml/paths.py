# Urban Heat AI v2 — ML model file paths
# Deliberately has NO sklearn/pandas/joblib imports. train_lst_model.py (which
# needs sklearn to TRAIN) and ml_service.py (which only needs to know WHERE the
# trained file lives, and loads it lazily/defensively) both import from here so
# that importing ml_service.py never drags in scikit-learn as a side effect.
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "models"
MODEL_PATH = MODEL_DIR / "lst_model.joblib"
META_PATH = MODEL_DIR / "lst_model_meta.json"

# ── 10-day Forecast models (additive — original LST model above is untouched) ──
# Three separate single-step regressors (LST / NDVI / rainfall), each used
# recursively (walk-forward) by forecast_service.py to project 1..N days
# ahead. Kept as separate files/constants so training or reloading the
# forecast models can never interfere with the existing lst_model.joblib.
FORECAST_LST_MODEL_PATH  = MODEL_DIR / "forecast_lst.joblib"
FORECAST_NDVI_MODEL_PATH = MODEL_DIR / "forecast_ndvi.joblib"
FORECAST_RAIN_MODEL_PATH = MODEL_DIR / "forecast_rain.joblib"
FORECAST_META_PATH       = MODEL_DIR / "forecast_meta.json"
