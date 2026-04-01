# radar-exynax-api

Backend FastAPI mínimo com CORS para `https://radarexynax.com` e health check.

## Executar localmente

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000
```

- `GET /health` → `{"status": "ok"}`

## Deploy (ex.: Render)

- **Build:** `pip install -r requirements.txt`
- **Start:** `uvicorn api.app:app --host 0.0.0.0 --port $PORT`
- **Root directory:** raiz deste repositório (onde está a pasta `api/`).
