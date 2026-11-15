from app.services.budget import acquisition_costs, acquisition_tax_rate, budget_range, fmt_eok, loan_limit, max_price_within


def test_tax_matches_pdf_screenshot():
    # PDF p6: 비산롯데캐슬 7억9,000 → 취득세 1,793만3천, 지방교육세 179만3천3백, 중개보수 최대 316만
    c = acquisition_costs(79000, 84)
    assert round(c.acquisition_tax, 1) == 1793.3
    assert round(c.education_tax, 2) == 179.33
    assert c.rural_tax == 0
    assert c.brokerage == 316


def test_tax_brackets():
    assert acquisition_tax_rate(60000) == 0.01
    assert acquisition_tax_rate(90000) == 0.03
    assert acquisition_tax_rate(100000) == 0.03
    assert acquisition_tax_rate(80000, house_count_after=2, regulated=True) == 0.08
    assert acquisition_tax_rate(80000, house_count_after=2, regulated=False) == acquisition_tax_rate(80000)


def test_rural_tax_over_85():
    assert acquisition_costs(80000, 101).rural_tax == 160


def test_budget_range_pdf_example():
    # PDF: 4.43억 + 4.5억 = 8.93억, ±3천 → 부대비용 빼면 대략 8.4억 ~ 9억 (PDF는 2,300만 고정 차감)
    br = budget_range(44300, 45000, 3000)
    assert br.total == 89300
    assert 83000 <= br.price_lo <= 84500
    assert 88500 <= br.price_hi <= 90000
    # 경계 조건 만족
    assert br.price_hi + acquisition_costs(br.price_hi).total <= br.bound_hi
    assert br.contains(86000) and not br.contains(95000)


def test_max_price_monotonic():
    assert max_price_within(50000) < max_price_within(60000)


def test_loan_limit_binding():
    r = loan_limit(90000, annual_income=8000, loan_rate=4, stress_rate=1.5, years=30, dsr_limit=40, ltv_pct=70)
    assert r["binding"] == "DSR" and r["limit"] < 63000
    r2 = loan_limit(90000, annual_income=0, ltv_pct=40)
    assert r2["limit"] == 36000 and r2["binding"] == "LTV"


def test_fmt():
    assert fmt_eok(89000) == "8.9억"
    assert fmt_eok(84300) == "8.43억"
    assert fmt_eok(None) == "-"
