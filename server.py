#!/usr/bin/env python3
"""원스타스케이트파크 웹앱 - http.server + (Postgres via DATABASE_URL | 로컬 sqlite3)."""
import json, os, sqlite3, datetime, re, urllib.request, mimetypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "onestar.db")
PORT = int(os.environ.get("PORT", 8800))
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "onestar2026")
LLM_KEY = os.environ.get("OPENAI_API_KEY", "")
LLM_URL = os.environ.get("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
NAVER = "https://map.naver.com/p/entry/place/33890325"
PHONE = "010-5263-6249"
WEEKDAY_SLOTS = [("15:00", "키즈 그룹", "kids", 5), ("17:00", "키즈 그룹", "kids", 5), ("19:00", "성인 기초반", "adult", 5)]
SAT_SLOTS = [("15:00", "주말반", "weekend", 4), ("16:00", "주말반", "weekend", 4)]
SUN_SLOTS = [("10:00", "주말반", "weekend", 4), ("14:00", "주말반", "weekend", 4)]
# kind별 요금: (총액, 예약금, 잔금)
PRICES = {
    "kids": (50000, 5000, 45000),
    "adult": (75000, 7500, 67500),
    "weekend": (60000, 6000, 54000),
}
DEFAULT_PRICE = (50000, 5000, 45000)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"): DATABASE_URL = "postgresql://" + DATABASE_URL[11:]
PG = bool(DATABASE_URL)
if PG:
    import psycopg2, psycopg2.extras

class _Cur:
    def __init__(self, cur, lastrowid=None): self.cur, self.lastrowid = cur, lastrowid
    def fetchone(self): return self.cur.fetchone()
    def fetchall(self): return self.cur.fetchall()
    def __iter__(self): return iter(self.cur.fetchall())

class PgConn:
    """sqlite3 스타일 SQL(?, INSERT OR IGNORE/REPLACE)을 Postgres로 변환하는 얇은 어댑터."""
    def __init__(self):
        self.c = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor, connect_timeout=10)
    def execute(self, sql, params=()):
        sql = sql.strip()
        if sql == "BEGIN IMMEDIATE": return _Cur(None)
        ret = False
        if sql.startswith("INSERT OR IGNORE"):
            sql = sql.replace("INSERT OR IGNORE", "INSERT", 1) + " ON CONFLICT DO NOTHING"
        elif sql.startswith("INSERT OR REPLACE INTO classes"):
            sql = sql.replace("INSERT OR REPLACE", "INSERT", 1) + " ON CONFLICT(date,time) DO UPDATE SET title=EXCLUDED.title, kind=EXCLUDED.kind, capacity=EXCLUDED.capacity, cancelled=0"
        elif sql.startswith("INSERT"):
            sql += " RETURNING id"; ret = True
        cur = self.c.cursor(); cur.execute(sql.replace("?", "%s"), tuple(params))
        return _Cur(cur, cur.fetchone()["id"] if ret else None)
    def executescript(self, sql):
        cur = self.c.cursor()
        for st in sql.split(";"):
            if st.strip(): cur.execute(st)
    def commit(self): self.c.commit()
    def rollback(self): self.c.rollback()
    def close(self): self.c.close()

def db():
    if PG: return PgConn()
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init():
    c = db()
    pk = "SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY"
    c.executescript(f"""
    CREATE TABLE IF NOT EXISTS classes(id {pk}, date TEXT, time TEXT, title TEXT, kind TEXT,
      capacity INTEGER, cancelled INTEGER DEFAULT 0, UNIQUE(date,time));
    CREATE TABLE IF NOT EXISTS bookings(id {pk}, class_id INTEGER, name TEXT, phone TEXT,
      age TEXT, count INTEGER, status TEXT DEFAULT '예약금 대기', deposit_paid INTEGER DEFAULT 0,
      balance_paid INTEGER DEFAULT 0, attended INTEGER DEFAULT 0, cancelled INTEGER DEFAULT 0, created TEXT);
    """); c.commit(); c.close()

def ensure_classes(date):
    d = datetime.date.fromisoformat(date)
    wd = d.weekday()  # Mon=0 .. Sun=6
    if wd in (1, 2, 3, 4):
        slots = WEEKDAY_SLOTS
    elif wd == 5:  # Saturday
        slots = SAT_SLOTS
    elif wd == 6:  # Sunday
        slots = SUN_SLOTS
    else:
        return
    c = db()
    for t, title, kind, cap in slots:
        c.execute("INSERT OR IGNORE INTO classes(date,time,title,kind,capacity) VALUES(?,?,?,?,?)", (date, t, title, kind, cap))
    c.commit(); c.close()

def price_for(kind):
    return PRICES.get(kind, DEFAULT_PRICE)

def classes_for(date):
    ensure_classes(date)
    c = db()
    rows = c.execute("""SELECT cl.*, COALESCE((SELECT SUM(count) FROM bookings b WHERE b.class_id=cl.id AND b.cancelled=0),0) booked
      FROM classes cl WHERE date=? ORDER BY time""", (date,)).fetchall()
    c.close()
    out = []
    for r in rows:
        total, dep, bal = price_for(r["kind"])
        out.append(dict(r, booked=int(r["booked"]), remaining=max(0, r["capacity"] - int(r["booked"])), price=total, deposit=dep, balance=bal))
    return out

