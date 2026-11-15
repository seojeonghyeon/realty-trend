from __future__ import annotations

import csv
import datetime as dt
import io
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import check_credentials, client_ip, login_blocked, require_login
from app.db import get_db
from app.models import (
    Complex,
    ComplexDaily,
    ComplexEval,
    DailyAnalysis,
    JobRun,
    LegalDong,
    Listing,
    NewsItem,
    Trade,
    VisitNote,
    WatchRegion,
)
from app.services import interior, matrix
from app.services.budget import EOK, acquisition_costs, fmt_eok, loan_limit
from app.services.metrics import region_pyeong_price
from app.services.region_resolver import level_of
from app.services.views import (
    data_date,
    eval_map,
    get_budget,
    get_profile,
    is_regulated,
    region_rows,
    rows_by_region,
)
from collector.sources.naver_land import article_url, complex_url

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["eok"] = fmt_eok
templates.env.filters["pct"] = lambda v, d=0: "-" if v is None else f"{v * 100:.{d}f}%"
templates.env.filters["num"] = lambda v: "-" if v is None else f"{v:,}"
templates.env.globals.update(article_url=article_url, complex_url=complex_url, GRADES=interior.GRADES)



def all_in_cost(price: int, exclusive_m2: float, p, interior_total: int = 0) -> int:
    """매매가 + 취득세·중개보수·기타 + 인테리어 = 실투입 총액."""
    c = acquisition_costs(
        price, exclusive_m2 or 84, house_count_after=p.house_count_after, first_home=p.first_home,
        brokerage_vat=p.brokerage_vat, extra=p.extra_costs,
    )
    return round(price + c.total + interior_total)


templates.env.globals["all_in_cost"] = all_in_cost

EVAL_GRADES = ["", "Good", "Soso", "Bad"]
CHECKLIST = ["샷시(내부/외부)", "확장 여부", "보일러", "누수 자국", "타일 깨짐", "곰팡이(북측 외벽)", "베란다 창고", "화장실", "싱크대", "도배/장판"]


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("user", request.session.get("user"))
    ctx.setdefault("path", request.url.path)
    return templates.TemplateResponse(request, name, ctx)


def back(request: Request, default: str = "/") -> RedirectResponse:
    ref = request.headers.get("referer") or default
    return RedirectResponse(ref, status_code=303)


# ---------------------------------------------------------------- 로그인
@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return render(request, "login.html", next=next, error=None)


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    ip = client_ip(request)
    if login_blocked(ip):
        return render(request, "login.html", next=next, error="로그인 실패가 많아 15분간 차단되었습니다.")
    if not check_credentials(ip, username, password):
        return render(request, "login.html", next=next, error="아이디 또는 비밀번호가 틀렸습니다.")
    request.session["user"] = username
    return RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------- 대시보드
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), _=Depends(require_login)):
    profile, br = get_budget(db)
    date = data_date(db)
    summary = db.scalar(
        select(DailyAnalysis).where(DailyAnalysis.region_id == 0).order_by(DailyAnalysis.date.desc()).limit(1)
    )
    region_cards = []
    for region, complexes, rows in rows_by_region(db, profile, date):
        ids = [c.id for c in complexes]
        in_budget = [r for r in rows if br.contains(r.price)]
        new_cnt = db.scalar(
            select(func.count(Listing.id)).where(
                Listing.complex_id.in_(ids),
                Listing.first_seen == date,
                Listing.is_primary.is_(True),
                Listing.status == "active",
                Listing.trade_type == "sale",
                Listing.price.between(br.price_lo, br.price_hi),
            )
        ) if ids else 0
        drops = sum(1 for r in rows if r.ask_change is not None and r.ask_change < 0)
        analysis = db.scalar(
            select(DailyAnalysis).where(DailyAnalysis.region_id == region.id).order_by(DailyAnalysis.date.desc()).limit(1)
        )
        region_cards.append(
            dict(
                region=region,
                complexes=len(complexes),
                in_budget=len({r.complex_id for r in in_budget}),
                new=new_cnt,
                drops=drops,
                pyeong_price=region_pyeong_price(rows),
                analysis=analysis,
            )
        )
    region_cards.sort(key=lambda x: -(x["pyeong_price"] or 0))
    news = db.scalars(select(NewsItem).order_by(NewsItem.date.desc(), NewsItem.id).limit(6)).all()
    job = db.scalar(select(JobRun).where(JobRun.job == "daily").order_by(JobRun.id.desc()).limit(1))
    loan = None
    if profile.annual_income:
        loan = loan_limit(
            br.price_hi,
            annual_income=profile.annual_income,
            other_debt_payment=profile.other_debt_payment,
            loan_rate=profile.loan_rate,
            stress_rate=profile.stress_rate,
            years=profile.loan_years,
            dsr_limit=profile.dsr_limit,
            ltv_pct=profile.ltv_pct,
            cap=profile.loan_cap,
        )
    calls = db.scalars(
        select(VisitNote).where(VisitNote.status == "todo", VisitNote.call_by.is_not(None)).order_by(VisitNote.call_by).limit(5)
    ).all()
    return render(
        request, "dashboard.html", profile=profile, br=br, date=date, summary=summary,
        cards=region_cards, news=news, job=job, loan=loan, calls=calls,
    )


