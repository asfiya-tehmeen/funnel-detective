# Deployment

The app has two services: a FastAPI backend (Python) and a Next.js frontend. The browser only ever talks
to the frontend origin, and Next.js proxies `/api/*` to the backend. You therefore don't need CORS in
production, and the backend can stay private.

> **Important:** Next.js evaluates `rewrites()` at **build time**. Set `BACKEND_URL` when you *build* the
> frontend (Docker build arg or the hosting provider's build environment), not only at runtime.

## Local, without Docker

See the README quick start. `BACKEND_URL` defaults to `http://127.0.0.1:8000`.

## Local, with Docker

```bash
docker compose up --build          # frontend on :3000, backend on :8000
ANTHROPIC_API_KEY=sk-... docker compose up --build   # optional LLM mode
```

The backend image generates the dataset during the build (deterministic, about 10 s), so it needs no volume.

## Public deployment (example: Render or Fly.io for the API, Vercel for the web app)

1. **Backend**, from `backend/Dockerfile`:
   - Render: create a *Web Service* from the repo, root `backend`, Docker runtime. The image honours `$PORT`.
   - Fly.io: `cd backend && fly launch --dockerfile Dockerfile` (internal port 8000), then `fly deploy`.
   - Optional environment variables: `ANTHROPIC_API_KEY`, `FD_LLM_MODEL`.
   - About 512 MB of memory is enough: the dataset is about 185k rows in in-memory DuckDB.
   - The audit log is SQLite in `/tmp/fd-var`. It resets on redeploy, which is acceptable for a demo; mount a
     volume and set `FD_VAR_DIR` to keep it.
2. **Frontend**: import the repo in Vercel with root directory `frontend`, and set the build environment
   variable `BACKEND_URL=https://<your-backend-host>`. Deploy.
3. **Smoke test:** open `https://<frontend>/api/health` (proxied) and run one example question.

## Configuration reference

| Variable | Service | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | backend | unset | Enables the LLM planner and narrator |
| `FD_LLM_PROVIDER` | backend | `auto` | `none` forces deterministic mode |
| `FD_LLM_MODEL` | backend | `claude-opus-5-5` | Model ID for the Anthropic adapter |
| `FD_LLM_TIMEOUT_S` | backend | `60` | Per-request timeout |
| `FD_CORS_ORIGINS` | backend | localhost:3000 | Only needed if the browser calls the API directly |
| `FD_SQL_ROW_LIMIT`, `FD_SQL_TIMEOUT_S` | backend | `500`, `5` | SQL console limits |
| `FD_DATA_DIR`, `FD_VAR_DIR` | backend | `backend/data`, `backend/var` | Dataset and audit locations |
| `FD_AUTOGENERATE` | backend | `1` | Generate the dataset at startup if it's missing |
| `BACKEND_URL` | frontend (build) | `http://127.0.0.1:8000` | Proxy target for `/api/*` |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Page shows "Cannot reach the analytics API" | Start the backend on port 8000, or rebuild the frontend with the correct `BACKEND_URL` |
| `Dataset not found` on startup | Run `python -m scripts.generate_data` in `backend/`, or leave `FD_AUTOGENERATE=1` |
| `python` runs Python 2 or the wrong version (Windows) | Use `py -3.12 -m venv .venv` and the venv's interpreter |
| `ModuleNotFoundError: app` when running scripts | Run from `backend/` with `python -m ...`; `pytest.ini` sets `pythonpath = .` |
| LLM mode shows "Deterministic template" with a fallback note | The model's text failed validation or the provider was unreachable. Expand "Why the LLM text was rejected" |
| Slow `npm install` / `next build` | The repo sits in a cloud-synced folder (OneDrive/Dropbox). Exclude `node_modules` and `.next` from sync |
| Port already in use | `uvicorn app.main:app --port 8001` and `BACKEND_URL=http://127.0.0.1:8001 npm run dev` |
