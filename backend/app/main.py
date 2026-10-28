"""AgriTrust AI application entrypoint.

Wires the four layers together so the project runs on a plain checkout:

    frontend (Jinja templates + JS)
        -> calls the REST API under ``/api/v1``
            -> routers / services / ai
                -> SQLAlchemy session (``app.database``)
                    -> MariaDB/MySQL, or SQLite for a zero-setup local run

Run it with::

    ./scripts/run.sh
    # or: cd backend && PYTHONPATH=. ../.venv/bin/uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sqlalchemy import inspect, text

from app.config import settings
from app.database import Base, engine

# importing the models package registers every table on ``Base.metadata``
import app.models  # noqa: F401  (side-effect import)
from app.i18n import (
    LANGUAGE_COOKIE,
    _load_catalog,
    get_language,
    negotiate_language,
    normalise_language,
    translate,
)
from app.integrations.identity import nida_length_phrase
from app.routers import (
    admin,
    ai,
    assessments,
    auth,
    farmers,
    farms,
    finance,
    identity,
    insurance,
    loans,
    market,
    notifications,
    payments,
    planning,
    sms,
    ussd,
    marketplace,
)
from app import scheduler

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("agritrust.main")
# the form parser logs every byte at DEBUG, drowning the USSD callbacks
logging.getLogger("python_multipart").setLevel(logging.INFO)

TEMPLATES_DIR = settings.frontend_dir / "templates"
STATIC_DIR = settings.frontend_dir / "static"

#: every API router mounted under ``settings.api_v1_prefix``
API_ROUTERS = (
    auth.router,
    identity.router,
    assessments.router,
    farmers.router,
    farms.router,
    finance.router,
    loans.router,
    insurance.router,
    ai.router,
    market.router,
    notifications.router,
    payments.router,
    planning.router,
    sms.router,
    ussd.router,
    admin.router,
    marketplace.router,
)


#: columns added after the first release: (table, column, SQL type)
ADDED_COLUMNS = [
    ("financial_accounts", "account_number_encrypted", "TEXT"),
    # lenders arrived after applications existed, so the column is added in place
    # rather than forcing a migration
    ("loan_applications", "lender_code", "VARCHAR(48)"),
    ("crop_orders", "payment_reference", "VARCHAR(24)"),
    # NIDA numbers come in more than one length, so the row keeps the real one
    # and a mask can reflect it
    ("identity_verifications", "nida_length", "INTEGER"),
    # loan purposes became categories rather than free text only
    ("loan_applications", "purpose_code", "VARCHAR(16)"),
    # a farmer can photograph the farm they are insuring
    ("farms", "photo_filename", "VARCHAR(80)"),
]


def init_db() -> None:
    """Create any missing tables so a fresh clone runs without a migration step."""
    Base.metadata.create_all(bind=engine)
    # create_all never alters an existing table, so add newer columns by hand
    existing = inspect(engine)
    with engine.begin() as conn:
        for table, column, sql_type in ADDED_COLUMNS:
            if column not in {c["name"] for c in existing.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
                logger.info("added column %s.%s", table, column)
    logger.info("database ready: %s", engine.url.render_as_string(hide_password=True))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("%s ready in %s mode", settings.app_name, settings.app_env)
    # the assistant keeps working on its own: it refreshes every farmer's
    # recommendations and pushes the new ones, so a farmer is helped without asking
    scheduler.start(app)
    try:
        yield
    finally:
        await scheduler.stop(app)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="AI agricultural finance and risk network.",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_language(request: Request, call_next):
        # Set the language once, in the request's own context: sync endpoints run in a
        # threadpool that copies this context, whereas a language set inside a
        # dependency's worker thread would never reach the endpoint.
        negotiate_language(request)
        return await call_next(request)

    for router in API_ROUTERS:
        app.include_router(router, prefix=settings.api_v1_prefix)

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def asset(path: str) -> str:
        """Static URL with a version stamp, so browsers fetch a file again when it changes."""
        try:
            version = int((STATIC_DIR / path).stat().st_mtime)
        except OSError:
            version = 0
        return f"/static/{path}?v={version}"

    templates.env.globals["asset"] = asset

    def render(request: Request, name: str, **context):
        lang = negotiate_language(request)
        response = templates.TemplateResponse(
            request, name, {"lang": lang, "t": translate, **context}
        )
        # pages carry the language in their HTML, so never serve a stale copy after a switch
        response.headers["Cache-Control"] = "no-cache"
        # a ?lang= link should stick for the pages that follow
        if "lang" in request.query_params:
            response.set_cookie(LANGUAGE_COOKIE, lang, max_age=60 * 60 * 24 * 365, samesite="lax")
        return response

    # ----------------------------------------------------------------- pages
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request):
        # the splash screen is the front door: the photograph, then sign in
        return render(request, "splash.html", page="splash")

    @app.get("/splash", response_class=HTMLResponse, include_in_schema=False)
    def splash(request: Request):
        return render(request, "splash.html", page="splash")

    @app.get("/home", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        """The written overview, kept one click behind the splash screen."""
        return render(request, "index.html", page="home")

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page(request: Request):
        return render(request, "login.html", page="login")

    @app.get("/register", response_class=HTMLResponse, include_in_schema=False)
    def register_page(request: Request):
        # The identity section is only rendered while verification is switched
        # on; with IDENTITY_REQUIRED off the page is exactly the form it was
        # before the feature existed, and the server gate is off to match.
        #
        # The page also needs to know *which* roles are gated, because the role
        # picker can change after load: a farmer has to verify a national ID, a
        # buyer or an officer does not. The same list is enforced in
        # /auth/register, so the button state here only mirrors it.
        return render(
            request,
            "register.html",
            page="register",
            identity_required=settings.identity_required,
            identity_roles=sorted(settings.identity_roles) if settings.identity_required else [],
            nida_lengths=nida_length_phrase(get_language()),
        )

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard_page(request: Request):
        return render(request, "dashboard.html", page="dashboard")

    @app.get("/pay/{reference}", response_class=HTMLResponse, include_in_schema=False)
    def pay_page(request: Request, reference: str):
        """Opened by scanning a payment QR code on a phone."""
        return render(request, "pay.html", page="pay", reference=reference, sandbox=settings.mock_mode)

    @app.get("/health", include_in_schema=False)
    def health_redirect():
        return RedirectResponse(url=f"{settings.api_v1_prefix}/health")

    @app.post(f"{settings.api_v1_prefix}/lang", include_in_schema=False)
    async def set_lang(request: Request):
        """Persist the language choice in a cookie, then let JS reload the page."""
        data = await request.json()
        lang = normalise_language(str(data.get("language", "en")))  # only en / sw are ever stored
        response = JSONResponse({"language": lang})
        response.set_cookie(LANGUAGE_COOKIE, lang, max_age=60 * 60 * 24 * 365, samesite="lax")
        return response

    # ----------------------------------------------------------------- health
    @app.get(f"{settings.api_v1_prefix}/health", tags=["system"])
    def health():
        return {
            "status": "ok",
            "app": settings.app_name,
            "environment": settings.app_env,
            "database": "sqlite" if settings.is_sqlite else "mysql",
            "mock_mode": settings.mock_mode,
            "languages": ["en", "sw"],
        }

    @app.get(f"{settings.api_v1_prefix}/i18n/{{lang}}", tags=["system"])
    def i18n_catalog(lang: str):
        """Flat translation catalog so the browser uses the same strings as the server."""
        return JSONResponse(_load_catalog(normalise_language(lang)), headers={"Cache-Control": "no-cache"})

    @app.get(f"{settings.api_v1_prefix}/locales/{{lang}}", tags=["system"])
    def locale_catalog(lang: str):
        """Serve a translation catalog so the browser can resolve API error keys.

        The API returns translation keys (e.g. ``auth.invalid_credentials``) as
        error details; the JavaScript layer swaps in the right string here.
        """
        code = normalise_language(lang)
        return JSONResponse(_load_catalog(code), headers={"Cache-Control": "max-age=300"})

    @app.get(f"{settings.api_v1_prefix}/config", tags=["system"])
    def public_config():
        """Values the frontend needs to talk to the API correctly."""
        return {
            "api_prefix": settings.api_v1_prefix,
            "app_name": settings.app_name,
            "languages": {"en": "English", "sw": "Kiswahili"},
            "password_min_length": settings.password_min_length,
            "mock_mode": settings.mock_mode,
            "demo_mode": settings.demo_mode,
            "ussd_code": settings.africastalking_ussd_code,
        }

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover - manual launch helper
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        reload_dirs=[str(Path(__file__).resolve().parent)],
    )
