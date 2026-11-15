"""PDF 4단계 인테리어 비용 추정 (p34 비용표 × 계수).

수리등급(매물 상태) → 매수 후 추가로 필요한 공사 항목:
- 특올수리 : 샷시 포함 내부 올수리 완료 → 추가 공사 없음
- 올수리   : 샷시 제외 올수리 → 샷시만
- 부분수리 : 일부만/예전에 수리 → 도배·장판·조명·페인트 외 전부
- 미수리   : 수리 안 한 기본 상태 → 전부
"""
from __future__ import annotations

import re

from app.models import DEFAULT_INTERIOR_TABLE

GRADES = ["특올수리", "올수리", "부분수리", "미수리"]

COSMETIC = {"도배", "장판", "조명", "페인트"}


def needed_items(grade: str, table: dict) -> list[str]:
    items = list(table.keys())
    if grade == "특올수리":
        return []
    if grade == "올수리":
        return [i for i in items if i == "샷시"]
    if grade == "부분수리":
        return [i for i in items if i not in COSMETIC]
    return items  # 미수리 또는 모름 → 보수적으로 전부


def item_cost(values: list[float], pyeong: float) -> float:
    """24평/32평 값을 선형 보간 (범위 밖은 평수 비례로 외삽, 최소 0)."""
    a, b = values
    if pyeong <= 0:
        pyeong = 32
    return max(0.0, a + (b - a) * (pyeong - 24) / 8)


def estimate(grade: str, supply_pyeong: float, table: dict | None = None, coef: float = 1.2) -> dict:
    table = table or DEFAULT_INTERIOR_TABLE
    items = needed_items(grade, table)
    breakdown = {i: round(item_cost(table[i], supply_pyeong) * coef) for i in items}
    return {"grade": grade or "모름", "items": breakdown, "total": sum(breakdown.values())}


# ---------------------------------------------------------------- 매물 설명 → 수리등급 (규칙 기반 1차 분류)
_RULES: list[tuple[str, re.Pattern]] = [
    ("특올수리", re.compile(r"특올|샷시\s*(포함|교체|까지)|섀시\s*(포함|교체)|확장\s*올수리.*샷시|풀\s*리모델링|올리모델링")),
    ("올수리", re.compile(r"올\s*수리|전체\s*수리|올\s*리모|인테리어\s*완료|리모델링\s*완료|전체\s*인테리어")),
    ("부분수리", re.compile(r"부분\s*수리|일부\s*수리|내부\s*수리|도배\s*장판|화장실\s*수리|주방\s*수리|싱크대\s*교체|수리된|수리됨")),
    ("미수리", re.compile(r"기본\s*(상태|형)|수리\s*(필요|요망|안\s*됨|안된)|올\s*수리\s*필요|원상태|입주\s*당시")),
]


def classify_repair(text: str) -> str:
    """키워드로 판단 못 하면 '' (→ Claude routine 이 2차 분류)."""
    t = text or ""
    # '수리 필요' 류가 먼저 매칭되도록 미수리 우선 확인
    if _RULES[3][1].search(t):
        return "미수리"
    for grade, pat in _RULES[:3]:
        if pat.search(t):
            return grade
    return ""
