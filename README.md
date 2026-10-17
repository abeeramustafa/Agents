# SourceDesk

One person directs a small team of agents through the first half of a wholesale sourcing workflow. The catalog is sample data in `data/`. The page is labeled **Sample catalog, fictional data**.

Agents never invent products, suppliers, or prices. pandas scores the catalog. Every model reply is JSON checked with Pydantic.

## Run locally

Python 3.11 or newer.

```powershell
cd c:\Users\User\Desktop\Agents
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env. Save as a file named exactly .env, not .env.txt.
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 3000
```

Open http://127.0.0.1:3000

Health check: http://127.0.0.1:3000/health

LLM smoke test: http://127.0.0.1:3000/api/llm-check

One process only. Session state is in memory, so a second worker would not see your tab.

## Environment

Copy `.env.example` to `.env` in this folder. The app reads:

| Name | Purpose |
|---|---|
| `LLM_BASE_URL` | OpenAI-compatible API root. Groq: `https://api.groq.com/openai/v1` |
| `LLM_API_KEY` | Secret. Never commit it |
| `LLM_MODEL` | Model id from that provider |
| `PORT` | Listen port, default `3000`. Must match the Docker/host port |
| `ENABLE_AUDITOR` | Run Karim on Omar and Lina, default `true` |
| `COST_ALLOWANCE` | Cost share of retail price, default `0.40` |

`.env` is git-ignored. Do not paste the key into Python, HTML, or this README.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

## Manual test checklist

1. "Electronics under $30 retail, good margins, low returns" → ranked products with flags.
2. "I want to start selling something but have no idea what" → one clarifying question.
3. "drones" → out-of-catalog card with closest matches, no invented products.
4. "earfones" → fuzzy match to earbuds / earphones.
5. "shoes" → out of scope (catalog is electronics only).
6. Approve 3 products → supplier stage shows the cheap, risky supplier flagged rather than auto-selected. Checkboxes start empty.
7. Break the LLM key → friendly error, app keeps running (`/health` still returns ok).

Also click a product with risk flags, pick a flagged supplier, and confirm the plan says **You chose … despite** and the activity log uses local time.

## Docker

```powershell
docker build -t sourcedesk .
docker run --rm -p 3000:3000 --env-file .env sourcedesk
```

The container listens on `0.0.0.0` and `${PORT:-3000}` with one worker.

## Alternate Clouds

1. Push this folder as the Docker context (Dockerfile at the repo root).
2. Set Variables: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `ENABLE_AUDITOR`, `COST_ALLOWANCE`, and `PORT=3000`.
3. **Rebuild, then Deploy.** A variable change does not apply until you rebuild.
4. `PORT` must match the Dockerfile (`3000` unless you change both).
5. Confirm `GET /health` returns `{"status":"ok"}`.
