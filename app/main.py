from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os

from app.database import init_db
from app.routes import profiles, games, stats, news, court, sos, auditor, admin

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Obsessed", version="1.0.0", docs_url="/docs", redoc_url="/redoc")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# Static assets (CSS/JS/images)
# Static files mount — CSS/JS/fonts at /static/*
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# API routes
app.include_router(profiles.router)
app.include_router(games.router)
app.include_router(stats.router)
app.include_router(news.router)
app.include_router(court.router)
app.include_router(sos.router)
app.include_router(auditor.router)
app.include_router(admin.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Obsessed"}


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
def serve_index():
    path = os.path.join(BASE_DIR, "static", "index.html")
    with open(path, "r") as f:
        return f.read()


@app.get("/admin.html", response_class=HTMLResponse)
def serve_admin():
    path = os.path.join(BASE_DIR, "static", "admin.html")
    with open(path, "r") as f:
        return f.read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)