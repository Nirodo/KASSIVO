"""App-Fabrik: Sicherheits-Middleware, Fehlerseiten, Router."""
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from kassivo import restore
from kassivo.db import connect
from kassivo.web import (
    auto_routes, backup_routes, categories_routes, dues_routes, import_routes, members_routes, reports_routes,
    overview_routes, settings_routes, transactions_routes, wiki_routes,
)
from kassivo.web.common import HERE, render

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
UNSAFE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}
# Sec-Fetch-Site setzt der Browser selbst. Zulässig sind eigene Seite ("same-origin"), direkte
# Eingabe/Lesezeichen ("none") und ein fehlender Header (curl, Tests, alte Browser). "cross-site",
# "same-site" und jeder unbekannte Wert werden bei ändernden Anfragen abgelehnt.
ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def _hostname(host: str) -> str:
    return host.rsplit(":", 1)[0] if ":" in host else host


def create_app(db_path, extra_hosts=()) -> FastAPI:
    """extra_hosts: zusätzliche Hostnamen, nur für Tests ("testserver")."""
    # Einmal beim Start öffnen: Migration oder Ablehnung (zu neue Datenbank) passiert hier,
    # nicht erst bei der ersten Anfrage.
    connect(db_path).close()
    if str(db_path) != ":memory:":
        restore.startup_cleanup(Path(db_path))  # Reste früherer Läufe, keine Anfrage läuft
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = Path(db_path)
    allowed = ALLOWED_HOSTS | frozenset(extra_hosts)

    @app.middleware("http")
    async def security(request: Request, call_next):
        host = request.headers.get("host", "")
        if _hostname(host) not in allowed:
            resp = PlainTextResponse("Ungültiger Host.", status_code=400)
        else:
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
            foreign_fetch = bool(fetch_site) and fetch_site not in ALLOWED_FETCH_SITES
            if request.method in UNSAFE_METHODS and (
                    foreign_fetch
                    or (origin is not None and urlsplit(origin).netloc != host)):
                resp = PlainTextResponse("Anfrage von fremder Herkunft abgelehnt.", status_code=403)
            elif import_routes.upload_too_large(request):
                too_big = (backup_routes.TOO_BIG if request.url.path.startswith("/backup")
                           else import_routes.TOO_BIG)
                resp = render(request, "error.html", 413, message=too_big)
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

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.include_router(overview_routes.router)
    app.include_router(members_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(import_routes.router)
    app.include_router(transactions_routes.router)
    app.include_router(categories_routes.router)
    app.include_router(dues_routes.router)
    app.include_router(auto_routes.router)
    app.include_router(reports_routes.router)
    app.include_router(backup_routes.router)
    app.include_router(wiki_routes.router)
    return app
