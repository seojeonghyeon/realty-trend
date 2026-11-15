import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
_tmp = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite:///{_tmp}/test.db",
    ROUTINE_TOKEN="test-token",
    ADMIN_USERNAME="admin",
    ADMIN_PASSWORD="pw",
    SECRET_KEY="x",
    MOLIT_SERVICE_KEY="",
    TELEGRAM_BOT_TOKEN="",
    KAKAO_REST_KEY="",
)
