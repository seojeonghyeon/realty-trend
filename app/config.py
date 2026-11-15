from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./realty.db"
    secret_key: str = "dev-secret"
    admin_username: str = "admin"
    admin_password: str = "admin"
    routine_token: str = ""

    molit_service_key: str = ""
    molit_daily_limit: int = 8000

    naver_delay_sec: float = 1.5
    naver_daily_limit: int = 3000

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    kakao_rest_key: str = ""
    public_base_url: str = "http://localhost:8000"

    # 쿠키 Secure 플래그 (HTTPS 뒤에서 운영할 때 true)
    cookie_secure: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
