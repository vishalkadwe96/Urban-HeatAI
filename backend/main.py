# Urban Heat AI v2 — FastAPI Entry Point
from dotenv import load_dotenv
load_dotenv()  # must run before services read GROQ_API_KEY from os.environ

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from backend.config import FRONTEND_DIR
from backend.api import heat, health, recommendations, zones, chatbot, activity, ml, report, alerts, cities,vulnerability, forecast, auth, admin
from backend.database import init_db
import uvicorn

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # creates backend/data/urban_heat_ai.db + tables if not present
    yield

app = FastAPI(
    title="Urban Heat AI API",
    description="AI-powered Urban Heat Island analytics for 10 Indian cities",
    version="2.0.0",
    contact={"name": "IS-14 Hackathon Team"},
    lifespan=lifespan,
)

# CORS (allow all origins for dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# API Routers
app.include_router(heat.router)
app.include_router(health.router)
app.include_router(recommendations.router)
app.include_router(zones.router)
app.include_router(chatbot.router)
app.include_router(activity.router)
app.include_router(ml.router)
app.include_router(report.router)
app.include_router(alerts.router)
app.include_router(cities.router) 
app.include_router(vulnerability.router)
app.include_router(forecast.router)  # NEW — 10-day forecast feature
app.include_router(auth.router)      # NEW — login / register / Google sign-in
app.include_router(admin.router)

# Status endpoint
@app.get("/api/status", tags=["System"])
async def status():
    return {"status": "online", "version": "2.0.0", "platform": "Urban Heat AI"}

# Serve frontend static files
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
    app.mount("/css",    StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
    app.mount("/js",     StaticFiles(directory=str(FRONTEND_DIR / "js")),  name="js")

    # HTML pages must never be browser-cached — otherwise switching which
    # page "/" serves (e.g. index.html -> login.html) silently shows the
    # OLD page from cache with no new request ever reaching the server.
    NO_CACHE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate"}

    def _html(name: str) -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / name), headers=NO_CACHE_HEADERS)

    @app.get("/", include_in_schema=False)
    async def root():
        return _html("login.html")

    @app.get("/index.html", include_in_schema=False)
    async def index():
        return _html("index.html")

    @app.get("/dashboard.html", include_in_schema=False)
    async def dashboard():
        return _html("dashboard.html")

    @app.get("/login.html", include_in_schema=False)
    async def login_page():
        return _html("login.html")

    @app.get("/admin.html", include_in_schema=False)
    async def admin_page():
        return _html("admin.html")

    @app.get("/database.html", include_in_schema=False)
    async def database():
        return FileResponse(str(FRONTEND_DIR / "database.html"))

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
