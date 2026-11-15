"""매일 06:00 수집 작업.

    python -m collector.run_daily [--date 2026-11-14] [--skip-naver] [--skip-molit] [--dong 4111710300]

순서: 국토부 실거래(최근 3개월) → 네이버 단지 목록(1단계) → 예산 근처 단지만 상세/매물(2단계)
     → 실거래↔단지 매칭 → 중복·의심 표시 → 수리등급 1차 분류 → 스냅샷 → 객관 지표/자동 평가 → 알림
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import traceback

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import (
    BudgetProfile,
    Complex,
    ComplexArea,
    ComplexDaily,
    ComplexEval,
    JobRun,
    LegalDong,
    Listing,
    Trade,
    VisitNote,
    area_group,
)
from app.services import interior
from app.services.budget import fmt_eok, range_from_profile
from app.services.listing_quality import parse_floor, refresh_quality
from app.services.metrics import write_snapshots
from app.services.region_resolver import resolve
from collector.matching import match_trades
from collector.sources import extras
from collector.sources.molit import MolitClient, MolitError, rent_record, sale_record
from collector.sources.naver_land import NaverBlocked, NaverClient, NaverError, article_url
from collector.util import Quota, QuotaExceeded, log, month_list

RECENT_MONTHS = 3
NEAR_RANGE = 10000  # 예산 범위 ±1억 안의 단지만 상세 매물 수집
DETAIL_REFRESH_DAYS = 30
FAR_REFRESH_DAYS = 7  # 예산 밖 단지도 주 1회는 확인


class JobLog:
    def __init__(self, db: Session, job: str, run_date: dt.date):
        self.db = db
        self.row = JobRun(job=job, run_date=run_date, status="running", stats={}, log="")
        db.add(self.row)
        db.commit()
        self.lines: list[str] = []
        self.errors: list[str] = []
        self.stats: dict = {}

    def info(self, msg: str, *args) -> None:
        text = msg % args if args else msg
        log.info(text)
        self.lines.append(text)

    def error(self, msg: str, *args) -> None:
        text = msg % args if args else msg
        log.error(text)
        self.lines.append("ERROR " + text)
        self.errors.append(text)

    def finish(self, status: str | None = None) -> None:
        self.row.finished_at = dt.datetime.now()
        self.row.status = status or ("partial" if self.errors else "ok")
        self.row.stats = self.stats
        self.row.log = "\n".join(self.lines)[-20000:]  # MariaDB TEXT(64KB) 안에 들도록
        self.db.commit()


def get_profile(db: Session) -> BudgetProfile:
    p = db.get(BudgetProfile, 1)
    if p is None:
        p = BudgetProfile(id=1)
        db.add(p)
        db.flush()
    return p


# ---------------------------------------------------------------- 국토부
def upsert_trades(db: Session, records: list[dict], dong_lookup: dict[tuple[str, str], str]) -> int:
    if not records:
        return 0
    keys = [r["dedupe_key"] for r in records]
    existing = {
        t.dedupe_key: t
        for t in db.scalars(select(Trade).where(Trade.dedupe_key.in_(keys)))
    }
    new = 0
    for r in records:
        dong_name = r.pop("dong_name", "")
        if not r["dong_code"]:
            r["dong_code"] = dong_lookup.get((r["lawd_cd"], dong_name), "")
        t = existing.get(r["dedupe_key"])
        if t is None:
            t = Trade(**r, area_group=area_group(r["exclusive_m2"]))
            db.add(t)
            existing[r["dedupe_key"]] = t
            new += 1
        else:
            t.cancelled = r["cancelled"]
    db.flush()
    return new


def dong_lookup_for(db: Session, lawd_cds: set[str]) -> dict[tuple[str, str], str]:
    out = {}
    for ld in db.scalars(select(LegalDong).where(LegalDong.active.is_(True))):
        if ld.code[:5] in lawd_cds and ld.eupmyeondong:
            out[(ld.code[:5], ld.eupmyeondong)] = ld.code
    return out


def collect_molit(db: Session, job: JobLog, lawd_cds: set[str], today: dt.date, months: list[str]) -> None:
    settings = get_settings()
    if not settings.molit_service_key:
        job.error("MOLIT_SERVICE_KEY 미설정 — 실거래 수집 건너뜀")
        return
    client = MolitClient(settings.molit_service_key, Quota(db, "molit", settings.molit_daily_limit, today))
    lookup = dong_lookup_for(db, lawd_cds)
    new = 0
    for lawd in sorted(lawd_cds):
        for ym in months:
            try:
                sales = [sale_record(i, lawd) for i in client.sales(lawd, ym)]
                rents = [rent_record(i, lawd) for i in client.rents(lawd, ym)]
                new += upsert_trades(db, sales + rents, lookup)
                db.commit()
            except QuotaExceeded as e:
                job.error(str(e))
                job.stats["molit_new"] = new
                return
            except (MolitError, ValueError, KeyError) as e:
                db.rollback()
                job.error(f"실거래 {lawd} {ym}: {e}")
    job.stats["molit_new"] = new
    job.info("국토부 신규 거래 %d건", new)


# ---------------------------------------------------------------- 네이버
def upsert_complex_summary(db: Session, item: dict, today: dt.date) -> Complex:
    c = db.scalar(select(Complex).where(Complex.naver_no == item["naver_no"]))
    if c is None:
        c = Complex(naver_no=item["naver_no"], name=item["name"], dong_code=item["dong_code"])
        db.add(c)
    c.name = item["name"] or c.name
    c.dong_code = item["dong_code"] or c.dong_code
    for f in ("households", "built_year", "lat", "lng"):
        if item.get(f) is not None:
            setattr(c, f, item[f])
    c.list_deal_count = item.get("deal_count", 0)
    c.list_lease_count = item.get("lease_count", 0)
    c.last_listed = today
    db.flush()
    snap = db.scalar(
        select(ComplexDaily).where(
            ComplexDaily.complex_id == c.id, ComplexDaily.area_group == 0, ComplexDaily.date == today
        )
    ) or ComplexDaily(complex_id=c.id, area_group=0, date=today)
    snap.listing_cnt, snap.jeonse_cnt = c.list_deal_count, c.list_lease_count
    db.add(snap)
    return c


def apply_detail(db: Session, c: Complex, d: dict, today: dt.date) -> None:
    for f in ("address", "jibun", "corridor"):
        if d.get(f):
            setattr(c, f, d[f])
    for f in ("households", "built_year", "lat", "lng"):
        if d.get(f) is not None:
            setattr(c, f, d[f])
    if d.get("areas"):
        existing = {a.pyeong_no: a for a in c.areas}
        for a in d["areas"]:
            row = existing.get(a["pyeong_no"])
            if row is None:
                row = ComplexArea(complex_id=c.id)
                c.areas.append(row)
            for k, v in a.items():
                setattr(row, k, v)
    c.detail_fetched = today


def upsert_listings(db: Session, c: Complex, items: list[dict], today: dt.date) -> tuple[int, int]:
    existing = {li.article_no: li for li in db.scalars(select(Listing).where(Listing.complex_id == c.id))}
    new = changed = 0
    for it in items:
        li = existing.get(it["article_no"])
        if li is None:
            li = Listing(article_no=it["article_no"], complex_id=c.id, first_seen=today, last_seen=today)
            db.add(li)
            existing[it["article_no"]] = li
            new += 1
        elif li.status == "active" and li.price != it["price"]:
            li.prev_price, li.price_changed_at = li.price, today
            changed += 1
        for k, v in it.items():
            if k != "article_no":
                setattr(li, k, v)
        li.area_group = area_group(it["exclusive_m2"])
        li.floor, li.total_floor = parse_floor(it["floor_text"])
        li.last_seen, li.status = today, "active"
        if not li.repair_grade or li.repair_source == "rule":
            grade = interior.classify_repair(it["description"])
            if grade:
                li.repair_grade, li.repair_source = grade, "rule"
    # 오늘 목록에 없는 매물은 거래완료/내림으로 본다
    for li in existing.values():
        if li.status == "active" and li.last_seen < today:
            li.status = "closed"
    db.flush()
    return new, changed


def latest_price(db: Session, c: Complex) -> int | None:
    v = db.scalar(
        select(func.min(ComplexDaily.ask_min)).where(
            ComplexDaily.complex_id == c.id,
            ComplexDaily.area_group > 0,
            ComplexDaily.date == select(func.max(ComplexDaily.date)).where(
                ComplexDaily.complex_id == c.id, ComplexDaily.area_group > 0
            ).scalar_subquery(),
        )
    )
    if v is not None:
        return v
    return db.scalar(
        select(Trade.price)
        .where(Trade.complex_id == c.id, Trade.kind == "sale", Trade.cancelled.is_(False))
        .order_by(Trade.deal_date.desc())
        .limit(1)
    )


def needs_articles(db: Session, c: Complex, lo: int, hi: int, min_households: int, today: dt.date) -> bool:
    if c.eval and (c.eval.keep or c.eval.priority):
        return True
    if c.eval and c.eval.excluded:
        return c.articles_fetched is None or (today - c.articles_fetched).days >= FAR_REFRESH_DAYS
    if min_households and c.households and c.households < min_households:
        return False
    if c.list_deal_count == 0 and c.list_lease_count == 0:
        return False
    if c.articles_fetched is None:
        return True
    price = latest_price(db, c)
    if price is None or lo <= price <= hi:
        return True
    return (today - c.articles_fetched).days >= FAR_REFRESH_DAYS


def collect_naver(db: Session, job: JobLog, dong_codes: list[str], today: dt.date, lo: int, hi: int, min_hh: int) -> list[int]:
    settings = get_settings()
    client = NaverClient(Quota(db, "naver", settings.naver_daily_limit, today), delay=settings.naver_delay_sec)
    touched: list[int] = []
    complexes: list[Complex] = []
    st = {"naver_complexes": 0, "naver_detail": 0, "naver_article_complexes": 0, "listings_new": 0, "listings_changed": 0}
    try:
        # 1단계: 동별 단지 목록
        for code in dong_codes:
            try:
                for item in client.complexes_in_dong(code):
                    complexes.append(upsert_complex_summary(db, item, today))
                db.commit()
            except (NaverError, QuotaExceeded) as e:
                db.rollback()
                if isinstance(e, (NaverBlocked, QuotaExceeded)):
                    raise
                job.error(f"단지목록 {code}: {e}")
        st["naver_complexes"] = len(complexes)

        # 2단계: 예산 근처 단지만 상세/매물
        targets = [c for c in complexes if needs_articles(db, c, lo - NEAR_RANGE, hi + NEAR_RANGE, min_hh, today)]
        job.info("단지 %d개 중 상세 매물 수집 대상 %d개", len(complexes), len(targets))
        for c in targets:
            try:
                if c.detail_fetched is None or (today - c.detail_fetched).days >= DETAIL_REFRESH_DAYS:
                    apply_detail(db, c, client.complex_detail(c.naver_no), today)
                    st["naver_detail"] += 1
                items = []
                if c.list_deal_count:
                    items += list(client.articles(c.naver_no, "sale"))
                if c.list_lease_count:
                    items += list(client.articles(c.naver_no, "jeonse"))
                n, ch = upsert_listings(db, c, items, today)
                st["listings_new"] += n
                st["listings_changed"] += ch
                c.articles_fetched = today
                touched.append(c.id)
                st["naver_article_complexes"] += 1
                db.commit()
            except (NaverBlocked, QuotaExceeded):
                db.rollback()
                raise
            except NaverError as e:
                db.rollback()
                job.error(f"매물 {c.name}({c.naver_no}): {e}")
    except (NaverBlocked, QuotaExceeded) as e:
        job.error(f"네이버 수집 중단: {e}")
    job.stats.update(st)
    job.info("네이버: %s", st)
    return touched


# ---------------------------------------------------------------- 후처리
def objective_metrics(db: Session, job: JobLog, complex_ids: list[int]) -> None:
    key = get_settings().kakao_rest_key
    if not key:
        return
    n = 0
    for c in db.scalars(select(Complex).where(Complex.id.in_(complex_ids), Complex.station_m.is_(None))):
        if c.lat is None or c.lng is None:
            continue
        st = extras.nearest_place(key, c.lat, c.lng, "SW8")
        sc = extras.nearest_place(key, c.lat, c.lng, "SC4", "초등학교")
        c.station_name, c.station_m = (st[0], st[1]) if st else ("", 9999)
        c.school_name, c.school_m = (sc[0], sc[1]) if sc else ("", 9999)
        n += 1
    db.commit()
    job.stats["objective_updated"] = n


def grade(v: int | None, good: int, soso: int, higher_better: bool = False) -> str:
    if v is None:
        return ""
    if higher_better:
        return "Good" if v >= good else ("Soso" if v >= soso else "Bad")
    return "Good" if v <= good else ("Soso" if v <= soso else "Bad")


def auto_evals(db: Session, complex_ids: list[int]) -> None:
    """객관 지표로 Good/Soso/Bad 초안. 사용자/Claude 가 손댄 평가는 건드리지 않는다."""
    for c in db.scalars(select(Complex).where(Complex.id.in_(complex_ids))):
        ev = c.eval
        if ev is None:
            ev = ComplexEval(complex_id=c.id, source="auto")
            db.add(ev)
            c.eval = ev
        if ev.source != "auto":
            continue
        ev.households = grade(c.households, 1000, 500, higher_better=True)
        ev.transport = grade(c.station_m, 500, 1000)
        ev.school = grade(c.school_m, 400, 800)
    db.commit()


def notify(db: Session, job: JobLog, today: dt.date, brange, touched: list[int]) -> None:
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_chat_id):
        return
    lines = [f"<b>🏠 {today:%m/%d} 부동산 수집</b>  예산 {fmt_eok(brange.price_lo)}~{fmt_eok(brange.price_hi)}"]
    new = db.scalars(
        select(Listing)
        .join(Complex)
        .where(
            Listing.first_seen == today,
            Listing.status == "active",
            Listing.is_primary.is_(True),
            Listing.trade_type == "sale",
            Listing.price.between(brange.price_lo, brange.price_hi),
        )
        .order_by(Listing.price)
        .limit(15)
    ).all()
    new = [li for li in new if not (li.complex.eval and li.complex.eval.excluded)]
    if new:
        lines.append(f"\n<b>예산 범위 신규 매물 {len(new)}건</b>")
        for li in new:
            fl = " ⚠" + ",".join(li.flags) if li.flags else ""
            lines.append(
                f"• <a href=\"{article_url(li.article_no)}\">{li.complex.name} {li.building} {li.floor_text}</a> "
                f"{li.area_group}㎡ {fmt_eok(li.price)} {li.repair_grade}{fl}"
            )
    drops = db.scalars(
        select(Listing)
        .join(Complex)
        .where(
            Listing.price_changed_at == today,
            Listing.prev_price.is_not(None),
            Listing.price < Listing.prev_price,
            Listing.trade_type == "sale",
            Listing.is_primary.is_(True),
        )
        .limit(30)
    ).all()
    drops = [
        li for li in drops
        if brange.contains(li.price) or (li.complex.eval and (li.complex.eval.keep or li.complex.eval.priority))
    ][:10]
    if drops:
        lines.append(f"\n<b>호가 인하 {len(drops)}건</b>")
        for li in drops:
            lines.append(f"• {li.complex.name} {li.area_group}㎡ {fmt_eok(li.prev_price)} → {fmt_eok(li.price)}")
    calls = db.scalars(select(VisitNote).where(VisitNote.call_by == today, VisitNote.status == "todo")).all()
    if calls:
        lines.append("\n<b>📞 오늘 전화할 곳</b>")
        for v in calls:
            lines.append(f"• {v.complex.name} {v.realtor_name} {v.realtor_phone}")
    if job.errors:
        lines.append(f"\n⚠️ 오류 {len(job.errors)}건: " + "; ".join(job.errors[:3]))
    lines.append(f"\n{s.public_base_url}")
    extras.send_telegram(s.telegram_bot_token, s.telegram_chat_id, "\n".join(lines))


def check_region_codes(db: Session, job: JobLog) -> None:
    """폐지된 법정동코드를 쓰는 관심 지역 경고 (행정구역 개편 대비)."""
    from app.models import WatchRegion

    for r in db.scalars(select(WatchRegion).where(WatchRegion.active.is_(True))):
        ld = db.get(LegalDong, r.code)
        if ld is None or not ld.active:
            job.error(f"관심 지역 '{r.display_name}'({r.code}) 코드가 폐지/변경됨 — 지역을 다시 선택하세요")


# ---------------------------------------------------------------- main
def run(today: dt.date | None = None, skip_naver: bool = False, skip_molit: bool = False, only_dongs: list[str] | None = None) -> str:
    today = today or dt.date.today()
    init_db()
    db = SessionLocal()
    job = JobLog(db, "daily", today)
    try:
        profile = get_profile(db)
        brange = range_from_profile(profile)
        resolved = resolve(db)
        dongs = only_dongs or resolved.dong_codes
        lawds = {d[:5] for d in dongs}
        job.info("관심 지역 → 동 %d개, 시군구 %d개", len(dongs), len(lawds))
        check_region_codes(db, job)
        if not dongs:
            job.info("활성 관심 지역 없음")
            job.finish("ok")
            return "ok"

        if not skip_molit:
            collect_molit(db, job, lawds, today, month_list(today, RECENT_MONTHS))
        touched = [] if skip_naver else collect_naver(
            db, job, dongs, today, brange.price_lo, brange.price_hi, profile.min_households
        )

        job.stats["match"] = match_trades(db, dongs)
        db.commit()
        region_complex_ids = db.scalars(select(Complex.id).where(Complex.dong_code.in_(dongs))).all()
        job.stats["quality"] = refresh_quality(db, touched, today)
        job.stats["snapshots"] = write_snapshots(db, touched, today)
        db.commit()
        objective_metrics(db, job, region_complex_ids)
        auto_evals(db, region_complex_ids)
        notify(db, job, today, brange, touched)
        job.finish()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        job.error(f"치명적 오류: {e}\n{traceback.format_exc()}")
        job.finish("failed")
        s = get_settings()
        extras.send_telegram(s.telegram_bot_token, s.telegram_chat_id, f"❌ 부동산 수집 실패: {e}")
    finally:
        status = job.row.status
        db.close()
    return status


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=dt.date.fromisoformat)
    ap.add_argument("--skip-naver", action="store_true")
    ap.add_argument("--skip-molit", action="store_true")
    ap.add_argument("--dong", action="append", help="특정 법정동코드만 (테스트용)")
    a = ap.parse_args()
    print(run(a.date, a.skip_naver, a.skip_molit, a.dong))


if __name__ == "__main__":
    main()