# ---------------------------------------------------------------- 관심 지역
@router.get("/regions", response_class=HTMLResponse)
def regions_page(request: Request, db: Session = Depends(get_db), _=Depends(require_login)):
    regions = db.scalars(select(WatchRegion).order_by(WatchRegion.id)).all()
    legal_count = db.scalar(select(func.count()).select_from(LegalDong))
    retired = {r.id for r in regions if (ld := db.get(LegalDong, r.code)) is None or not ld.active}
    return render(request, "regions.html", regions=regions, legal_count=legal_count, retired=retired)


@router.get("/regions/search", response_class=HTMLResponse)
def region_search(request: Request, q: str = "", db: Session = Depends(get_db), _=Depends(require_login)):
    q = q.strip()
    results = []
    if len(q) >= 2:
        terms = q.split()
        stmt = select(LegalDong).where(LegalDong.active.is_(True))
        for t in terms:
            stmt = stmt.where(LegalDong.name.contains(t))
        results = db.scalars(stmt.order_by(LegalDong.code).limit(30)).all()  # 상위 행정구역이 먼저
    return render(request, "_region_results.html", results=results, level_of=level_of)


@router.post("/regions")
def region_add(request: Request, code: str = Form(...), group_label: str = Form(""), db: Session = Depends(get_db), _=Depends(require_login)):
    ld = db.get(LegalDong, code)
    if ld is None:
        raise HTTPException(404, "법정동코드 없음")
    exists = db.scalar(select(WatchRegion).where(WatchRegion.code == code))
    if exists is None:
        name = ld.name.replace("특별시", "").replace("광역시", "").replace("특별자치시", "").replace("특별자치도", "")
        db.add(WatchRegion(level=ld.level, code=code, display_name=" ".join(name.split()[1:]) or ld.name, group_label=group_label.strip()))
        db.commit()
    return RedirectResponse("/regions", status_code=303)


@router.post("/regions/{rid}/update")
def region_update(rid: int, request: Request, group_label: str = Form(""), active: str = Form(""), db: Session = Depends(get_db), _=Depends(require_login)):
    r = db.get(WatchRegion, rid) or _404()
    r.group_label = group_label.strip()
    r.active = active == "on"
    db.commit()
    return RedirectResponse("/regions", status_code=303)


@router.post("/regions/{rid}/delete")
def region_delete(rid: int, db: Session = Depends(get_db), _=Depends(require_login)):
    r = db.get(WatchRegion, rid) or _404()
    db.delete(r)
    db.commit()
    return RedirectResponse("/regions", status_code=303)


def _404():
    raise HTTPException(404)


