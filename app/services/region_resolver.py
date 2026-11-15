"""관심 지역(시도/시군구/읍면동 혼합) → 수집 단위로 전개.

- 국토부 실거래 API 호출 단위: LAWD_CD (법정동코드 앞 5자리, 일반구가 있는 시는 구 단위)
- 네이버 단지 목록 호출 단위: 읍면동 법정동코드(10자리, cortarNo)

일반구가 있는 시(예: 수원시 4111000000)를 등록하면 하위 구(41111, 41113, ...)의 동이
모두 포함된다. 여러 관심 지역이 겹쳐도 동은 한 번만 수집한다.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LegalDong, WatchRegion


def level_of(code: str) -> str:
    if code[2:] == "00000000":
        return "sido"
    if code[5:] == "00000":
        return "sigungu"
    return "dong"


def is_eupmyeondong(code: str) -> bool:
    """리(ri) 제외 읍면동 코드인가."""
    return code[5:8] != "000" and code[8:] == "00"


def prefix_for(level: str, code: str) -> str:
    if level == "sido":
        return code[:2]
    if level == "sigungu":
        # 일반구가 있는 시(xxxx0)는 앞 4자리로 하위 구까지 포함
        return code[:4] if code[4] == "0" else code[:5]
    return code


@dataclass
class Resolved:
    lawd_cds: set[str] = field(default_factory=set)
    dong_regions: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))

    @property
    def dong_codes(self) -> list[str]:
        return sorted(self.dong_regions)


def resolve_codes(regions: Iterable[tuple[int, str, str]], all_codes: Iterable[str]) -> Resolved:
    """regions: (region_id, level, code). all_codes: 활성 법정동코드 전체."""
    dongs = [c for c in all_codes if is_eupmyeondong(c)]
    out = Resolved()
    for rid, level, code in regions:
        if level == "dong":
            targets = [code] if is_eupmyeondong(code) else [code[:8] + "00"]
        else:
            p = prefix_for(level, code)
            targets = [d for d in dongs if d.startswith(p)]
        for d in targets:
            out.dong_regions[d].add(rid)
            out.lawd_cds.add(d[:5])
    return out


def resolve(db: Session, active_only: bool = True) -> Resolved:
    q = select(WatchRegion)
    if active_only:
        q = q.where(WatchRegion.active.is_(True))
    regions = [(r.id, r.level, r.code) for r in db.scalars(q)]
    if not regions:
        return Resolved()
    codes = db.scalars(select(LegalDong.code).where(LegalDong.active.is_(True))).all()
    return resolve_codes(regions, codes)


def dong_name_map(db: Session, codes: Iterable[str]) -> dict[str, str]:
    codes = list(codes)
    if not codes:
        return {}
    rows = db.scalars(select(LegalDong).where(LegalDong.code.in_(codes))).all()
    return {r.code: (r.eupmyeondong or r.name.split()[-1]) for r in rows}
