# OneStar Skate Park web app v4 (원스타스케이트파크)
Python 3 (http.server). Mobile-first PWA. v4 = 1인 운영 시스템: 공개 홈(상품·가격·문의·체험 예약) + 관리자(할 일, 문의 파이프라인, 스케줄, 수강생, 재등록, 장부, 주간 리포트, 상품).

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

## v4 (1인 운영 시스템)
- 공개: 상품/수강료(체험은 인당), 운영 시간, 주소, 문의 폼(`POST /api/inquiry`), 체험 신청(`POST /api/trial`, `/api/book`도 동일): 희망 날짜(월 휴무·휴무일 제외)+희망 시간대, 시간은 통화 후 확정. 예약금(기본 10,000원, 상품 탭에서 변경)은 총액에서 차감, 확정 시 안내(계좌번호 표시 안 함). 정규 시간표/정원은 정규수업 관리용으로만 사용.
- 관리자 🙋체험 탭: 시간 확정(운영시간 검사) → 예약금 입금확인 → 체험 완료 → 잔금 결제. 장부에는 예약금(trial_deposit)과 잔금(trial)이 따로 1건씩 기록돼 중복이 없어요.
- 관리자 탭: ✅할 일 · 📥문의(신규문의→체험예약→체험완료→정규등록/보류, 경로·메모·다음 연락일) · 📆스케줄 · 👥수강생 · 🔁재등록(문구 복사만, 앱은 메시지를 보내지 않음) · 💰장부(수강권/체험 결제 자동 기록 + 수동 매출·지출, 월별 순이익) · 📊주간 리포트 · 🏷️상품.
- 새 테이블: products, leads, payments, expenses, settings. 기존 테이블에는 컬럼만 추가(멱등). v3 수강권 납부액은 처음 시작할 때 장부로 한 번 이관돼요.
- 상품 기본값(네이버 스마트플레이스 가격)은 code 기준으로 한 번만 들어가요. 이후 수정·숨김은 관리자 '상품'에서.

## 테스트 (운영 DB 사용 금지)
- `python3 tests/test_v4.py` : 임시 SQLite로 서버를 띄워 전체 흐름 검사.
- `TEST_DATABASE_URL=postgresql://...(로컬 테스트 DB) python3 tests/test_v4.py` : Postgres 경로 검사.
- 스크린샷용 예시 데이터: `SQLITE_PATH=/tmp/demo.db python3 tests/seed_demo.py` (DATABASE_URL이 있거나 onestar.db면 거부).
