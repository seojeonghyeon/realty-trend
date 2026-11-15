"""https://api.time-page.kr/realty 처럼 경로 prefix 뒤에서 서비스할 때 링크·리다이렉트·쿠키가 prefix 를 따르는지."""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_templates_have_no_hardcoded_root_links():
    bad = []
    for f in (ROOT / "app/templates").glob("*.html"):
        for m in re.finditer(r"""(?:href|src|action|hx-get|hx-post)=["'`]/(?!/)|fetch\(`/|`/complexes""", f.read_text()):
            bad.append(f"{f.name}: {m.group(0)}")
    assert not bad, bad


SCRIPT = r"""
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as c:
    r = c.get("/regions", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/realty/login?next=/regions", r.headers
    page = c.get("/login").text
    assert 'action="/realty/login"' in page and 'href="/realty/static/style.css"' in page, page[:800]
    r = c.post("/login", data={"username": "admin", "password": "pw", "next": "/regions"}, follow_redirects=False)
    assert r.headers["location"] == "/realty/regions", r.headers
    assert "path=/realty" in r.headers["set-cookie"] and "realty_session=" in r.headers["set-cookie"]
    # 프록시가 prefix 를 떼고 넘긴 요청 (쿠키 path 는 브라우저 기준이라 TestClient 에서는 직접 전달)
    c.cookies.set("realty_session", r.cookies["realty_session"])
    home = c.get("/").text
    assert 'href="/realty/regions"' in home and 'href="/regions"' not in home
    r = c.post("/login", data={"username": "admin", "password": "pw", "next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/realty/"
print("OK")
"""


def test_prefix_links_redirects_cookie():
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp}/p.db", "PUBLIC_BASE_URL": "http://testserver/realty",
           "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "pw", "SECRET_KEY": "x", "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]
