"""매일 DB 백업: DATABASE_URL 의 MariaDB 를 덤프해 /backup 에 gzip 저장 (14일 보관).

로컬 compose(컨테이너 DB)와 운영(NAS MariaDB 패키지, host.docker.internal) 모두 같은 방식으로 동작.
비밀번호는 명령줄 대신 MYSQL_PWD 환경변수로 전달한다.
"""
from __future__ import annotations

import datetime as dt
import gzip
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy.engine import make_url

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backup"))
KEEP_DAYS = int(os.environ.get("BACKUP_KEEP_DAYS", "14"))


def main() -> int:
    url = make_url(os.environ["DATABASE_URL"])
    if not url.drivername.startswith(("mysql", "mariadb")):
        print(f"skip: {url.drivername} 는 백업 대상 아님")
        return 0
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / f"realty-{dt.date.today():%Y%m%d}.sql.gz"
    cmd = ["mariadb-dump", "-h", url.host or "localhost", "-P", str(url.port or 3306), "-u", url.username or "",
           "--single-transaction", "--routines", "--default-character-set=utf8mb4", url.database or ""]
    env = {**os.environ, "MYSQL_PWD": url.password or ""}
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, env=env) as p, gzip.open(out, "wb") as gz:
        shutil.copyfileobj(p.stdout, gz)
    if p.returncode != 0:
        out.unlink(missing_ok=True)
        print("backup failed", file=sys.stderr)
        return 1
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in BACKUP_DIR.glob("realty-*.sql.gz"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
    print(f"backup ok: {out.name} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
