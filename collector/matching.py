"""국토부 실거래(aptSeq) ↔ 네이버 단지 매칭.

같은 법정동 안에서 (지번 일치, 이름 유사도, 준공연도) 점수로 매칭하고,
확신이 낮으면 매칭하지 않는다 → 관리 화면에서 수동 연결.
"""
from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Complex, ComplexArea, Trade, area_group
from collector.util import normalize_name

THRESHOLD = 0.75


def _score(c: Complex, apt_name: str, jibun: str, built_year: int | None) -> float:
    s = SequenceMatcher(None, normalize_name(c.name), normalize_name(apt_name)).ratio()
    if jibun and c.jibun and jibun.split("-")[0] == c.jibun.split("-")[0]:
        s += 0.35 if jibun == c.jibun else 0.2
    if built_year and c.built_year:
        diff = abs(built_year - c.built_year)
        s += 0.1 if diff <= 1 else (-0.2 if diff > 3 else 0)
    return s


def match_trades(db: Session, dong_codes: list[str] | None = None) -> dict:
    """complex_id 가 비어 있는 거래를 단지에 연결. 반환: 통계."""
    q = select(Trade).where(Trade.complex_id.is_(None))
    if dong_codes:
        q = q.where(Trade.lawd_cd.in_({d[:5] for d in dong_codes}))
    trades = db.scalars(q).all()
    if not trades:
        return {"linked": 0, "unmatched": 0}

    complexes = db.scalars(select(Complex)).all()
    by_seq = {c.molit_apt_seq: c for c in complexes if c.molit_apt_seq}
    by_dong: dict[str, list[Complex]] = defaultdict(list)
    for c in complexes:
        by_dong[c.dong_code].append(c)

    # 매매 거래로 (aptSeq → 이름/지번/연도) 사전 구성 → 전월세(aptSeq 없을 수 있음) 이름 매칭에도 사용
    groups: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    for t in trades:
        key = t.apt_seq or f"{t.lawd_cd}|{normalize_name(t.apt_name)}|{t.jibun}"
        groups[(key, t.dong_code)].append(t)

    linked = unmatched = 0
    for (key, dong_code), ts in groups.items():
        sample = ts[0]
        target = by_seq.get(sample.apt_seq) if sample.apt_seq else None
        if target is None:
            candidates = by_dong.get(dong_code, []) if dong_code else [
                c for c in complexes if c.dong_code.startswith(sample.lawd_cd)
            ]
            best, best_s = None, 0.0
            for c in candidates:
                s = _score(c, sample.apt_name, sample.jibun, sample.built_year)
                if s > best_s:
                    best, best_s = c, s
            if best is not None and best_s >= THRESHOLD:
                target = best
                if sample.apt_seq and not best.molit_apt_seq:
                    best.molit_apt_seq = sample.apt_seq
                    by_seq[sample.apt_seq] = best
        if target is None:
            unmatched += len(ts)
            continue
        groups_of_target = _area_groups(db, target.id)
        for t in ts:
            t.complex_id = target.id
            t.area_group = _nearest_group(t.exclusive_m2, groups_of_target)
            linked += 1
    db.flush()
    return {"linked": linked, "unmatched": unmatched}


def _area_groups(db: Session, complex_id: int) -> list[float]:
    return [a.exclusive_m2 for a in db.scalars(select(ComplexArea).where(ComplexArea.complex_id == complex_id))]


def _nearest_group(excl: float, known: list[float]) -> int:
    """실거래 전용면적을 네이버 평형 그룹에 맞춘다 (±1.5m² 이내면 해당 그룹)."""
    if known:
        best = min(known, key=lambda k: abs(k - excl))
        if abs(best - excl) <= 1.5:
            return area_group(best)
    return area_group(excl)


def link_manually(db: Session, complex_id: int, apt_seq: str) -> int:
    c = db.get(Complex, complex_id)
    c.molit_apt_seq = apt_seq
    res = db.execute(update(Trade).where(Trade.apt_seq == apt_seq).values(complex_id=complex_id))
    return res.rowcount or 0
