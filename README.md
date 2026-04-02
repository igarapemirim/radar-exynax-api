# radar-exynax-api

Backend **FastAPI** em produção (Render): login (magic link), convite de cadastro com JWT (72h) e `POST /register`.

## Variáveis de ambiente

Ver `api/.env.example`. Obrigatórias para o fluxo completo:

- `AUTH_JWT_SECRET` — assinatura dos JWT (magic, `pre_register`, sessão)
- `DATABASE_URL` — Postgres (Render)
- `RESEND_API_KEY` — envio de e-mails
- `PUBLIC_SITE_URL` — ex.: `https://www.radarexynax.com` (link no e-mail de convite)
- `PUBLIC_API_URL` — URL pública **desta** API (magic link → `/auth/confirm`)

## Fluxo de login (`POST /api/v1/auth/login`)

1. E-mail **já** em `registrations` → envia magic link (`GET /auth/confirm` → redireciona com `#exynax_session=`).
2. E-mail **não** encontrado → responde JSON `flow: "pre_register"` e envia e-mail com link direto a  
   `https://www.radarexynax.com/register.html?email=...&token=...` (JWT `typ: pre_register`, **72h**).

## `POST /register`

Campo opcional `register_intent_token`: se enviado, deve ser o JWT do convite e o `sub` deve coincidir com o e-mail do formulário.

## Módulo de e-mail

Lógica Resend em `api/login/send_auth.py` (`send_complete_registration_email`, `send_magic_link`).

## Executar localmente

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000
```

## Deploy (Render)

- **Root directory:** raiz do repositório
- **Build:** `pip install -r requirements.txt`
- **Start:** `uvicorn api.app:app --host 0.0.0.0 --port $PORT`
