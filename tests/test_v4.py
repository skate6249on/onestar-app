"""v4 end-to-end test: fresh temp SQLite, real HTTP. Run: python3 tests/test_v4.py"""
import json, os, subprocess, sys, tempfile, time, urllib.request, urllib.parse, datetime, sqlite3
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(); DBP = os.path.join(tmp, "t.db"); PORT = 8831; PW = "test-pw"
env = dict(os.environ, SQLITE_PATH=DBP, ADMIN_PASSWORD=PW, PORT=str(PORT)); env.pop("DATABASE_URL", None)
PGURL = os.environ.get("TEST_DATABASE_URL", "")  # 로컬 테스트용 Postgres(운영 DB 금지)
if PGURL: env["DATABASE_URL"] = PGURL
ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  ✓", name)
    else: fail += 1; print("  ✗", name, extra)
def req(path, body=None, admin=False, expect=200):
    h = {"Content-Type": "application/json"}
    if admin: h["X-Admin-Password"] = PW
    path = urllib.parse.quote(path, safe="/?=&:")
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", json.dumps(body).encode() if body is not None else None, h)
    try:
        with urllib.request.urlopen(r) as resp: code, d = resp.status, json.load(resp)
    except urllib.error.HTTPError as e: code, d = e.code, json.load(e)
    if code != expect: print("    !", path, code, d)
    return code, d
A = lambda p, b=None, e=200: req("/api/admin/" + p, b, True, e)[1]