@router.get("/regions/{rid}", response_class=HTMLResponse)
def region_detail(
    rid: int, request: Request, view: str = "matrix", below: float = 1.0,
    db: Session = Depends(get_db), _=Depends(require_login),
):
    region = db.get(WatchRegion, rid) or _404()
    profile, br = get_budget(db)
    date = data_date(db)
    complexes, rows = region_rows(db, region, profile, date)
    evals = eval_map(complexes)
    m = matrix.build([(r.dong or "?", r) for r in rows], br, evals, below=int(below * EOK))
    analysis = db.scalars(
        select(DailyAnalysis).where(DailyAnalysis.region_id == rid).order_by(DailyAnalysis.date.desc()).limit(7)
    ).all()
    rows_sorted = sorted(rows, key=lambda r: (r.dong, -(r.price or 0)))
    return render(
        request, "region_detail.html", region=region, br=br, date=date, rows=rows_sorted, m=m, evals=evals,
        view=view, below=below, analysis=analysis, pyeong_price=region_pyeong_price(rows),
        regulated=is_regulated(region.code, profile.regulated_codes), land_permit=is_regulated(region.code, profile.land_permit_codes),
        mode=profile.mode,
    )


SURVEY_COLUMNS = [
    ("dong", "읍/면/동"), ("name", "단지명"), ("built_year", "연식"), ("households", "단지세대수"),
    ("supply_m2", "공급면적"), ("exclusive_m2", "전용면적"), ("area_name", "타입"), ("type_households", "유닛세대수"),
    ("rooms", "방"), ("baths", "화장실"), ("entrance", "구조"), ("peak", "전고점"), ("ask_min", "매매가(호가최저)"),
    ("last_trade", "최근실거래"), ("jeonse", "전세가"), ("invest", "투자금"), ("jeonse_ratio", "전세가율"),
    ("vs_peak", "전고대비"), ("pyeong_price", "평단가"), ("listing_cnt", "매물수"),
]


@router.get("/regions/{rid}/export.csv")
def region_export(rid: int, db: Session = Depends(get_db), _=Depends(require_login)):
    region = db.get(WatchRegion, rid) or _404()
    profile, _br = get_budget(db)
    _c, rows = region_rows(db, region, profile, data_date(db))
    buf = io.StringIO()
    buf.write("﻿")  # 엑셀 한글
    w = csv.writer(buf)
    w.writerow([h for _, h in SURVEY_COLUMNS])
    for r in rows:
        d = r.as_dict()
        w.writerow([d.get(k) if d.get(k) is not None else "" for k, _ in SURVEY_COLUMNS])
    fname = f"survey_{rid}_{dt.date.today():%Y%m%d}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={fname}"})


# ---------------------------------------------------------------- 통합 우선순위 매트릭스
@router.get("/matrix", response_class=HTMLResponse)
def matrix_page(request: Request, survivors: int = 1, db: Session = Depends(get_db), _=Depends(require_login)):
    profile, br = get_budget(db)
    date = data_date(db)
    items, evals, labels = [], {}, []
    for region, complexes, rows in rows_by_region(db, profile, date):
        evals.update(eval_map(complexes))
        if region.label not in labels:
            labels.append(region.label)
        items += [(region.label, r) for r in rows]
    m = matrix.build(items, br, evals, below=0, columns=labels, only_survivors=bool(survivors))
    return render(request, "matrix.html", m=m, br=br, date=date, survivors=survivors)


@router.get("/matrix/export.csv")
def matrix_export(db: Session = Depends(get_db), _=Depends(require_login)):
    profile, br = get_budget(db)
    items, evals, labels = [], {}, []
    for region, complexes, rows in rows_by_region(db, profile, data_date(db)):
        evals.update(eval_map(complexes))
        labels.append(region.label)
        items += [(region.label, r) for r in rows]
    m = matrix.build(items, br, evals, below=0, columns=labels, only_survivors=True)
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(["가격"] + m.columns)
    for row in m.rows:
        w.writerow([f"{row / EOK:.1f}"] + ["\n".join((f"[{e.priority}] " if e.priority else "") + e.label for e in m.get(row, c)) for c in m.columns])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=matrix.csv"})


