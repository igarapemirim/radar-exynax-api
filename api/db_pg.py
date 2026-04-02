"""
PostgreSQL direto (ex.: Render Postgres — https://render.com/docs/databases).
Tabelas: registrations, credit_accounts, credit_ledger (ver api/sql/*.sql).

Nomes das tabelas: REGISTRATIONS_TABLE, CREDIT_ACCOUNTS_TABLE, CREDIT_LEDGER_TABLE
(ou legado SUPABASE_*_TABLE para compatibilidade).
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


def _env_strip(value: str | None) -> str:
    if not value:
        return ""
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1].strip()
    return s


def database_url() -> str | None:
    u = _env_strip(os.getenv("DATABASE_URL"))
    return u or None


def connection_string() -> str | None:
    """
    DSN para psycopg. PostgreSQL gerenciado no Render exige TLS: se DATABASE_URL não
    trouxer sslmode, acrescenta sslmode=require (exceto localhost).
    """
    dsn = database_url()
    if not dsn:
        return None
    try:
        p = urlparse(dsn)
        host = (p.hostname or "").lower()
        if host in ("localhost", "127.0.0.1"):
            return dsn
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)]
        keys_lower = {k.lower() for k, _ in q}
        if "sslmode" not in keys_lower:
            q.append(("sslmode", "require"))
        new_query = urlencode(q)
        return urlunparse(
            (p.scheme, p.netloc, p.path, p.params, new_query, p.fragment)
        )
    except Exception:
        sep = "&" if "?" in dsn else "?"
        return f"{dsn}{sep}sslmode=require"


T = TypeVar("T")

_DB_RETRY_REQUEST = 4
_DB_RETRY_BOOTSTRAP = 8


def run_with_db_retries(
    fn: Callable[[], T],
    *,
    label: str,
    attempts: int = _DB_RETRY_REQUEST,
) -> T:
    """Reconexão automática em falhas transitórias (Render cold start, rede)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            print(
                f"[db_pg] {label} tentativa {i + 1}/{attempts}: {e!r}",
                flush=True,
            )
            if i + 1 < attempts:
                time.sleep(min(3.0, 0.45 * (2**i)))
    assert last is not None
    raise last