def start():
    p = subprocess.Popen([sys.executable, os.path.join(BASE, "server.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(50):
        try: urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/info"); return p
        except Exception: time.sleep(0.1)
    raise SystemExit("server did not start")

srv = start()
try:
    print("공개 페이지/상품")
    _, info = req("/api/info")
    names = {p["name"]: p for p in info["products"]}
    check("체험 1:3 40분 40,000원", names["체험수업 1:3 · 40분"]["price"] == 40000)
    check("체험 1:2 40분 인당 50,000원", names["체험수업 1:2 · 40분"]["price"] == 50000 and names["체험수업 1:2 · 40분"]["per_person"] == 1)
    check("체험 1:1 30분 60,000원", names["체험수업 1:1 · 30분"]["price"] == 60000)
    check("1시간 체험 상품 없음", not any("1시간" in n or "60분" in n for n in names))
    check("체험 상품은 40분·30분 3개만", sorted(n for n, p in names.items() if p["cat"] == "체험수업") == ["체험수업 1:1 · 30분", "체험수업 1:2 · 40분", "체험수업 1:3 · 40분"])
    check("정규 주말 성인 350,000원", names["정규 주말 성인 (주1회 · 총4회)"]["price"] == 350000)
    check("아이 1:1 주중 월 4회 250,000원", names["아이 1:1 수업 주중 (월 4회)"]["price"] == 250000 and names["아이 1:1 수업 주중 (월 4회)"]["cat"] == "정규수업")
    check("아이 1:1 주말 월 4회 300,000원", names["아이 1:1 수업 주말 (월 4회)"]["price"] == 300000 and names["아이 1:1 수업 주말 (월 4회)"]["cat"] == "정규수업")
    regs = [p["name"] for p in info["products"] if p["cat"] == "정규수업"]
    check("정규 순서: 기존 4개 뒤에 아이 1:1 2개", regs == ["정규 주중 키즈 (주1회 · 총4회)", "정규 주중 성인 (주1회 · 총4회)", "정규 주말 키즈 (주1회 · 총4회)", "정규 주말 성인 (주1회 · 총4회)", "아이 1:1 수업 주중 (월 4회)", "아이 1:1 수업 주말 (월 4회)"], regs)
    check("아이 1:1은 체험 예약 대상 아님", not names["아이 1:1 수업 주중 (월 4회)"]["bookable"] and not names["아이 1:1 수업 주말 (월 4회)"]["bookable"])
    check("스노보드: 지산리조트 + 출장 가능", "지산리조트" in names["겨울철 스노보드 강습"]["descr"] and "출장" in names["겨울철 스노보드 강습"]["descr"])
    _, r = req("/api/chat", {"message": "스노보드 어디서 해요"}); check("챗 스노보드 장소", "지산리조트" in r["reply"] and "출장" in r["reply"])
    check("스노보드·단체 = 문의(가격 없음)", names["겨울철 스노보드 강습"]["price"] is None and names["단체 스케이트보드 수업"]["price"] is None)
    check("'무료' 문구 없음", "무료 체험" not in json.dumps(info, ensure_ascii=False) and "체험 무료" not in json.dumps(info, ensure_ascii=False))
    html = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/").read().decode()
    check("홈에 예약금/옛 가격 없음", "50,000원" not in html and "예약금 5,000" not in html)
    check("홈에 운영시간/주소/스노보드 장소", "13:00 ~ 21:00" in html and "원적로512번길 103" in html and "지산리조트" in html)
    _, r = req("/api/chat", {"message": "가격 얼마예요"})
    check("챗 FAQ 새 가격(인당)", "1:3 40분 인당 4만원" in r["reply"] and "50,000" not in r["reply"])
    check("챗 FAQ 아이 1:1 가격", "아이 1:1" in r["reply"] and "주중 25만원" in r["reply"] and "주말 30만원" in r["reply"])
    check("챗 FAQ에 1시간 체험 없음", "1시간" not in r["reply"] and "7만원" not in r["reply"] and "9만원" not in r["reply"])
    check("홈에 1시간 체험 없음", "1시간" not in html and "60분" not in html)
    _, r = req("/api/chat", {"message": "예약금 있나요"}); check("챗 예약금 안내(계좌번호 없음)", "1만원" in r["reply"] and "계좌" not in r["reply"])

    print("관리자 인증")
    code, _ = req("/api/admin/dashboard", None, False, 401); check("비밀번호 없으면 401", code == 401)

    print("문의 → 리드")
    code, r = req("/api/inquiry", {"name": "테스트문의", "phone": "010-1111-2222", "source": "인스타", "product_id": names["단체 스케이트보드 수업"]["id"], "people": 12, "message": "학교 단체"})
    check("문의 접수", code == 200 and r["ok"])
    lid = r["id"]
    code, r2 = req("/api/inquiry", {"name": "테스트문의", "phone": "01011112222"}); check("10분 내 중복 접수 방지", r2.get("dup"))
    code, r3 = req("/api/inquiry", {"name": "봇", "phone": "010-0000-0000", "website": "x"}); check("숨은 칸(봇) 무시", r3.get("ok") and "id" not in r3)
    L = A("leads"); check("리드 목록에 신규문의", L["counts"]["신규문의"] == 1 and L["leads"][0]["source"] == "인스타")
    tom = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    A("lead_followup", {"id": lid, "note": "통화함, 견적 요청", "next_date": tom})
    l = A(f"lead?id={lid}"); check("연락 기록 + 다음 연락일", l["next_date"] == tom and "통화함" in l["memo"])
    A("lead_stage", {"id": lid, "stage": "보류"}); check("보류 단계", A(f"lead?id={lid}")["stage"] == "보류")

    print("체험 신청(시간표와 별개) → 리드 → 시간 확정 → 예약금 → 체험 → 잔금")
    check("체험 가격 모두 인당", all(p["per_person"] == 1 for p in info["products"] if p["cat"] == "체험수업"))
    check("공개 정보에 예약금 10,000원", info["deposit"] == 10000)
    d = datetime.date.today() + datetime.timedelta(days=1)
    while d.weekday() != 1: d += datetime.timedelta(days=1)   # 다음 화요일
    mon = d - datetime.timedelta(days=1)
    code, r = req("/api/trial", {"date": mon.isoformat(), "time_pref": "오후", "name": "월요일", "phone": "010-9999-0000", "count": 1}, expect=400)
    check("월요일 신청 거부", code == 400)
    code, r = req("/api/trial", {"date": d.isoformat(), "name": "시간없음", "phone": "010-9999-0001", "count": 1}, expect=400)
    check("희망 시간대 필수", code == 400)
    trial = names["체험수업 1:2 · 40분"]
    _, cls_before = req(f"/api/classes?date={d.isoformat()}")
    code, b = req("/api/trial", {"date": d.isoformat(), "time_pref": "오후 (13~17시) / 3시 이후", "name": "김체험", "phone": "010-3333-4444", "age": "9살", "count": 2, "product_id": trial["id"], "source": "네이버"})
    check("체험 신청 접수(확정 대기)", code == 200 and b["status"] == "확정 대기")
    check("예상 금액 인당 5만 x 2 = 10만, 예약금 1만, 현장 9만", b["total"] == 100000 and b["deposit"] == 10000 and b["onsite"] == 90000, b)
    _, cls_after = req(f"/api/classes?date={d.isoformat()}")
    check("정규 시간표 정원에 영향 없음", [c["remaining"] for c in cls_before] == [c["remaining"] for c in cls_after])
    code, b2 = req("/api/book", {"date": d.isoformat(), "time_pref": "저녁", "name": "옛경로", "phone": "010-3333-5555", "count": 1})
    check("/api/book 도 체험 신청으로 처리", code == 200 and b2["status"] == "확정 대기")
    A("trial_update", {"id": b2["id"], "cancelled": 1})
    tid = b["id"]
    L = A("leads?stage=체험예약"); bl = [x for x in L["leads"] if x["phone"] == "010-3333-4444"]
    check("신청 → 리드(체험예약, 네이버, 키즈)", len(bl) == 1 and bl[0]["source"] == "네이버" and bl[0]["kind"] == "kids")
    blid = bl[0]["id"]
    dash = A("dashboard"); check("할 일: 시간 확정 필요", any(x["id"] == tid for x in dash["trial_wait"]))
    code, _ = req("/api/admin/trial_update", {"id": tid, "conf_date": d.isoformat(), "conf_time": "22:00"}, True, 400)
    check("운영시간 밖 확정 거부", code == 400)
    A("trial_update", {"id": tid, "conf_date": d.isoformat(), "conf_time": "15:00"})
    t = A(f"trial?id={tid}"); check("시간 확정", t["confirmed"] == 1 and t["conf_time"] == "15:00" and t["status"] == "확정 · 예약금 대기")
    dash = A("dashboard"); check("할 일: 예약금 입금 확인", any(x["id"] == tid for x in dash["deposit_due"]))
    A("trial_update", {"id": tid, "deposit_paid": 1})
    ym = datetime.date.today().isoformat()[:7]
    led = A("ledger?ym=" + ym)
    check("예약금 → 장부 10,000원", [p["amount"] for p in led["payments"] if p["source"] == "trial_deposit"] == [10000])
    A("trial_update", {"id": tid, "deposit_paid": 0}); A("trial_update", {"id": tid, "deposit_paid": 1})
    led = A("ledger?ym=" + ym); check("예약금 토글 반복해도 1건", len([p for p in led["payments"] if p["source"] == "trial_deposit"]) == 1)
    dash = A("dashboard"); check("입금 후 할 일에서 빠짐", not any(x["id"] == tid for x in dash["deposit_due"]))
    A("trial_update", {"id": tid, "attended": 1})
    check("체험 완료 → 리드 체험완료", A(f"lead?id={blid}")["stage"] == "체험완료")
    dash = A("dashboard"); ub = [x for x in dash["trial_unpaid"] if x["id"] == tid]
    check("할 일: 잔금 90,000원 (예약금 차감)", ub and ub[0]["balance_due"] == 90000)
    A("trial_update", {"id": tid, "paid": 1, "pay_method": "카드"})  # 금액 생략 → 남은 금액
    led = A("ledger?ym=" + ym)
    tp = [p["amount"] for p in led["payments"] if p["source"] in ("trial", "trial_deposit")]
    check("장부: 예약금 1만 + 잔금 9만 = 총액 10만 (중복 없음)", sorted(tp) == [10000, 90000] and sum(tp) == 100000, tp)
    A("trial_update", {"id": tid, "paid": 0}); led = A("ledger?ym=" + ym)
    check("잔금 취소 → 장부에서 잔금만 빠짐", [p["source"] for p in led["payments"] if p["source"].startswith("trial")] == ["trial_deposit"])
    A("trial_update", {"id": tid, "paid": 1, "amount": 90000, "pay_method": "카드"})
    A("settings_save", {"deposit_amount": 20000}); _, inf2 = req("/api/info")
    check("예약금 설정 변경", inf2["deposit"] == 20000 and A(f"trial?id={tid}")["deposit_amount"] == 10000)
    A("settings_save", {"deposit_amount": 10000})
    tl = A("trials?f=past"); check("체험 목록(지난/완료)", any(x["id"] == tid for x in tl["items"]))

    print("정규 등록(전환) → 수강생 + 4회 수강권 + 장부")
    r = A("lead_convert", {"id": blid, "name": "김체험", "kind": "kids", "ptype": "wd_kids", "pay_status": "완납", "pay_method": "계좌이체"})
    sid, pkid = r["student_id"], r["package_id"]
    check("리드 정규등록", A(f"lead?id={blid}")["stage"] == "정규등록")
    st = A(f"student?id={sid}"); check("수강권 4회 · 200,000원(상품 가격)", st["packages"][0]["remaining"] == 4 and st["packages"][0]["price"] == 200000)
    led = A("ledger?ym=" + ym); check("수강권 결제 → 장부 200,000원", any(p["source"] == "package" and p["amount"] == 200000 for p in led["payments"]))
    rep = A("report"); check("주간 리포트: 문의 3 / 전환 1 / 새 수강권 1", rep["inquiries"] == 3 and rep["conversions"] == 1 and rep["new_packages"] == 1, rep)

    print("고정 반 + 출석 차감 + 재등록")
    A("enroll_add", {"student_id": sid, "slot_key": "1|15:00", "start_date": datetime.date.today().isoformat()})
    _, cls = req(f"/api/classes?date={d.isoformat()}")
    c1 = [c for c in cls if c["time"] == "15:00"][0]
    cdet = A(f"class?id={c1['id']}"); check("명단에 정규 수강생", any(x["student_id"] == sid for x in cdet["regulars"]))
    # 3회 출석 처리(다른 화요일들)
    days = [d + datetime.timedelta(days=7 * i) for i in range(3)]
    for dd in days:
        _, cl = req(f"/api/classes?date={dd.isoformat()}"); cid = [c for c in cl if c["time"] == "15:00"][0]["id"]
        A("attend", {"class_id": cid, "student_id": sid, "status": "출석"})
    st = A(f"student?id={sid}"); check("출석 3회 → 잔여 1회", st["left"] == 1)
    rn = A("renewals"); it = [x for x in rn["items"] if x["id"] == sid]
    check("재등록 목록에 표시", len(it) == 1 and it[0]["left"] == 1)
    check("안내 문구에 이름/금액", it and "김체험" in it[0]["message"] and "200,000원" in it[0]["message"])
    A("renew_mark", {"student_id": sid}); check("안내함 표시", [x for x in A("renewals")["items"] if x["id"] == sid][0]["notified"])
    # 출석 취소 → 복구
    A("attend", {"class_id": cid, "student_id": sid, "status": ""}); check("출석 취소 → 잔여 복구(2)", A(f"student?id={sid}")["left"] == 2)
    A("attend", {"class_id": cid, "student_id": sid, "status": "출석"})
    # 재등록
    A("package_save", {"student_id": sid, "ptype": "wd_kids", "pay_status": "일부 납부", "paid_amount": 100000, "pay_method": "현금"})
    check("재등록 후 목록에서 빠짐", not [x for x in A("renewals")["items"] if x["id"] == sid])
    dash = A("dashboard"); up = [p for p in dash["unpaid_packages"] if p["student_id"] == sid]
    check("할 일: 미납 수강권 남은 금액 100,000원", up and up[0]["due"] == 100000)
    r11 = A("student_save", {"name": "일대일", "kind": "kids", "phone": "010-5555-0011"}); sid11 = r11.get("id") or r11.get("student_id")
    A("package_save", {"student_id": sid11, "ptype": "we_kids11", "remaining": 1, "pay_status": "미납"})
    it11 = [x for x in A("renewals")["items"] if x["id"] == sid11]
    check("아이 1:1 재등록 문구(월 4회 · 300,000원)", it11 and "아이 1:1 주말 수업(월 4회)" in it11[0]["message"] and "300,000원" in it11[0]["message"], it11)
    A("student_save", {"id": sid11, "name": "일대일", "kind": "kids", "phone": "010-5555-0011", "active": 0})

    print("장부: 수동 매출/지출/순이익")
    A("payment_save", {"product": "보드 판매", "amount": 120000, "method": "카드"})
    A("expense_save", {"category": "광고", "amount": 30000, "memo": "테스트"})
    led = A("ledger?ym=" + ym)
    exp_income = 200000 + 100000 + 120000 + 100000
    check("월 매출 합계", led["income"] == exp_income, led["income"])
    check("순이익 = 매출 - 지출", led["net"] == led["income"] - 30000)
    check("상품별 합계에 보드 판매", any(x["name"] == "보드 판매" and x["amount"] == 120000 for x in led["by_product"]))
    autop = [p for p in led["payments"] if p["source"] == "package"][0]
    code, r = req("/api/admin/payment_delete", {"id": autop["id"]}, True, 400); check("자동 기록은 장부에서 삭제 불가", code == 400)

    print("상품 관리 → 공개 페이지 반영")
    snow = names["겨울철 스노보드 강습"]
    A("product_save", dict(snow, descr="일정과 수강료는 문의 주세요 (테스트)", active=1))
    _, info2 = req("/api/info"); check("설명 수정 반영", any(p["descr"].endswith("(테스트)") for p in info2["products"]))
    A("product_save", dict(snow, active=0)); _, info3 = req("/api/info")
    check("숨김 상품은 공개 목록에서 빠짐", not any(p["id"] == snow["id"] for p in info3["products"]))
    A("product_save", {"id": names["정규 주중 키즈 (주1회 · 총4회)"]["id"], "cat": "정규수업", "name": "정규 주중 키즈 (주1회 · 총4회)", "price": 210000, "ptype": "wd_kids", "sessions": 4, "active": 1})
    meta = A("meta"); check("상품 가격 → 수강권 기본가 연동", [p for p in meta["ptypes"] if p["key"] == "wd_kids"][0]["price"] == 210000)
    check("수강권 종류에 아이 1:1 주중/주말", {p["key"]: p["price"] for p in meta["ptypes"]}.get("wd_kids11") == 250000 and {p["key"]: p["price"] for p in meta["ptypes"]}.get("we_kids11") == 300000)

    print("v3 기능 유지")
    A("holiday_add", {"date": days[1].isoformat(), "reason": "테스트 휴무"})
    _, cl = req(f"/api/classes?date={days[1].isoformat()}"); check("휴무일 → 수업 휴강", all(c["cancelled"] for c in cl))
    A("holiday_del", {"date": days[1].isoformat()})
    _, cl = req(f"/api/classes?date={days[2].isoformat()}")
    s2 = A("student_save", {"name": "보강학생", "kind": "kids"})["id"]
    A("makeup_add", {"class_id": cl[0]["id"], "student_id": s2}); check("보강 추가", any(x["student_id"] == s2 for x in A(f"class?id={cl[0]['id']}")["makeups"]))
    r = A("students_bulk", {"text": "가나다, 엄마, 010-5555-6666, 초2, 키즈"}); check("일괄 등록", r["added"] == 1)
    wk = A("week?start=" + d.isoformat()); check("주간 스케줄", len(wk["days"]) == 7)
finally:
    srv.terminate(); srv.wait()

if PGURL:
    print("마이그레이션 멱등성(Postgres, 서버 재시작 2회)")
    srv = start(); srv.terminate(); srv.wait(); srv = start()
    try:
        check("상품 중복 없음", len(A("products")["products"]) == 11)
        led = A("ledger?ym=" + datetime.date.today().isoformat()[:7])
        check("수강권 결제 중복 없음", len([p for p in led["payments"] if p["source"] == "package"]) == 2)
    finally:
        srv.terminate(); srv.wait()
    print(f"\n결과: {ok} 통과 / {fail} 실패"); sys.exit(1 if fail else 0)
print("마이그레이션 멱등성(서버 재시작 2회)")
srv = start(); srv.terminate(); srv.wait(); srv = start()
try:
    _, info = req("/api/info")
    c = sqlite3.connect(DBP)
    check("상품 중복 없음", c.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 11)
    check("체험 인당 마이그레이션 1회", c.execute("SELECT COUNT(*) FROM products WHERE code LIKE 'trial%' AND per_person=1").fetchone()[0] == 3)
    check("수강권 결제 중복 이관 없음", c.execute("SELECT COUNT(*) FROM payments WHERE source='package'").fetchone()[0] == 2)
finally:
    srv.terminate(); srv.wait()

print("기존 v4 DB(1시간 체험 상품 있음) → 판매 중단 마이그레이션")
v4 = os.path.join(tmp, "v4.db"); env["SQLITE_PATH"] = v4; srv = start(); srv.terminate(); srv.wait()
c = sqlite3.connect(v4)
for code, name, price, sort in (("trial_13_60", "체험수업 1:3 · 1시간", 50000, 20), ("trial_12_60", "체험수업 1:2 · 1시간", 70000, 40), ("trial_11_60", "체험수업 1:1 · 1시간", 90000, 60)):
    c.execute("INSERT INTO products(code,cat,name,price,per_person,ptype,sessions,descr,sort,active,bookable,updated) VALUES(?,?,?,?,1,'',1,'',?,1,1,'')", (code, "체험수업", name, price, sort))
pid60 = c.execute("SELECT id FROM products WHERE code='trial_12_60'").fetchone()[0]
c.execute("INSERT INTO trials(name,phone,count,product_id,pref_date,pref_time,deposit_amount,created) VALUES('기존신청','010-7777-0000',2,?,'2026-11-03','오후',10000,'2026-10-01')", (pid60,))
c.execute("DELETE FROM settings WHERE k='mig_no_trial_60'")
c.execute("DELETE FROM products WHERE code IN ('reg_wd_kids11','reg_we_kids11')"); c.execute("DELETE FROM settings WHERE k='mig_kids11'")
c.execute("UPDATE products SET sort=sort+30 WHERE cat<>'체험수업'")  # 옛 DB 정렬값(정규 70~100, 시즌 110~120)
c.execute("UPDATE products SET price=210000, descr='사장님 수정' WHERE code='reg_wd_kids'")  # 다른 상품 수정값 유지 확인
before = {r[0]: r[1:] for r in c.execute("SELECT code,name,price,descr,sort,active FROM products WHERE code NOT IN ('trial_13_60','trial_12_60','trial_11_60')")}
c.commit(); c.close()
srv = start()
try:
    pub = req("/api/info")[1]["products"]
    check("기존 DB: 1시간 체험 공개 목록에서 빠짐", not any("1시간" in p["name"] for p in pub))
    regs = [p["name"] for p in pub if p["cat"] == "정규수업"]
    check("기존 DB: 아이 1:1 2개 추가(정규 성인 주말 뒤)", regs[-2:] == ["아이 1:1 수업 주중 (월 4회)", "아이 1:1 수업 주말 (월 4회)"] and len(regs) == 6, regs)
    check("기존 DB: 시즌·단체는 정규 뒤 그대로", [p["cat"] for p in pub][-2:] == ["시즌·단체", "시즌·단체"])
    c = sqlite3.connect(v4)
    after = {r[0]: r[1:] for r in c.execute("SELECT code,name,price,descr,sort,active FROM products WHERE code IN (%s)" % ",".join("?" * len(before)), list(before))}
    check("기존 DB: 다른 상품 행은 그대로(수정값 유지)", after == before)
    c.close()
    check("기존 DB: 짧은 체험 3개는 그대로", sorted(p["name"] for p in pub if p["cat"] == "체험수업") == ["체험수업 1:1 · 30분", "체험수업 1:2 · 40분", "체험수업 1:3 · 40분"])
    adm = [p for p in A("products")["products"] if p["code"] in ("trial_13_60", "trial_12_60", "trial_11_60")]
    check("기존 DB: 1시간 상품 행은 남고 숨김·예약불가", len(adm) == 3 and all(p["active"] == 0 and p["bookable"] == 0 for p in adm))
    c = sqlite3.connect(v4)
    check("기존 체험 신청 기록 유지", c.execute("SELECT COUNT(*) FROM trials WHERE name='기존신청' AND product_id=?", (pid60,)).fetchone()[0] == 1)
    c.close()
    code, r = req("/api/trial", {"date": "2026-11-04", "time_pref": "오후 (13~17시)", "name": "새신청", "phone": "010-7777-0001", "count": 1, "product_id": pid60})
    c = sqlite3.connect(v4)
    check("숨긴 1시간 상품으로는 새 신청에 상품 연결 안 됨", c.execute("SELECT product_id FROM trials WHERE name='새신청'").fetchone()[0] is None)
    c.close()
finally:
    srv.terminate(); srv.wait()
srv = start()
try:
    A("product_save", {"id": pid60, "cat": "체험수업", "name": "체험수업 1:2 · 1시간", "price": 70000, "per_person": 1, "active": 1})
    srv.terminate(); srv.wait(); srv = start()
    check("마이그레이션 1회만(사장님이 다시 켜면 유지)", any(p["id"] == pid60 for p in req("/api/info")[1]["products"]))
    k11 = [p for p in A("products")["products"] if p["code"] == "reg_we_kids11"][0]
    A("product_save", {"id": k11["id"], "cat": "정규수업", "name": k11["name"], "price": 300000, "ptype": "we_kids11", "sessions": 4, "active": 0})
    srv.terminate(); srv.wait(); srv = start()
    c = sqlite3.connect(v4)
    check("아이 1:1 추가도 1회만(숨김 유지·중복 없음)", c.execute("SELECT COUNT(*), MAX(active) FROM products WHERE code='reg_we_kids11'").fetchone() == (1, 0))
    c.close()
finally:
    srv.terminate(); srv.wait()

print("v3 DB → v4 마이그레이션")
v3 = os.path.join(tmp, "v3.db"); c = sqlite3.connect(v3)
c.executescript("""CREATE TABLE classes(id INTEGER PRIMARY KEY, date TEXT, time TEXT, title TEXT, kind TEXT, capacity INTEGER, cancelled INTEGER DEFAULT 0, UNIQUE(date,time));
CREATE TABLE bookings(id INTEGER PRIMARY KEY, class_id INTEGER, name TEXT, phone TEXT, age TEXT, count INTEGER, status TEXT DEFAULT '예약금 대기', deposit_paid INTEGER DEFAULT 0, balance_paid INTEGER DEFAULT 0, attended INTEGER DEFAULT 0, cancelled INTEGER DEFAULT 0, created TEXT);
CREATE TABLE students(id INTEGER PRIMARY KEY, name TEXT NOT NULL, guardian TEXT, phone TEXT, grade TEXT, kind TEXT DEFAULT 'kids', memo TEXT, active INTEGER DEFAULT 1, created TEXT);
CREATE TABLE packages(id INTEGER PRIMARY KEY, student_id INTEGER, ptype TEXT, sessions INTEGER, remaining INTEGER, price INTEGER, start_date TEXT, pay_status TEXT DEFAULT '미납', pay_method TEXT, paid_amount INTEGER DEFAULT 0, memo TEXT, created TEXT);
INSERT INTO students(name,kind,active) VALUES('옛학생','kids',1);
INSERT INTO packages(student_id,ptype,sessions,remaining,price,start_date,pay_status,paid_amount) VALUES(1,'wd_kids',4,1,200000,'2026-09-01','완납',200000);""")
c.commit(); c.close()
env["SQLITE_PATH"] = v3; srv = start()
try:
    led = A("ledger?ym=2026-09"); check("v3 수강권 결제 → 장부 이관", led["income"] == 200000)
    check("v3 수강생 재등록 목록", any(x["name"] == "옛학생" for x in A("renewals")["items"]))
    check("v3 DB 스노보드 설명", any("지산리조트" in p["descr"] for p in req("/api/info")[1]["products"]))
    check("v3 DB에도 체험 인당 적용", all(p["per_person"] == 1 for p in req("/api/info")[1]["products"] if p["cat"] == "체험수업"))
finally:
    srv.terminate(); srv.wait()
print(f"\n결과: {ok} 통과 / {fail} 실패")
sys.exit(1 if fail else 0)
