import os
from pathlib import Path
from urllib.parse import urlencode

import resend
from dotenv import load_dotenv

# Este ficheiro está em api/login/ → pasta da API é o parent.
_API_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _API_DIR.parent
load_dotenv(Path("/etc/secrets/.env"), override=False)
load_dotenv(_API_DIR / ".env", override=False)
load_dotenv(_REPO_ROOT / ".env", override=False)
load_dotenv(override=False)

domain_id = os.getenv("DOMAIN_ID")


def _resend_api_key() -> str:
    v = os.getenv("RESEND_API_KEY")
    if not v:
        return ""
    s = v.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1].strip()
    return s


def _api_public_base() -> str:
    """Origem pública onde esta API expõe GET /auth/confirm."""
    u = (os.getenv("PUBLIC_API_URL") or "").strip().rstrip("/")
    if u:
        return u
    ext = (os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    if ext:
        return ext
    site = (os.getenv("PUBLIC_SITE_URL") or "").lower()
    if "radarexynax.com" in site:
        return "https://api.radarexynax.com"
    if (os.getenv("RENDER") or "").lower() == "true":
        return "https://api.radarexynax.com"
    return "http://127.0.0.1:8000"


def send_magic_link(user_email, auth_token, next_page=None) -> bool:
    key = _resend_api_key()
    if not key:
        print("Error: RESEND_API_KEY ausente ou vazia após normalização.", flush=True)
        return False
    resend.api_key = key

    api_public = _api_public_base()
    if next_page is None:
        next_page = "/register.html"

    query = urlencode(
        {
            "token": auth_token,
            "remember": "true",
            "redirect_to": next_page,
        }
    )
    login_url = f"{api_public}/auth/confirm?{query}"

    from_addr = os.getenv("RESEND_FROM", "Radar Exynax <login@auth.radarexynax.com>")
    reply_to = os.getenv("RESEND_REPLY_TO", "igarapemirim@gmail.com")

    headers = {}
    if domain_id:
        headers["X-Entity-Ref-ID"] = domain_id

    params = {
        "from": from_addr,
        "to": [user_email],
        "subject": "Complete Your Registration - Radar Exynax",
        "reply_to": reply_to,
        "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #0f172a; border: 1px solid #e2e8f0; padding: 25px; border-radius: 10px;">
                <h2 style="color: #0A2463; text-align: center;">Radar Exynax</h2>
                <p>Hello,</p>
                <p>Click the button below to verify your access and complete your registration.</p>
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{login_url}"
                       style="background: #0A2463; color: #ffffff; padding: 15px 30px;
                       text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                       Sign In & Register
                    </a>
                </div>
                <p style="font-size: 11px; color: #cbd5e1; text-align: center;">
                    &copy; 2026 Radar Exynax - Secure PJ Infrastructure
                </p>
            </div>
        """,
    }
    if headers:
        params["headers"] = headers

    try:
        email_response = resend.Emails.send(params)
        eid = None
        if isinstance(email_response, dict):
            eid = email_response.get("id")
        print(f"Success! Sent to {user_email}. ID: {eid}", flush=True)
        return True
    except Exception as e:
        print(f"Error: {e}", flush=True)
        return False


def send_complete_registration_email(user_email: str, auth_token: str) -> bool:
    """
    Login sem cadastro: convite com link direto a register.html?email=&token= (JWT 72h).
    """
    key = _resend_api_key()
    if not key:
        print("Error: RESEND_API_KEY ausente (complete registration).", flush=True)
        return False
    resend.api_key = key

    site = (os.getenv("PUBLIC_SITE_URL") or "https://www.radarexynax.com").rstrip("/")
    query = urlencode({"email": user_email, "token": auth_token})
    register_url = f"{site}/register.html?{query}"

    from_addr = os.getenv("RESEND_FROM", "Radar Exynax <login@auth.radarexynax.com>")
    reply_to = os.getenv("RESEND_REPLY_TO", "igarapemirim@gmail.com")

    headers = {}
    if domain_id:
        headers["X-Entity-Ref-ID"] = domain_id

    params = {
        "from": from_addr,
        "to": [user_email],
        "subject": "Complete your registration on Radar Exynax",
        "reply_to": reply_to,
        "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #0f172a; border: 1px solid #e2e8f0; padding: 25px; border-radius: 10px;">
                <p style="margin: 0 0 20px; font-size: 16px; line-height: 1.5;">Hello, To begin your registration, click the link below.</p>
                <div style="text-align: center; margin: 24px 0;">
                    <a href="{register_url}"
                       style="background: #0A2463; color: #ffffff; padding: 15px 30px;
                       text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                       Continue registration
                    </a>
                </div>
            </div>
        """,
    }
    if headers:
        params["headers"] = headers

    try:
        email_response = resend.Emails.send(params)
        eid = None
        if isinstance(email_response, dict):
            eid = email_response.get("id")
        print(f"[register-invite] Sent to {user_email}. ID: {eid}", flush=True)
        return True
    except Exception as e:
        print(f"[register-invite] Error: {e}", flush=True)
        return False


def send_pre_register_magic_link(user_email: str, auth_token: str) -> bool:
    """Fluxo legado: link para /auth/confirm-pre-register."""
    key = _resend_api_key()
    if not key:
        print("Error: RESEND_API_KEY ausente (pré-cadastro).", flush=True)
        return False
    resend.api_key = key

    api_public = _api_public_base()
    query = urlencode({"token": auth_token})
    verify_url = f"{api_public}/auth/confirm-pre-register?{query}"

    from_addr = os.getenv("RESEND_FROM", "Radar Exynax <login@auth.radarexynax.com>")
    reply_to = os.getenv("RESEND_REPLY_TO", "igarapemirim@gmail.com")

    headers = {}
    if domain_id:
        headers["X-Entity-Ref-ID"] = domain_id

    params = {
        "from": from_addr,
        "to": [user_email],
        "subject": "Radar Exynax — confirme seu e-mail para continuar o cadastro",
        "reply_to": reply_to,
        "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #0f172a; border: 1px solid #e2e8f0; padding: 25px; border-radius: 10px;">
                <h2 style="color: #0A2463; text-align: center;">Radar Exynax</h2>
                <p>Olá,</p>
                <p>Este e-mail ainda não está cadastrado. Clique no botão abaixo para confirmar que é você e abrir o formulário de cadastro final.</p>
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{verify_url}"
                       style="background: #0A2463; color: #ffffff; padding: 15px 30px;
                       text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                       Confirmar e continuar cadastro
                    </a>
                </div>
                <p style="font-size: 12px; color: #64748b;">Se você não solicitou isso, ignore este e-mail.</p>
                <p style="font-size: 11px; color: #cbd5e1; text-align: center;">
                    &copy; 2026 Radar Exynax
                </p>
            </div>
        """,
    }
    if headers:
        params["headers"] = headers

    try:
        email_response = resend.Emails.send(params)
        eid = None
        if isinstance(email_response, dict):
            eid = email_response.get("id")
        print(f"[pre-register] Sent to {user_email}. ID: {eid}", flush=True)
        return True
    except Exception as e:
        print(f"[pre-register] Error: {e}", flush=True)
        return False
