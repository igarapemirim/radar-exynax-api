"""
Radar Exynax API — autenticação (magic link), convite de cadastro e POST /register.

- POST /api/v1/auth/login — e-mail em registrations → magic link; não encontrado → e-mail com
  https://www.radarexynax.com/register.html?email=&token= (JWT pre_register, 72h, AUTH_JWT_SECRET)
- POST /register — aceita register_intent_token opcional (valida JWT vs e-mail)
- GET /auth/confirm — magic link → sessão (#exynax_session=)
- GET /auth/confirm-pre-register — legado → redirect register.html?email=
"""
from __future__ import annotations

import asyncio
import os
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field

load_dotenv(Path("/etc/secrets/.env"), override=False)
load_dotenv(override=False)

API_DIR = Path(__file__).resolve().parent
REPO_ROOT = API_DIR.parent
load_dotenv(API_DIR / ".env", override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

from api.login.send_auth import (  # noqa: E402
    send_complete_registration_email,
    send_magic_link,
)

import api.db_pg as db_pg  # noqa: E402


class ServiceUnavailableDbSync(Exception):
    """PostgreSQL indisponível ou falha após retentativas."""


DB_SERVICE_UNAVAILABLE_JSON = {
    "error": "Service temporarily unavailable",
    "detail": "Database synchronization issue. Radar Exynax Foreign Trade Intelligence will retry shortly.",
}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _log_startup_env()
    if db_pg.database_url():
        try:
            await asyncio.to_thread(db_pg.ensure_bootstrap_schema)
        except Exception as exc:
            print(f"[startup] Bootstrap falhou: {exc!r}", flush=True)
            if (os.getenv("RENDER") or "").lower() == "true":
                raise SystemExit(1) from exc
    else:
        print(
            "[startup] AVISO: DATABASE_URL ausente — login e cadastro não funcionarão.",
            flush=True,
        )
    yield


app = FastAPI(
    title="Radar Exynax API",
    version="1.0.0",
    lifespan=_lifespan,
)


@app.exception_handler(ServiceUnavailableDbSync)
async def _handle_service_unavailable_db(
    request: Request, exc: ServiceUnavailableDbSync
):
    return JSONResponse(status_code=503, content=DB_SERVICE_UNAVAILABLE_JSON)


_cors_mandatory = [
    "https://radarexynax.com",
    "https://www.radarexynax.com",
    "https://radar-exynax-frontend.vercel.app",
    "https://radar-exynax-oficial.vercel.app",
]
_cors_extra = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_cors_from_env = [
    o.strip() for o in (os.getenv("CORS_ORIGINS") or "").split(",") if o.strip()
]
_cors_origins = list(dict.fromkeys(_cors_mandatory + _cors_from_env + _cors_extra))
_cors_regex = (os.getenv("CORS_ORIGIN_REGEX") or "").strip() or None

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _json_on_unhandled_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except HTTPException:
        raise
    except ServiceUnavailableDbSync:
        raise
    except Exception:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error. Try again later."},
        )


def _env_strip(value: str | None) -> str:
    if not value:
        return ""
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1].strip()
    return s


def _resend_api_key() -> str:
    return _env_strip(os.getenv("RESEND_API_KEY")).strip()


def _log_startup_env() -> None:
    print("=== RADAR EXYNAX API (oficial) ===", flush=True)
    print(f"RESEND_API_KEY present: {bool(_resend_api_key())}", flush=True)
    print(
        f"DATABASE_URL present: {bool((os.getenv('DATABASE_URL') or '').strip())}",
        flush=True,
    )
    print(
        f"AUTH_JWT_SECRET present: {bool((os.getenv('AUTH_JWT_SECRET') or '').strip())}",
        flush=True,
    )
    print(f"PORT: {os.getenv('PORT', '8000')}", flush=True)


def _public_site_url() -> str:
    return (os.getenv("PUBLIC_SITE_URL") or "https://www.radarexynax.com").rstrip("/")


def _jwt_secret() -> str:
    s = (os.getenv("AUTH_JWT_SECRET") or "").strip()
    if not s:
        raise HTTPException(
            status_code=500,
            detail="AUTH_JWT_SECRET is not set on the server",
        )
    return s


def mint_magic_token(email: str, ttl_minutes: int = 60) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=ttl_minutes)
    payload = {"sub": email, "typ": "magic", "exp": exp}
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def mint_pre_register_token(email: str, ttl_hours: int = 72) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=ttl_hours)
    em = str(email).lower().strip()
    payload = {"sub": em, "typ": "pre_register", "exp": exp}
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_magic_token(token: str) -> dict:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired token")


def decode_pre_register_token(token: str) -> dict:
    try:
        p = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    if p.get("typ") != "pre_register":
        raise HTTPException(status_code=400, detail="Invalid token type")
    return p


def mint_session_token(email: str, ttl_days: int = 30) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=ttl_days)
    em = str(email).lower().strip()
    payload = {"sub": em, "typ": "session", "exp": exp}
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_session_token(token: str) -> str:
    try:
        p = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if p.get("typ") != "session":
        raise HTTPException(status_code=401, detail="Invalid session token")
    sub = p.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid session")
    return str(sub).lower().strip()


