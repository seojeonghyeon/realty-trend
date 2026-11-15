"""DB 모델. 금액 단위는 모두 '만원'(int), 면적은 m²."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> dt.datetime:
    return dt.datetime.now()


# ---------------------------------------------------------------- 지역
class LegalDong(Base):
    """법정동코드 (10자리). 리(ri) 단위는 적재하지 않는다."""

    __tablename__ = "legal_dong"
    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)  # 전체 명칭
    sido: Mapped[str] = mapped_column(String(20), default="")
    sigungu: Mapped[str] = mapped_column(String(40), default="")
    eupmyeondong: Mapped[str] = mapped_column(String(40), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def level(self) -> str:
        if self.code[2:] == "00000000":
            return "sido"
        if self.code[5:] == "00000":
            return "sigungu"
        return "dong"


class WatchRegion(Base):
    """사용자 관심 지역. 시도/시군구/읍면동 어느 단위든 등록 가능."""

    __tablename__ = "watch_region"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(10))  # sido|sigungu|dong
    code: Mapped[str] = mapped_column(String(10), index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    group_label: Mapped[str] = mapped_column(String(50), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    @property
    def label(self) -> str:
        return self.group_label or self.display_name


# ---------------------------------------------------------------- 예산/설정
DEFAULT_INTERIOR_TABLE = {
    # 항목: [24평, 32평] (만원) — PDF p34
    "샷시": [1000, 1300],
    "화장실": [300, 500],
    "필름": [300, 300],
    "싱크대": [200, 350],
    "타일": [120, 150],
    "조명": [110, 130],
    "목공": [110, 120],
    "도배": [100, 120],
    "페인트": [100, 120],
    "중문": [100, 100],
    "장판": [80, 110],
    "신발장": [55, 55],
    "창고장": [35, 35],
    "방문": [20, 20],
}


class BudgetProfile(Base):
    __tablename__ = "budget_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(10), default="live")  # live|invest
    cash: Mapped[int] = mapped_column(Integer, default=40000)
    loan: Mapped[int] = mapped_column(Integer, default=40000)
    tolerance: Mapped[int] = mapped_column(Integer, default=3000)
    extra_costs: Mapped[int] = mapped_column(Integer, default=200)  # 이사비+법무사비 등
    brokerage_vat: Mapped[bool] = mapped_column(Boolean, default=False)
    first_home: Mapped[bool] = mapped_column(Boolean, default=False)
    house_count_after: Mapped[int] = mapped_column(Integer, default=1)  # 취득 후 주택 수
    interior_coef: Mapped[float] = mapped_column(Float, default=1.2)
    interior_table: Mapped[dict] = mapped_column(JSON, default=lambda: dict(DEFAULT_INTERIOR_TABLE))
    min_households: Mapped[int] = mapped_column(Integer, default=0)
    area_min: Mapped[float] = mapped_column(Float, default=0)
    area_max: Mapped[float] = mapped_column(Float, default=200)
    # A4 대출 한도 추정
    annual_income: Mapped[int] = mapped_column(Integer, default=0)
    other_debt_payment: Mapped[int] = mapped_column(Integer, default=0)  # 연간 기타부채 원리금
    loan_rate: Mapped[float] = mapped_column(Float, default=4.0)
    stress_rate: Mapped[float] = mapped_column(Float, default=1.5)
    loan_years: Mapped[int] = mapped_column(Integer, default=30)
    dsr_limit: Mapped[float] = mapped_column(Float, default=40.0)
    ltv_pct: Mapped[float] = mapped_column(Float, default=70.0)
    ltv_pct_regulated: Mapped[float] = mapped_column(Float, default=40.0)
    loan_cap: Mapped[int] = mapped_column(Integer, default=0)  # 0 = 상한 없음
    # 규제지역/토지거래허가구역 시군구·법정동 코드 접두사 목록 (사용자 편집)
    regulated_codes: Mapped[list] = mapped_column(JSON, default=list)
    land_permit_codes: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


# ---------------------------------------------------------------- 단지
class Complex(Base):
    __tablename__ = "complex"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    naver_no: Mapped[str | None] = mapped_column(String(20), unique=True)
    molit_apt_seq: Mapped[str | None] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(100))
    dong_code: Mapped[str] = mapped_column(String(10), index=True)
    address: Mapped[str] = mapped_column(String(200), default="")
    jibun: Mapped[str] = mapped_column(String(30), default="")
    built_year: Mapped[int | None] = mapped_column(Integer)
    households: Mapped[int | None] = mapped_column(Integer)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    corridor: Mapped[str] = mapped_column(String(20), default="")  # 계단식/복도식/혼합
    station_name: Mapped[str] = mapped_column(String(50), default="")
    station_m: Mapped[int | None] = mapped_column(Integer)
    school_name: Mapped[str] = mapped_column(String(50), default="")
    school_m: Mapped[int | None] = mapped_column(Integer)
    list_deal_count: Mapped[int] = mapped_column(Integer, default=0)
    list_lease_count: Mapped[int] = mapped_column(Integer, default=0)
    last_listed: Mapped[dt.date | None] = mapped_column(Date)  # 단지 목록에서 마지막으로 본 날
    detail_fetched: Mapped[dt.date | None] = mapped_column(Date)
    articles_fetched: Mapped[dt.date | None] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    areas: Mapped[list[ComplexArea]] = relationship(back_populates="complex", cascade="all, delete-orphan")
    eval: Mapped[ComplexEval | None] = relationship(back_populates="complex", uselist=False, cascade="all, delete-orphan")


class ComplexArea(Base):
    __tablename__ = "complex_area"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complex_id: Mapped[int] = mapped_column(ForeignKey("complex.id", ondelete="CASCADE"), index=True)
    pyeong_no: Mapped[str] = mapped_column(String(10), default="")
    area_name: Mapped[str] = mapped_column(String(20), default="")
    supply_m2: Mapped[float] = mapped_column(Float, default=0)
    exclusive_m2: Mapped[float] = mapped_column(Float, default=0)
    rooms: Mapped[int | None] = mapped_column(Integer)
    baths: Mapped[int | None] = mapped_column(Integer)
    entrance: Mapped[str] = mapped_column(String(20), default="")
    households: Mapped[int | None] = mapped_column(Integer)

    complex: Mapped[Complex] = relationship(back_populates="areas")

    @property
    def area_group(self) -> int:
        return area_group(self.exclusive_m2)

    @property
    def pyeong(self) -> float:
        return round(self.supply_m2 / 3.3058, 1) if self.supply_m2 else 0


def area_group(exclusive_m2: float | None) -> int:
    """유사 평형 합치기: 전용면적 정수부 (84.97 → 84)."""
    return int(exclusive_m2 or 0)


class ComplexDaily(Base):
    """일별 스냅샷 — 동향 차트의 원천. area_group=0 은 단지 전체 요약."""

    __tablename__ = "complex_daily"
    __table_args__ = (UniqueConstraint("complex_id", "area_group", "date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complex_id: Mapped[int] = mapped_column(ForeignKey("complex.id", ondelete="CASCADE"), index=True)
    area_group: Mapped[int] = mapped_column(Integer, default=0)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    ask_min: Mapped[int | None] = mapped_column(Integer)
    ask_median: Mapped[int | None] = mapped_column(Integer)
    listing_cnt: Mapped[int] = mapped_column(Integer, default=0)
    jeonse_min: Mapped[int | None] = mapped_column(Integer)
    jeonse_cnt: Mapped[int] = mapped_column(Integer, default=0)


class ComplexEval(Base):
    """정성평가 (PDF p16). source=user 인 값은 Claude 가 덮어쓰지 않는다."""

    __tablename__ = "complex_eval"
    complex_id: Mapped[int] = mapped_column(ForeignKey("complex.id", ondelete="CASCADE"), primary_key=True)
    transport: Mapped[str] = mapped_column(String(10), default="")
    homogeneity: Mapped[str] = mapped_column(String(10), default="")
    school: Mapped[str] = mapped_column(String(10), default="")
    households: Mapped[str] = mapped_column(String(10), default="")
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    exclude_reason: Mapped[str] = mapped_column(String(100), default="")
    keep: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(10), default="auto")  # auto|claude|user
    claude_suggestion: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    complex: Mapped[Complex] = relationship(back_populates="eval")


# ---------------------------------------------------------------- 거래/매물
class Trade(Base):
    """국토부 실거래 (매매/전세/월세)."""

    __tablename__ = "trade"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[str] = mapped_column(String(10), index=True)  # sale|jeonse|monthly
    apt_seq: Mapped[str] = mapped_column(String(30), default="", index=True)
    complex_id: Mapped[int | None] = mapped_column(ForeignKey("complex.id", ondelete="SET NULL"), index=True)
    lawd_cd: Mapped[str] = mapped_column(String(5), index=True)
    dong_code: Mapped[str] = mapped_column(String(10), default="", index=True)
    apt_name: Mapped[str] = mapped_column(String(100), default="")
    jibun: Mapped[str] = mapped_column(String(30), default="")
    built_year: Mapped[int | None] = mapped_column(Integer)
    exclusive_m2: Mapped[float] = mapped_column(Float, default=0)
    area_group: Mapped[int] = mapped_column(Integer, default=0)
    deal_date: Mapped[dt.date] = mapped_column(Date, index=True)
    price: Mapped[int] = mapped_column(Integer, default=0)  # 매매가 또는 보증금
    monthly: Mapped[int] = mapped_column(Integer, default=0)
    floor: Mapped[int | None] = mapped_column(Integer)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


class Listing(Base):
    """네이버 매물."""

    __tablename__ = "listing"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_no: Mapped[str] = mapped_column(String(20), unique=True)
    complex_id: Mapped[int] = mapped_column(ForeignKey("complex.id", ondelete="CASCADE"), index=True)
    trade_type: Mapped[str] = mapped_column(String(10), default="sale")  # sale|jeonse
    area_name: Mapped[str] = mapped_column(String(20), default="")
    supply_m2: Mapped[float] = mapped_column(Float, default=0)
    exclusive_m2: Mapped[float] = mapped_column(Float, default=0)
    area_group: Mapped[int] = mapped_column(Integer, default=0, index=True)
    building: Mapped[str] = mapped_column(String(30), default="")
    floor_text: Mapped[str] = mapped_column(String(20), default="")
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floor: Mapped[int | None] = mapped_column(Integer)
    direction: Mapped[str] = mapped_column(String(20), default="")
    price: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    realtor: Mapped[str] = mapped_column(String(100), default="")
    realtor_count: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_at: Mapped[dt.date | None] = mapped_column(Date)
    first_seen: Mapped[dt.date] = mapped_column(Date, index=True)
    last_seen: Mapped[dt.date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(10), default="active", index=True)
    prev_price: Mapped[int | None] = mapped_column(Integer)
    price_changed_at: Mapped[dt.date | None] = mapped_column(Date)
    repair_grade: Mapped[str] = mapped_column(String(10), default="")  # 특올수리|올수리|부분수리|미수리|''
    repair_source: Mapped[str] = mapped_column(String(10), default="")  # rule|claude|user
    flags: Mapped[list] = mapped_column(JSON, default=list)
    dup_key: Mapped[str] = mapped_column(String(120), default="", index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)  # 중복 그룹 대표
    dup_count: Mapped[int] = mapped_column(Integer, default=1)  # 묶인 매물 수(대표에만 의미)

    complex: Mapped[Complex] = relationship()


# ---------------------------------------------------------------- 분석/뉴스
class DailyAnalysis(Base):
    __tablename__ = "daily_analysis"
    __table_args__ = (UniqueConstraint("date", "region_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    region_id: Mapped[int] = mapped_column(Integer, default=0)  # 0 = 전체 요약
    markdown: Mapped[str] = mapped_column(Text, default="")
    highlights: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class NewsItem(Base):
    __tablename__ = "news_item"
    __table_args__ = (UniqueConstraint("date", "url"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(100), default="")
    published: Mapped[str] = mapped_column(String(30), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[str] = mapped_column(String(20), default="기타")
    sentiment: Mapped[str] = mapped_column(String(10), default="중립")  # 호재|악재|중립


# ---------------------------------------------------------------- 임장/통화 (A7)
class VisitNote(Base):
    __tablename__ = "visit_note"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complex_id: Mapped[int] = mapped_column(ForeignKey("complex.id", ondelete="CASCADE"), index=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listing.id", ondelete="SET NULL"))
    realtor_name: Mapped[str] = mapped_column(String(100), default="")
    realtor_phone: Mapped[str] = mapped_column(String(30), default="")
    call_by: Mapped[dt.date | None] = mapped_column(Date)
    visit_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    memo: Mapped[str] = mapped_column(Text, default="")
    checklist: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="todo")  # todo|called|visited|drop
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    complex: Mapped[Complex] = relationship()
    listing: Mapped[Listing | None] = relationship()


# ---------------------------------------------------------------- 운영
class JobRun(Base):
    __tablename__ = "job_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job: Mapped[str] = mapped_column(String(30), index=True)
    run_date: Mapped[dt.date] = mapped_column(Date, index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), default="running")  # running|ok|partial|failed
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    log: Mapped[str] = mapped_column(Text, default="")


class ApiQuota(Base):
    __tablename__ = "api_quota"
    __table_args__ = (UniqueConstraint("source", "date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    date: Mapped[dt.date] = mapped_column(Date)
    count: Mapped[int] = mapped_column(Integer, default=0)


class BackfillTask(Base):
    """전고점 계산용 과거 실거래 백필 큐."""

    __tablename__ = "backfill_task"
    __table_args__ = (UniqueConstraint("kind", "lawd_cd", "ym"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # sale|rent
    lawd_cd: Mapped[str] = mapped_column(String(5), index=True)
    ym: Mapped[str] = mapped_column(String(6))
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
