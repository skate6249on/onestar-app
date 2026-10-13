#!/usr/bin/env python3
"""원스타스케이트파크 웹앱 - http.server + (Postgres via DATABASE_URL | 로컬 sqlite3)."""
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
    c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; return c

KST = datetime.timezone(datetime.timedelta(hours=9))
def today():
    return datetime.datetime.now(KST).date().isoformat()
def now():
    return datetime.datetime.now(KST).replace(tzinfo=None).isoformat(timespec="seconds")

DOW_KO = "월화수목금토일"
# 정규 수강권(4회)
PTYPES = {"wd_kids": ("주중 키즈", 200000), "wd_adult": ("주중 성인", 300000),
          "we_kids": ("주말 키즈", 250000), "we_adult": ("주말 성인", 350000)}
PAY_STATUS = ["미납", "일부 납부", "완납"]
PAY_METHODS = ["현금", "카드", "계좌이체", "스포츠강좌이용권", "기타"]

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
    """)
    for col, decl in (("note", "TEXT"), ("slot_key", "TEXT"), ("moved_from", "INTEGER"), ("moved_to", "INTEGER")):
        add_column(c, "classes", col, decl)
    add_column(c, "bookings", "memo", "TEXT")
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

def price_for(kind):
    return PRICES.get(kind, DEFAULT_PRICE)

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
        total, dep, bal = price_for(cl["kind"])
        cl.update(booked=booked, remaining=max(0, cl["capacity"] - booked), price=total, deposit=dep, balance=bal,
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
    pub = ("id", "date", "time", "title", "kind", "capacity", "cancelled", "booked", "remaining", "price", "deposit", "balance", "holiday")
    return [{k: cl.get(k) for k in pub} for cl in cls]

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

def dashboard(c):
    t = today()
    cls, hol = load_range(c, t, t)
    unpaid_b = [dict(r) for r in c.execute("""SELECT b.*, cl.date, cl.time, cl.title, cl.kind FROM bookings b JOIN classes cl ON cl.id=b.class_id
              WHERE b.cancelled=0 AND b.deposit_paid=0 AND cl.date>=? ORDER BY cl.date, cl.time""", (t,)).fetchall()]
    for b in unpaid_b: b["deposit"] = price_for(b["kind"])[1] * int(b["count"] or 1)
    unpaid_p = [dict(r) for r in c.execute("""SELECT p.*, s.name, s.phone, s.guardian FROM packages p JOIN students s ON s.id=p.student_id
              WHERE p.pay_status<>'완납' AND s.active=1 ORDER BY p.start_date""").fetchall()]
    expiring = [dict(r) for r in c.execute("""SELECT s.id, s.name, s.phone, s.guardian, s.kind, SUM(p.remaining) AS left_n FROM students s
              JOIN packages p ON p.student_id=s.id WHERE s.active=1 GROUP BY s.id, s.name, s.phone, s.guardian, s.kind
              HAVING SUM(p.remaining)<=1 ORDER BY SUM(p.remaining), s.name""").fetchall()]
    for e in expiring: e["left_n"] = int(e["left_n"] or 0)
    nst = c.execute("SELECT COUNT(*) AS n FROM students WHERE active=1").fetchone()["n"]
    return {"today": t, "dow": DOW_KO[datetime.date.fromisoformat(t).weekday()], "holiday": hol.get(t), "classes": cls,
            "unpaid_bookings": unpaid_b, "unpaid_packages": unpaid_p, "expiring": expiring, "students": int(nst)}

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
        kind = d.get("kind") if d.get("kind") in PRICES else "adult"
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
        for k in ("deposit_paid", "balance_paid", "attended", "cancelled"):
            if k in d: c.execute(f"UPDATE bookings SET {k}=? WHERE id=?", (1 if d[k] and d[k] != "0" else 0, bid))
        if "memo" in d: c.execute("UPDATE bookings SET memo=? WHERE id=?", (s("memo", 300), bid))
        b = c.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
        if not b: raise ValueError("예약을 찾을 수 없어요.")
        st = "취소" if b["cancelled"] else "결제 완료" if b["balance_paid"] else "예약 확정" if b["deposit_paid"] else "예약금 대기"
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
            c.execute("UPDATE packages SET student_id=?, ptype=?, sessions=?, remaining=?, price=?, start_date=?, pay_status=?, pay_method=?, paid_amount=?, memo=? WHERE id=?",
                      vals + (_int(d["id"]),))
            return {"ok": True, "id": _int(d["id"])}
        cur = c.execute("INSERT INTO packages(student_id,ptype,sessions,remaining,price,start_date,pay_status,pay_method,paid_amount,memo,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        vals + (now(),))
        return {"ok": True, "id": cur.lastrowid}
    if act == "package_delete":
        pid = _int(d.get("id"))
        c.execute("UPDATE attendance SET package_id=NULL WHERE package_id=?", (pid,))
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
    raise NotFound()

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
            if u.path == "/api/classes":
                return self.send(classes_for(_date(g("date", today()))))
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
                        out = {"slots": slot_occupancy(c), "ptypes": [{"key": k, "label": v[0], "price": v[1]} for k, v in PTYPES.items()],
                               "pay_status": PAY_STATUS, "pay_methods": PAY_METHODS, "today": today()}
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
        if u.path == "/api/book":
            name, phone = str(d.get("name", "")).strip(), str(d.get("phone", "")).strip()
            count = _int(d.get("count", 1), 0)
            if not name or not re.fullmatch(r"[0-9\- ]{9,14}", phone) or not (1 <= count <= 5):
                return self.send({"error": "이름, 전화번호, 인원(1~5명)을 확인해 주세요."}, 400)
            c = db()
            try:
                c.execute("BEGIN IMMEDIATE")
                cl = c.execute("SELECT * FROM classes WHERE id=?" + (" FOR UPDATE" if PG else ""), (_int(d.get("class_id")),)).fetchone()
                if not cl or cl["cancelled"]: raise ValueError("해당 수업은 예약할 수 없어요.")
                if cl["date"] < today(): raise ValueError("지난 날짜는 예약할 수 없어요.")
                full = get_class(c, cl["id"])
                if count > full["remaining"]: raise ValueError(f"남은 자리가 {full['remaining']}석이에요.")
                cur = c.execute("INSERT INTO bookings(class_id,name,phone,age,count,created) VALUES(?,?,?,?,?,?)",
                    (cl["id"], name[:40], phone, str(d.get("age", ""))[:50], count, now()))
                c.commit()
                total, dep, bal = price_for(cl["kind"])
                return self.send({"ok": True, "id": cur.lastrowid, "status": "예약금 대기",
                                  "deposit": dep * count, "balance": bal * count, "total": total * count,
                                  "naver": NAVER, "class": dict(cl)})
            except ValueError as e:
                c.rollback(); return self.send({"error": str(e)}, 409)
            except Exception as e:
                c.rollback(); return self.send({"error": "예약 처리 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.", "detail": type(e).__name__}, 500)
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