def _ensure_bootstrap_schema_once() -> None:
    dsn = connection_string()
    if not dsn:
        return
    reg_sql = """
    CREATE TABLE IF NOT EXISTS public.registrations (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      created_at timestamptz NOT NULL DEFAULT now(),
      company text NOT NULL,
      tx_id text NOT NULL,
      address text NOT NULL,
      country text NOT NULL,
      website text,
      email text NOT NULL,
      email_verified_at timestamptz,
      whatsapp text,
      telegram text,
      contact_name text NOT NULL,
      hs_codes text[] NOT NULL,
      type text[] NOT NULL,
      terms_agree boolean NOT NULL DEFAULT true
    );
    """
    users_sql = """
    CREATE TABLE IF NOT EXISTS public.users (
      id SERIAL PRIMARY KEY,
      email TEXT UNIQUE NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
            except Exception as ext_exc:
                print(
                    f"[db_pg] Aviso: CREATE EXTENSION pgcrypto ignorado: {ext_exc!r}",
                    flush=True,
                )
            cur.execute(users_sql)
            cur.execute(reg_sql)
        conn.commit()
    try:
        with psycopg.connect(dsn, autocommit=True) as conn2:
            with conn2.cursor() as cur2:
                cur2.execute(
                    "ALTER TABLE public.registrations ENABLE ROW LEVEL SECURITY"
                )
    except Exception as e:
        print(f"[db_pg] Aviso RLS registrations: {e}", flush=True)
    print("[db_pg] Schema bootstrap: public.users e public.registrations OK", flush=True)


def ensure_bootstrap_schema() -> None:
    """
    Primeira operação de DB no deploy: cria public.users e public.registrations.
    Retentativas antes de falhar (sincronização com Postgres no Render).
    """
    dsn = connection_string()
    if not dsn:
        print(
            "[db_pg] DATABASE_URL ausente — defina no Render (PostgreSQL linkado ao serviço web).",
            flush=True,
        )
        return
    last: Exception | None = None
    for i in range(_DB_RETRY_BOOTSTRAP):
        try:
            _ensure_bootstrap_schema_once()
            return
        except Exception as e:
            last = e
            print(
                f"[db_pg] bootstrap tentativa {i + 1}/{_DB_RETRY_BOOTSTRAP}: {e!r}",
                flush=True,
            )
            if i + 1 < _DB_RETRY_BOOTSTRAP:
                time.sleep(min(4.0, 1.0 * (i + 1)))
    assert last is not None
    raise last


def ensure_user_row(email: str) -> None:
    """Registra e-mail em public.users (contato / pré-login); não substitui cadastro completo."""
    dsn = connection_string()
    if not dsn:
        return
    em = str(email or "").lower().strip()
    if not em:
        return

    def _go() -> None:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO public.users (email) VALUES (%s) "
                    "ON CONFLICT (email) DO NOTHING",
                    (em,),
                )
            conn.commit()

    run_with_db_retries(_go, label="ensure_user_row")


def _ident_table(name: str, default: str) -> str:
    t = (name or default).strip()
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", t):
        raise ValueError(f"invalid SQL identifier: {t!r}")
    return t


def _reg_table() -> str:
    t = (
        os.getenv("REGISTRATIONS_TABLE")
        or os.getenv("SUPABASE_REGISTRATIONS_TABLE")
        or ""
    )
    return _ident_table(t, "registrations")


def _credit_tables() -> tuple[str, str]:
    return (
        _ident_table(
            os.getenv("CREDIT_ACCOUNTS_TABLE")
            or os.getenv("SUPABASE_CREDIT_ACCOUNTS_TABLE")
            or "",
            "credit_accounts",
        ),
        _ident_table(
            os.getenv("CREDIT_LEDGER_TABLE")
            or os.getenv("SUPABASE_CREDIT_LEDGER_TABLE")
            or "",
            "credit_ledger",
        ),
    )


def registration_email_exists(email: str) -> bool:
    em = str(email).lower().strip()
    dsn = connection_string()
    if not dsn:
        return False
    reg = _reg_table()

    def _go() -> bool:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT 1 AS x FROM public.{} WHERE lower(email) = lower(%s) LIMIT 1"
                    ).format(sql.Identifier(reg)),
                    (em,),
                )
                return cur.fetchone() is not None

    return run_with_db_retries(_go, label="registration_email_exists")


def mark_registration_email_verified(email: str) -> None:
    em = str(email).lower().strip()
    dsn = connection_string()
    if not dsn:
        return
    reg = _reg_table()
    now = datetime.now(timezone.utc)

    def _go() -> None:
        try:
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            "UPDATE public.{} SET email_verified_at = %s WHERE lower(email) = lower(%s)"
                        ).format(sql.Identifier(reg)),
                        (now, em),
                    )
                conn.commit()
        except psycopg.errors.UndefinedColumn:
            pass

    run_with_db_retries(_go, label="mark_registration_email_verified")


def insert_registration(row: dict[str, Any]) -> None:
    """row = model_dump() do RegistrationBody (email já normalizado)."""
    dsn = connection_string()
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    reg = _reg_table()
    cols = list(row.keys())
    values = [row[k] for k in cols]
    stmt = sql.SQL("INSERT INTO public.{} ({}) VALUES ({})").format(
        sql.Identifier(reg),
        sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        sql.SQL(", ").join(sql.Placeholder() * len(cols)),
    )

    def _go() -> None:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, values)
            conn.commit()

    run_with_db_retries(_go, label="insert_registration")


def credit_ensure_account(email: str) -> dict[str, Any]:
    em = str(email).lower().strip()
    dsn = connection_string()
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    acc_t, _ = _credit_tables()

    def _go() -> dict[str, Any]:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT email, balance, pending_reserved FROM public.{} WHERE email = %s"
                    ).format(sql.Identifier(acc_t)),
                    (em,),
                )
                row = cur.fetchone()
                if row:
                    return dict(row)
                cur.execute(
                    sql.SQL(
                        "INSERT INTO public.{} (email, balance, pending_reserved) VALUES (%s, 0, 0) "
                        "ON CONFLICT (email) DO NOTHING"
                    ).format(sql.Identifier(acc_t)),
                    (em,),
                )
                cur.execute(
                    sql.SQL(
                        "SELECT email, balance, pending_reserved FROM public.{} WHERE email = %s"
                    ).format(sql.Identifier(acc_t)),
                    (em,),
                )
                row2 = cur.fetchone()
                if row2:
                    return dict(row2)
            conn.commit()
        raise RuntimeError("credit_ensure_account failed")

    return run_with_db_retries(_go, label="credit_ensure_account")


def credit_sum_used_this_month(email: str) -> int:
    em = str(email).lower().strip()
    dsn = connection_string()
    if not dsn:
        return 0
    _, led_t = _credit_tables()
    start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "SELECT COALESCE(SUM(delta), 0) AS s FROM public.{} "
                    "WHERE email = %s AND created_at >= %s AND delta < 0"
                ).format(sql.Identifier(led_t)),
                (em, start),
            )
            r = cur.fetchone()
            total = int(r[0] if r else 0)
            return abs(total)


def credit_grant_from_stripe_checkout(
    purchaser_email: str, session_id: str, grant: int
) -> None:
    if grant <= 0:
        return
    email = str(purchaser_email or "").lower().strip()
    if not email:
        return
    dsn = connection_string()
    if not dsn:
        return
    acc_t, led_t = _credit_tables()
    with psycopg.connect(dsn) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT id FROM public.{} WHERE stripe_session_id = %s LIMIT 1"
                    ).format(sql.Identifier(led_t)),
                    (session_id,),
                )
                if cur.fetchone():
                    return
                cur.execute(
                    sql.SQL(
                        "INSERT INTO public.{} (email, balance, pending_reserved) VALUES (%s, 0, 0) "
                        "ON CONFLICT (email) DO NOTHING"
                    ).format(sql.Identifier(acc_t)),
                    (email,),
                )
                cur.execute(
                    sql.SQL(
                        "SELECT balance FROM public.{} WHERE email = %s FOR UPDATE"
                    ).format(sql.Identifier(acc_t)),
                    (email,),
                )
                acc = cur.fetchone()
                if not acc:
                    return
                bal = int(acc[0] or 0)
                new_bal = bal + grant
                cur.execute(
                    sql.SQL(
                        "UPDATE public.{} SET balance = %s, updated_at = now() WHERE email = %s"
                    ).format(sql.Identifier(acc_t)),
                    (new_bal, email),
                )
                cur.execute(
                    sql.SQL(
                        "INSERT INTO public.{} "
                        "(email, delta, balance_after, operation, detail, stripe_session_id) "
                        "VALUES (%s, %s, %s, %s, %s, %s)"
                    ).format(sql.Identifier(led_t)),
                    (
                        email,
                        grant,
                        new_bal,
                        "Package",
                        f"Credits top-up (Stripe — {grant})",
                        session_id,
                    ),
                )
            conn.commit()
        except psycopg.errors.UniqueViolation:
            conn.rollback()


def credit_ledger_list(email: str, limit: int) -> list[dict[str, Any]]:
    em = str(email).lower().strip()
    dsn = connection_string()
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    _, led_t = _credit_tables()
    lim = max(1, min(int(limit), 100))

    def _go() -> list:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT created_at, operation, detail, delta, balance_after FROM public.{} "
                        "WHERE email = %s ORDER BY created_at DESC LIMIT %s"
                    ).format(sql.Identifier(led_t)),
                    (em, lim),
                )
                return cur.fetchall()

    rows = run_with_db_retries(_go, label="credit_ledger_list")
    out = []
    for r in rows:
        d = dict(r)
        ca = d.get("created_at")
        if hasattr(ca, "isoformat"):
            d["created_at"] = ca.isoformat()
        out.append(d)
    return out
