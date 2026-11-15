"""화면/다이제스트에서 공통으로 쓰는 조회 헬퍼."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import BudgetProfile, Complex, ComplexEval, JobRun, LegalDong, WatchRegion
from app.services.budget import BudgetRange, range_from_profile
from app.services.metrics import SurveyRow, survey_rows
from app.services.region_resolver import dong_name_map, resolve_codes


def get_profile(db: Session) -> BudgetProfile:
    p = db.get(BudgetProfile, 1)
    if p is None:
        p = BudgetProfile(id=1)
        db.add(p)
        db.commit()
    return p


def get_budget(db: Session) -> tuple[BudgetProfile, BudgetRange]:
    p = get_profile(db)
    return p, range_from_profile(p)


def active_codes(db: Session) -> list[str]:
    return db.scalars(select(LegalDong.code).where(LegalDong.active.is_(True))).all()


def region_dongs(db: Session, region: WatchRegion, codes: list[str] | None = None) -> list[str]:
    codes = codes if codes is not None else active_codes(db)
    return resolve_codes([(region.id, region.level, region.code)], codes).dong_codes


def region_complexes(db: Session, dongs: list[str]) -> list[Complex]:
    if not dongs:
        return []
    return db.scalars(
        select(Complex)
        .where(Complex.dong_code.in_(dongs))
        .options(selectinload(Complex.areas), selectinload(Complex.eval))
        .order_by(Complex.name)
    ).all()


def data_date(db: Session) -> dt.date:
    """가장 최근 수집일 (없으면 오늘)."""
    d = db.scalar(select(func.max(JobRun.run_date)).where(JobRun.job == "daily", JobRun.status.in_(["ok", "partial"])))
    return d or dt.date.today()


def region_rows(db: Session, region: WatchRegion, profile: BudgetProfile, date: dt.date, codes=None):
    dongs = region_dongs(db, region, codes)
    complexes = region_complexes(db, dongs)
    names = dong_name_map(db, {c.dong_code for c in complexes})
    rows = survey_rows(db, complexes, date, names, profile.area_min, profile.area_max)
    if profile.min_households:
        rows = [r for r in rows if (r.households or 0) >= profile.min_households]
    return complexes, rows


def eval_map(complexes: list[Complex]) -> dict[int, ComplexEval]:
    return {c.id: c.eval for c in complexes if c.eval}


def all_regions(db: Session, active_only: bool = True) -> list[WatchRegion]:
    q = select(WatchRegion).order_by(WatchRegion.id)
    if active_only:
        q = q.where(WatchRegion.active.is_(True))
    return db.scalars(q).all()


def is_regulated(code: str, prefixes: list[str] | None) -> bool:
    return any(code.startswith(p) for p in (prefixes or []) if p)


def rows_by_region(db: Session, profile, date) -> list[tuple[WatchRegion, list[Complex], list[SurveyRow]]]:
    codes = active_codes(db)
    out = []
    for r in all_regions(db):
        complexes, rows = region_rows(db, r, profile, date, codes)
        out.append((r, complexes, rows))
    return out
