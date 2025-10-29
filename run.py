from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from djlib import webapp

BASE_DIR = Path(__file__).resolve().parent
WEBUI_DIR = BASE_DIR / "webui"
INDEX_HTML = WEBUI_DIR / "index.html"

def create_app() -> FastAPI:
    app = FastAPI(title="DJ Library Manager")

    # Statyki (Twoje webui/) na /static
    if WEBUI_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEBUI_DIR), name="static")

    # Strona główna -> index.html (koniec z {"detail":"Not Found"})
    @app.get("/", include_in_schema=False)
    def root():
        if INDEX_HTML.exists():
            return FileResponse(INDEX_HTML)
        # Fallback: jakby nie było index.html, to chociaż pokaż docs
        return RedirectResponse(url="/docs")

    # Prosty healthcheck
    @app.get("/api/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    # Endpoints pod konfigurację (Krok 0 w UI)
    @app.get("/api/config")
    def api_get_config():
        return webapp.load_config()

    @app.post("/api/config")
    def api_set_config(lib_root: str, inbox: str):
        webapp.save_config_paths(lib_root=lib_root, inbox=inbox)
        return {"ok": True}

    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("run:app", host="127.0.0.1", port=8000, reload=True)
