#!/usr/bin/env python3
"""원스타스케이트파크 웹앱 v4 (1인 운영 시스템) - http.server + (Postgres via DATABASE_URL | 로컬 sqlite3).
공개: 소개/상품/체험 예약/문의.  관리자: 할 일, 문의(리드), 스케줄, 수강생, 재등록, 장부, 주간 리포트, 상품.
이 앱은 문자·메시지를 보내지 않아요(안내 문구는 복사만)."""
import json, os, sqlite3, datetime, re, urllib.request, mimetypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("SQLITE_PATH") or ("/tmp/onestar.db" if os.environ.get("VERCEL") else os.path.join(BASE, "onestar.db"))
PORT = int(os.environ.get("PORT", 8800))
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "")  # 미설정 시 관리자 기능 비활성화
LLM_KEY = os.environ.get("OPENAI_API_KEY", "")
LLM_URL = os.environ.get("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
NAVER = "https://map.naver.com/p/entry/place/33890325"
PHONE = "010-5263-6249"
WEEKDAY_SLOTS = [("15:00", "키즈 그룹", "kids", 5), ("17:00", "키즈 그룹", "kids", 5), ("19:00", "성인 기초반", "adult", 5)]
SAT_SLOTS = [("15:00", "주말반", "weekend", 4), ("16:00", "주말반", "weekend", 4)]
SUN_SLOTS = [("10:00", "주말반", "weekend", 4), ("14:00", "주말반", "weekend", 4)]
KINDS = {"kids": "키즈", "adult": "성인", "weekend": "주말"}  # 수업 구분(요금은 상품 관리에서)
ADDRESS = "경기도 이천시 신둔면 원적로512번길 103 (도봉리 621-3)"
HOURS = [("화~금", "13:00~21:00"), ("토", "10:00~18:00"), ("일", "10:00~16:00"), ("월", "정기휴무")]
INSTA = "onestarskate"
# 상품 기본값(네이버 스마트플레이스 기준). 처음 한 번만 넣고, 이후엔 관리자 '상품' 화면에서 수정.
# (code, 분류, 이름, 가격(None=문의), 인당여부, 수강권종류, 횟수, 설명, 예약가능)
TRIAL_NOTE = "강습료·입장료·장비 대여 포함"
SNOW_DESC = "지산리조트에서 진행 · 원하는 리조트로 출장 가능 · 일정과 수강료는 문의 주세요"
DEFAULT_PRODUCTS = [
    ("trial_13_40", "체험수업", "체험수업 1:3 · 40분", 40000, 1, "", 1, TRIAL_NOTE, 1),
    ("trial_12_40", "체험수업", "체험수업 1:2 · 40분", 50000, 1, "", 1, TRIAL_NOTE, 1),
    ("trial_11_30", "체험수업", "체험수업 1:1 · 30분", 60000, 1, "", 1, TRIAL_NOTE, 1),
    ("reg_wd_kids", "정규수업", "정규 주중 키즈 (주1회 · 총4회)", 200000, 0, "wd_kids", 4, "장비 포함", 0),
    ("reg_wd_adult", "정규수업", "정규 주중 성인 (주1회 · 총4회)", 300000, 0, "wd_adult", 4, "장비 포함", 0),
    ("reg_we_kids", "정규수업", "정규 주말 키즈 (주1회 · 총4회)", 250000, 0, "we_kids", 4, "장비 포함", 0),
    ("reg_we_adult", "정규수업", "정규 주말 성인 (주1회 · 총4회)", 350000, 0, "we_adult", 4, "장비 포함", 0),
    ("reg_wd_kids11", "정규수업", "아이 1:1 수업 주중 (월 4회)", 250000, 0, "wd_kids11", 4, "장비 포함", 0),
    ("reg_we_kids11", "정규수업", "아이 1:1 수업 주말 (월 4회)", 300000, 0, "we_kids11", 4, "장비 포함", 0),
    ("snow", "시즌·단체", "겨울철 스노보드 강습", None, 0, "", 1, SNOW_DESC, 0),
    ("group", "시즌·단체", "단체 스케이트보드 수업", None, 0, "", 1, "인원·일정에 맞춰 안내해 드려요. 문의 주세요", 0),
]
ADDED_V42 = ("reg_wd_kids11", "reg_we_kids11")  # 2026-10 추가(아이 1:1). 기존 DB에는 한 번만 넣음(정규 성인 주말 바로 뒤)
RETIRED_PRODUCTS = ("trial_13_60", "trial_12_60", "trial_11_60")  # 판매 중단(1시간 체험)
PRODUCT_CATS = ["체험수업", "정규수업", "시즌·단체", "용품", "기타"]
STAGES = ["신규문의", "체험예약", "체험완료", "정규등록", "보류"]
STAGE_AT = {"체험예약": "trial_at", "체험완료": "done_at", "정규등록": "reg_at", "보류": "hold_at"}
DEFAULT_DEPOSIT = 10000  # 체험 예약금(예약 1건당 고정, 관리자 설정에서 변경)
# 희망 시간대(요일별, 운영시간 안). 0=월 … 6=일
TIME_PREFS = {"wd": ["오후 (13~17시)", "저녁 (17~21시)", "상관없음"], 5: ["오전 (10~12시)", "오후 (12~18시)", "상관없음"],
              6: ["오전 (10~12시)", "오후 (12~16시)", "상관없음"]}
OPEN_RANGE = {1: ("13:00", "21:00"), 2: ("13:00", "21:00"), 3: ("13:00", "21:00"), 4: ("13:00", "21:00"), 5: ("10:00", "18:00"), 6: ("10:00", "16:00")}
SOURCES = ["네이버", "인스타", "전화", "소개", "구글", "기타"]
EXPENSE_CATS = ["임대료", "장비·용품 구입", "광고", "공과금", "인건비", "기타"]
DEFAULT_RENEW_TPL = ("안녕하세요, 원스타스케이트예요 🛹\n{이름}님 정규수업이 {잔여}회 남았어요.\n"
                     "다음 {상품} {금액} 재등록 원하시면 편하게 말씀 주세요.\n감사합니다! 📞 " + "010-5263-6249")

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
    c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; return c

KST = datetime.timezone(datetime.timedelta(hours=9))
def today():
    return datetime.datetime.now(KST).date().isoformat()
def now():
    return datetime.datetime.now(KST).replace(tzinfo=None).isoformat(timespec="seconds")

DOW_KO = "월화수목금토일"
# 정규 수강권(4회)
PTYPES = {"wd_kids": ("주중 키즈", 200000), "wd_adult": ("주중 성인", 300000),
          "we_kids": ("주말 키즈", 250000), "we_adult": ("주말 성인", 350000),
          "wd_kids11": ("아이 1:1 주중", 250000), "we_kids11": ("아이 1:1 주말", 300000)}
PAY_STATUS = ["미납", "일부 납부", "완납"]
PAY_METHODS = ["현금", "카드", "계좌이체", "스포츠강좌이용권", "이천사랑화폐/제로페이", "기타"]

def slots_of(wd):
    if wd in (1, 2, 3, 4): return WEEKDAY_SLOTS
    if wd == 5: return SAT_SLOTS
    if wd == 6: return SUN_SLOTS
    return []

def slot_keys():
    out = []
    for wd in range(7):
        for t, title, kind, cap in slots_of(wd):
            out.append({"key": f"{wd}|{t}", "dow": wd, "time": t, "title": title, "kind": kind, "capacity": cap,
                        "label": f"{DOW_KO[wd]} {t} {title}"})
    return out
SLOT_MAP = {s["key"]: s for s in slot_keys()}

def _cols(c, table):
    if PG:
        return {r["column_name"] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name=?", (table,)).fetchall()}
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}

def add_column(c, table, col, decl):
    """멱등 컬럼 추가 (Postgres / SQLite 공통)."""
    if PG:
        c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {decl}")
    elif col not in _cols(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

def init():
    c = db()
    pk = "SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY"
    c.executescript(f"""
    CREATE TABLE IF NOT EXISTS classes(id {pk}, date TEXT, time TEXT, title TEXT, kind TEXT,
      capacity INTEGER, cancelled INTEGER DEFAULT 0, UNIQUE(date,time));
    CREATE TABLE IF NOT EXISTS bookings(id {pk}, class_id INTEGER, name TEXT, phone TEXT,
      age TEXT, count INTEGER, status TEXT DEFAULT '예약금 대기', deposit_paid INTEGER DEFAULT 0,
      balance_paid INTEGER DEFAULT 0, attended INTEGER DEFAULT 0, cancelled INTEGER DEFAULT 0, created TEXT);
    CREATE TABLE IF NOT EXISTS students(id {pk}, name TEXT NOT NULL, guardian TEXT, phone TEXT, grade TEXT,
      kind TEXT DEFAULT 'kids', memo TEXT, active INTEGER DEFAULT 1, created TEXT);
    CREATE TABLE IF NOT EXISTS packages(id {pk}, student_id INTEGER, ptype TEXT, sessions INTEGER, remaining INTEGER,
      price INTEGER, start_date TEXT, pay_status TEXT DEFAULT '미납', pay_method TEXT, paid_amount INTEGER DEFAULT 0,
      memo TEXT, created TEXT);
    CREATE TABLE IF NOT EXISTS enrollments(id {pk}, student_id INTEGER, slot_key TEXT, start_date TEXT, end_date TEXT, created TEXT);
    CREATE TABLE IF NOT EXISTS attendance(id {pk}, class_id INTEGER, student_id INTEGER, package_id INTEGER,
      status TEXT, created TEXT, UNIQUE(class_id, student_id));
    CREATE TABLE IF NOT EXISTS makeups(id {pk}, student_id INTEGER, class_id INTEGER, note TEXT, created TEXT, UNIQUE(class_id, student_id));
    CREATE TABLE IF NOT EXISTS holidays(id {pk}, date TEXT UNIQUE, reason TEXT);
    CREATE TABLE IF NOT EXISTS products(id {pk}, code TEXT UNIQUE, cat TEXT, name TEXT, price INTEGER, per_person INTEGER DEFAULT 0,
      ptype TEXT, sessions INTEGER DEFAULT 1, descr TEXT, sort INTEGER DEFAULT 0, active INTEGER DEFAULT 1, bookable INTEGER DEFAULT 0, updated TEXT);
    CREATE TABLE IF NOT EXISTS leads(id {pk}, name TEXT, phone TEXT, kind TEXT, interest TEXT, product_id INTEGER, people INTEGER DEFAULT 1,
      source TEXT, origin TEXT, stage TEXT DEFAULT '신규문의', memo TEXT, message TEXT, preferred TEXT, next_date TEXT,
      student_id INTEGER, booking_id INTEGER, created TEXT, updated TEXT, trial_at TEXT, done_at TEXT, reg_at TEXT, hold_at TEXT);
    CREATE TABLE IF NOT EXISTS payments(id {pk}, date TEXT, amount INTEGER, product TEXT, product_id INTEGER, method TEXT,
      source TEXT DEFAULT 'manual', ref_id INTEGER, student_id INTEGER, lead_id INTEGER, memo TEXT, created TEXT);
    CREATE TABLE IF NOT EXISTS expenses(id {pk}, date TEXT, category TEXT, amount INTEGER, memo TEXT, created TEXT);
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS trials(id {pk}, name TEXT, phone TEXT, age TEXT, count INTEGER DEFAULT 1, product_id INTEGER,
      pref_date TEXT, pref_time TEXT, message TEXT, source TEXT, lead_id INTEGER, confirmed INTEGER DEFAULT 0, conf_date TEXT, conf_time TEXT,
      deposit_amount INTEGER DEFAULT 0, deposit_paid INTEGER DEFAULT 0, attended INTEGER DEFAULT 0, balance_paid INTEGER DEFAULT 0,
      paid_amount INTEGER DEFAULT 0, pay_method TEXT, cancelled INTEGER DEFAULT 0, memo TEXT, created TEXT);
    """)
    for col, decl in (("note", "TEXT"), ("slot_key", "TEXT"), ("moved_from", "INTEGER"), ("moved_to", "INTEGER")):
        add_column(c, "classes", col, decl)
    add_column(c, "bookings", "memo", "TEXT")
    for col, decl in (("product_id", "INTEGER"), ("confirmed", "INTEGER DEFAULT 0"), ("paid_amount", "INTEGER DEFAULT 0"),
                      ("pay_method", "TEXT"), ("source", "TEXT"), ("lead_id", "INTEGER"), ("message", "TEXT")):
        add_column(c, "bookings", col, decl)
    add_column(c, "students", "lead_id", "INTEGER")
    add_column(c, "leads", "trial_id", "INTEGER")
    add_column(c, "students", "renew_notice", "TEXT")
    add_column(c, "packages", "created_by_lead", "INTEGER")
    for i, (code, cat, name, price, pp, ptype, ses, desc, bk) in enumerate(DEFAULT_PRODUCTS):
        if code in ADDED_V42: continue  # 아래 한 번만 단계에서 넣음
        c.execute("INSERT OR IGNORE INTO products(code,cat,name,price,per_person,ptype,sessions,descr,sort,active,bookable,updated) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
                  (code, cat, name, price, pp, ptype, ses, desc, (i + 1) * 10, bk, now()))
    # 체험수업 가격은 모두 인당(2026-10 사장님 확인). 기존 DB에 한 번만 적용.
    if not c.execute("SELECT k FROM settings WHERE k='mig_trial_pp'").fetchone():
        c.execute("UPDATE products SET per_person=1 WHERE code LIKE ?", ("trial%",))
        c.execute("INSERT OR IGNORE INTO settings(k,v) VALUES('mig_trial_pp','1')")
    # 스노보드 강습 장소(지산리조트, 출장 가능) 반영. 한 번만, 사장님이 설명을 직접 바꾼 경우는 그대로 둠.
    if not c.execute("SELECT k FROM settings WHERE k='mig_snow_desc'").fetchone():
        c.execute("UPDATE products SET descr=?, updated=? WHERE code='snow' AND (descr IS NULL OR descr='' OR descr=?)",
                  (SNOW_DESC, now(), "일정과 수강료는 문의 주세요"))
        c.execute("INSERT OR IGNORE INTO settings(k,v) VALUES('mig_snow_desc','1')")
    # 아이 1:1 수업(주중·주말 월 4회) 추가. 한 번만, 없을 때만 넣고 다른 상품은 건드리지 않음.
    if not c.execute("SELECT k FROM settings WHERE k='mig_kids11'").fetchone():
        r = c.execute("SELECT MAX(sort) AS m FROM products WHERE cat='정규수업'").fetchone()
        base = int(r["m"] if r and r["m"] is not None else 70)
        for j, (code, cat, name, price, pp, ptype, ses, desc, bk) in enumerate(p for p in DEFAULT_PRODUCTS if p[0] in ADDED_V42):
            c.execute("INSERT OR IGNORE INTO products(code,cat,name,price,per_person,ptype,sessions,descr,sort,active,bookable,updated) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
                      (code, cat, name, price, pp, ptype, ses, desc, base + j + 1, bk, now()))
        c.execute("INSERT OR IGNORE INTO settings(k,v) VALUES('mig_kids11','1')")
    # 1시간 체험(1:3·1:2·1:1) 판매 중단(2026-10 사장님 요청). 상품 행·기존 신청/결제 기록은 그대로 두고 숨김만. 한 번만.
    if not c.execute("SELECT k FROM settings WHERE k='mig_no_trial_60'").fetchone():
        c.execute("UPDATE products SET active=0, bookable=0, updated=? WHERE code IN (?,?,?)", (now(),) + RETIRED_PRODUCTS)
        c.execute("INSERT OR IGNORE INTO settings(k,v) VALUES('mig_no_trial_60','1')")
    # v3 수강권 결제 -> 장부 이관(한 번만: 연결된 결제 기록이 없는 경우만)
    for p in c.execute("""SELECT p.* FROM packages p WHERE COALESCE(p.paid_amount,0)>0 AND p.pay_status<>'미납'
                          AND NOT EXISTS (SELECT 1 FROM payments y WHERE y.source='package' AND y.ref_id=p.id)""").fetchall():
        c.execute("INSERT INTO payments(date,amount,product,method,source,ref_id,student_id,memo,created) VALUES(?,?,?,?,?,?,?,?,?)",
                  (p["start_date"] or today(), int(p["paid_amount"] or 0), PTYPES.get(p["ptype"], (p["ptype"],))[0] + " 4회",
                   p["pay_method"] or "", "package", p["id"], p["student_id"], "v3 수강권에서 이관", now()))
    c.execute("CREATE INDEX IF NOT EXISTS ix_leads_stage ON leads(stage)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_trials_date ON trials(pref_date)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_pay_date ON payments(date)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_exp_date ON expenses(date)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_classes_date ON classes(date)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_bookings_class ON bookings(class_id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_pkg_student ON packages(student_id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_enr_student ON enrollments(student_id)")
    c.commit(); c.close()

def dates(d0, d1):
    a, b = datetime.date.fromisoformat(d0), datetime.date.fromisoformat(d1)
    while a <= b:
        yield a; a += datetime.timedelta(days=1)

def ensure_range(c, d0, d1):
    """기본 시간표 수업을 필요한 날짜에만 생성(이미 있으면 건너뜀). 커밋은 호출자가."""
    have = {(r["date"], r["time"]) for r in c.execute("SELECT date,time FROM classes WHERE date>=? AND date<=?", (d0, d1)).fetchall()}
    hol = {r["date"] for r in c.execute("SELECT date FROM holidays WHERE date>=? AND date<=?", (d0, d1)).fetchall()}
    for d in dates(d0, d1):
        ds, wd = d.isoformat(), d.weekday()
        for t, title, kind, cap in slots_of(wd):
            if (ds, t) not in have:
                c.execute("INSERT OR IGNORE INTO classes(date,time,title,kind,capacity,cancelled,slot_key) VALUES(?,?,?,?,?,?,?)",
                          (ds, t, title, kind, cap, 1 if ds in hol else 0, f"{wd}|{t}"))

def ensure_classes(date):
    c = db(); ensure_range(c, date, date); c.commit(); c.close()

def kind_label(kind):
    return KINDS.get(kind, kind or "")

def class_key(cl):
    if cl.get("slot_key"): return cl["slot_key"]
    wd = datetime.date.fromisoformat(cl["date"]).weekday()
    k = f"{wd}|{cl['time']}"
    return k if k in SLOT_MAP else None

def _in_window(e, ds):
    return (e["start_date"] or "") <= ds and (not e["end_date"] or e["end_date"] >= ds)

def remaining_map(c, ids=None):
    rows = c.execute("SELECT student_id, SUM(remaining) AS r, COUNT(*) AS n FROM packages GROUP BY student_id").fetchall()
    return {r["student_id"]: (int(r["r"] or 0), int(r["n"])) for r in rows}

def load_range(c, d0, d1, roster=True):
    ensure_range(c, d0, d1)
    hol = {r["date"]: r["reason"] for r in c.execute("SELECT date,reason FROM holidays WHERE date>=? AND date<=?", (d0, d1)).fetchall()}
    cls = [dict(r) for r in c.execute("SELECT * FROM classes WHERE date>=? AND date<=? ORDER BY date,time", (d0, d1)).fetchall()]
    sub = "(SELECT id FROM classes WHERE date>=? AND date<=?)"
    bk, mk, att = {}, {}, {}
    for r in c.execute(f"SELECT * FROM bookings WHERE class_id IN {sub} ORDER BY id", (d0, d1)).fetchall():
        bk.setdefault(r["class_id"], []).append(dict(r))
    for r in c.execute(f"""SELECT m.*, s.name, s.kind AS skind, s.phone, s.guardian, s.grade FROM makeups m
                          JOIN students s ON s.id=m.student_id WHERE m.class_id IN {sub} ORDER BY m.id""", (d0, d1)).fetchall():
        mk.setdefault(r["class_id"], []).append(dict(r))
    for r in c.execute(f"SELECT * FROM attendance WHERE class_id IN {sub}", (d0, d1)).fetchall():
        att[(r["class_id"], r["student_id"])] = r["status"]
    enr = [dict(r) for r in c.execute("""SELECT e.*, s.name, s.kind AS skind, s.phone, s.guardian, s.grade FROM enrollments e
          JOIN students s ON s.id=e.student_id WHERE s.active=1 AND e.start_date<=? AND (e.end_date IS NULL OR e.end_date='' OR e.end_date>=?)
          ORDER BY s.name""", (d1, d0)).fetchall()]
    rem = remaining_map(c) if roster else {}
    out = []
    for cl in cls:
        key = class_key(cl)
        regs = [e for e in enr if key and e["slot_key"] == key and _in_window(e, cl["date"])]
        mks = mk.get(cl["id"], [])
        bks = bk.get(cl["id"], [])
        nb = sum(int(b["count"] or 0) for b in bks if not b["cancelled"])
        booked = nb + len(regs) + len(mks)
        cl.update(booked=booked, remaining=max(0, cl["capacity"] - booked),
                  holiday=hol.get(cl["date"]), regular_count=len(regs) + len(mks), booking_count=nb)
        if roster:
            def ent(x, src):
                r, n = rem.get(x["student_id"], (0, 0))
                return {"student_id": x["student_id"], "name": x["name"], "kind": x["skind"], "phone": x["phone"],
                        "guardian": x["guardian"], "grade": x["grade"], "src": src, "id": x["id"],
                        "status": att.get((cl["id"], x["student_id"])), "left": r, "has_pkg": n > 0, "note": x.get("note")}
            cl["regulars"] = [ent(e, "regular") for e in regs]
            cl["makeups"] = [ent(m, "makeup") for m in mks]
            cl["bookings"] = bks
            done = sum(1 for e in cl["regulars"] + cl["makeups"] if e["status"]) + sum(1 for b in bks if b["attended"] and not b["cancelled"])
            cl["checked"] = done
        out.append(cl)
    return out, hol

def classes_for(date):
    c = db()
    try:
        cls, _ = load_range(c, date, date, roster=False); c.commit()
    finally: c.close()
    pub = ("id", "date", "time", "title", "kind", "capacity", "cancelled", "booked", "remaining", "holiday")
    nowt = datetime.datetime.now(KST).strftime("%H:%M"); t = today()
    out = []
    for cl in cls:
        x = {k: cl.get(k) for k in pub}
        x["past"] = cl["date"] < t or (cl["date"] == t and cl["time"] <= nowt)
        out.append(x)
    return out

def get_class(c, cid):
    r = c.execute("SELECT date FROM classes WHERE id=?", (cid,)).fetchone()
    if not r: raise ValueError("수업을 찾을 수 없어요.")
    cls, _ = load_range(c, r["date"], r["date"])
    return next(x for x in cls if x["id"] == cid)

def pick_package(c, sid, date):
    wk = "we" if datetime.date.fromisoformat(date).weekday() >= 5 else "wd"
    rows = [dict(r) for r in c.execute("SELECT * FROM packages WHERE student_id=? AND remaining>0 ORDER BY start_date, id", (sid,)).fetchall()]
    rows.sort(key=lambda p: (0 if (p["ptype"] or "").startswith(wk) else 1, (p["start_date"] or "") > date))
    return rows[0] if rows else None

def set_attendance(c, cid, sid, status):
    cl = c.execute("SELECT * FROM classes WHERE id=?", (cid,)).fetchone()
    if not cl: raise ValueError("수업을 찾을 수 없어요.")
    prev = c.execute("SELECT * FROM attendance WHERE class_id=? AND student_id=?", (cid, sid)).fetchone()
    if prev:
        if prev["status"] == "출석" and prev["package_id"]:
            c.execute("UPDATE packages SET remaining=remaining+1 WHERE id=?", (prev["package_id"],))
        c.execute("DELETE FROM attendance WHERE id=?", (prev["id"],))
    if status not in ("출석", "결석", ""): raise ValueError("잘못된 출석 상태예요.")
    pid = None
    if status == "출석":
        p = pick_package(c, sid, cl["date"])
        if p:
            pid = p["id"]; c.execute("UPDATE packages SET remaining=remaining-1 WHERE id=?", (pid,))
    if status:
        c.execute("INSERT INTO attendance(class_id,student_id,package_id,status,created) VALUES(?,?,?,?,?)", (cid, sid, pid, status, now()))
    return pid

PHONE_RE = re.compile(r"^0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}$")
GRADE_RE = re.compile(r"^(초|중|고|유치|유아|미취학|\d+\s*(살|세|학년)|성인|대학|직장)")
KIND_WORDS = {"키즈": "kids", "아동": "kids", "어린이": "kids", "kids": "kids", "성인": "adult", "adult": "adult", "어른": "adult"}

def parse_bulk(text):
    import csv, io
    out = []
    for line in text.splitlines():
        if not line.strip(): continue
        if "\t" in line: f = line.split("\t")
        elif "," in line: f = next(csv.reader(io.StringIO(line)))
        else: f = line.split()
        f = [x.strip() for x in f]
        if not f or not f[0] or f[0] in ("이름", "name", "성명"): continue
        name, rest = f[0], [x for x in f[1:]]
        phone = next((x for x in rest if PHONE_RE.match(x)), "")
        if phone: rest.remove(phone)
        kind = ""
        for x in list(rest):
            if x.lower() in KIND_WORDS: kind = KIND_WORDS[x.lower()]; rest.remove(x); break
        guardian = grade = ""
        if rest and not GRADE_RE.match(rest[0]): guardian = rest.pop(0)
        elif rest and len(rest) >= 2 and GRADE_RE.match(rest[1]) and not GRADE_RE.match(rest[0]): guardian = rest.pop(0)
        if rest and (GRADE_RE.match(rest[0]) or len(rest) > 1): grade = rest.pop(0)
        memo = " ".join(x for x in rest if x)
        if not kind: kind = "adult" if re.search(r"성인|대학|직장", grade) else "kids"
        out.append({"name": name[:40], "guardian": guardian[:40], "phone": phone, "grade": grade[:20], "kind": kind, "memo": memo[:300]})
    return out

def student_detail(c, sid):
    s = c.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if not s: raise ValueError("수강생을 찾을 수 없어요.")
    s = dict(s)
    s["packages"] = [dict(r) for r in c.execute("SELECT * FROM packages WHERE student_id=? ORDER BY start_date DESC, id DESC", (sid,)).fetchall()]
    s["enrollments"] = [dict(r, label=SLOT_MAP.get(r["slot_key"], {}).get("label", r["slot_key"])) for r in
                        c.execute("SELECT * FROM enrollments WHERE student_id=? ORDER BY id", (sid,)).fetchall()]
    s["history"] = [dict(r) for r in c.execute("""SELECT a.id, a.status, a.package_id, a.class_id, cl.date, cl.time, cl.title FROM attendance a
                    JOIN classes cl ON cl.id=a.class_id WHERE a.student_id=? ORDER BY cl.date DESC, cl.time DESC""", (sid,)).fetchall()]
    s["makeups"] = [dict(r) for r in c.execute("""SELECT m.id, m.note, m.class_id, cl.date, cl.time, cl.title FROM makeups m JOIN classes cl ON cl.id=m.class_id
                    WHERE m.student_id=? ORDER BY cl.date DESC""", (sid,)).fetchall()]
    s["left"] = sum(int(p["remaining"] or 0) for p in s["packages"])
    return s

def slot_occupancy(c, ds=None):
    ds = ds or today()
    occ = {}
    for r in c.execute("""SELECT e.slot_key FROM enrollments e JOIN students s ON s.id=e.student_id
                          WHERE s.active=1 AND (e.end_date IS NULL OR e.end_date='' OR e.end_date>=?)""", (ds,)).fetchall():
        occ[r["slot_key"]] = occ.get(r["slot_key"], 0) + 1
    return [dict(s, used=occ.get(s["key"], 0)) for s in slot_keys()]

def students_list(c, q=""):
    rows = [dict(r) for r in c.execute("SELECT * FROM students ORDER BY active DESC, name").fetchall()]
    rem = remaining_map(c); t = today()
    enr = {}
    for r in c.execute("SELECT * FROM enrollments WHERE end_date IS NULL OR end_date='' OR end_date>=?", (t,)).fetchall():
        enr.setdefault(r["student_id"], []).append(SLOT_MAP.get(r["slot_key"], {}).get("label", r["slot_key"]))
    unpaid = {r["student_id"] for r in c.execute("SELECT DISTINCT student_id FROM packages WHERE pay_status<>'완납'").fetchall()}
    out = []
    q = (q or "").strip().lower()
    for s in rows:
        if q and q not in " ".join(str(s.get(k) or "") for k in ("name", "guardian", "phone", "grade", "memo")).lower(): continue
        r, n = rem.get(s["id"], (0, 0))
        out.append(dict(s, left=r, has_pkg=n > 0, renew=n > 0 and r <= 1, slots=enr.get(s["id"], []), unpaid=s["id"] in unpaid))
    return out

# ======================= v4: 1인 운영 시스템 =======================
def get_setting(c, k, default=""):
    r = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r["v"] if r and r["v"] is not None else default

def set_setting(c, k, v):
    if c.execute("SELECT k FROM settings WHERE k=?", (k,)).fetchone():
        c.execute("UPDATE settings SET v=? WHERE k=?", (v, k))
    else:
        c.execute("INSERT OR IGNORE INTO settings(k,v) VALUES(?,?)", (k, v))

def products(c, active_only=False):
    rows = [dict(r) for r in c.execute("SELECT * FROM products ORDER BY sort, id").fetchall()]
    return [r for r in rows if r["active"]] if active_only else rows

def ptype_prices(c):
    """정규 수강권 종류별 가격: 상품 관리 값 우선, 없으면 기본값."""
    out = {k: v[1] for k, v in PTYPES.items()}
    for p in products(c, True):
        if p["ptype"] in out and p["price"] is not None: out[p["ptype"]] = int(p["price"])
    return out

def booking_status(b):
    if b["cancelled"]: return "취소"
    if b["balance_paid"]: return "결제 완료"
    if b["attended"]: return "체험 완료(미결제)"
    if b["confirmed"] or b["deposit_paid"]: return "예약 확정"
    return "신청 접수"

def booking_amount_hint(c, b):
    """예약 상품 기준 예상 금액(인당 상품만 인원 곱함). 실제 받은 금액은 관리자가 확인."""
    if not b["product_id"]: return None
    p = c.execute("SELECT * FROM products WHERE id=?", (b["product_id"],)).fetchone()
    if not p or p["price"] is None: return None
    return int(p["price"]) * (int(b["count"] or 1) if p["per_person"] else 1)

def booking_pay(c, bid, amount, method=""):
    """amount>0: 결제 완료 + 장부 기록(upsert). amount==0: 결제 취소 + 장부 기록 삭제. amount<0: 예상 금액 사용."""
    b = c.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    if not b: raise ValueError("예약을 찾을 수 없어요.")
    if amount < 0:
        amount = booking_amount_hint(c, b) or 0
        if not amount: raise ValueError("받은 금액을 입력해 주세요.")
    if amount > 100000000: raise ValueError("금액을 확인해 주세요.")
    ex = c.execute("SELECT * FROM payments WHERE source='booking' AND ref_id=?", (bid,)).fetchone()
    if amount == 0:
        c.execute("UPDATE bookings SET balance_paid=0, paid_amount=0 WHERE id=?", (bid,))
        c.execute("DELETE FROM payments WHERE source='booking' AND ref_id=?", (bid,))
        return
    pname = "체험수업"
    if b["product_id"]:
        p = c.execute("SELECT name FROM products WHERE id=?", (b["product_id"],)).fetchone()
        if p: pname = p["name"]
    cl = c.execute("SELECT date FROM classes WHERE id=?", (b["class_id"],)).fetchone()
    m = method if method in PAY_METHODS else (b["pay_method"] or "")
    c.execute("UPDATE bookings SET balance_paid=1, paid_amount=?, pay_method=? WHERE id=?", (amount, m, bid))
    if ex:
        c.execute("UPDATE payments SET amount=?, method=?, product=? WHERE id=?", (amount, m, pname, ex["id"]))
    else:
        c.execute("INSERT INTO payments(date,amount,product,product_id,method,source,ref_id,lead_id,memo,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (min(today(), cl["date"]) if cl else today(), amount, pname, b["product_id"], m, "booking", bid, b["lead_id"],
                   f"{b['name']} {int(b['count'] or 1)}명", now()))

def sync_package_payment(c, pid):
    """수강권 납부 금액을 장부와 맞춤(수강권 1개당 자동 기록 1개)."""
    p = c.execute("SELECT * FROM packages WHERE id=?", (pid,)).fetchone()
    if not p: return
    amt = int(p["paid_amount"] or 0) if p["pay_status"] != "미납" else 0
    ex = c.execute("SELECT * FROM payments WHERE source='package' AND ref_id=?", (pid,)).fetchone()
    st = c.execute("SELECT name FROM students WHERE id=?", (p["student_id"],)).fetchone()
    label = PTYPES.get(p["ptype"], (p["ptype"],))[0] + f" {p['sessions']}회"
    memo = (st["name"] if st else "") + " 수강권"
    if amt <= 0:
        if ex: c.execute("DELETE FROM payments WHERE id=?", (ex["id"],))
    elif ex:
        c.execute("UPDATE payments SET amount=?, method=?, product=?, student_id=?, memo=? WHERE id=?",
                  (amt, p["pay_method"] or "", label, p["student_id"], memo, ex["id"]))
    else:
        c.execute("INSERT INTO payments(date,amount,product,method,source,ref_id,student_id,memo,created) VALUES(?,?,?,?,?,?,?,?,?)",
                  (today(), amt, label, p["pay_method"] or "", "package", pid, p["student_id"], memo, now()))

def set_stage(c, lid, stage):
    if stage not in STAGES: raise ValueError("단계를 확인해 주세요.")
    l = c.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
    if not l: raise ValueError("문의를 찾을 수 없어요.")
    c.execute("UPDATE leads SET stage=?, updated=? WHERE id=?", (stage, now(), lid))
    col = STAGE_AT.get(stage)
    if col and not l[col]: c.execute(f"UPDATE leads SET {col}=? WHERE id=?", (now(), lid))

def norm_phone(p):
    return re.sub(r"\D", "", p or "")

def find_open_lead(c, phone):
    ph = norm_phone(phone)
    if not ph: return None
    for r in c.execute("SELECT * FROM leads WHERE stage<>'정규등록' ORDER BY id DESC").fetchall():
        if norm_phone(r["phone"]) == ph: return r
    return None

def create_lead(c, name, phone, source, origin, interest="", product_id=None, people=1, message="", preferred="", stage="신규문의", kind="", memo="", next_date=None):
    lid = c.execute("""INSERT INTO leads(name,phone,kind,interest,product_id,people,source,origin,stage,memo,message,preferred,next_date,created,updated)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (name[:40], phone[:20], kind, interest[:80], product_id, people, source if source in SOURCES else "기타", origin,
                     "신규문의", memo[:500], message[:500], preferred[:80], next_date, now(), now())).lastrowid
    if stage != "신규문의": set_stage(c, lid, stage)
    return lid

def leads_list(c, stage="", q=""):
    rows = [dict(r) for r in c.execute("SELECT * FROM leads ORDER BY CASE WHEN next_date IS NULL OR next_date='' THEN 1 ELSE 0 END, next_date, id DESC").fetchall()]
    counts = {st: 0 for st in STAGES}
    for r in rows: counts[r["stage"]] = counts.get(r["stage"], 0) + 1
    q = (q or "").strip().lower()
    out = [r for r in rows if (not stage or r["stage"] == stage) and
           (not q or q in " ".join(str(r.get(k) or "") for k in ("name", "phone", "interest", "memo", "message")).lower())]
    return {"leads": out, "counts": counts, "stages": STAGES, "sources": SOURCES, "today": today()}

def lead_detail(c, lid):
    l = c.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
    if not l: raise ValueError("문의를 찾을 수 없어요.")
    l = dict(l)
    l["bookings"] = [dict(r) for r in c.execute("""SELECT b.*, cl.date, cl.time, cl.title FROM bookings b JOIN classes cl ON cl.id=b.class_id
                      WHERE b.lead_id=? ORDER BY cl.date DESC""", (lid,)).fetchall()]
    l["trials"] = [trial_view(c, r) for r in c.execute("SELECT * FROM trials WHERE lead_id=? ORDER BY id DESC", (lid,)).fetchall()]
    if l["student_id"]:
        st = c.execute("SELECT id, name FROM students WHERE id=?", (l["student_id"],)).fetchone()
        l["student"] = dict(st) if st else None
    return l

def last_attend_map(c):
    return {r["student_id"]: r["d"] for r in c.execute("""SELECT a.student_id, MAX(cl.date) AS d FROM attendance a JOIN classes cl ON cl.id=a.class_id
              WHERE a.status='출석' GROUP BY a.student_id""").fetchall()}

def renewals(c):
    t = today(); valid = _int(get_setting(c, "pkg_valid_days", "0"), 0)
    tpl = get_setting(c, "renew_tpl", DEFAULT_RENEW_TPL)
    prices = ptype_prices(c); last = last_attend_map(c)
    pk = {}
    for p in c.execute("SELECT p.* FROM packages p JOIN students s ON s.id=p.student_id WHERE s.active=1 ORDER BY p.start_date, p.id").fetchall():
        pk.setdefault(p["student_id"], []).append(dict(p))
    out = []
    for s in c.execute("SELECT * FROM students WHERE active=1 ORDER BY name").fetchall():
        ps = pk.get(s["id"])
        if not ps: continue
        left = sum(int(p["remaining"] or 0) for p in ps)
        live = [p for p in ps if int(p["remaining"] or 0) > 0]
        expired = False
        if valid and live:
            expired = all((datetime.date.fromisoformat(p["start_date"]) + datetime.timedelta(days=valid)).isoformat() < t for p in live if p["start_date"])
        if left > 1 and not expired: continue
        lastp = ps[-1]; pt = lastp["ptype"] if lastp["ptype"] in PTYPES else ("wd_adult" if s["kind"] == "adult" else "wd_kids")
        prod = f"{PTYPES[pt][0]} 수업(월 4회)" if pt.endswith("11") else f"정규수업({PTYPES[pt][0]} 주1회·총4회)"; price = prices.get(pt, PTYPES[pt][1])
        msg = (tpl.replace("{이름}", s["name"]).replace("{잔여}", str(left)).replace("{상품}", prod)
                  .replace("{금액}", f"{price:,}원").replace("\\n", "\n"))
        out.append({"id": s["id"], "name": s["name"], "guardian": s["guardian"], "phone": s["phone"], "kind": s["kind"], "left": left,
                    "expired": expired, "last_attend": last.get(s["id"]), "ptype": pt, "product": prod, "price": price,
                    "notified": s["renew_notice"], "message": msg})
    out.sort(key=lambda x: (1 if x["notified"] else 0, x["left"], x["name"]))
    return {"items": out, "template": tpl, "default_template": DEFAULT_RENEW_TPL, "valid_days": valid, "today": t}

def month_bounds(ym):
    d0 = datetime.date.fromisoformat(_date(ym + "-01"))
    d1 = (d0.replace(day=28) + datetime.timedelta(days=4)); d1 = d1 - datetime.timedelta(days=d1.day)
    return d0.isoformat(), d1.isoformat()

def ledger(c, ym):
    d0, d1 = month_bounds(ym)
    pays = [dict(r) for r in c.execute("SELECT * FROM payments WHERE date>=? AND date<=? ORDER BY date DESC, id DESC", (d0, d1)).fetchall()]
    exps = [dict(r) for r in c.execute("SELECT * FROM expenses WHERE date>=? AND date<=? ORDER BY date DESC, id DESC", (d0, d1)).fetchall()]
    byp, bym, bye = {}, {}, {}
    for p in pays:
        byp[p["product"] or "기타"] = byp.get(p["product"] or "기타", 0) + int(p["amount"] or 0)
        bym[p["method"] or "미지정"] = bym.get(p["method"] or "미지정", 0) + int(p["amount"] or 0)
    for e in exps: bye[e["category"] or "기타"] = bye.get(e["category"] or "기타", 0) + int(e["amount"] or 0)
    inc = sum(byp.values()); exp = sum(bye.values())
    hist = []
    y, m = int(ym[:4]), int(ym[5:7])
    for i in range(5, -1, -1):
        yy, mm = y, m - i
        while mm <= 0: yy -= 1; mm += 12
        a, b = month_bounds(f"{yy:04d}-{mm:02d}")
        ii = int(c.execute("SELECT COALESCE(SUM(amount),0) AS n FROM payments WHERE date>=? AND date<=?", (a, b)).fetchone()["n"] or 0)
        ee = int(c.execute("SELECT COALESCE(SUM(amount),0) AS n FROM expenses WHERE date>=? AND date<=?", (a, b)).fetchone()["n"] or 0)
        hist.append({"ym": f"{yy:04d}-{mm:02d}", "income": ii, "expense": ee})
    srt = lambda dct: sorted(({"name": k, "amount": v} for k, v in dct.items()), key=lambda x: -x["amount"])
    return {"ym": ym, "income": inc, "expense": exp, "net": inc - exp, "by_product": srt(byp), "by_method": srt(bym),
            "by_expense": srt(bye), "payments": pays, "expenses": exps, "history": hist, "today": today()}

def week_of(ds):
    d = datetime.date.fromisoformat(ds); d0 = d - datetime.timedelta(days=d.weekday())
    return d0.isoformat(), (d0 + datetime.timedelta(days=6)).isoformat()

def weekly_report(c, start=None):
    d0, d1 = week_of(start or today())
    lo, hi = d0, d1 + "T23:59:59"
    cnt = lambda sql, *a: int(c.execute(sql, a).fetchone()["n"] or 0)
    inq = [dict(r) for r in c.execute("SELECT * FROM leads WHERE created>=? AND created<=?", (lo, hi)).fetchall()]
    src = {}
    for l in inq: src[l["source"] or "기타"] = src.get(l["source"] or "기타", 0) + 1
    trials = cnt("""SELECT COUNT(*) AS n FROM bookings b JOIN classes cl ON cl.id=b.class_id WHERE b.cancelled=0 AND cl.date>=? AND cl.date<=?""", d0, d1)
    trial_done = cnt("""SELECT COUNT(*) AS n FROM bookings b JOIN classes cl ON cl.id=b.class_id WHERE b.cancelled=0 AND b.attended=1 AND cl.date>=? AND cl.date<=?""", d0, d1)
    lead_done = cnt("SELECT COUNT(*) AS n FROM leads WHERE done_at>=? AND done_at<=?", lo, hi)
    trials += cnt("SELECT COUNT(*) AS n FROM trials WHERE cancelled=0 AND COALESCE(conf_date,pref_date)>=? AND COALESCE(conf_date,pref_date)<=?", d0, d1)
    trial_done += cnt("SELECT COUNT(*) AS n FROM trials WHERE cancelled=0 AND attended=1 AND COALESCE(conf_date,pref_date)>=? AND COALESCE(conf_date,pref_date)<=?", d0, d1)
    conv = cnt("SELECT COUNT(*) AS n FROM leads WHERE reg_at>=? AND reg_at<=?", lo, hi)
    new_pk = cnt("SELECT COUNT(*) AS n FROM packages WHERE created>=? AND created<=?", lo, hi)
    new_st = cnt("SELECT COUNT(*) AS n FROM students WHERE created>=? AND created<=?", lo, hi)
    rev = cnt("SELECT COALESCE(SUM(amount),0) AS n FROM payments WHERE date>=? AND date<=?", d0, d1)
    exp = cnt("SELECT COALESCE(SUM(amount),0) AS n FROM expenses WHERE date>=? AND date<=?", d0, d1)
    att = cnt("""SELECT COUNT(*) AS n FROM attendance a JOIN classes cl ON cl.id=a.class_id WHERE a.status='출석' AND cl.date>=? AND cl.date<=?""", d0, d1)
    absn = cnt("""SELECT COUNT(*) AS n FROM attendance a JOIN classes cl ON cl.id=a.class_id WHERE a.status='결석' AND cl.date>=? AND cl.date<=?""", d0, d1)
    t = today(); t7 = (datetime.date.fromisoformat(t) + datetime.timedelta(days=7)).isoformat()
    ups = [dict(r) for r in c.execute("""SELECT id,name,phone,stage,next_date,interest FROM leads WHERE next_date IS NOT NULL AND next_date<>''
            AND next_date<=? AND stage NOT IN ('정규등록') ORDER BY next_date""", (t7,)).fetchall()]
    pd0 = (datetime.date.fromisoformat(d0) - datetime.timedelta(days=7)).isoformat()
    prev_rev = cnt("SELECT COALESCE(SUM(amount),0) AS n FROM payments WHERE date>=? AND date<?", pd0, d0)
    prev_inq = cnt("SELECT COUNT(*) AS n FROM leads WHERE created>=? AND created<?", pd0, d0)
    return {"start": d0, "end": d1, "today": t, "inquiries": len(inq), "prev_inquiries": prev_inq, "by_source": src,
            "trials": trials, "trial_done": max(trial_done, lead_done),
            "conversions": conv, "conv_rate": round(conv * 100 / len(inq)) if inq else None,
            "new_packages": new_pk, "new_students": new_st, "revenue": rev, "prev_revenue": prev_rev, "expense": exp,
            "attendance": att, "absent": absn, "followups": ups,
            "pipeline": {st: cnt("SELECT COUNT(*) AS n FROM leads WHERE stage=?", st) for st in STAGES}}

# ---------- 체험수업 신청 (시간표와 별개, 시간은 통화 후 확정) ----------
def get_deposit(c):
    return max(0, _int(get_setting(c, "deposit_amount", str(DEFAULT_DEPOSIT)), DEFAULT_DEPOSIT))

def time_prefs(wd):
    return TIME_PREFS["wd"] if wd in (1, 2, 3, 4) else TIME_PREFS.get(wd, [])

def trial_total(c, t):
    """예상 총액 = 상품 가격 x 인원(인당 상품). 상품 미정/문의 상품이면 None."""
    if not t["product_id"]: return None
    p = c.execute("SELECT price, per_person FROM products WHERE id=?", (t["product_id"],)).fetchone()
    if not p or p["price"] is None: return None
    return int(p["price"]) * (int(t["count"] or 1) if p["per_person"] else 1)

def trial_status(t):
    if t["cancelled"]: return "취소"
    if t["balance_paid"]: return "결제 완료"
    if t["attended"]: return "체험 완료(잔금 미결제)"
    if t["confirmed"] and t["deposit_paid"]: return "확정 · 예약금 입금"
    if t["confirmed"]: return "확정 · 예약금 대기"
    return "확정 대기"

def trial_view(c, t):
    t = dict(t); total = trial_total(c, t)
    dep = int(t["deposit_amount"] or 0)
    paid_dep = dep if t["deposit_paid"] else 0
    t.update(total=total, status=trial_status(t), date=t["conf_date"] or t["pref_date"],
             balance_due=(max(0, total - paid_dep) if total is not None else None))
    if t["product_id"]:
        p = c.execute("SELECT name FROM products WHERE id=?", (t["product_id"],)).fetchone(); t["product"] = p["name"] if p else ""
    else: t["product"] = ""
    return t

def _trial_pay_row(c, t, source, amount, method, label):
    ex = c.execute("SELECT id FROM payments WHERE source=? AND ref_id=?", (source, t["id"])).fetchone()
    if amount <= 0:
        if ex: c.execute("DELETE FROM payments WHERE id=?", (ex["id"],))
        return
    pname = "체험수업"
    if t["product_id"]:
        p = c.execute("SELECT name FROM products WHERE id=?", (t["product_id"],)).fetchone(); pname = p["name"] if p else pname
    memo = f"{t['name']} {int(t['count'] or 1)}명 · {label}"
    if ex: c.execute("UPDATE payments SET amount=?, method=?, product=?, memo=? WHERE id=?", (amount, method, pname, memo, ex["id"]))
    else:
        c.execute("INSERT INTO payments(date,amount,product,product_id,method,source,ref_id,lead_id,memo,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (today(), amount, pname, t["product_id"], method, source, t["id"], t["lead_id"], memo, now()))

def trial_update(c, d, s):
    tid = _int(d.get("id"))
    t = c.execute("SELECT * FROM trials WHERE id=?", (tid,)).fetchone()
    if not t: raise ValueError("체험 신청을 찾을 수 없어요.")
    if "conf_date" in d or "conf_time" in d:
        cd = _date(d.get("conf_date") or t["pref_date"], "확정 날짜"); ct = s("conf_time", 5)
        if not re.fullmatch(r"\d\d:\d\d", ct): raise ValueError("확정 시간을 입력해 주세요. 예: 15:00")
        wd = datetime.date.fromisoformat(cd).weekday()
        rng = OPEN_RANGE.get(wd)
        if not rng and not d.get("force"): raise ValueError("월요일은 정기휴무예요.")
        if rng and not (rng[0] <= ct < rng[1]) and not d.get("force"):
            raise ValueError(f"운영 시간({rng[0]}~{rng[1]}) 밖이에요. 그래도 저장하려면 '운영시간 밖 허용'을 켜 주세요.")
        c.execute("UPDATE trials SET conf_date=?, conf_time=?, confirmed=1 WHERE id=?", (cd, ct, tid))
    for k in ("confirmed", "attended", "cancelled"):
        if k in d: c.execute(f"UPDATE trials SET {k}=? WHERE id=?", (1 if d[k] and d[k] != "0" else 0, tid))
    if "memo" in d: c.execute("UPDATE trials SET memo=? WHERE id=?", (s("memo", 300), tid))
    if "deposit_amount" in d:
        v = _int(d.get("deposit_amount"), -1)
        if not 0 <= v <= 1000000: raise ValueError("예약금을 확인해 주세요.")
        c.execute("UPDATE trials SET deposit_amount=? WHERE id=?", (v, tid))
    t = c.execute("SELECT * FROM trials WHERE id=?", (tid,)).fetchone()
    if "deposit_paid" in d or "deposit_amount" in d:
        on = d.get("deposit_paid", t["deposit_paid"]); on = 1 if on and on != "0" else 0
        if on and not int(t["deposit_amount"] or 0): raise ValueError("이 예약은 예약금이 0원이에요.")
        c.execute("UPDATE trials SET deposit_paid=? WHERE id=?", (on, tid))
        t = c.execute("SELECT * FROM trials WHERE id=?", (tid,)).fetchone()
        _trial_pay_row(c, t, "trial_deposit", int(t["deposit_amount"] or 0) if on else 0, s("pay_method", 30) if s("pay_method", 30) in PAY_METHODS else "계좌이체", "예약금")
    if "paid" in d:
        on = d["paid"] and d["paid"] != "0"
        amt = _int(d.get("amount"), -1) if on else 0
        if on and amt < 0:
            amt = trial_view(c, t)["balance_due"] or 0
            if not amt: raise ValueError("받은 잔금을 입력해 주세요.")
        if amt > 100000000: raise ValueError("금액을 확인해 주세요.")
        m = s("pay_method", 30) if s("pay_method", 30) in PAY_METHODS else ""
        c.execute("UPDATE trials SET balance_paid=?, paid_amount=?, pay_method=? WHERE id=?", (1 if amt > 0 else 0, amt, m, tid))
        t = c.execute("SELECT * FROM trials WHERE id=?", (tid,)).fetchone()
        _trial_pay_row(c, t, "trial", amt, m, "현장 결제")
    if (d.get("attended") and d["attended"] != "0") and t["lead_id"]:
        l = c.execute("SELECT stage FROM leads WHERE id=?", (t["lead_id"],)).fetchone()
        if l and l["stage"] in ("신규문의", "체험예약"): set_stage(c, t["lead_id"], "체험완료")
    return {"ok": True, "status": trial_status(c.execute("SELECT * FROM trials WHERE id=?", (tid,)).fetchone())}

def trials_list(c, f=""):
    t = today()
    rows = [trial_view(c, r) for r in c.execute("SELECT * FROM trials ORDER BY COALESCE(conf_date, pref_date), COALESCE(conf_time,''), id").fetchall()]
    groups = {"wait": [r for r in rows if not r["cancelled"] and not r["confirmed"]],
              "up": [r for r in rows if not r["cancelled"] and r["confirmed"] and r["date"] >= t and not r["attended"]],
              "past": [r for r in rows if not r["cancelled"] and (r["attended"] or (r["confirmed"] and r["date"] < t))],
              "cancel": [r for r in rows if r["cancelled"]]}
    groups["past"].reverse()
    return {"items": groups.get(f or "wait", []), "counts": {k: len(v) for k, v in groups.items()}, "today": t, "deposit": get_deposit(c)}

def create_trial(c, d):
    name, phone = str(d.get("name", "")).strip()[:40], str(d.get("phone", "")).strip()[:20]
    count = _int(d.get("count", 1), 0)
    if not name or not re.fullmatch(r"[0-9\- ]{9,14}", phone) or not (1 <= count <= 10):
        raise ValueError("이름, 전화번호, 인원(1~10명)을 확인해 주세요.")
    pdte = _date(d.get("date"), "희망 날짜")
    wd = datetime.date.fromisoformat(pdte).weekday()
    if pdte < today(): raise ValueError("지난 날짜는 신청할 수 없어요.")
    if wd == 0: raise ValueError("월요일은 정기휴무예요. 다른 날짜를 골라 주세요.")
    if c.execute("SELECT id FROM holidays WHERE date=?", (pdte,)).fetchone(): raise ValueError("그날은 휴무일이에요. 다른 날짜를 골라 주세요.")
    ptime = str(d.get("time_pref", "")).strip()[:60]
    if not ptime: raise ValueError("희망 시간대를 골라 주세요.")
    prod = c.execute("SELECT * FROM products WHERE id=? AND active=1", (_int(d.get("product_id")),)).fetchone() if d.get("product_id") else None
    src = d.get("source") if d.get("source") in SOURCES else "기타"
    msg = str(d.get("message", ""))[:300]; age = str(d.get("age", ""))[:50]
    dep = get_deposit(c)
    tid = c.execute("""INSERT INTO trials(name,phone,age,count,product_id,pref_date,pref_time,message,source,deposit_amount,created)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (name, phone, age, count, prod["id"] if prod else None, pdte, ptime, msg, src, dep, now())).lastrowid
    pref = f"{pdte} {ptime}"
    kind = "adult" if re.search(r"성인|어른|대학|직장", age) else ("kids" if age.strip() else "")
    ol = find_open_lead(c, phone)
    if ol:
        lid = ol["id"]
        c.execute("UPDATE leads SET trial_id=?, preferred=?, interest=COALESCE(NULLIF(?,''),interest), people=?, updated=? WHERE id=?",
                  (tid, pref, prod["name"] if prod else "", count, now(), lid))
        if ol["stage"] in ("신규문의", "보류"): set_stage(c, lid, "체험예약")
    else:
        lid = create_lead(c, name, phone, src, "홈페이지 체험 신청", prod["name"] if prod else "체험수업", prod["id"] if prod else None,
                          count, msg, pref, "체험예약", kind)
        c.execute("UPDATE leads SET trial_id=? WHERE id=?", (tid, lid))
    c.execute("UPDATE trials SET lead_id=? WHERE id=?", (lid, tid))
    t = trial_view(c, c.execute("SELECT * FROM trials WHERE id=?", (tid,)).fetchone())
    return {"ok": True, "id": tid, "status": t["status"], "product": t["product"] or None, "count": count, "date": pdte, "time_pref": ptime,
            "total": t["total"], "deposit": dep, "onsite": (max(0, t["total"] - dep) if t["total"] is not None else None), "phone": PHONE}

def dashboard(c):
    """할 일 + 오늘 수업."""
    t = today()
    cls, hol = load_range(c, t, t)
    follow = [dict(r) for r in c.execute("""SELECT * FROM leads WHERE next_date IS NOT NULL AND next_date<>'' AND next_date<=?
               AND stage<>'정규등록' ORDER BY next_date, id""", (t,)).fetchall()]
    new_leads = [dict(r) for r in c.execute("""SELECT * FROM leads WHERE stage='신규문의' AND (next_date IS NULL OR next_date='') ORDER BY id DESC""").fetchall()]
    rn = renewals(c)["items"]
    week_ago = (datetime.date.fromisoformat(t) - datetime.timedelta(days=7)).isoformat()
    renew_due = [x for x in rn if not x["notified"] or x["notified"] < week_ago]
    unpaid_p = [dict(r) for r in c.execute("""SELECT p.*, s.name, s.phone, s.guardian FROM packages p JOIN students s ON s.id=p.student_id
              WHERE p.pay_status<>'완납' AND s.active=1 ORDER BY p.start_date""").fetchall()]
    for p in unpaid_p: p["due"] = max(0, int(p["price"] or 0) - (int(p["paid_amount"] or 0) if p["pay_status"] != "미납" else 0))
    unpaid_b = [dict(r) for r in c.execute("""SELECT b.*, cl.date, cl.time, cl.title FROM bookings b JOIN classes cl ON cl.id=b.class_id
              WHERE b.cancelled=0 AND b.balance_paid=0 AND (b.attended=1 OR cl.date<?) ORDER BY cl.date, cl.time""", (t,)).fetchall()]
    for b in unpaid_b: b["hint"] = booking_amount_hint(c, b)
    new_b = [dict(r) for r in c.execute("""SELECT b.*, cl.date, cl.time, cl.title FROM bookings b JOIN classes cl ON cl.id=b.class_id
              WHERE b.cancelled=0 AND COALESCE(b.confirmed,0)=0 AND COALESCE(b.deposit_paid,0)=0 AND b.attended=0 AND cl.date>=? ORDER BY cl.date, cl.time""", (t,)).fetchall()]
    tv = [trial_view(c, r) for r in c.execute("SELECT * FROM trials WHERE cancelled=0 ORDER BY COALESCE(conf_date,pref_date), COALESCE(conf_time,''), id").fetchall()]
    trial_wait = [x for x in tv if not x["confirmed"]]
    deposit_due = [x for x in tv if x["confirmed"] and not x["deposit_paid"] and not x["balance_paid"] and not x["attended"]
                   and int(x["deposit_amount"] or 0) > 0 and x["date"] >= t]
    trial_unpaid = [x for x in tv if not x["balance_paid"] and (x["attended"] or (x["confirmed"] and x["date"] < t))]
    trials_today = [x for x in tv if x["confirmed"] and x["date"] == t]
    nst = c.execute("SELECT COUNT(*) AS n FROM students WHERE active=1").fetchone()["n"]
    wk = weekly_report(c)
    return {"today": t, "dow": DOW_KO[datetime.date.fromisoformat(t).weekday()], "holiday": hol.get(t), "classes": cls,
            "followups": follow, "new_leads": new_leads, "renew_due": renew_due, "unpaid_packages": unpaid_p, "unpaid_bookings": unpaid_b,
            "new_bookings": new_b, "students": int(nst), "trial_wait": trial_wait, "deposit_due": deposit_due,
            "trial_unpaid": trial_unpaid, "trials_today": trials_today,
            "week": {k: wk[k] for k in ("inquiries", "trials", "conversions", "revenue")},
            # v3 호환
            "expiring": [{"id": x["id"], "name": x["name"], "phone": x["phone"], "guardian": x["guardian"], "kind": x["kind"], "left_n": x["left"]} for x in rn]}

def public_info(c):
    ps = [{k: p[k] for k in ("id", "cat", "name", "price", "per_person", "descr", "bookable")} for p in products(c, True)]
    return {"products": ps, "hours": HOURS, "address": ADDRESS, "phone": PHONE, "naver": NAVER, "instagram": INSTA,
            "blog": "https://m.blog.naver.com/" + "skate0", "sources": SOURCES, "deposit": get_deposit(c),
            "time_prefs": {str(k): time_prefs(k) for k in range(7)}, "open_range": {str(k): v for k, v in OPEN_RANGE.items()},
            "holidays": [r["date"] for r in c.execute("SELECT date FROM holidays WHERE date>=? ORDER BY date", (today(),)).fetchall()]}

def admin_action_v4(c, act, d, s):
    if act == "lead_save":
        name = s("name", 40); phone = s("phone", 20)
        if not name and not phone: raise ValueError("이름이나 연락처를 입력해 주세요.")
        if phone and not re.fullmatch(r"[0-9\- ]{9,14}", phone): raise ValueError("연락처 형식을 확인해 주세요. 예: 010-1234-5678")
        src = d.get("source") if d.get("source") in SOURCES else "기타"
        nd = _date(d["next_date"], "다음 연락일") if d.get("next_date") else None
        pid = _int(d.get("product_id")) or None
        interest = s("interest", 80)
        if pid and not interest:
            p = c.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone(); interest = p["name"] if p else ""
        kind = d.get("kind") if d.get("kind") in ("kids", "adult") else ""
        people = max(1, min(100, _int(d.get("people"), 1)))
        if d.get("id"):
            lid = _int(d["id"])
            c.execute("""UPDATE leads SET name=?, phone=?, kind=?, interest=?, product_id=?, people=?, source=?, memo=?, preferred=?, next_date=?, updated=? WHERE id=?""",
                      (name, phone, kind, interest, pid, people, src, s("memo", 500), s("preferred", 80), nd, now(), lid))
            if d.get("stage") in STAGES: set_stage(c, lid, d["stage"])
            return {"ok": True, "id": lid}
        lid = create_lead(c, name or "(이름 없음)", phone, src, "관리자", interest, pid, people, "", s("preferred", 80),
                          d.get("stage") if d.get("stage") in STAGES else "신규문의", kind, s("memo", 500), nd)
        return {"ok": True, "id": lid}
    if act == "lead_stage":
        lid = _int(d.get("id")); set_stage(c, lid, d.get("stage"))
        if "next_date" in d:
            c.execute("UPDATE leads SET next_date=? WHERE id=?", (_date(d["next_date"]) if d.get("next_date") else None, lid))
        return {"ok": True}
    if act == "lead_followup":
        lid = _int(d.get("id"))
        nd = _date(d["next_date"], "다음 연락일") if d.get("next_date") else None
        note = s("note", 200)
        l = c.execute("SELECT memo FROM leads WHERE id=?", (lid,)).fetchone()
        if not l: raise ValueError("문의를 찾을 수 없어요.")
        memo = l["memo"] or ""
        if note: memo = (f"[{today()[5:]}] {note}\n" + memo)[:1500]
        c.execute("UPDATE leads SET next_date=?, memo=?, updated=? WHERE id=?", (nd, memo, now(), lid))
        return {"ok": True}
    if act == "lead_delete":
        lid = _int(d.get("id"))
        c.execute("UPDATE bookings SET lead_id=NULL WHERE lead_id=?", (lid,))
        c.execute("UPDATE trials SET lead_id=NULL WHERE lead_id=?", (lid,))
        c.execute("UPDATE students SET lead_id=NULL WHERE lead_id=?", (lid,))
        c.execute("UPDATE payments SET lead_id=NULL WHERE lead_id=?", (lid,))
        c.execute("DELETE FROM leads WHERE id=?", (lid,))
        return {"ok": True}
    if act == "lead_convert":
        """문의 -> 수강생(+선택: 4회 수강권). 단계는 정규등록으로."""
        lid = _int(d.get("id"))
        l = c.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
        if not l: raise ValueError("문의를 찾을 수 없어요.")
        kind = d.get("kind") if d.get("kind") in ("kids", "adult") else (l["kind"] or "kids")
        sid = l["student_id"]
        if not sid or not c.execute("SELECT id FROM students WHERE id=?", (sid,)).fetchone():
            sid = c.execute("INSERT INTO students(name,guardian,phone,grade,kind,memo,active,created,lead_id) VALUES(?,?,?,?,?,?,1,?,?)",
                            (s("name", 40) or l["name"], s("guardian", 40), l["phone"] or "", s("grade", 20), kind,
                             f"문의 경로: {l['source'] or '-'}", now(), lid)).lastrowid
            c.execute("UPDATE leads SET student_id=? WHERE id=?", (sid, lid))
        pkid = None
        if d.get("ptype") in PTYPES:
            r = admin_action(c, "package_save", {"student_id": sid, "ptype": d["ptype"], "price": d.get("price"),
                             "pay_status": d.get("pay_status"), "pay_method": d.get("pay_method"), "paid_amount": d.get("paid_amount"),
                             "start_date": d.get("start_date") or today()})
            pkid = r["id"]
        set_stage(c, lid, "정규등록")
        c.execute("UPDATE leads SET next_date=NULL WHERE id=?", (lid,))
        return {"ok": True, "student_id": sid, "package_id": pkid}
    if act == "trial_update": return trial_update(c, d, s)
    if act == "trial_add":  # 전화로 받은 체험 신청을 관리자가 직접 입력
        r = create_trial(c, d)
        return r
    if act == "renew_mark":
        sid = _int(d.get("student_id"))
        c.execute("UPDATE students SET renew_notice=? WHERE id=?", (today() if d.get("on", 1) else None, sid))
        return {"ok": True}
    if act == "settings_save":
        if "renew_tpl" in d:
            v = str(d.get("renew_tpl") or "").strip()[:800]
            set_setting(c, "renew_tpl", v or DEFAULT_RENEW_TPL)
        if "deposit_amount" in d:
            v = _int(d.get("deposit_amount"), -1)
            if not 0 <= v <= 1000000: raise ValueError("예약금은 0~1,000,000원이에요. 0이면 예약금 없음.")
            set_setting(c, "deposit_amount", str(v))
        if "pkg_valid_days" in d:
            v = _int(d.get("pkg_valid_days"), 0)
            if not 0 <= v <= 365: raise ValueError("유효기간은 0~365일이에요. 0이면 기간 제한 없음.")
            set_setting(c, "pkg_valid_days", str(v))
        return {"ok": True}
    if act == "payment_save":
        amt = _int(d.get("amount"), 0)
        if not 0 < amt <= 100000000: raise ValueError("금액을 확인해 주세요.")
        pid = _int(d.get("product_id")) or None; pname = s("product", 80)
        if pid and not pname:
            p = c.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone(); pname = p["name"] if p else ""
        m = d.get("method") if d.get("method") in PAY_METHODS else ""
        vals = (_date(d.get("date") or today()), amt, pname or "기타", pid, m, s("memo", 200))
        if d.get("id"):
            ex = c.execute("SELECT source FROM payments WHERE id=?", (_int(d["id"]),)).fetchone()
            if not ex: raise ValueError("기록을 찾을 수 없어요.")
            if ex["source"] != "manual": raise ValueError("자동 기록은 수강권·예약 화면에서 고쳐 주세요.")
            c.execute("UPDATE payments SET date=?, amount=?, product=?, product_id=?, method=?, memo=? WHERE id=?", vals + (_int(d["id"]),))
            return {"ok": True}
        c.execute("INSERT INTO payments(date,amount,product,product_id,method,memo,source,created) VALUES(?,?,?,?,?,?,'manual',?)", vals + (now(),))
        return {"ok": True}
    if act == "payment_delete":
        ex = c.execute("SELECT source FROM payments WHERE id=?", (_int(d.get("id")),)).fetchone()
        if ex and ex["source"] != "manual": raise ValueError("자동 기록은 수강권·예약 화면에서 고쳐 주세요.")
        c.execute("DELETE FROM payments WHERE id=? AND source='manual'", (_int(d.get("id")),))
        return {"ok": True}
    if act == "expense_save":
        amt = _int(d.get("amount"), 0)
        if not 0 < amt <= 100000000: raise ValueError("금액을 확인해 주세요.")
        cat = d.get("category") if d.get("category") in EXPENSE_CATS else "기타"
        vals = (_date(d.get("date") or today()), cat, amt, s("memo", 200))
        if d.get("id"):
            c.execute("UPDATE expenses SET date=?, category=?, amount=?, memo=? WHERE id=?", vals + (_int(d["id"]),))
        else:
            c.execute("INSERT INTO expenses(date,category,amount,memo,created) VALUES(?,?,?,?,?)", vals + (now(),))
        return {"ok": True}
    if act == "expense_delete":
        c.execute("DELETE FROM expenses WHERE id=?", (_int(d.get("id")),))
        return {"ok": True}
    if act == "product_save":
        name = s("name", 60)
        if not name: raise ValueError("상품 이름을 입력해 주세요.")
        cat = d.get("cat") if d.get("cat") in PRODUCT_CATS else "기타"
        price = None if str(d.get("price") or "").strip() == "" else _int(d.get("price"), -1)
        if price is not None and not 0 <= price <= 100000000: raise ValueError("가격을 확인해 주세요. 문의 상품은 비워 두세요.")
        ptype = d.get("ptype") if d.get("ptype") in PTYPES else ""
        vals = (cat, name, price, 1 if d.get("per_person") else 0, ptype, max(1, _int(d.get("sessions"), 1)), s("descr", 200),
                _int(d.get("sort"), 999), 0 if d.get("active") in (0, "0", False) else 1, 1 if d.get("bookable") else 0, now())
        if d.get("id"):
            c.execute("UPDATE products SET cat=?, name=?, price=?, per_person=?, ptype=?, sessions=?, descr=?, sort=?, active=?, bookable=?, updated=? WHERE id=?",
                      vals + (_int(d["id"]),))
            return {"ok": True, "id": _int(d["id"])}
        code = "c" + datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
        pid = c.execute("INSERT INTO products(cat,name,price,per_person,ptype,sessions,descr,sort,active,bookable,updated,code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        vals + (code,)).lastrowid
        return {"ok": True, "id": pid}
    return None

def _int(v, default=0):
    try: return int(v)
    except (TypeError, ValueError): return default

def _date(v, field="날짜"):
    try: return datetime.date.fromisoformat(str(v)).isoformat()
    except Exception: raise ValueError(f"{field}를 확인해 주세요.")

class NotFound(Exception): pass

def admin_action(c, act, d):
    """관리자 POST 액션. 반환값은 JSON으로 응답. ValueError -> 400."""
    s = lambda k, n=200: str(d.get(k) or "").strip()[:n]
    if act == "login": return {"ok": True}
    if act == "class_update":
        cid = _int(d.get("id"))
        if "capacity" in d:
            cap = _int(d["capacity"], -1)
            if not 0 <= cap <= 50: raise ValueError("정원은 0~50명이에요.")
            c.execute("UPDATE classes SET capacity=? WHERE id=?", (cap, cid))
        if "cancelled" in d: c.execute("UPDATE classes SET cancelled=? WHERE id=?", (1 if d["cancelled"] else 0, cid))
        if "note" in d: c.execute("UPDATE classes SET note=? WHERE id=?", (s("note", 300), cid))
        if "title" in d and s("title"): c.execute("UPDATE classes SET title=? WHERE id=?", (s("title", 40), cid))
        return {"ok": True}
    if act == "class_add":
        ds = _date(d.get("date")); tm = s("time", 5)
        if not re.fullmatch(r"\d\d:\d\d", tm): raise ValueError("시간을 확인해 주세요.")
        kind = d.get("kind") if d.get("kind") in KINDS else "adult"
        ex = c.execute("SELECT * FROM classes WHERE date=? AND time=?", (ds, tm)).fetchone()
        if ex and not ex["cancelled"]: raise ValueError("그 시간에 이미 수업이 있어요.")
        cap = _int(d.get("capacity"), 5)
        if ex:
            c.execute("UPDATE classes SET title=?, kind=?, capacity=?, cancelled=0, note=?, moved_to=NULL WHERE id=?",
                      (s("title", 40) or "특별 수업", kind, cap, s("note", 300), ex["id"]))
            return {"ok": True, "id": ex["id"]}
        cur = c.execute("INSERT INTO classes(date,time,title,kind,capacity,note) VALUES(?,?,?,?,?,?)",
                        (ds, tm, s("title", 40) or "특별 수업", kind, cap, s("note", 300)))
        return {"ok": True, "id": cur.lastrowid}
    if act == "class_move":
        cid = _int(d.get("id")); ds = _date(d.get("date")); tm = s("time", 5)
        if not re.fullmatch(r"\d\d:\d\d", tm): raise ValueError("시간을 확인해 주세요.")
        cl = c.execute("SELECT * FROM classes WHERE id=?", (cid,)).fetchone()
        if not cl: raise ValueError("수업을 찾을 수 없어요.")
        if (cl["date"], cl["time"]) == (ds, tm): raise ValueError("같은 시간으로는 옮길 수 없어요.")
        ensure_range(c, ds, ds)
        ex = c.execute("SELECT * FROM classes WHERE date=? AND time=?", (ds, tm)).fetchone()
        if ex and not ex["cancelled"]: raise ValueError("옮길 시간에 이미 다른 수업이 있어요. 먼저 그 수업을 휴강해 주세요.")
        key = class_key(dict(cl))
        note = f"{cl['date']} {cl['time']}에서 이동"
        if ex:
            if c.execute("SELECT COUNT(*) AS n FROM bookings WHERE class_id=? AND cancelled=0", (ex["id"],)).fetchone()["n"]:
                raise ValueError("옮길 시간에 취소된 수업의 예약이 남아 있어요.")
            c.execute("UPDATE classes SET title=?, kind=?, capacity=?, cancelled=0, slot_key=?, moved_from=?, note=? WHERE id=?",
                      (cl["title"], cl["kind"], cl["capacity"], key, cid, note, ex["id"]))
            nid = ex["id"]
        else:
            nid = c.execute("INSERT INTO classes(date,time,title,kind,capacity,slot_key,moved_from,note) VALUES(?,?,?,?,?,?,?,?)",
                            (ds, tm, cl["title"], cl["kind"], cl["capacity"], key, cid, note)).lastrowid
        c.execute("UPDATE bookings SET class_id=? WHERE class_id=?", (nid, cid))
        c.execute("UPDATE makeups SET class_id=? WHERE class_id=?", (nid, cid))
        c.execute("UPDATE classes SET cancelled=1, moved_to=?, note=?, slot_key=? WHERE id=?", (nid, f"{ds} {tm}(으)로 이동", key, cid))
        return {"ok": True, "id": nid}
    if act == "holiday_add":
        ds = _date(d.get("date"))
        c.execute("INSERT OR IGNORE INTO holidays(date,reason) VALUES(?,?)", (ds, s("reason", 100) or "휴무"))
        c.execute("UPDATE holidays SET reason=? WHERE date=?", (s("reason", 100) or "휴무", ds))
        ensure_range(c, ds, ds)
        c.execute("UPDATE classes SET cancelled=1 WHERE date=?", (ds,))
        return {"ok": True}
    if act == "holiday_del":
        ds = _date(d.get("date"))
        c.execute("DELETE FROM holidays WHERE date=?", (ds,))
        c.execute("UPDATE classes SET cancelled=0 WHERE date=? AND moved_to IS NULL", (ds,))
        return {"ok": True}
    if act == "booking_update":
        bid = _int(d.get("id"))
        b = c.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
        if not b: raise ValueError("예약을 찾을 수 없어요.")
        for k in ("confirmed", "attended", "cancelled"):
            if k in d: c.execute(f"UPDATE bookings SET {k}=? WHERE id=?", (1 if d[k] and d[k] != "0" else 0, bid))
        if "deposit_paid" in d:  # v3 호환: 예약금 확인 = 예약 확정
            c.execute("UPDATE bookings SET confirmed=? WHERE id=?", (1 if d["deposit_paid"] and d["deposit_paid"] != "0" else 0, bid))
        if "memo" in d: c.execute("UPDATE bookings SET memo=? WHERE id=?", (s("memo", 300), bid))
        if "paid" in d or "balance_paid" in d:
            on = d.get("paid", d.get("balance_paid"))
            booking_pay(c, bid, _int(d.get("amount"), -1) if on and on != "0" else 0, s("pay_method", 30))
        b = c.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
        if d.get("attended") and d["attended"] != "0" and b["lead_id"]:
            l = c.execute("SELECT * FROM leads WHERE id=?", (b["lead_id"],)).fetchone()
            if l and l["stage"] in ("신규문의", "체험예약"): set_stage(c, l["id"], "체험완료")
        st = booking_status(b)
        c.execute("UPDATE bookings SET status=? WHERE id=?", (st, bid))
        return {"ok": True, "status": st}
    if act == "student_save":
        name = s("name", 40)
        if not name: raise ValueError("이름을 입력해 주세요.")
        phone = s("phone", 20)
        if phone and not re.fullmatch(r"[0-9\- ]{9,14}", phone): raise ValueError("연락처 형식을 확인해 주세요. 예: 010-1234-5678")
        kind = "adult" if d.get("kind") == "adult" else "kids"
        vals = (name, s("guardian", 40), phone, s("grade", 20), kind, s("memo", 500), 0 if d.get("active") in (0, "0", False) else 1)
        if d.get("id"):
            c.execute("UPDATE students SET name=?, guardian=?, phone=?, grade=?, kind=?, memo=?, active=? WHERE id=?", vals + (_int(d["id"]),))
            return {"ok": True, "id": _int(d["id"])}
        cur = c.execute("INSERT INTO students(name,guardian,phone,grade,kind,memo,active,created) VALUES(?,?,?,?,?,?,?,?)", vals + (now(),))
        return {"ok": True, "id": cur.lastrowid}
    if act == "student_delete":
        sid = _int(d.get("id"))
        for t in ("attendance", "makeups", "enrollments", "packages"):
            c.execute(f"DELETE FROM {t} WHERE student_id=?", (sid,))
        # 장부 기록은 매출 이력이라 남기고 연결만 끊음
        c.execute("UPDATE payments SET student_id=NULL WHERE student_id=?", (sid,))
        c.execute("UPDATE leads SET student_id=NULL WHERE student_id=?", (sid,))
        c.execute("DELETE FROM students WHERE id=?", (sid,))
        return {"ok": True}
    if act == "students_bulk":
        rows = parse_bulk(str(d.get("text") or "")[:200000])
        if not rows: raise ValueError("등록할 수강생이 없어요. 한 줄에 한 명씩 입력해 주세요.")
        exist = {(r["name"], r["phone"] or "") for r in c.execute("SELECT name, phone FROM students").fetchall()}
        added, skipped = [], []
        for r in rows[:500]:
            if (r["name"], r["phone"]) in exist: skipped.append(r["name"]); continue
            c.execute("INSERT INTO students(name,guardian,phone,grade,kind,memo,active,created) VALUES(?,?,?,?,?,?,1,?)",
                      (r["name"], r["guardian"], r["phone"], r["grade"], r["kind"], r["memo"], now()))
            exist.add((r["name"], r["phone"])); added.append(r["name"])
        return {"ok": True, "added": len(added), "skipped": skipped, "rows": rows[:500]}
    if act == "package_save":
        ptype = d.get("ptype")
        if ptype not in PTYPES: raise ValueError("수강권 종류를 선택해 주세요.")
        sid = _int(d.get("student_id"))
        if not c.execute("SELECT id FROM students WHERE id=?", (sid,)).fetchone(): raise ValueError("수강생을 찾을 수 없어요.")
        sessions = _int(d.get("sessions"), 4); rem = _int(d.get("remaining"), sessions)
        if not (1 <= sessions <= 100 and 0 <= rem <= sessions): raise ValueError("횟수를 확인해 주세요.")
        price = _int(d.get("price"), PTYPES[ptype][1])
        pst = d.get("pay_status") if d.get("pay_status") in PAY_STATUS else "미납"
        pm = d.get("pay_method") if d.get("pay_method") in PAY_METHODS else ""
        paid = _int(d.get("paid_amount"), 0)
        if pst == "완납" and not paid: paid = price
        vals = (sid, ptype, sessions, rem, price, _date(d.get("start_date") or today(), "시작일"), pst, pm, paid, s("memo", 300))
        if d.get("id"):
            pid = _int(d["id"])
            c.execute("UPDATE packages SET student_id=?, ptype=?, sessions=?, remaining=?, price=?, start_date=?, pay_status=?, pay_method=?, paid_amount=?, memo=? WHERE id=?",
                      vals + (pid,))
        else:
            pid = c.execute("INSERT INTO packages(student_id,ptype,sessions,remaining,price,start_date,pay_status,pay_method,paid_amount,memo,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            vals + (now(),)).lastrowid
            st = c.execute("SELECT lead_id FROM students WHERE id=?", (sid,)).fetchone()
            if st and st["lead_id"]:
                l = c.execute("SELECT stage FROM leads WHERE id=?", (st["lead_id"],)).fetchone()
                if l and l["stage"] != "정규등록": set_stage(c, st["lead_id"], "정규등록")
            c.execute("UPDATE students SET renew_notice=NULL WHERE id=?", (sid,))
        sync_package_payment(c, pid)
        return {"ok": True, "id": pid}
    if act == "package_delete":
        pid = _int(d.get("id"))
        c.execute("UPDATE attendance SET package_id=NULL WHERE package_id=?", (pid,))
        c.execute("DELETE FROM payments WHERE source='package' AND ref_id=?", (pid,))
        c.execute("DELETE FROM packages WHERE id=?", (pid,))
        return {"ok": True}
    if act == "enroll_add":
        sid = _int(d.get("student_id")); key = d.get("slot_key")
        if key not in SLOT_MAP: raise ValueError("반을 선택해 주세요.")
        st = c.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
        if not st: raise ValueError("수강생을 찾을 수 없어요.")
        sd = _date(d.get("start_date") or today(), "시작일")
        if c.execute("SELECT id FROM enrollments WHERE student_id=? AND slot_key=? AND (end_date IS NULL OR end_date='' OR end_date>=?)", (sid, key, sd)).fetchone():
            raise ValueError("이미 이 반에 배정되어 있어요.")
        used = next(x["used"] for x in slot_occupancy(c, sd) if x["key"] == key)
        if used >= SLOT_MAP[key]["capacity"] and not d.get("force"):
            raise ValueError(f"{SLOT_MAP[key]['label']} 고정 인원이 정원({SLOT_MAP[key]['capacity']}명)에 찼어요.")
        cur = c.execute("INSERT INTO enrollments(student_id,slot_key,start_date,end_date,created) VALUES(?,?,?,NULL,?)", (sid, key, sd, now()))
        return {"ok": True, "id": cur.lastrowid}
    if act == "enroll_end":
        eid = _int(d.get("id"))
        e = c.execute("SELECT * FROM enrollments WHERE id=?", (eid,)).fetchone()
        if not e: raise ValueError("배정 정보를 찾을 수 없어요.")
        end = (datetime.date.fromisoformat(_date(d.get("date") or today())) - datetime.timedelta(days=1)).isoformat()
        if end < (e["start_date"] or ""):
            c.execute("DELETE FROM enrollments WHERE id=?", (eid,))
        else:
            c.execute("UPDATE enrollments SET end_date=? WHERE id=?", (end, eid))
        return {"ok": True}
    if act == "attend":
        pid = set_attendance(c, _int(d.get("class_id")), _int(d.get("student_id")), str(d.get("status") or ""))
        return {"ok": True, "package_id": pid}
    if act == "makeup_add":
        cid, sid = _int(d.get("class_id")), _int(d.get("student_id"))
        cl = get_class(c, cid)
        if cl["cancelled"]: raise ValueError("휴강된 수업에는 보강을 넣을 수 없어요.")
        if any(x["student_id"] == sid for x in cl["regulars"] + cl["makeups"]): raise ValueError("이미 이 수업 명단에 있어요.")
        if cl["remaining"] <= 0 and not d.get("force"): raise ValueError("정원이 찼어요.")
        c.execute("INSERT INTO makeups(student_id,class_id,note,created) VALUES(?,?,?,?)", (sid, cid, s("note", 200) or "보강", now()))
        return {"ok": True}
    if act == "makeup_delete":
        m = c.execute("SELECT * FROM makeups WHERE id=?", (_int(d.get("id")),)).fetchone()
        if m:
            set_attendance(c, m["class_id"], m["student_id"], "")
            c.execute("DELETE FROM makeups WHERE id=?", (m["id"],))
        return {"ok": True}
    r = admin_action_v4(c, act, d, s)
    if r is not None: return r
    raise NotFound()

# ---------- FAQ 자동응답 ----------
PRICE_TXT = ("[체험수업] 인당 가격 · 강습료·입장료·장비 대여 포함\n• 1:3 40분 인당 4만원\n• 1:2 40분 인당 5만원\n"
             "• 1:1 30분 6만원\n[정규수업] 주 1회 · 총 4회\n• 주중 키즈 20만원 · 주중 성인 30만원\n• 주말 키즈 25만원 · 주말 성인 35만원\n"
             "[아이 1:1 수업] 월 4회\n• 주중 25만원 · 주말 30만원\n"
             "겨울철 스노보드 강습·단체 수업은 문의해 주세요.")
HOURS_TXT = "운영 시간: 화~금 13:00~21:00, 토 10:00~18:00, 일 10:00~16:00 (매주 월요일 정기휴무)"
FAQ = [
  (r"시간|스케줄|일정|언제|요일|몇시|운영|영업|휴무", HOURS_TXT + "\n체험수업 시간은 문의 후 확정해요. 앱의 '예약'에서 희망 날짜와 시간대를 남겨 주시면 연락드려요."),
  (r"가격|비용|얼마|요금|수강료|체험|정규", "수강료 안내예요.\n" + PRICE_TXT),
  (r"예약금|선금|보증금|입금|결제", "체험수업은 예약금 1만원이 있어요(총액에서 빠져요). 예약금 안내는 예약이 확정될 때 드려요. 나머지는 현장에서 결제해요. 이 앱에서는 결제를 받지 않아요."),
  (r"위치|주소|어디|오시는|찾아", "경기도 이천시 신둔면 원적로512번길 103 (지번 도봉리 621-3) 3동이에요.\n네이버 지도: " + NAVER),
  (r"주차", "무료 주차 가능해요."),
  (r"전화|연락|번호|문의", "전화 문의: " + PHONE),
  (r"스노보드|보드강습|리조트|지산|출장", "겨울철 스노보드 강습은 지산리조트에서 진행하고, 원하시는 리조트로 출장도 가능해요. 일정과 수강료는 " + PHONE + "로 문의해 주세요."),
  (r"단체|학교|기관|생일|파티", "단체 스케이트보드 수업도 가능해요. 인원과 날짜를 알려 주시면 안내해 드려요. 앱의 '문의'를 남기거나 " + PHONE + "로 연락 주세요."),
  (r"나이|몇 ?살|유아|아이|어린이|키즈|성인|어른", "나이 제한은 없어요. 아이부터 어른까지 수준에 맞춰 알려 드려요."),
  (r"장비|보드|헬멧|보호대|준비물", "장비는 체험비와 수업료에 포함돼 있어요. 빈손으로 오셔도 돼요. 마음에 드시면 구매도 할 수 있어요."),
  (r"비|날씨|실내", "실내 파크라 날씨 걱정 없이 수업해요."),
  (r"인스타|instagram|sns", "인스타그램 @onestarskate 에서 수업 영상을 볼 수 있어요: https://instagram.com/onestarskate"),
  (r"예약|신청|등록", "체험 신청은 앱의 '예약'에서 희망 날짜와 시간대를 남기면 돼요. 시간은 통화로 확정해요. 네이버 예약도 가능해요: " + NAVER),
  (r"안녕|하이|hello", "안녕하세요! 원스타스케이트예요. 수업, 가격, 위치, 예약에 대해 물어보세요."),
]
FACTS = f"""원스타스케이트(파크) 정보(이것 외의 정보는 절대 지어내지 말 것):
- 실내 스케이트보드 파크. 개인·그룹·단체·성인 1:1·맞춤 수업. 나이 제한 없음. 장비는 체험비·수업료에 포함, 구매도 가능.
- {HOURS_TXT}. 수업 시간은 문의/예약으로 조율.
- 수강료: {PRICE_TXT.replace(chr(10), ' ')}
- 체험수업은 유료(인당 가격). 절대 무료라고 말하지 말 것. 예약금 1만원(총액에서 차감, 확정 시 안내, 나머지 현장 결제). 계좌번호는 말하지 말 것.
- 체험수업 시간은 문의 후 확정.
- 겨울철 스노보드 강습: 지산리조트에서 진행, 원하는 리조트로 출장 가능. 수강료·일정은 문의.
- 주소: {ADDRESS} 3동, 무료 주차
- 전화: {PHONE}, 인스타그램 @onestarskate, 네이버 예약: {NAVER}
모르는 내용은 "{PHONE}로 전화 주세요"라고 답하라. 예약을 원하면 앱의 '예약' 메뉴를 안내하라. 한국어로 짧게 답하라."""

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


# ---------- Naver blog RSS ----------
BLOG_ID = "skate0"
_BLOG_CACHE = {"t": 0, "data": None}
def blog_posts():
    import time, xml.etree.ElementTree as ET, html as _h
    if _BLOG_CACHE["data"] and time.time() - _BLOG_CACHE["t"] < 600: return _BLOG_CACHE["data"]
    req = urllib.request.Request("https://rss.blog.naver.com/%s.xml" % BLOG_ID, headers={"User-Agent": "Mozilla/5.0 (OneStarApp)"})
    raw = urllib.request.urlopen(req, timeout=8).read()
    root = ET.fromstring(raw); posts = []; cats = []
    for it in root.iter("item"):
        t = lambda k: (it.findtext(k) or "").strip()
        link = t("link"); desc = t("description")
        m = re.search(r'<img[^>]+src=["\']([^"\']+)', desc)
        img = _h.unescape(m.group(1)) if m else ""
        if img.startswith("http://"): img = "https://" + img[7:]
        mm = re.search(r"blog\.naver\.com/([^/?]+)/(\d+)", link) or re.search(r"logNo=(\d+)", link)
        no = mm.groups()[-1] if mm else ""
        mlink = "https://m.blog.naver.com/%s/%s" % (BLOG_ID, no) if no else link
        text = re.sub(r"\s+", " ", _h.unescape(re.sub(r"https?://\S+", " ", re.sub(r"<[^>]+>", " ", desc)))).strip()[:90]
        cat = re.sub(r"\s+", " ", t("category").replace("\xa0", " ")).strip()
        if cat and cat not in cats: cats.append(cat)
        posts.append({"title": _h.unescape(t("title")), "link": mlink, "pubDate": t("pubDate"), "category": cat, "image": img, "summary": text})
    data = {"blog": "https://m.blog.naver.com/%s" % BLOG_ID, "categories": cats, "posts": posts}
    _BLOG_CACHE.update(t=time.time(), data=data); return data

# ---------- Naver blog: full category listing ----------
BLOG_CATS = [(41,"스케이트보드 강좌"),(40,"스케이트보드 이벤트 영상"),(42,"파크제작기"),(43,"로드투 LA28 스케이트보드"),
             (44,"강준이"),(45,"스케이트보드와 건강"),(46,"스케이트보드 역사"),(47,"스케이트보드 브랜드"),
             (30,"원스타스케이트"),(29,"체험학습"),(31,"스케이트보드샵")]
_CAT_CACHE = {}
def _cat_page(cno, page):
    from urllib.parse import unquote_plus
    url = "https://blog.naver.com/PostTitleListAsync.naver?blogId=%s&categoryNo=%d&currentPage=%d&countPerPage=30" % (BLOG_ID, cno, page)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (OneStarApp)", "Referer": "https://blog.naver.com/%s" % BLOG_ID})
    raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace")
    d = json.loads(raw.replace("\\'", "'"))  # Naver emits invalid JSON escape \'
    if d.get("resultCode") != "S": raise ValueError("naver resultCode %s" % d.get("resultCode"))
    import html as _h
    out = [{"logNo": it.get("logNo", ""), "title": _h.unescape(unquote_plus(it.get("title", ""))), "date": it.get("addDate", "")} for it in d.get("postList") or []]
    return out, int(d.get("totalCount") or 0)
def blog_category(cno):
    import time
    from concurrent.futures import ThreadPoolExecutor
    c = _CAT_CACHE.get(cno)
    if c and time.time() - c["t"] < 600: return c["data"]
    name = dict(BLOG_CATS).get(cno)
    if not name: raise KeyError(cno)
    first, total = _cat_page(cno, 1); items = list(first)
    pages = min((total + 29) // 30, 40)
    if pages > 1:
        with ThreadPoolExecutor(8) as ex:
            for r, _ in ex.map(lambda pg: _cat_page(cno, pg), range(2, pages + 1)): items += r
    thumbs = {}
    try:
        for p in blog_posts()["posts"]:
            n = p["link"].rsplit("/", 1)[-1]
            if p.get("image"): thumbs[n] = p["image"]
    except Exception: pass
    seen = set(); posts = []
    for it in items:
        if not it["logNo"] or it["logNo"] in seen: continue
        seen.add(it["logNo"])
        posts.append({"title": it["title"], "link": "https://m.blog.naver.com/%s/%s" % (BLOG_ID, it["logNo"]),
                      "date": it["date"], "category": name, "image": thumbs.get(it["logNo"], "")})
    data = {"categoryNo": cno, "category": name, "total": total, "posts": posts}
    _CAT_CACHE[cno] = {"t": time.time(), "data": data}; return data

# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", len(b)); self.end_headers(); self.wfile.write(b)
    def body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try: return json.loads(self.rfile.read(n) or b"{}")
        except ValueError: return {}
    def admin_ok(self):
        import hmac
        return bool(ADMIN_PW) and hmac.compare_digest(self.headers.get("X-Admin-Password", "").encode(), ADMIN_PW.encode())
    def admin_deny(self):
        if not ADMIN_PW:
            return self.send({"error": "관리자 기능이 비활성화되어 있어요. 서버에 ADMIN_PASSWORD 환경변수를 설정해 주세요."}, 503)
        return self.send({"error": "비밀번호가 맞지 않아요."}, 401)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query); g = lambda k, dflt="": q.get(k, [dflt])[0]
        try:
            if u.path == "/api/blog":
                cn = g("categoryNo")
                if cn and not (cn.isdigit() and int(cn) in dict(BLOG_CATS)): return self.send({"error": "없는 카테고리예요."}, 404)
                try:
                    if cn: data = blog_category(int(cn))
                    else: data = dict(blog_posts(), categoryList=[{"no": n, "name": nm} for n, nm in BLOG_CATS])
                except Exception as e: return self.send({"error": "블로그를 불러오지 못했어요.", "detail": type(e).__name__}, 502)
                b = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "public, max-age=300, s-maxage=600, stale-while-revalidate=3600")
                self.send_header("Content-Length", len(b)); self.end_headers(); self.wfile.write(b); return
            if u.path == "/api/classes":
                return self.send(classes_for(_date(g("date", today()))))
            if u.path == "/api/info":
                c = db()
                try: out = public_info(c)
                finally: c.close()
                return self.send(out)
            if u.path.startswith("/api/admin/"):
                if not self.admin_ok(): return self.admin_deny()
                act = u.path.split("/")[-1]; c = db()
                try:
                    if act in ("week", "month"):
                        if act == "week":
                            d0 = datetime.date.fromisoformat(_date(g("start", today()))); d1 = d0 + datetime.timedelta(days=6)
                        else:
                            ym = g("ym", today()[:7]); d0 = datetime.date.fromisoformat(_date(ym + "-01"))
                            d1 = (d0.replace(day=28) + datetime.timedelta(days=4)); d1 = d1 - datetime.timedelta(days=d1.day)
                        cls, hol = load_range(c, d0.isoformat(), d1.isoformat(), roster=(act == "week" or g("roster") == "1"))
                        days = []
                        for d in dates(d0.isoformat(), d1.isoformat()):
                            ds = d.isoformat()
                            days.append({"date": ds, "dow": DOW_KO[d.weekday()], "holiday": hol.get(ds), "classes": [x for x in cls if x["date"] == ds]})
                        out = {"start": d0.isoformat(), "end": d1.isoformat(), "today": today(), "days": days}
                    elif act == "dashboard": out = dashboard(c)
                    elif act == "class": out = get_class(c, _int(g("id")))
                    elif act == "students": out = students_list(c, g("q"))
                    elif act == "student": out = student_detail(c, _int(g("id")))
                    elif act == "meta":
                        pp = ptype_prices(c)
                        out = {"slots": slot_occupancy(c), "ptypes": [{"key": k, "label": v[0], "price": pp[k]} for k, v in PTYPES.items()],
                               "pay_status": PAY_STATUS, "pay_methods": PAY_METHODS, "today": today(), "stages": STAGES, "sources": SOURCES,
                               "expense_cats": EXPENSE_CATS, "product_cats": PRODUCT_CATS, "kinds": KINDS,
                               "products": products(c, True)}
                    elif act == "leads": out = leads_list(c, g("stage"), g("q"))
                    elif act == "lead": out = lead_detail(c, _int(g("id")))
                    elif act == "renewals": out = renewals(c)
                    elif act == "trials": out = trials_list(c, g("f"))
                    elif act == "trial":
                        r = c.execute("SELECT * FROM trials WHERE id=?", (_int(g("id")),)).fetchone()
                        if not r: raise ValueError("체험 신청을 찾을 수 없어요.")
                        out = trial_view(c, r)
                    elif act == "settings": out = {"deposit_amount": get_deposit(c), "pkg_valid_days": _int(get_setting(c, "pkg_valid_days", "0"), 0)}
                    elif act == "ledger": out = ledger(c, g("ym", today()[:7]))
                    elif act == "report": out = weekly_report(c, _date(g("start", today())))
                    elif act == "products": out = {"products": products(c), "cats": PRODUCT_CATS, "ptypes": [{"key": k, "label": v[0]} for k, v in PTYPES.items()]}
                    else: return self.send({"error": "not found"}, 404)
                    c.commit(); return self.send(out)
                finally: c.close()
        except ValueError as e:
            return self.send({"error": str(e)}, 400)
        except Exception as e:
            return self.send({"error": "서버 오류가 발생했어요. 잠시 후 다시 시도해 주세요.", "detail": type(e).__name__}, 500)
        if u.path.startswith("/api/"): return self.send({"error": "not found"}, 404)
        p = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        f = os.path.normpath(os.path.join(BASE, "static", p))
        if not f.startswith(os.path.join(BASE, "static")) or not os.path.isfile(f):
            f = os.path.join(BASE, "static", "index.html")
        b = open(f, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(f)[0] or "application/octet-stream") + ("; charset=utf-8" if f.endswith((".html", ".js", ".json", ".css")) else ""))
        if f.endswith((".html", "sw.js")): self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", len(b)); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        u = urlparse(self.path); d = self.body()
        if not isinstance(d, dict): d = {}
        if u.path == "/api/chat":
            r, src = reply(str(d.get("message", ""))[:500]); return self.send({"reply": r, "source": src})
        if u.path in ("/api/book", "/api/trial"):
            if d.get("website"): return self.send({"ok": True})  # 봇 방지(숨은 칸)
            c = db()
            try:
                out = create_trial(c, d); c.commit(); return self.send(out)
            except ValueError as e:
                c.rollback(); return self.send({"error": str(e)}, 400)
            except Exception as e:
                c.rollback(); return self.send({"error": "신청 처리 중 오류가 발생했어요. 전화로 문의해 주세요: " + PHONE, "detail": type(e).__name__}, 500)
            finally: c.close()
        if u.path == "/api/inquiry":
            if d.get("website"): return self.send({"ok": True})  # 봇 방지(숨은 칸)
            name, phone = str(d.get("name", "")).strip()[:40], str(d.get("phone", "")).strip()[:20]
            if not name or not re.fullmatch(r"[0-9\- ]{9,14}", phone):
                return self.send({"error": "이름과 연락처를 확인해 주세요. 예: 010-1234-5678"}, 400)
            c = db()
            try:
                prod = c.execute("SELECT * FROM products WHERE id=? AND active=1", (_int(d.get("product_id")),)).fetchone() if d.get("product_id") else None
                src = d.get("source") if d.get("source") in SOURCES else "기타"
                msg = str(d.get("message", "")).strip()[:500]; pref = str(d.get("preferred", "")).strip()[:80]
                people = max(1, min(100, _int(d.get("people"), 1)))
                ol = find_open_lead(c, phone)
                if ol and ol["created"] and ol["created"] >= (datetime.datetime.now(KST).replace(tzinfo=None) - datetime.timedelta(minutes=10)).isoformat():
                    return self.send({"ok": True, "id": ol["id"], "dup": True})  # 10분 내 같은 번호 중복 접수 방지
                lid = create_lead(c, name, phone, src, "홈페이지 문의", prod["name"] if prod else str(d.get("interest", ""))[:80],
                                  prod["id"] if prod else None, people, msg, pref)
                c.commit(); return self.send({"ok": True, "id": lid})
            except Exception as e:
                c.rollback(); return self.send({"error": "문의 접수 중 오류가 발생했어요. 전화로 문의해 주세요: " + PHONE, "detail": type(e).__name__}, 500)
            finally: c.close()
        if u.path.startswith("/api/admin/"):
            if not self.admin_ok(): return self.admin_deny()
            c = db(); act = u.path.split("/")[-1]
            try:
                out = admin_action(c, act, d); c.commit(); return self.send(out)
            except NotFound:
                c.rollback(); return self.send({"error": "not found"}, 404)
            except ValueError as e:
                c.rollback(); return self.send({"error": str(e)}, 400)
            except Exception as e:
                c.rollback(); return self.send({"error": "저장 중 오류가 발생했어요.", "detail": type(e).__name__ + ": " + str(e)[:200]}, 500)
            finally: c.close()
        self.send({"error": "not found"}, 404)

if __name__ == "__main__":
    init()
    print(f"원스타 앱: http://localhost:{PORT}  (LLM: {'on' if LLM_KEY else 'off, FAQ 모드'}, DB: {'postgres' if PG else 'sqlite'})")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
