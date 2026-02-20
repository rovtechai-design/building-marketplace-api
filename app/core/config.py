from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "local"
    APP_NAME: str = "Building Marketplace API"

    DATABASE_URL: str

    FIREBASE_SERVICE_ACCOUNT_JSON: str

    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_STORAGE_BUCKET: str = "listings"
    SUPABASE_PUBLIC_BASE: str


settings = Settings()
