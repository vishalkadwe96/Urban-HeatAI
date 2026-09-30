# Urban Heat AI v2 — 10-Day Forecast API Routes (NEW — additive, doesn't touch
# any existing router/endpoint)
from fastapi import APIRouter, Query
from backend.services.forecast_service import predict_forecast, get_forecast_model_info

router = APIRouter(prefix="/forecast", tags=["Forecast"])


@router.get("/model-info", summary="Trained forecast model metadata (5-fold backtest results, features)")
def model_info():
    return get_forecast_model_info()


@router.get("/predict", summary="N-day-ahead forecast: LST, NDVI, heat risk, rainfall, landslide risk")
def predict(
    city: str = Query(..., description="City key"),
    start_date: str = Query(None, description="YYYY-MM-DD, defaults to today, max 6 days out"),
    days: int = Query(10, ge=1, le=10, description="Number of days to forecast (max 10)"),
):
    return predict_forecast(city, start_date=start_date, days=days)
