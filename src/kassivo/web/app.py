"""App-Fabrik: Sicherheits-Middleware, Fehlerseiten, Router."""
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from kassivo.web import (
    categories_routes, dues_routes, import_routes, members_routes, reports_routes,
    settings_routes, transactions_routes,
)
from kassivo.web.common import HERE, render

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}
UNSAFE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def _hostname(host: str) -> str:
    return host.rsplit(":", 1)[0] if ":" in host else host


def create_app(db_path) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = Path(db_path)

    @app.middleware("http")
    async def security(request: Request, call_next):
        host = request.headers.get("host", "")
        if _hostname(host) not in ALLOWED_HOSTS:
            resp = PlainTextResponse("Ungültiger Host.", status_code=400)
        else:
            origin = request.headers.get("origin")
            if (request.method in UNSAFE_METHODS and origin is not None
                    and urlsplit(origin).netloc != host):
                resp = PlainTextResponse("Anfrage von fremder Herkunft abgelehnt.", status_code=403)
            else:
                resp = await call_next(request)
        resp.headers.update(SECURITY_HEADERS)
        return resp

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception):
        # läuft außerhalb der Middleware: Header selbst setzen
        resp = render(request, "error.html", 500,
                      message="Interner Fehler. Bitte prüfen Sie, ob die Änderung gespeichert wurde.")
        resp.headers.update(SECURITY_HEADERS)
        return resp

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        texts = {404: "Seite nicht gefunden.", 405: "Methode nicht erlaubt."}
        return render(request, "error.html", exc.status_code,
                      message=texts.get(exc.status_code, "Fehler bei der Anfrage."))

    @app.get("/")
    async def root():
        return RedirectResponse("/dues")

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.include_router(members_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(import_routes.router)
    app.include_router(transactions_routes.router)
    app.include_router(categories_routes.router)
    app.include_router(dues_routes.router)
    app.include_router(reports_routes.router)
    return app
