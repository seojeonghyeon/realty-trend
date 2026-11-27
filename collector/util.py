from __future__ import annotations

import datetime as dt
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApiQuota

log = logging.getLogger("collector")
# httpx 는 INFO 로 요청 URL 전체를 찍어 국토부 ServiceKey 가 로그에 남는다 → 경고 이상만
logging.getLogger("httpx").setLevel(logging.WARNING)


class QuotaExceeded(RuntimeError):
    pass


class Quota:
    """일일 호출 수 카운터 (DB 저장 → 재실행해도 누적)."""

    def __init__(self, db: Session, source: str, limit: int, today: dt.date | None = None):
        self.db, self.source, self.limit = db, source, limit
        self.today = today or dt.date.today()
        row = db.scalar(select(ApiQuota).where(ApiQuota.source == source, ApiQuota.date == self.today))
        if row is None:
            row = ApiQuota(source=source, date=self.today, count=0)
            db.add(row)
            db.flush()
        self.row = row

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.row.count)

    def take(self, n: int = 1) -> None:
        if self.row.count + n > self.limit:
            raise QuotaExceeded(f"{self.source} 일일 호출 상한({self.limit}) 도달")
        self.row.count += n
        self.db.flush()


def parse_price(text: str | None) -> int | None:
    """'8억 9,000' → 89000, '9억' → 90000, '9,500' → 9500 (만원)."""
    if not text:
        return None
    t = text.replace(",", "").replace(" ", "")
    m = re.fullmatch(r"(?:(\d+)억)?(\d+)?", t)
    if not m or (m.group(1) is None and m.group(2) is None):
        return None
    return int(m.group(1) or 0) * 10000 + int(m.group(2) or 0)


def parse_ymd(text: str | None) -> dt.date | None:
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) < 8:
        return None
    try:
        return dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def normalize_name(name: str) -> str:
    n = re.sub(r"\(.*?\)", "", name or "")
    n = re.sub(r"아파트|APT|apt|\s|·|-|_", "", n)
    return n.lower()


def month_list(end: dt.date, months: int) -> list[str]:
    """end 가 속한 달부터 과거로 months 개 'YYYYMM'."""
    out, y, m = [], end.year, end.month
    for _ in range(months):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out
