import datetime as dt

from app.models import Listing
from app.services import interior
from app.services.budget import budget_range
from app.services.listing_quality import compute_flags, dedupe, parse_floor
from app.services.matrix import bucket, build, cell_label
from app.services.metrics import SurveyRow
from app.services.region_resolver import resolve_codes
from collector.util import month_list, normalize_name, parse_price


def test_interior_estimates():
    full = interior.estimate("미수리", 32)
    assert full["total"] == 4092  # 32평 열 합계 3,410만 × 1.2
    assert interior.estimate("특올수리", 32)["total"] == 0
    assert interior.estimate("올수리", 32)["items"] == {"샷시": 1560}
    part = interior.estimate("부분수리", 24)
    assert "도배" not in part["items"] and "샷시" in part["items"]
    # 24~32 보간
    assert interior.item_cost([1000, 1300], 28) == 1150


def test_classify_repair():
    assert interior.classify_repair("3년전 올확장 특올수리 조망좋은 라인") == "특올수리"
    assert interior.classify_repair("로열동층 거실 방2확장 앞뒤시원한전망 올수리되어 깨끗") == "올수리"
    assert interior.classify_repair("정남향 로열동층 방1확장 부분수리 예쁜집") == "부분수리"
    assert interior.classify_repair("기본상태 올수리 필요") == "미수리"
    assert interior.classify_repair("정상입주 밝은집 아주깨끗") == ""


def test_parse_price_and_floor():
    assert parse_price("8억 9,000") == 89000
    assert parse_price("9억") == 90000
    assert parse_price("9,500") == 9500
    assert parse_price("12억 5,000") == 125000
    assert parse_price("") is None
    assert parse_floor("6/20") == (6, 20)
    assert parse_floor("저/15") == (None, 15)
    assert parse_floor("B1/10") == (-1, 10)


def test_region_resolution_mixed_levels():
    codes = [
        "4100000000", "4111000000", "4111100000", "4111112900", "4111700000", "4111710300", "4111710400",
        "4113000000", "4113100000", "4113110100", "4113110200", "4131000000", "4131010100", "4131010200",
        "4131010121",  # 리 단위는 제외
    ]
    r = resolve_codes(
        [(1, "sigungu", "4111000000"), (2, "dong", "4113110100"), (3, "sigungu", "4131000000"), (4, "sigungu", "4111700000")],
        codes,
    )
    assert r.lawd_cds == {"41111", "41117", "41131", "41310"}
    assert "4113110200" not in r.dong_codes  # 동 단위 등록은 그 동만
    assert "4131010121" not in r.dong_codes
    assert r.dong_regions["4111710300"] == {1, 4}  # 겹쳐도 한 번만 수집
    sido = resolve_codes([(9, "sido", "4100000000")], codes)
    assert len(sido.dong_codes) == 7


def test_month_list_and_names():
    assert month_list(dt.date(2026, 2, 10), 3) == ["202602", "202601", "202512"]
    assert normalize_name("면목현대(1차) 아파트") == normalize_name("면목현대")


def _row(cid, name, price, dong="신흥동", year=1993, ag=71):
    return SurveyRow(cid, name, "x", dong, year, 570, ag, "", 82.0, 71.3, 120, 3, 1, "계단식", None, None, price, price, 1,
                     None, None, None, "", None, None, None, None, None, None)


def test_matrix_bucketing():
    br = budget_range(44300, 45000, 3000)
    rows = [("신흥동", _row(1, "두산", 84300)), ("금광동", _row(2, "래미안금광", 88000, "금광동", 2006, 59)),
            ("금광동", _row(3, "너무쌈", 60000, "금광동"))]
    m = build(rows, br, below=10000)
    assert bucket(84300) == 84000
    assert m.columns == ["금광동", "신흥동"]
    assert m.get(84000, "신흥동")[0].label == "두산(93^/71/8.43)"
    assert all(e.complex_id != 3 for v in m.cells.values() for e in v)  # 범위 밖 제외
    assert cell_label(_row(2, "래미안금광", 88000, year=2006, ag=59)) == "래미안금광(06^/59/8.8)"


def test_dedupe_and_flags():
    today = dt.date(2026, 11, 14)
    mk = lambda no, realtor, conf: Listing(article_no=no, complex_id=1, trade_type="sale", area_group=84, building="102동",
                                           floor_text="6/20", direction="남동향", price=89000, status="active",
                                           realtor=realtor, realtor_count=1, confirmed_at=conf, flags=[])
    a, b, c = mk("1", "A", dt.date(2026, 11, 10)), mk("2", "B", dt.date(2026, 11, 12)), mk("3", "C", dt.date(2026, 9, 1))
    c.price = 85000
    assert dedupe([a, b, c]) == 1
    assert b.is_primary and not a.is_primary and b.dup_count == 2 and c.is_primary
    c.floor, c.total_floor = parse_floor("2/20")
    flags = compute_flags(c, today, {(1, 84): 86000})
    assert set(flags) == {"확인오래됨", "실거래대비저가", "저층"}
