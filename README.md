# OneStar Skate Park web app (원스타스케이트파크)
Python 3 (http.server). Mobile-first PWA: class schedule, booking with capacity checks, admin page, chat helper.

## Render (free Web Service)
- Runtime: Python 3
- Build command: `pip install -r requirements.txt`
- Start command: `python server.py`

## Environment variables
- `ADMIN_PASSWORD` - admin page password. Set this; the built-in default is not safe.
- `DATABASE_URL` - Postgres connection string (Neon or Supabase free tier). Tables are created on startup.
  If it is not set, the app uses a local SQLite file (onestar.db). On Render that file is wiped on restart or redeploy.
- `OPENAI_API_KEY` - optional. Without it, the chat answers from the built-in FAQ.
- `LLM_API_URL`, `LLM_MODEL` - optional (default: OpenAI gpt-4o-mini)
- `PORT` - Render sets this automatically.
