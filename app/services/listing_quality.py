"""A2 매물 중복 묶기 + A3 허위·미끼 의심/주의 표시."""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Listing, Trade

STALE_DAYS = 30


def parse_floor(text: str) -> tuple[int | None, int | None]:
    """'6/20' → (6, 20), '저/15' → (None, 15), 'B1/10' → (-1, 10)."""
    if not text:
        return None, None
    parts = text.split("/")
    cur, total = parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""
    f = None
    if re.fullmatch(r"\d+", cur):
        f = int(cur)
    elif re.fullmatch(r"B\d+", cur, re.I):
        f = -int(cur[1:])
    t = int(total) if re.fullmatch(r"\d+", total) else None
    return f, t


def dup_key(li: Listing) -> str:
    floor = li.floor_text.split("/")[0] if li.floor_text else ""
    return "|".join(
        str(x)
        for x in (li.complex_id, li.trade_type, li.area_group, li.building, floor, li.price, li.direction)
    )


def dedupe(listings: list[Listing]) -> int:
    """같은 매물을 여러 중개사가 올린 경우 대표 1건만 is_primary=True. 묶인 그룹 수 반환."""
    groups: dict[str, list[Listing]] = defaultdict(list)
    for li in listings:
        li.dup_key = dup_key(li)
        if li.status == "active":
            groups[li.dup_key].append(li)
    merged = 0
    for items in groups.values():
        items.sort(key=lambda x: (x.confirmed_at or dt.date.min, x.realtor_count), reverse=True)
        for i, li in enumerate(items):
            li.is_primary = i == 0
            li.dup_count = len(items) if i == 0 else 1
        if len(items) > 1:
            merged += 1
    return merged


def trade_p5(db: Session, complex_ids: list[int], today: dt.date) -> dict[tuple[int, int], int]:
    since = today - dt.timedelta(days=365)
    prices: dict[tuple[int, int], list[int]] = defaultdict(list)
    for cid, ag, p in db.execute(
        select(Trade.complex_id, Trade.area_group, Trade.price).where(
            Trade.complex_id.in_(complex_ids),
            Trade.kind == "sale",
            Trade.cancelled.is_(False),
            Trade.deal_date >= since,
        )
    ):
        prices[(cid, ag)].append(p)
    out = {}
    for k, v in prices.items():
        if len(v) >= 5:
            v.sort()
            out[k] = v[max(0, int(len(v) * 0.05) - 1)]
    return out


def compute_flags(li: Listing, today: dt.date, p5: dict[tuple[int, int], int]) -> list[str]:
    flags = []
    if li.confirmed_at and (today - li.confirmed_at).days > STALE_DAYS:
        flags.append("확인오래됨")
    if li.trade_type == "sale":
        ref = p5.get((li.complex_id, li.area_group))
        if ref and li.price < ref:
            flags.append("실거래대비저가")
    head = li.floor_text.split("/")[0] if li.floor_text else ""
    if (li.floor is not None and li.floor <= 2) or head == "저":
        flags.append("저층")
    if li.floor is not None and li.total_floor and li.floor == li.total_floor:
        flags.append("탑층")
    return flags


def refresh_quality(db: Session, complex_ids: list[int], today: dt.date) -> dict:
    if not complex_ids:
        return {"merged": 0}
    listings = db.scalars(select(Listing).where(Listing.complex_id.in_(complex_ids))).all()
    merged = dedupe(listings)
    p5 = trade_p5(db, complex_ids, today)
    for li in listings:
        li.flags = compute_flags(li, today, p5)
    return {"merged": merged}