# ---------- FAQ 자동응답 ----------
FAQ = [
  (r"시간|스케줄|일정|언제|요일|몇시|주말", "수업 일정이에요.\n• 화~금 키즈 그룹: 15:00, 17:00 (타임당 5명)\n• 화~금 성인 기초반: 19:00 (5명)\n• 주말반 토: 15:00, 16:00 (타임당 4명)\n• 주말반 일: 10:00, 14:00 (타임당 4명)\n아래 '예약하기'에서 남은 자리를 확인할 수 있어요."),
  (r"예약금|선금|보증금|입금", "평일 키즈는 예약금 5,000원 + 현장 잔금 45,000원(1회 50,000원),\n평일 성인 기초반은 예약금 7,500원 + 현장 잔금 67,500원(1회 75,000원),\n주말반(토·일)은 예약금 6,000원 + 현장 잔금 54,000원(1회 60,000원)이에요."),
  (r"가격|비용|얼마|요금|수강료", "평일 키즈 1회 50,000원(예약금 5,000원 + 잔금 45,000원),\n평일 성인 기초반 1회 75,000원(예약금 7,500원 + 잔금 67,500원),\n주말반(토·일) 1회 60,000원(예약금 6,000원 + 잔금 54,000원)이에요."),
  (r"위치|주소|어디|오시는|찾아", "경기 이천시 신둔면 원적로512번길 103 3동이에요.\n네이버 지도: " + NAVER),
  (r"주차", "주차는 무료예요."),
  (r"전화|연락|번호|문의", "전화 문의: " + PHONE),
  (r"스노보드|보드강습|지산", "스노보드 강습은 지산리조트에서 진행해요. 자세한 일정과 비용은 " + PHONE + "로 문의해 주세요."),
  (r"인스타|instagram|sns", "인스타그램 @onestarskate 에서 수업 영상을 볼 수 있어요: https://instagram.com/onestarskate"),
  (r"예약|신청|등록", "예약은 앱의 '예약하기'에서 날짜와 시간을 고르면 돼요. 네이버 예약도 가능해요: " + NAVER),
  (r"키즈|아이|어린이|초등", "키즈 그룹은 화~금 15:00, 17:00, 타임당 5명이에요. 1회 50,000원(예약금 5,000원)."),
  (r"성인|어른|직장", "성인 기초반은 화~금 19:00, 정원 5명이에요. 1회 75,000원(예약금 7,500원 + 현장 잔금 67,500원)."),
  (r"주말반|토요일|토요|일요일|일요", "주말반은 토 15:00·16:00, 일 10:00·14:00, 타임당 4명이에요. 1회 60,000원(예약금 6,000원 + 현장 잔금 54,000원)."),
  (r"안녕|하이|hello", "안녕하세요! 원스타스케이트파크예요. 수업 시간, 가격, 위치, 예약에 대해 물어보세요."),
]
FACTS = f"""원스타스케이트파크 정보(이것 외의 정보는 절대 지어내지 말 것):
- 평일 수업: 화~금. 키즈 그룹 15:00, 17:00(정원 5) 1회 50,000원(예약금 5,000원 + 잔금 45,000원). 성인 기초반 19:00(정원 5) 1회 75,000원(예약금 7,500원 + 잔금 67,500원)
- 주말반: 토 15:00, 16:00 / 일 10:00, 14:00(정원 4). 1회 60,000원(예약금 6,000원 + 잔금 54,000원)
- 주소: 경기 이천시 신둔면 원적로512번길 103 3동, 주차 무료
- 전화: {PHONE}, 인스타그램 @onestarskate, 네이버 예약: {NAVER}
- 스노보드 강습: 지산리조트
모르는 내용은 "{PHONE}로 전화 주세요"라고 답하라. 예약을 원하면 앱의 '예약하기' 버튼을 안내하라. 한국어로 짧게 답하라."""

def rule_reply(msg):
    hits = [a for p, a in FAQ if re.search(p, msg, re.I)]
    if hits:
        return "\n\n".join(dict.fromkeys(hits[:2]))
    return f"정확한 답변을 드리기 어려운 내용이에요. {PHONE}로 전화 주시면 친절히 안내해 드릴게요."

def reply(msg):
    if LLM_KEY:
        try:
            body = json.dumps({"model": LLM_MODEL, "messages": [{"role": "system", "content": FACTS}, {"role": "user", "content": msg}], "temperature": 0.2}).encode()
            req = urllib.request.Request(LLM_URL, body, {"Content-Type": "application/json", "Authorization": "Bearer " + LLM_KEY})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)["choices"][0]["message"]["content"], "llm"
        except Exception:
            pass
    return rule_reply(msg), "faq"

# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(b)); self.end_headers(); self.wfile.write(b)
    def body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}")
    def admin_ok(self):
        return self.headers.get("X-Admin-Password") == ADMIN_PW

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/api/classes":
            return self.send(classes_for(q.get("date", [datetime.date.today().isoformat()])[0]))
        if u.path == "/api/admin/week":
            if not self.admin_ok(): return self.send({"error": "unauthorized"}, 401)
            start = datetime.date.fromisoformat(q.get("start", [datetime.date.today().isoformat()])[0])
            out = []
            for i in range(7):
                d = (start + datetime.timedelta(days=i)).isoformat()
                cls = classes_for(d)
                c = db()
                for cl in cls:
                    cl["bookings"] = [dict(r) for r in c.execute("SELECT * FROM bookings WHERE class_id=? ORDER BY id", (cl["id"],))]
                c.close()
                out.append({"date": d, "classes": cls})
            return self.send(out)
        p = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        f = os.path.normpath(os.path.join(BASE, "static", p))
        if not f.startswith(os.path.join(BASE, "static")) or not os.path.isfile(f):
            f = os.path.join(BASE, "static", "index.html")
        b = open(f, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(f)[0] or "application/octet-stream") + ("; charset=utf-8" if f.endswith((".html", ".js", ".json", ".css")) else ""))
        self.send_header("Content-Length", len(b)); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        u = urlparse(self.path); d = self.body()
        if u.path == "/api/chat":
            r, src = reply(str(d.get("message", ""))[:500]); return self.send({"reply": r, "source": src})
        if u.path == "/api/book":
            name, phone = str(d.get("name", "")).strip(), str(d.get("phone", "")).strip()
            try: count = int(d.get("count", 1))
            except: count = 0
            if not name or not re.fullmatch(r"[0-9\- ]{9,14}", phone) or not (1 <= count <= 5):
                return self.send({"error": "이름, 전화번호, 인원(1~5명)을 확인해 주세요."}, 400)
            c = db()
            try:
                c.execute("BEGIN IMMEDIATE")
                cl = c.execute("SELECT * FROM classes WHERE id=?" + (" FOR UPDATE" if PG else ""), (int(d.get("class_id") or 0),)).fetchone()
                if not cl or cl["cancelled"]: raise ValueError("해당 수업은 예약할 수 없어요.")
                if cl["date"] < datetime.date.today().isoformat(): raise ValueError("지난 날짜는 예약할 수 없어요.")
                booked = c.execute("SELECT COALESCE(SUM(count),0) AS n FROM bookings WHERE class_id=? AND cancelled=0", (cl["id"],)).fetchone()["n"]
                booked = int(booked)
                if booked + count > cl["capacity"]: raise ValueError(f"남은 자리가 {cl['capacity']-booked}석이에요.")
                cur = c.execute("INSERT INTO bookings(class_id,name,phone,age,count,created) VALUES(?,?,?,?,?,?)",
                    (cl["id"], name, phone, str(d.get("age", ""))[:50], count, datetime.datetime.now().isoformat(timespec="seconds")))
                c.commit()
                total, dep, bal = price_for(cl["kind"])
                return self.send({"ok": True, "id": cur.lastrowid, "status": "예약금 대기",
                                  "deposit": dep * count, "balance": bal * count, "total": total * count,
                                  "naver": NAVER, "class": dict(cl)})
            except ValueError as e:
                c.rollback(); return self.send({"error": str(e)}, 409)
            finally: c.close()
        if u.path.startswith("/api/admin/"):
            if not self.admin_ok(): return self.send({"error": "unauthorized"}, 401)
            c = db(); act = u.path.split("/")[-1]
            if act == "login": pass
            elif act == "class_update":
                for k in ("capacity", "cancelled"):
                    if k in d: c.execute(f"UPDATE classes SET {k}=? WHERE id=?", (int(d[k]), d["id"]))
            elif act == "class_add":
                c.execute("INSERT OR REPLACE INTO classes(date,time,title,kind,capacity) VALUES(?,?,?,?,?)",
                          (d["date"], d["time"], d.get("title", "특별 수업"), d.get("kind", "adult"), int(d.get("capacity", 5))))
            elif act == "booking_update":
                for k in ("deposit_paid", "balance_paid", "attended", "cancelled"):
                    if k in d: c.execute(f"UPDATE bookings SET {k}=? WHERE id=?", (int(d[k]), d["id"]))
                b = c.execute("SELECT * FROM bookings WHERE id=?", (d["id"],)).fetchone()
                st = "취소" if b["cancelled"] else "결제 완료" if b["balance_paid"] else "예약 확정" if b["deposit_paid"] else "예약금 대기"
                c.execute("UPDATE bookings SET status=? WHERE id=?", (st, d["id"]))
            c.commit(); c.close(); return self.send({"ok": True})
        self.send({"error": "not found"}, 404)

if __name__ == "__main__":
    init()
    print(f"원스타 앱: http://localhost:{PORT}  (LLM: {'on' if LLM_KEY else 'off, FAQ 모드'}, DB: {'postgres' if PG else 'sqlite'})")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
