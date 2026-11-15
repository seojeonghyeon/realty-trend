"""PDF 2단계 '전수조사 표' 지표 계산.

행 단위 = (단지, 유사평형 그룹[전용 m² 정수부]).
매매가 = 네이버 매매 호가 최저(중복 제거된 대표 매물 기준), 없으면 최근 실거래.
전세가 = 전세 호가 최저, 없으면 최근 6개월 전세 실거래 평균.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import Complex, ComplexArea, ComplexDaily, Listing, Trade


@dataclass
class SurveyRow:
    complex_id: int
    name: str
    dong_code: str
    dong: str
    built_year: int | None
    households: int | None
    area_group: int
    area_name: str
    supply_m2: float
    exclusive_m2: float
    type_households: int | None
    rooms: int | None
    baths: int | None
    entrance: str
    peak: int | None
    peak_date: str | None
    ask_min: int | None
    ask_median: int | None
    listing_cnt: int
    last_trade: int | None
    last_trade_date: str | None
    jeonse: int | None
    jeonse_src: str
    invest: int | None
    jeonse_ratio: float | None
    vs_peak: float | None
    pyeong_price: int | None
    prev_ask_min: int | None
    prev_listing_cnt: int | None

    @property
    def price(self) -> int | None:
        """매트릭스/필터에 쓰는 대표 가격."""
        return self.ask_min if self.ask_min is not None else self.last_trade

    @property
    def ask_change(self) -> int | None:
        if self.ask_min is None or self.prev_ask_min is None:
            return None
        return self.ask_min - self.prev_ask_min

    def as_dict(self) -> dict:
        d = asdict(self)
        d["price"] = self.price
        d["ask_change"] = self.ask_change
        return d


def _median(vals: list[int]) -> int | None:
    return int(statistics.median(vals)) if vals else None


def listing_stats(listings: list[Listing]) -> dict[tuple[int, int], dict]:
    """대표 매물만으로 (complex, area_group)별 매매/전세 호가 통계."""
    sale: dict[tuple[int, int], list[int]] = defaultdict(list)
    jeonse: dict[tuple[int, int], list[int]] = defaultdict(list)
    for li in listings:
        if li.status != "active" or not li.is_primary:
            continue
        key = (li.complex_id, li.area_group)
        (sale if li.trade_type == "sale" else jeonse)[key].append(li.price)
    out = {}
    for key in set(sale) | set(jeonse):
        s, j = sale.get(key, []), jeonse.get(key, [])
        out[key] = {
            "ask_min": min(s) if s else None,
            "ask_median": _median(s),
            "listing_cnt": len(s),
            "jeonse_min": min(j) if j else None,
            "jeonse_cnt": len(j),
        }
    return out


def survey_rows(
    db: Session,
    complexes: list[Complex],
    date: dt.date,
    dong_names: dict[str, str] | None = None,
    area_min: float = 0,
    area_max: float = 1000,
) -> list[SurveyRow]:
    if not complexes:
        return []
    ids = [c.id for c in complexes]
    dong_names = dong_names or {}

    listings = db.scalars(
        select(Listing).where(Listing.complex_id.in_(ids), Listing.status == "active")
    ).all()
    lstats = listing_stats(listings)

    # 전고점
    peaks: dict[tuple[int, int], tuple[int, dt.date]] = {}
    for cid, ag, price, d in db.execute(
        select(Trade.complex_id, Trade.area_group, Trade.price, Trade.deal_date).where(
            Trade.complex_id.in_(ids), Trade.kind == "sale", Trade.cancelled.is_(False)
        )
    ):
        k = (cid, ag)
        if k not in peaks or price > peaks[k][0]:
            peaks[k] = (price, d)

    # 최근 매매 실거래
    last_trade: dict[tuple[int, int], tuple[int, dt.date]] = {}
    for cid, ag, price, d in db.execute(
        select(Trade.complex_id, Trade.area_group, Trade.price, Trade.deal_date)
        .where(Trade.complex_id.in_(ids), Trade.kind == "sale", Trade.cancelled.is_(False))
        .order_by(Trade.deal_date)
    ):
        last_trade[(cid, ag)] = (price, d)

    # 최근 6개월 전세 실거래 평균
    since = date - dt.timedelta(days=183)
    jeonse_trade: dict[tuple[int, int], int] = {}
    for cid, ag, avg in db.execute(
        select(Trade.complex_id, Trade.area_group, func.avg(Trade.price))
        .where(Trade.complex_id.in_(ids), Trade.kind == "jeonse", Trade.deal_date >= since)
        .group_by(Trade.complex_id, Trade.area_group)
    ):
        jeonse_trade[(cid, ag)] = int(avg)

    # 전일 스냅샷
    prev: dict[tuple[int, int], ComplexDaily] = {}
    prev_date = db.scalar(
        select(func.max(ComplexDaily.date)).where(ComplexDaily.complex_id.in_(ids), ComplexDaily.date < date)
    )
    if prev_date:
        for snap in db.scalars(
            select(ComplexDaily).where(ComplexDaily.complex_id.in_(ids), ComplexDaily.date == prev_date)
        ):
            prev[(snap.complex_id, snap.area_group)] = snap

    rows: list[SurveyRow] = []
    for c in complexes:
        groups: dict[int, list[ComplexArea]] = defaultdict(list)
        for a in c.areas:
            groups[a.area_group].append(a)
        # 면적 정보가 없더라도 매물/거래가 있으면 행 생성
        for (cid, ag) in list(lstats) + list(last_trade):
            if cid == c.id and ag not in groups:
                groups[ag] = []
        for ag, areas in sorted(groups.items()):
            if ag == 0 or not (area_min <= ag <= area_max):
                continue
            rep = max(areas, key=lambda a: a.households or 0) if areas else None
            k = (c.id, ag)
            ls = lstats.get(k, {})
            ask_min = ls.get("ask_min")
            lt = last_trade.get(k)
            price = ask_min if ask_min is not None else (lt[0] if lt else None)
            if ls.get("jeonse_min") is not None:
                jeonse, jsrc = ls["jeonse_min"], "호가"
            elif k in jeonse_trade:
                jeonse, jsrc = jeonse_trade[k], "실거래"
            else:
                jeonse, jsrc = None, ""
            peak = peaks.get(k)
            supply = rep.supply_m2 if rep else 0
            pv = prev.get(k)
            rows.append(
                SurveyRow(
                    complex_id=c.id,
                    name=c.name,
                    dong_code=c.dong_code,
                    dong=dong_names.get(c.dong_code, ""),
                    built_year=c.built_year,
                    households=c.households,
                    area_group=ag,
                    area_name="/".join(sorted({a.area_name for a in areas if a.area_name})) if areas else "",
                    supply_m2=supply,
                    exclusive_m2=rep.exclusive_m2 if rep else float(ag),
                    type_households=sum(a.households or 0 for a in areas) or None,
                    rooms=rep.rooms if rep else None,
                    baths=rep.baths if rep else None,
                    entrance=rep.entrance if rep else "",
                    peak=peak[0] if peak else None,
                    peak_date=peak[1].isoformat() if peak else None,
                    ask_min=ask_min,
                    ask_median=ls.get("ask_median"),
                    listing_cnt=ls.get("listing_cnt", 0),
                    last_trade=lt[0] if lt else None,
                    last_trade_date=lt[1].isoformat() if lt else None,
                    jeonse=jeonse,
                    jeonse_src=jsrc,
                    invest=(price - jeonse) if price and jeonse else None,
                    jeonse_ratio=round(jeonse / price, 3) if price and jeonse else None,
                    vs_peak=round((price - peak[0]) / peak[0], 3) if price and peak else None,
                    pyeong_price=round(price / (supply / 3.3058)) if price and supply else None,
                    prev_ask_min=pv.ask_min if pv else None,
                    prev_listing_cnt=pv.listing_cnt if pv else None,
                )
            )
    return rows


def write_snapshots(db: Session, complex_ids: list[int], date: dt.date) -> int:
    """오늘 매물 상태로 complex_daily 스냅샷 upsert."""
    if not complex_ids:
        return 0
    listings = db.scalars(
        select(Listing).where(Listing.complex_id.in_(complex_ids), Listing.status == "active")
    ).all()
    stats = listing_stats(listings)
    existing = {
        (s.complex_id, s.area_group): s
        for s in db.scalars(
            select(ComplexDaily).where(and_(ComplexDaily.complex_id.in_(complex_ids), ComplexDaily.date == date))
        )
    }
    n = 0
    for (cid, ag), st in stats.items():
        snap = existing.get((cid, ag)) or ComplexDaily(complex_id=cid, area_group=ag, date=date)
        snap.ask_min, snap.ask_median = st["ask_min"], st["ask_median"]
        snap.listing_cnt, snap.jeonse_min, snap.jeonse_cnt = st["listing_cnt"], st["jeonse_min"], st["jeonse_cnt"]
        db.add(snap)
        n += 1
    return n


def region_pyeong_price(rows: list[SurveyRow]) -> int | None:
    """지역 평단가 (PDF p14 순위표): 평형별 평단가의 중앙값."""
    vals = [r.pyeong_price for r in rows if r.pyeong_price]
    return _median(vals)
