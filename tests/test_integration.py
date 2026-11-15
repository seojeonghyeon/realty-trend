"""수집기(가짜 네이버/국토부) → DB → 화면/routine API 전체 흐름."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db
from app.models import BudgetProfile, ComplexEval, LegalDong, Listing, Trade, WatchRegion
from collector import run_daily

TODAY = dt.date(2026, 11, 14)
YESTERDAY = TODAY - dt.timedelta(days=1)

LEGAL = [
    ("4100000000", "경기도"),
    ("4111000000", "경기도 수원시"),
    ("4111700000", "경기도 수원시 영통구"),
    ("4111710300", "경기도 수원시 영통구 영통동"),
    ("4113000000", "경기도 성남시"),
    ("4113100000", "경기도 성남시 수정구"),
    ("4113110100", "경기도 성남시 수정구 신흥동"),
]


class FakeNaver:
    """두 번째 호출일에는 매물 하나가 사라지고 하나는 가격이 내려간다."""

    day = YESTERDAY

    def __init__(self, *a, **k):
        pass

    def complexes_in_dong(self, code):
        if code == "4111710300":
            return [{"naver_no": "100", "name": "벽적골롯데", "dong_code": code, "households": 1040, "built_year": 2002,
                     "lat": 37.25, "lng": 127.07, "deal_count": 3, "lease_count": 1}]
        if code == "4113110100":
            return [{"naver_no": "200", "name": "두산", "dong_code": code, "households": 570, "built_year": 1993,
                     "lat": 37.44, "lng": 127.14, "deal_count": 1, "lease_count": 0}]
        return []

    def complex_detail(self, no):
        area = {"100": (79.46, 59.96, "79A"), "200": (82.03, 71.36, "82")}[no]
        return {"name": "", "address": "경기도", "jibun": "960" if no == "100" else "3393", "households": None,
                "built_year": None, "lat": None, "lng": None, "corridor": "계단식",
                "areas": [{"pyeong_no": "1", "area_name": area[2], "supply_m2": area[0], "exclusive_m2": area[1],
                           "rooms": 3, "baths": 1, "entrance": "계단식", "households": 416}]}

    def articles(self, no, trade="sale", max_pages=10):
        base = dict(area_name="79A", supply_m2=79.46, exclusive_m2=59.96, direction="남향", realtor="A공인",
                    realtor_count=1, confirmed_at=FakeNaver.day)
        if no == "100" and trade == "sale":
            yield {**base, "article_no": "a1", "trade_type": "sale", "building": "101동", "floor_text": "6/15",
                   "price": 88000 if FakeNaver.day == YESTERDAY else 86500, "description": "올수리 남향"}
            yield {**base, "article_no": "a2", "trade_type": "sale", "building": "101동", "floor_text": "6/15",
                   "price": 88000 if FakeNaver.day == YESTERDAY else 86500, "description": "올수리 남향 급매"}
            if FakeNaver.day == YESTERDAY:
                yield {**base, "article_no": "a3", "trade_type": "sale", "building": "105동", "floor_text": "1/15",
                       "price": 84000, "description": "기본상태"}
        if no == "100" and trade == "jeonse":
            yield {**base, "article_no": "j1", "trade_type": "jeonse", "building": "102동", "floor_text": "10/15",
                   "price": 45000, "description": ""}
        if no == "200" and trade == "sale":
            yield {**base, "area_name": "82", "supply_m2": 82.03, "exclusive_m2": 71.36, "article_no": "b1",
                   "trade_type": "sale", "building": "3동", "floor_text": "5/15", "price": 84300, "description": "정상입주"}


@pytest.fixture(scope="module")
def client():
    init_db()
    db = SessionLocal()
    for code, name in LEGAL:
        parts = name.split()
        db.add(LegalDong(code=code, name=name, sido=parts[0], sigungu=" ".join(parts[1:-1]) if code[5:] != "00000" else " ".join(parts[1:]),
                         eupmyeondong=parts[-1] if code[5:] != "00000" else "", active=True))
    db.add(BudgetProfile(id=1, cash=44300, loan=45000, tolerance=3000))
    db.add(WatchRegion(level="sigungu", code="4111000000", display_name="수원시", group_label="수원"))  # 시 단위
    db.add(WatchRegion(level="dong", code="4113110100", display_name="성남시 수정구 신흥동", group_label="구성남"))
    # 국토부 실거래 (매칭 대상)
    for i, (d, p) in enumerate([(dt.date(2021, 9, 1), 95000), (dt.date(2026, 10, 3), 86000)]):
        db.add(Trade(dedupe_key=f"t{i}", kind="sale", apt_seq="41117-1", lawd_cd="41117", dong_code="4111710300",
                     apt_name="벽적골롯데", jibun="960", built_year=2002, exclusive_m2=59.96, area_group=59, deal_date=d, price=p))
    db.commit()
    db.close()

    run_daily.NaverClient = FakeNaver
    FakeNaver.day = YESTERDAY
    assert run_daily.run(YESTERDAY, skip_molit=True) in ("ok", "partial")
    FakeNaver.day = TODAY
    assert run_daily.run(TODAY, skip_molit=True) in ("ok", "partial")

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_collector_effects(client):
    db = SessionLocal()
    a1, a2, a3 = (db.query(Listing).filter_by(article_no=n).one() for n in ("a1", "a2", "a3"))
    assert a3.status == "closed"  # 사라진 매물
    assert a1.prev_price == 88000 and a1.price == 86500 and a1.price_changed_at == TODAY
    assert a1.is_primary != a2.is_primary  # 같은 매물 중복 묶음
    assert {a1.repair_grade, a2.repair_grade} == {"올수리"}
    t = db.query(Trade).filter_by(dedupe_key="t0").one()
    assert t.complex_id is not None and t.area_group == 59  # 이름+지번 매칭
    ev = db.query(ComplexEval).filter_by(complex_id=t.complex_id).one()
    assert ev.households == "Good" and ev.source == "auto"
    db.close()


def login(client):
    r = client.post("/login", data={"username": "admin", "password": "pw", "next": "/"}, follow_redirects=False)
    assert r.status_code == 303


def test_pages_render(client):
    assert client.get("/", follow_redirects=False).status_code == 303
    login(client)
    db = SessionLocal()
    cid = db.query(Listing).filter_by(article_no="a1").one().complex_id
    rid = db.query(WatchRegion).first().id
    db.close()
    for url in ["/", "/regions", "/regions/search?q=영통", f"/regions/{rid}", f"/regions/{rid}?view=table",
                f"/regions/{rid}?view=analysis", f"/regions/{rid}/export.csv", "/matrix", "/matrix?survivors=0",
                "/matrix/export.csv", f"/complexes/{cid}", f"/complexes/{cid}/series.json?area=59", "/listings",
                "/listings?scope=all&new=1", "/news", "/map", "/visits", "/settings", "/jobs"]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.text[:500])
    page = client.get(f"/regions/{rid}").text
    assert "벽적골롯데(02^/59/8.65)" in page  # PDF 표기
    table = client.get(f"/regions/{rid}?view=table").text
    assert "95,000" in table  # 전고점
    series = client.get(f"/complexes/{cid}/series.json?area=59").json()
    assert [s["ask_min"] for s in series["snapshots"]] == [84000, 86500]


def test_user_actions(client):
    login(client)
    db = SessionLocal()
    cid = db.query(Listing).filter_by(article_no="a1").one().complex_id
    lid = db.query(Listing).filter_by(article_no="a1").one().id
    db.close()
    r = client.post(f"/complexes/{cid}/eval", data={"transport": "Soso", "homogeneity": "Good", "school": "Bad",
                                                     "households": "Good", "keep": "on", "priority": "2", "note": "내 평가"})
    assert r.status_code == 200
    assert "<span class=\"prio\">2</span>" in client.get("/matrix").text
    r = client.post("/visits", data={"complex_id": cid, "listing_id": str(lid), "visit_at": "2026-11-21T14:00"})
    assert r.status_code == 200
    assert "토요일 14시" in client.get("/visits").text
    assert "2026-11-19" in client.get("/visits").text  # 목요일 전화
    assert client.post("/settings", data={"cash": "40000", "loan": "40000", "tolerance": "3000", "interior_coef": "1.2",
                                          "area_max": "200", "loan_years": "30", "interior_table": "{bad json"}).status_code == 200
    # 교차 출처 POST 차단
    assert client.post("/settings", data={}, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/settings", data={}, headers={"origin": "null"}).status_code == 403


def test_routine_api(client):
    assert client.get("/api/v1/digest").status_code == 401
    H = {"Authorization": "Bearer test-token"}
    assert client.get("/api/v1/digest?date=2026-11-20", headers=H).json()["status"] == "not_ready"
    d = client.get(f"/api/v1/digest?date={TODAY}", headers=H).json()
    assert d["status"] == "ready" and d["budget"]["price_lo"] > 0
    assert len(d["regions"]) == 2
    assert any(c["name"] == "두산" for c in d["regions"][1]["candidates"])
    assert any(li["prev_price"] == 88000 for li in d["price_changes"])

    db = SessionLocal()
    lotte = db.query(Listing).filter_by(article_no="a1").one().complex_id
    dusan = db.query(Listing).filter_by(article_no="b1").one().complex_id
    b1 = db.query(Listing).filter_by(article_no="b1").one().id
    db.close()

    r = client.post("/api/v1/analysis", headers=H, json={"date": str(TODAY), "items": [
        {"region_id": 0, "markdown": "## 요약\n- 호가 인하", "highlights": ["벽적골롯데 1,500 인하"]}]})
    assert r.json() == {"saved": 1}
    r = client.post("/api/v1/analysis", headers=H, json={"date": str(TODAY), "items": [{"region_id": 0, "markdown": "수정"}]})
    assert r.json() == {"saved": 1}  # upsert
    assert client.post("/api/v1/analysis", headers=H, json={"date": str(TODAY), "items": [{"region_id": 999, "markdown": "x"}]}).status_code == 422
    r = client.post("/api/v1/news", headers=H, json={"date": str(TODAY), "items": [
        {"title": "기준금리 동결", "url": "https://news.example.com/1", "impact": "금리", "sentiment": "중립", "summary": "..."}]})
    assert r.json() == {"saved": 1}
    r = client.post("/api/v1/evals", headers=H, json={"items": [
        {"complex_id": lotte, "transport": "Bad", "note": "덮어쓰기 시도"},
        {"complex_id": dusan, "transport": "Soso", "homogeneity": "Good", "note": "역까지 도보 10분"}]})
    assert r.json() == {"applied": 1, "suggested_only": 1}
    r = client.post("/api/v1/listings/repair", headers=H, json={"items": [{"listing_id": b1, "repair_grade": "미수리"}]})
    assert r.json()["saved"] == 1

    db = SessionLocal()
    assert db.get(ComplexEval, lotte).transport == "Soso"  # 사용자 평가 유지
    assert db.get(ComplexEval, lotte).claude_suggestion["transport"] == "Bad"
    assert db.get(ComplexEval, dusan).source == "claude"
    db.close()
    login(client)
    assert "기준금리 동결" in client.get("/news").text
