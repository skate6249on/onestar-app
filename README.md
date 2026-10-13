# OneStar Skate Park web app (원스타스케이트파크)
Python 3 (http.server). Mobile-first PWA: class schedule, booking with capacity checks, admin page, chat helper.

## Render (free Web Service)
- Runtime: Python 3
- Build command: `pip install -r requirements.txt`
- Start command: `python server.py`

## Environment variables
- `ADMIN_PASSWORD` - admin page password. Required: if unset, admin endpoints return 503 (disabled). There is no default.
- `DATABASE_URL` - Postgres connection string (Neon or Supabase free tier). Tables are created on startup.
  If it is not set, the app uses a local SQLite file (onestar.db). On Render that file is wiped on restart or redeploy.
- `OPENAI_API_KEY` - optional. Without it, the chat answers from the built-in FAQ.
- `LLM_API_URL`, `LLM_MODEL` - optional (default: OpenAI gpt-4o-mini)
- `PORT` - Render sets this automatically.

## v3 관리자 기능
수강생 관리(일괄 등록 포함) · 4회 수강권/결제 · 고정 반 배정 · 주간/월간 스케줄 · 휴무일 · 수업 이동 · 보강 · 원탭 출석(잔여 차감) · 대시보드.
DB 테이블은 서버 시작 시 자동 생성/마이그레이션돼요(Postgres·SQLite 모두 여러 번 실행해도 안전). 관리자 기능은 ADMIN_PASSWORD 환경변수가 있어야 켜져요.