# ---------------------------------------------------------------- 단지
@router.get("/complexes/{cid}", response_class=HTMLResponse)
def complex_detail(cid: int, request: Request, db: Session = Depends(get_db), _=Depends(require_login)):
    c = db.scalar(select(Complex).where(Complex.id == cid).options(selectinload(Complex.areas), selectinload(Complex.eval))) or _404()
    profile, br = get_budget(db)
    listings = db.scalars(
        select(Listing).where(Listing.complex_id == cid, Listing.status == "active", Listing.is_primary.is_(True))
        .order_by(Listing.trade_type.desc(), Listing.area_group, Listing.price)
    ).all()
    table = profile.interior_table
    enriched = []
    for li in listings:
        pyeong = (li.supply_m2 or 0) / 3.3058
        est = interior.estimate(li.repair_grade, pyeong, table, profile.interior_coef) if li.trade_type == "sale" else None
        enriched.append((li, est))
    trades = db.scalars(
        select(Trade).where(Trade.complex_id == cid, Trade.kind == "sale").order_by(Trade.deal_date.desc()).limit(30)
    ).all()
    visits = db.scalars(select(VisitNote).where(VisitNote.complex_id == cid).order_by(VisitNote.id.desc())).all()
    groups = sorted({a.area_group for a in c.areas} | {li.area_group for li in listings})
    return render(
        request, "complex_detail.html", c=c, ev=c.eval, br=br, listings=enriched, trades=trades,
        visits=visits, groups=groups, EVAL_GRADES=EVAL_GRADES, CHECKLIST=CHECKLIST, profile=profile,
        regulated=is_regulated(c.dong_code, profile.regulated_codes), land_permit=is_regulated(c.dong_code, profile.land_permit_codes),
    )


@router.get("/complexes/{cid}/series.json")
def complex_series(cid: int, area: int = 0, db: Session = Depends(get_db), _=Depends(require_login)):
    q = select(ComplexDaily).where(ComplexDaily.complex_id == cid).order_by(ComplexDaily.date)
    if area:
        q = q.where(ComplexDaily.area_group == area)
    else:
        q = q.where(ComplexDaily.area_group > 0)
    snaps = db.scalars(q).all()
    by_date: dict[str, dict] = {}
    for s in snaps:
        d = by_date.setdefault(s.date.isoformat(), {"ask": [], "jeonse": [], "cnt": 0})
        if s.ask_min:
            d["ask"].append(s.ask_min)
        if s.jeonse_min:
            d["jeonse"].append(s.jeonse_min)
        d["cnt"] += s.listing_cnt
    tq = select(Trade).where(Trade.complex_id == cid, Trade.cancelled.is_(False), Trade.kind.in_(["sale", "jeonse"]))
    if area:
        tq = tq.where(Trade.area_group == area)
    trades = db.scalars(tq.order_by(Trade.deal_date)).all()
    return JSONResponse({
        "snapshots": [
            {"date": k, "ask_min": min(v["ask"]) if v["ask"] else None, "jeonse_min": min(v["jeonse"]) if v["jeonse"] else None, "listing_cnt": v["cnt"]}
            for k, v in by_date.items()
        ],
        "trades": [{"date": t.deal_date.isoformat(), "price": t.price, "kind": t.kind, "area": t.area_group, "floor": t.floor} for t in trades],
    })


