"""ERP MatCon — FastAPI App"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from .database import init_db
from .routers.api import router
from .routers.auth_router import router as auth_router

app = FastAPI(
    title="ERP MatCon",
    version="1.0.0",
    description="ERP Materiais de Construção — Curitiba PR",
)

# CORS — permite o frontend Vercel chamar o backend Railway
ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

app.include_router(router)
app.include_router(auth_router)

@app.get("/")
def root():
    return {"status": "ERP MatCon API online", "docs": "/docs"}

# Serve frontend estático só em desenvolvimento local
_frontend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
if os.path.isdir(_frontend):
    app.mount("/frontend", StaticFiles(directory=_frontend, html=True), name="frontend")

    @app.get("/app")
    def frontend_redirect():
        return RedirectResponse(url="/frontend/index.html")
