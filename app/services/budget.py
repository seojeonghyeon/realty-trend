"""PDF 1단계 '예산 산정' + 부대비용/대출 한도 계산. 금액 단위: 만원.

PDF 방식: 총액(현금+대출) ± 3천만원 → 각 경계에서 '매매가 + 취득세 + 중개보수 + 기타' 가
경계를 넘지 않는 최대 매매가를 구해 최종 필터 범위로 쓴다.
(PDF 는 부대비용을 2,300만원 고정으로 뺐지만 여기서는 가격별로 정확히 계산한다.)

※ 세율·요율은 2026년 기준 일반 규정을 단순화한 추정치이며 실제 세액은 다를 수 있다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

EOK = 10000  # 1억 = 10,000만원


@dataclass
class Costs:
    acquisition_tax: float
    education_tax: float
    rural_tax: float
    brokerage: float
    extra: float

    @property
    def total(self) -> float:
        return self.acquisition_tax + self.education_tax + self.rural_tax + self.brokerage + self.extra

    def as_dict(self) -> dict:
        d = asdict(self)
        d["total"] = round(self.total, 1)
        return {k: round(v, 1) for k, v in d.items()}


def acquisition_tax_rate(price: int, house_count_after: int = 1, regulated: bool = False) -> float:
    """취득세율(소수). 1주택 6~9억 구간은 (가격억×2/3−3)% 를 소수 넷째 자리까지 반올림."""
    heavy = (regulated and house_count_after == 2) or (house_count_after == 3 and not regulated)
    very_heavy = (regulated and house_count_after >= 3) or house_count_after >= 4
    if very_heavy:
        return 0.12
    if heavy:
        return 0.08
    if price <= 6 * EOK:
        return 0.01
    if price <= 9 * EOK:
        return round((price / EOK * 2 / 3 - 3) / 100, 4)
    return 0.03


def brokerage_rate(price: int) -> tuple[float, float | None]:
    """매매 중개보수 상한요율과 한도액 (2021.10 개정 기준)."""
    if price < 5000:
        return 0.006, 25
    if price < 2 * EOK:
        return 0.005, 80
    if price < 9 * EOK:
        return 0.004, None
    if price < 12 * EOK:
        return 0.005, None
    if price < 15 * EOK:
        return 0.006, None
    return 0.007, None


def acquisition_costs(
    price: int,
    exclusive_m2: float = 84.0,
    *,
    house_count_after: int = 1,
    regulated: bool = False,
    first_home: bool = False,
    brokerage_vat: bool = False,
    extra: int = 0,
) -> Costs:
    rate = acquisition_tax_rate(price, house_count_after, regulated)
    tax = price * rate
    if first_home and house_count_after == 1 and price <= 12 * EOK:
        tax = max(0.0, tax - 200)  # 생애최초 감면 (최대 200만원)
    if rate >= 0.08:
        edu = price * 0.004
        rural = price * (0.006 if rate == 0.08 else 0.01) if exclusive_m2 > 85 else 0.0
    else:
        edu = price * rate * 0.1
        rural = price * 0.002 if exclusive_m2 > 85 else 0.0
    b_rate, b_cap = brokerage_rate(price)
    brokerage = price * b_rate
    if b_cap is not None:
        brokerage = min(brokerage, b_cap)
    if brokerage_vat:
        brokerage *= 1.1
    return Costs(tax, edu, rural, brokerage, float(extra))


def max_price_within(bound: int, step: int = 10, **cost_kwargs) -> int:
    """price + costs(price) <= bound 를 만족하는 최대 price (step 만원 단위)."""
    lo, hi = 0, bound
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if mid + acquisition_costs(mid, **cost_kwargs).total <= bound:
            lo = mid
        else:
            hi = mid - 1
    return lo // step * step


@dataclass
class BudgetRange:
    total: int
    bound_lo: int
    bound_hi: int
    price_lo: int
    price_hi: int
    costs_at_hi: dict

    def contains(self, price: int | None) -> bool:
        return price is not None and self.price_lo <= price <= self.price_hi

    def as_dict(self) -> dict:
        return asdict(self)


def budget_range(
    cash: int,
    loan: int,
    tolerance: int = 3000,
    *,
    extra_costs: int = 0,
    house_count_after: int = 1,
    regulated: bool = False,
    first_home: bool = False,
    brokerage_vat: bool = False,
    exclusive_m2: float = 84.0,
) -> BudgetRange:
    total = cash + loan
    kw = dict(
        house_count_after=house_count_after,
        regulated=regulated,
        first_home=first_home,
        brokerage_vat=brokerage_vat,
        extra=extra_costs,
        exclusive_m2=exclusive_m2,
    )
    lo_b, hi_b = total - tolerance, total + tolerance
    p_lo = max_price_within(lo_b, **kw)
    p_hi = max_price_within(hi_b, **kw)
    return BudgetRange(total, lo_b, hi_b, p_lo, p_hi, acquisition_costs(p_hi, **kw).as_dict())


def range_from_profile(profile) -> BudgetRange:
    return budget_range(
        profile.cash,
        profile.loan,
        profile.tolerance,
        extra_costs=profile.extra_costs,
        house_count_after=profile.house_count_after,
        first_home=profile.first_home,
        brokerage_vat=profile.brokerage_vat,
    )


# ---------------------------------------------------------------- 대출 한도 (A4)
def annuity_principal(annual_payment: float, rate_pct: float, years: int) -> float:
    """원리금균등상환 기준, 연간 상환액으로 빌릴 수 있는 원금."""
    if annual_payment <= 0:
        return 0.0
    r = rate_pct / 100 / 12
    n = years * 12
    monthly = annual_payment / 12
    if r == 0:
        return monthly * n
    return monthly * (1 - (1 + r) ** -n) / r


def loan_limit(
    price: int,
    *,
    annual_income: int,
    other_debt_payment: int = 0,
    loan_rate: float = 4.0,
    stress_rate: float = 1.5,
    years: int = 30,
    dsr_limit: float = 40.0,
    ltv_pct: float = 70.0,
    cap: int = 0,
) -> dict:
    """LTV·스트레스 DSR·한도 중 가장 작은 값을 추정 한도로 본다."""
    by_ltv = price * ltv_pct / 100
    by_dsr = None
    if annual_income > 0:
        capacity = annual_income * dsr_limit / 100 - other_debt_payment
        by_dsr = annuity_principal(capacity, loan_rate + stress_rate, years)
    candidates = [by_ltv] + ([by_dsr] if by_dsr is not None else []) + ([cap] if cap else [])
    limit = min(candidates)
    binding = "LTV" if limit == by_ltv else ("DSR" if by_dsr is not None and limit == by_dsr else "한도")
    return {
        "by_ltv": round(by_ltv),
        "by_dsr": round(by_dsr) if by_dsr is not None else None,
        "cap": cap or None,
        "limit": round(limit),
        "binding": binding,
    }


def fmt_eok(manwon: int | float | None, digits: int = 2) -> str:
    """89000 → '8.9억'."""
    if manwon is None:
        return "-"
    v = round(manwon / EOK, digits)
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return f"{s}억"