@router.post("/complexes/{cid}/eval")
def complex_eval(
    cid: int, request: Request,
    transport: str = Form(""), homogeneity: str = Form(""), school: str = Form(""), households: str = Form(""),
    excluded: str = Form(""), exclude_reason: str = Form(""), keep: str = Form(""), priority: str = Form(""), note: str = Form(""),
    db: Session = Depends(get_db), _=Depends(require_login),
):
    db.get(Complex, cid) or _404()
    ev = db.get(ComplexEval, cid) or ComplexEval(complex_id=cid)
    for f, v in (("transport", transport), ("homogeneity", homogeneity), ("school", school), ("households", households)):
        if v not in EVAL_GRADES:
            raise HTTPException(422)
        setattr(ev, f, v)
    ev.excluded, ev.keep = excluded == "on", keep == "on"
    ev.exclude_reason, ev.note = exclude_reason[:100], note
    ev.priority = int(priority) if priority.strip().isdigit() else None
    ev.source = "user"
    db.add(ev)
    db.commit()
    return back(request, f"/complexes/{cid}")


@router.post("/complexes/{cid}/quick")
def complex_quick(cid: int, request: Request, action: str = Form(...), value: str = Form(""), db: Session = Depends(get_db), _=Depends(require_login)):
    """매트릭스에서 빠른 조작: 탈락/남김/우선순위."""
    ev = db.get(ComplexEval, cid) or ComplexEval(complex_id=cid)
    if action == "exclude":
        ev.excluded, ev.exclude_reason = not ev.excluded, value[:100]
    elif action == "keep":
        ev.keep = not ev.keep
    elif action == "priority":
        ev.priority = int(value) if value.strip().isdigit() else None
    ev.source = "user"
    db.add(ev)
    db.commit()
    return back(request, "/matrix")


@router.post("/listings/{lid}/repair")
def listing_repair(lid: int, request: Request, grade: str = Form(""), db: Session = Depends(get_db), _=Depends(require_login)):
    li = db.get(Listing, lid) or _404()
    if grade and grade not in interior.GRADES:
        raise HTTPException(422)
    li.repair_grade, li.repair_source = grade, "user" if grade else ""
    db.commit()
    return back(request)


# ---------------------------------------------------------------- 매물 모아보기
@router.get("/listings", response_class=HTMLResponse)
def listings_page(
    request: Request, scope: str = "budget", new: int = 0, hide_flagged: int = 0,
    db: Session = Depends(get_db), _=Depends(require_login),
):
    profile, br = get_budget(db)
    date = data_date(db)
    q = (
        select(Listing).join(Complex)
        .where(Listing.status == "active", Listing.is_primary.is_(True), Listing.trade_type == "sale")
        .options(selectinload(Listing.complex).selectinload(Complex.eval))
    )
    if scope == "budget":
        q = q.where(Listing.price.between(br.price_lo, br.price_hi))
    elif scope == "keep":
        q = q.join(ComplexEval, ComplexEval.complex_id == Complex.id).where(or_(ComplexEval.keep.is_(True), ComplexEval.priority.is_not(None)))
    if new:
        q = q.where(Listing.first_seen == date)
    items = db.scalars(q.order_by(Listing.price).limit(500)).all()
    items = [li for li in items if not (li.complex.eval and li.complex.eval.excluded)]
    if hide_flagged:
        items = [li for li in items if "실거래대비저가" not in li.flags and "확인오래됨" not in li.flags]
    rows = []
    for li in items:
        est = interior.estimate(li.repair_grade, (li.supply_m2 or 0) / 3.3058, profile.interior_table, profile.interior_coef)
        rows.append((li, est))
    return render(request, "listings.html", rows=rows, br=br, date=date, scope=scope, new=new, hide_flagged=hide_flagged)


# ---------------------------------------------------------------- 뉴스 / 분석 기록
@router.get("/news", response_class=HTMLResponse)
def news_page(request: Request, date: dt.date | None = None, db: Session = Depends(get_db), _=Depends(require_login)):
    dates = db.scalars(select(NewsItem.date).distinct().order_by(NewsItem.date.desc()).limit(30)).all()
    date = date or (dates[0] if dates else dt.date.today())
    items = db.scalars(select(NewsItem).where(NewsItem.date == date).order_by(NewsItem.impact, NewsItem.id)).all()
    summary = db.scalar(select(DailyAnalysis).where(DailyAnalysis.date == date, DailyAnalysis.region_id == 0))
    return render(request, "news.html", items=items, dates=dates, date=date, summary=summary)


