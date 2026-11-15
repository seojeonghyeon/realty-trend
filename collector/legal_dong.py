"""법정동코드 적재/갱신 (월 1회).

    python -m collector.legal_dong --api                 # data.go.kr 행정안전부_법정동코드 API
    python -m collector.legal_dong --file 법정동코드.txt   # code.go.kr '법정동코드 전체자료' (탭 구분)

리(ri) 단위는 제외. 목록에서 사라진 코드는 active=False (관심 지역 경고에 사용).
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import LegalDong
from collector.sources.molit import MolitClient

log = logging.getLogger("collector")


def split_name(code: str, full: str) -> tuple[str, str, str]:
    """코드 단위로 (시도, 시군구, 읍면동) 분리. '경기도 수원시 영통구 영통동' → ('경기도','수원시 영통구','영통동')."""
    parts = full.split()
    sido, rest = (parts[0] if parts else ""), parts[1:]
    if code[2:] == "00000000":
        return sido, "", ""
    if code[5:] == "00000":
        return sido, " ".join(rest), ""
    return sido, " ".join(rest[:-1]), (rest[-1] if rest else "")


def rows_from_file(path: Path) -> list[tuple[str, str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    out = []
    for line in text.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 3 and cols[2].strip() == "존재":
            out.append((cols[0].strip(), cols[1].strip()))
    return out


def rows_from_api() -> list[tuple[str, str]]:
    client = MolitClient(get_settings().molit_service_key)
    return [(r["region_cd"], r["locatadd_nm"].strip()) for r in client.legal_dongs()]


def load(rows: list[tuple[str, str]]) -> dict:
    init_db()
    db = SessionLocal()
    try:
        rows = [(c, n) for c, n in rows if len(c) == 10 and c[8:] == "00"]
        seen = set()
        existing = {ld.code: ld for ld in db.scalars(select(LegalDong))}
        added = 0
        for code, name in rows:
            sido, sgg, emd = split_name(code, name)
            ld = existing.get(code)
            if ld is None:
                ld = LegalDong(code=code)
                db.add(ld)
                added += 1
            ld.name, ld.sido, ld.sigungu, ld.eupmyeondong, ld.active = name, sido, sgg, emd, True
            seen.add(code)
        retired = 0
        if rows:
            for code, ld in existing.items():
                if code not in seen and ld.active:
                    ld.active = False
                    retired += 1
        db.commit()
        return {"total": len(seen), "added": added, "retired": retired}
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--api", action="store_true")
    g.add_argument("--file", type=Path)
    a = ap.parse_args()
    rows = rows_from_api() if a.api else rows_from_file(a.file)
    print(load(rows))


if __name__ == "__main__":
    main()
