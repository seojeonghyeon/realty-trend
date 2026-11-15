"""전고점 계산용 과거 실거래 백필 (매일 새벽, 국토부 호출 한도 안에서 조금씩).

    python -m collector.backfill [--reserve 600] [--since 2006]

관심 지역의 시군구(LAWD_CD)마다 (since년 1월 ~ 4개월 전) 월 단위 작업을 큐에 넣고,
최근 월부터 처리한다. 일일 수집(06:00)용으로 reserve 만큼 호출을 남겨둔다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import BackfillTask
from app.services.region_resolver import resolve
from collector.run_daily import JobLog, dong_lookup_for, upsert_trades
from collector.sources.molit import MolitClient, MolitError, rent_record, sale_record
from collector.util import Quota, QuotaExceeded, month_list


def enqueue(db, lawd_cds: set[str], today: dt.date, since_year: int) -> int:
    start = today.replace(day=1) - dt.timedelta(days=95)
    months = [m for m in month_list(start, (start.year - since_year + 1) * 12) if int(m[:4]) >= since_year]
    existing = {(t.kind, t.lawd_cd, t.ym) for t in db.scalars(select(BackfillTask))}
    n = 0
    for lawd in lawd_cds:
        for ym in months:
            for kind in ("sale", "rent"):
                if (kind, lawd, ym) not in existing:
                    db.add(BackfillTask(kind=kind, lawd_cd=lawd, ym=ym))
                    n += 1
    db.commit()
    return n


def run(reserve: int = 600, since_year: int = 2006) -> None:
    init_db()
    s = get_settings()
    db = SessionLocal()
    today = dt.date.today()
    job = JobLog(db, "backfill", today)
    try:
        if not s.molit_service_key:
            job.error("MOLIT_SERVICE_KEY 미설정")
            job.finish("failed")
            return
        lawds = resolve(db).lawd_cds
        job.stats["enqueued"] = enqueue(db, lawds, today, since_year)
        quota = Quota(db, "molit", s.molit_daily_limit, today)
        client = MolitClient(s.molit_service_key, quota)
        lookup = dong_lookup_for(db, lawds)
        done = new = 0
        tasks = db.scalars(
            select(BackfillTask)
            .where(BackfillTask.done.is_(False), BackfillTask.lawd_cd.in_(lawds))
            .order_by(BackfillTask.ym.desc())
        ).all()
        for t in tasks:
            if quota.remaining <= reserve:
                break
            try:
                gen = client.sales if t.kind == "sale" else client.rents
                conv = sale_record if t.kind == "sale" else rent_record
                new += upsert_trades(db, [conv(i, t.lawd_cd) for i in gen(t.lawd_cd, t.ym)], lookup)
                t.done = True
                done += 1
                db.commit()
            except QuotaExceeded:
                break
            except (MolitError, ValueError, KeyError) as e:
                db.rollback()
                job.error(f"{t.kind} {t.lawd_cd} {t.ym}: {e}")
        left = db.query(BackfillTask).filter(BackfillTask.done.is_(False)).count()
        job.stats.update({"done": done, "new_trades": new, "remaining_tasks": left})
        job.info("백필 %d건 처리, 신규 거래 %d, 남은 작업 %d", done, new, left)
        if done:
            from collector.matching import match_trades

            match_trades(db)
            db.commit()
        job.finish()
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--reserve", type=int, default=600)
    ap.add_argument("--since", type=int, default=2006)
    a = ap.parse_args()
    run(a.reserve, a.since)


if __name__ == "__main__":
    main()