# ---------------------------------------------------------------- 지도 (A5)
@router.get("/map", response_class=HTMLResponse)
def map_page(request: Request, db: Session = Depends(get_db), _=Depends(require_login)):
    profile, br = get_budget(db)
    points = []
    for region, complexes, rows in rows_by_region(db, profile, data_date(db)):
        best: dict[int, object] = {}
        for r in rows:
            if r.price and (r.complex_id not in best or abs(r.price - br.price_hi) < abs(best[r.complex_id].price - br.price_hi)):
                best[r.complex_id] = r
        for c in complexes:
            if c.lat is None:
                continue
            r = best.get(c.id)
            points.append({
                "id": c.id, "name": c.name, "lat": c.lat, "lng": c.lng, "region": region.label,
                "price": r.price if r else None, "area": r.area_group if r else None, "year": c.built_year,
                "hh": c.households, "in_budget": br.contains(r.price) if r else False,
                "excluded": bool(c.eval and c.eval.excluded), "priority": c.eval.priority if c.eval else None,
                "station": c.station_name, "station_m": c.station_m,
            })
    return render(request, "map.html", points_json=json.dumps(points, ensure_ascii=False), br=br)


# ---------------------------------------------------------------- 임장/통화 (A7)
WEEKDAYS = "월화수목금토일"


def call_script(li: Listing | None, c: Complex, visit_at: dt.datetime | None) -> str:
    when = f"{WEEKDAYS[visit_at.weekday()]}요일 {visit_at.hour}시" if visit_at else "이번 주말"
    if li:
        return (
            f"사장님, 네이버 부동산 보고 전화드렸는데요. {c.name} {li.building} {fmt_eok(li.price)} "
            f"{li.floor_text.split('/')[0]}층에 나온 매물 {when}에 볼 수 있을까요? "
            f"사장님이 갖고 계신 다른 매물도 타입별·향별로 같이 보고 싶어요."
        )
    return f"사장님, {c.name} 매물 보려고 하는데요. {when}에 타입별·향별로 몇 개 같이 볼 수 있을까요?"


@router.get("/visits", response_class=HTMLResponse)
def visits_page(request: Request, db: Session = Depends(get_db), _=Depends(require_login)):
    visits = db.scalars(
        select(VisitNote).options(selectinload(VisitNote.complex), selectinload(VisitNote.listing))
        .order_by(VisitNote.status, VisitNote.call_by, VisitNote.visit_at)
    ).all()
    return render(request, "visits.html", visits=visits, CHECKLIST=CHECKLIST, script=call_script)


def _next_thursday(today: dt.date) -> dt.date:
    """PDF p32: 토요일에 볼 거면 늦어도 목요일부터 전화."""
    return today + dt.timedelta(days=(3 - today.weekday()) % 7)


@router.post("/visits")
def visit_add(
    request: Request, complex_id: int = Form(...), listing_id: str = Form(""), realtor_name: str = Form(""),
    realtor_phone: str = Form(""), visit_at: str = Form(""), call_by: str = Form(""), memo: str = Form(""),
    db: Session = Depends(get_db), _=Depends(require_login),
):
    db.get(Complex, complex_id) or _404()
    va = dt.datetime.fromisoformat(visit_at) if visit_at else None
    cb = dt.date.fromisoformat(call_by) if call_by else (
        (va.date() - dt.timedelta(days=2)) if va else _next_thursday(dt.date.today())
    )
    li_id = int(listing_id) if listing_id.isdigit() else None
    if li_id and not realtor_name:
        li = db.get(Listing, li_id)
        realtor_name = li.realtor if li else ""
    db.add(VisitNote(complex_id=complex_id, listing_id=li_id, realtor_name=realtor_name, realtor_phone=realtor_phone,
                     visit_at=va, call_by=cb, memo=memo, checklist={}))
    db.commit()
    return back(request, "/visits")


