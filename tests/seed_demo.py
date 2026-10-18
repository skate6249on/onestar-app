"""스크린샷용 예시 데이터. 운영 DB에는 절대 쓰지 않음.
사용: SQLITE_PATH=/tmp/demo.db python3 tests/seed_demo.py"""
import os, sys, datetime
if os.environ.get("DATABASE_URL"): sys.exit("DATABASE_URL이 설정돼 있어요. 예시 데이터는 운영 DB에 넣지 않아요.")
p = os.environ.get("SQLITE_PATH", "")
if not p or os.path.basename(p) == "onestar.db": sys.exit("SQLITE_PATH를 별도 테스트 파일(예: /tmp/demo.db)로 지정해 주세요.")
if os.path.exists(p): os.remove(p)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S
S.init(); c = S.db(); A = lambda act, d: S.admin_action(c, act, d)
T = datetime.date.fromisoformat(S.today()); D = lambda n: (T + datetime.timedelta(days=n)).isoformat()
prod = {r["code"]: r["id"] for r in c.execute("SELECT id, code FROM products").fetchall()}
leads = [("박서연 (예시)", "010-0000-1001", "네이버", "trial_12_40", "신규문의", None, "주말 오후 체험 문의"),
         ("이도윤 (예시)", "010-0000-1002", "인스타", "trial_13_40", "신규문의", D(0), "친구 3명 같이"),
         ("최하은 (예시)", "010-0000-1003", "전화", "group", "체험예약", D(2), "초등 단체 15명"),
         ("정우진 (예시)", "010-0000-1004", "소개", "trial_11_30", "체험완료", D(0), "성인, 정규 고민 중"),
         ("한지민 (예시)", "010-0000-1005", "구글", "snow", "보류", D(10), "겨울 스노보드 일정 문의"),
         ("오시우 (예시)", "010-0000-1006", "네이버", "trial_12_40", "체험예약", D(1), "")]
ids = []
for n, ph, src, pc, st, nd, memo in leads:
    lid = S.create_lead(c, n, ph, src, "관리자", c.execute("SELECT name FROM products WHERE id=?", (prod[pc],)).fetchone()["name"], prod[pc], 1, "", "", st, "", memo, nd)
    ids.append(lid)
# 전환 1건
r = A("lead_convert", {"id": ids[3], "kind": "adult", "ptype": "wd_adult", "pay_status": "완납", "pay_method": "카드"})
# 기존 수강생들
studs = [("김민준 (예시)", "kids", "wd_kids", 1, "완납"), ("윤서아 (예시)", "kids", "we_kids", 0, "완납"), ("장현우 (예시)", "adult", "we_adult", 3, "일부 납부"),
         ("강하린 (예시)", "kids", "wd_kids", 2, "미납"), ("임도현 (예시)", "kids", "wd_kids", 4, "완납")]
for i, (n, k, pt, rem, pst) in enumerate(studs):
    sid = A("student_save", {"name": n, "kind": k, "phone": f"010-0000-20{i:02d}", "guardian": "보호자" if k == "kids" else ""})["id"]
    A("package_save", {"student_id": sid, "ptype": pt, "remaining": rem, "pay_status": pst, "paid_amount": 100000 if pst == "일부 납부" else "", "pay_method": "계좌이체", "start_date": D(-20)})
    A("enroll_add", {"student_id": sid, "slot_key": ("5|15:00" if pt.startswith("we") else "1|15:00"), "start_date": D(-20), "force": 1})
# 체험 신청 (시간은 통화 후 확정)
def nxt(n):
    d = T + datetime.timedelta(days=n)
    while d.weekday() == 0: d += datetime.timedelta(days=1)
    return d.isoformat()
t1 = S.create_trial(c, {"name": "오시우 (예시)", "phone": "010-0000-1006", "date": nxt(1), "time_pref": "오후 (13~17시) / 3시 이후", "count": 2, "age": "10살", "product_id": prod["trial_12_40"], "source": "네이버"})
t2 = S.create_trial(c, {"name": "서지후 (예시)", "phone": "010-0000-1007", "date": nxt(2), "time_pref": "저녁 (17~21시)", "count": 3, "age": "성인", "product_id": prod["trial_13_40"], "source": "인스타"})
A("trial_update", {"id": t2["id"], "conf_date": nxt(2), "conf_time": "18:00"})
t3 = S.create_trial(c, {"name": "문예린 (예시)", "phone": "010-0000-1008", "date": nxt(3), "time_pref": "상관없음", "count": 1, "age": "8살", "product_id": prod["trial_11_30"], "source": "구글"})
A("trial_update", {"id": t3["id"], "conf_date": nxt(3), "conf_time": "14:00"}); A("trial_update", {"id": t3["id"], "deposit_paid": 1})
A("payment_save", {"product": "스케이트보드 판매", "amount": 120000, "method": "카드", "date": D(-3)})
A("payment_save", {"product_id": prod["trial_13_40"], "amount": 40000, "method": "현금", "date": D(-5)})
A("expense_save", {"category": "임대료", "amount": 800000, "date": D(-10), "memo": "예시"})
A("expense_save", {"category": "광고", "amount": 50000, "date": D(-2), "memo": "예시"})
S.ensure_range(c, S.today(), S.today())
cl = c.execute("SELECT * FROM classes WHERE date=? ORDER BY time", (S.today(),)).fetchall()
if cl: A("attend", {"class_id": cl[0]["id"], "student_id": 1, "status": "출석"})
c.commit(); print("예시 데이터 완료:", p)