def require_session_email(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    raw = authorization[7:].strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_session_token(raw)


def safe_redirect_path(redirect_to: str | None) -> str:
    if not redirect_to:
        return "/credits.html"
    if "://" in redirect_to or not redirect_to.startswith("/"):
        return "/credits.html"
    if ".." in redirect_to:
        return "/credits.html"
    return redirect_to


def registration_email_exists(email: str) -> bool:
    if not db_pg.database_url():
        return False
    return db_pg.registration_email_exists(email)


def mark_registration_email_verified(email: str) -> None:
    if db_pg.database_url():
        db_pg.mark_registration_email_verified(email)


class MagicLinkBody(BaseModel):
    email: EmailStr
    redirect_to: str | None = None
    require_registered: bool = False


class RegistrationBody(BaseModel):
    company: str = Field(..., min_length=1)
    tx_id: str = Field(..., min_length=1)
    address: str = Field(..., min_length=1)
    country: str = Field(..., min_length=1)
    website: str = ""
    email: EmailStr
    whatsapp: str = ""
    telegram: str = ""
    contact_name: str = Field(..., min_length=1)
    hs_codes: list[str] = Field(..., min_length=1)
    type: list[str] = Field(..., min_length=1)
    terms_agree: bool = True
    register_intent_token: str | None = Field(
        default=None,
        description="JWT do e-mail de convite (login sem cadastro).",
    )


class V1AuthLoginBody(BaseModel):
    email: EmailStr
    redirect_to: str | None = Field(default="/credits.html")


def _post_magic_link_impl(body: MagicLinkBody) -> dict:
    if not _resend_api_key():
        raise HTTPException(
            status_code=503,
            detail="Email service not configured. Contact admin.",
        )
    if not (os.getenv("AUTH_JWT_SECRET") or "").strip():
        raise HTTPException(
            status_code=503,
            detail="Server authentication is not configured. Contact admin.",
        )
    if body.require_registered:
        if not db_pg.database_url():
            print("[login] DATABASE_URL ausente.", flush=True)
            raise ServiceUnavailableDbSync()
        try:
            db_pg.ensure_user_row(str(body.email))
        except Exception as exc:
            print(f"[magic-link] ensure_user_row failed: {exc!r}", flush=True)
            raise ServiceUnavailableDbSync() from exc
        try:
            exists = registration_email_exists(body.email)
        except Exception as exc:
            print(f"[magic-link] registration lookup failed: {exc!r}", flush=True)
            raise ServiceUnavailableDbSync() from exc
        if not exists:
            pre_tok = mint_pre_register_token(body.email)
            if not send_complete_registration_email(body.email, pre_tok):
                raise HTTPException(
                    status_code=502,
                    detail="Failed to send registration invitation email",
                )
            return {
                "ok": True,
                "flow": "pre_register",
                "message": (
                    "We've sent a link to your email to begin your registration."
                ),
            }
    token = mint_magic_token(body.email)
    next_page = None
    if body.redirect_to is not None:
        s = str(body.redirect_to).strip()
        if s:
            next_page = safe_redirect_path(s)
    if not send_magic_link(body.email, token, next_page=next_page):
        raise HTTPException(status_code=502, detail="Failed to send email")
    return {
        "ok": True,
        "message": "If the email is valid, you will receive a sign-in link shortly.",
    }


@app.post("/auth/magic-link")
def post_magic_link(body: MagicLinkBody):
    return _post_magic_link_impl(body)


@app.post("/api/v1/auth/login")
def post_api_v1_auth_login(body: V1AuthLoginBody):
    return _post_magic_link_impl(
        MagicLinkBody(
            email=body.email,
            redirect_to=body.redirect_to,
            require_registered=True,
        )
    )


@app.get("/auth/confirm-pre-register")
def get_auth_confirm_pre_register(token: str):
    payload = decode_pre_register_token(token)
    email = str(payload.get("sub") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Invalid token")
    site = _public_site_url().rstrip("/")
    q = urlencode({"email": email})
    return RedirectResponse(url=f"{site}/register.html?{q}", status_code=302)


@app.get("/auth/confirm")
def get_auth_confirm(
    token: str,
    redirect_to: str | None = None,
    remember: str | None = None,
):
    _ = remember
    payload = decode_magic_token(token)
    if payload.get("typ") != "magic":
        raise HTTPException(status_code=400, detail="Invalid token type")
    email = str(payload.get("sub") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Invalid token")
    mark_registration_email_verified(email)
    session_jwt = mint_session_token(email)
    path = safe_redirect_path(redirect_to)
    dest = f"{_public_site_url()}{path}#exynax_session={session_jwt}"
    return RedirectResponse(url=dest, status_code=302)


@app.post("/register")
async def post_register(body: RegistrationBody):
    row = body.model_dump()
    tok = row.pop("register_intent_token", None)
    row["email"] = str(row["email"]).lower().strip()
    if tok and str(tok).strip():
        try:
            payload = decode_pre_register_token(str(tok).strip())
            sub = str(payload.get("sub") or "").lower().strip()
            if not sub or sub != row["email"]:
                raise HTTPException(
                    status_code=400,
                    detail="Invitation token is invalid or does not match this email. Request a new link from Login.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            print(f"[register] register_intent_token inválido: {exc!r}", flush=True)
            raise HTTPException(
                status_code=400,
                detail="Invitation token is invalid or expired. Request a new link from Login.",
            ) from exc
    if not db_pg.database_url():
        raise ServiceUnavailableDbSync()
    try:
        await asyncio.to_thread(db_pg.insert_registration, row)
    except Exception as e:
        print(f"[register] insert_registration: {e!r}", flush=True)
        raise ServiceUnavailableDbSync() from e
    return {"ok": True, "message": "Registration saved."}


@app.get("/auth/me")
def get_auth_me(
    authorization: Annotated[str | None, Header()] = None,
):
    email = require_session_email(authorization)
    return {"email": email}


@app.get("/")
def root():
    return {"status": "online", "system": "Radar Exynax"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/health")
def api_health():
    return {
        "status": "ok",
        "service": "Radar Exynax API",
        "cors": "enabled",
    }