@router.post("/visits/{vid}/save")
async def visit_save(vid: int, request: Request, db: Session = Depends(get_db), _=Depends(require_login)):
    v = db.get(VisitNote, vid) or _404()
    form = await request.form()
    v.status = form.get("status", v.status) if form.get("status") in ("todo", "called", "visited", "drop") else v.status
    v.memo = str(form.get("memo", v.memo))
    v.realtor_phone = str(form.get("realtor_phone", v.realtor_phone))
    if form.get("visit_at"):
        v.visit_at = dt.datetime.fromisoformat(str(form["visit_at"]))
    v.checklist = {k: str(form.get(f"chk_{i}", "")) for i, k in enumerate(CHECKLIST) if form.get(f"chk_{i}")}
    db.commit()
    return back(request, "/visits")


# ---------------------------------------------------------------- 설정
@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), _=Depends(require_login)):
    profile, br = get_budget(db)
    loan = None
    if profile.annual_income:
        loan = loan_limit(
            br.price_hi, annual_income=profile.annual_income, other_debt_payment=profile.other_debt_payment,
            loan_rate=profile.loan_rate, stress_rate=profile.stress_rate, years=profile.loan_years,
            dsr_limit=profile.dsr_limit, ltv_pct=profile.ltv_pct, cap=profile.loan_cap,
        )
    return render(request, "settings.html", p=profile, br=br, loan=loan, table_json=json.dumps(profile.interior_table, ensure_ascii=False, indent=1))


@router.post("/settings")
async def settings_save(request: Request, db: Session = Depends(get_db), _=Depends(require_login)):
    p = get_profile(db)
    form = await request.form()

    def num(name, cast=int):
        v = str(form.get(name, "")).replace(",", "").strip()
        return cast(v) if v else cast(0)

    for f in ("cash", "loan", "tolerance", "extra_costs", "house_count_after", "min_households", "annual_income",
              "other_debt_payment", "loan_years", "loan_cap"):
        setattr(p, f, num(f))
    for f in ("interior_coef", "area_min", "area_max", "loan_rate", "stress_rate", "dsr_limit", "ltv_pct", "ltv_pct_regulated"):
        setattr(p, f, num(f, float))
    p.mode = "invest" if form.get("mode") == "invest" else "live"
    p.first_home = form.get("first_home") == "on"
    p.brokerage_vat = form.get("brokerage_vat") == "on"
    p.regulated_codes = [x.strip() for x in str(form.get("regulated_codes", "")).replace(",", "\n").splitlines() if x.strip()]
    p.land_permit_codes = [x.strip() for x in str(form.get("land_permit_codes", "")).replace(",", "\n").splitlines() if x.strip()]
    try:
        table = json.loads(str(form.get("interior_table", "{}")))
        if all(isinstance(v, list) and len(v) == 2 for v in table.values()):
            p.interior_table = table
    except json.JSONDecodeError:
        pass
    db.commit()
    return RedirectResponse("/settings", status_code=303)


# ---------------------------------------------------------------- 운영: 수집 기록, 수동 매칭
@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, db: Session = Depends(get_db), _=Depends(require_login)):
    jobs = db.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(40)).all()
    unmatched = db.execute(
        select(Trade.apt_seq, Trade.apt_name, Trade.dong_code, Trade.jibun, func.count())
        .where(Trade.complex_id.is_(None), Trade.kind == "sale", Trade.apt_seq != "")
        .group_by(Trade.apt_seq, Trade.apt_name, Trade.dong_code, Trade.jibun)
        .order_by(func.count().desc()).limit(50)
    ).all()
    return render(request, "jobs.html", jobs=jobs, unmatched=unmatched)


@router.post("/match")
def manual_match(request: Request, apt_seq: str = Form(...), complex_id: int = Form(...), db: Session = Depends(get_db), _=Depends(require_login)):
    from collector.matching import link_manually

    db.get(Complex, complex_id) or _404()
    link_manually(db, complex_id, apt_seq)
    db.commit()
    return back(request, "/jobs")
