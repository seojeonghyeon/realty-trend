"""PDF 2·3단계 매트릭스.

- 지역 매트릭스: 행 = 가격대(0.1억), 열 = 동, 셀 = '단지명(연식^/전용/가격)'
- 통합 우선순위 매트릭스: 행 = 가격대, 열 = 관심 지역(그룹), 탈락하지 않은 예산 범위 단지만
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.budget import EOK, BudgetRange
from app.services.metrics import SurveyRow

STEP = 1000  # 0.1억


def bucket(price: int) -> int:
    return price // STEP * STEP


def cell_label(r: SurveyRow) -> str:
    yy = f"{r.built_year % 100:02d}^" if r.built_year else "?"
    price = r.price or 0
    p = f"{price / EOK:.2f}".rstrip("0").rstrip(".")
    return f"{r.name}({yy}/{r.area_group}/{p})"


@dataclass
class Entry:
    label: str
    complex_id: int
    area_group: int
    price: int
    in_budget: bool
    excluded: bool = False
    exclude_reason: str = ""
    keep: bool = False
    priority: int | None = None
    flags: list[str] = field(default_factory=list)


@dataclass
class Matrix:
    columns: list[str]
    rows: list[int]  # bucket 값 (내림차순)
    cells: dict[tuple[int, str], list[Entry]]
    budget_lo: int
    budget_hi: int

    def get(self, row: int, col: str) -> list[Entry]:
        return self.cells.get((row, col), [])

    def row_in_budget(self, row: int) -> bool:
        return bucket(self.budget_lo) <= row <= bucket(self.budget_hi)


def build(
    items: list[tuple[str, SurveyRow]],
    budget: BudgetRange,
    evals: dict[int, object] | None = None,
    below: int = 10000,
    above: int = 0,
    columns: list[str] | None = None,
    only_survivors: bool = False,
    min_households: int = 0,
) -> Matrix:
    """items: (열 이름, 행). below/above: 예산 범위 밖으로 보여줄 폭(만원)."""
    evals = evals or {}
    lo = budget.price_lo - below
    hi = budget.price_hi + above
    cells: dict[tuple[int, str], list[Entry]] = {}
    cols_seen: list[str] = []
    for col, r in items:
        price = r.price
        if price is None or not (lo <= price <= hi):
            continue
        if min_households and (r.households or 0) < min_households:
            continue
        ev = evals.get(r.complex_id)
        excluded = bool(ev and ev.excluded)
        in_budget = budget.contains(price)
        if only_survivors and (excluded or not in_budget):
            continue
        e = Entry(
            label=cell_label(r),
            complex_id=r.complex_id,
            area_group=r.area_group,
            price=price,
            in_budget=in_budget,
            excluded=excluded,
            exclude_reason=(ev.exclude_reason if ev else "") or "",
            keep=bool(ev and ev.keep),
            priority=ev.priority if ev else None,
        )
        cells.setdefault((bucket(price), col), []).append(e)
        if col not in cols_seen:
            cols_seen.append(col)
    for v in cells.values():
        v.sort(key=lambda e: (-e.price, e.label))
    rows = sorted({k[0] for k in cells}, reverse=True)
    if columns is None:
        columns = sorted(cols_seen)
    else:
        columns = [c for c in columns if c in cols_seen] + [c for c in cols_seen if c not in columns]
    return Matrix(columns, rows, cells, budget.price_lo, budget.price_hi)
