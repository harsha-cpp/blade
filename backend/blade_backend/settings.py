from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import PydanticBaseSettingsSource

BACKEND_ROOT = Path(__file__).resolve().parents[1]

_CSV_LIST_FIELDS = frozenset(
    {
        "cors_origins",
        "api_rate_limit_exempt_paths",
        "landing_origins",
        "platform_admin_emails",
    }
)
_RELAXED_ENVS = frozenset({"local", "test"})
DEV_AUDIT_HMAC_KEY = "blade-local-audit-hmac-key-not-for-deployment"
MINIMUM_DEPLOYED_SECRET_LENGTH = 32


class _CsvOrJsonDecoder:
    def decode_complex_value(
        self,
        field_name: str,
        field: object,
        value: object,
    ) -> object:
        if field_name in _CSV_LIST_FIELDS and isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if not stripped.startswith(("[", "{")):
                return [item.strip() for item in stripped.split(",") if item.strip()]
        return super().decode_complex_value(field_name, field, value)  # type: ignore[misc]


class CsvOrJsonEnvSource(_CsvOrJsonDecoder, EnvSettingsSource):
    pass


class CsvOrJsonDotEnvSource(_CsvOrJsonDecoder, DotEnvSettingsSource):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = Field(default="local")
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    cors_origins: list[str] = Field(default_factory=list)
    landing_origins: list[str] = Field(default_factory=list)

    database_url: str = Field(default="postgresql+asyncpg://blade:blade@localhost:5433/blade")
    database_sync_url: str = Field(default="postgresql+psycopg://blade:blade@localhost:5433/blade")
    database_pool_size: int = Field(default=5)
    database_max_overflow: int = Field(default=5)
    database_disable_prepared_statements: bool = Field(default=True)
    readiness_timeout_seconds: float = Field(default=5.0)
    redis_url: str = Field(default="redis://localhost:6379/0")
    celery_broker_url: str = Field(default="redis://localhost:6379/0")
    celery_result_backend: str = Field(default="redis://localhost:6379/0")

    jwt_secret: SecretStr = Field(default=SecretStr("change-me-change-me-change-me-32!"))
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_ttl_seconds: int = Field(default=900)
    jwt_refresh_ttl_days: int = Field(default=30)
    refresh_cookie_name: str = Field(default="blade_refresh")
    refresh_cookie_secure: bool = Field(default=False)
    refresh_cookie_samesite: Literal["lax", "strict", "none"] = Field(default="lax")
    login_rate_limit_per_minute: int = Field(default=5)
    login_rate_limit_per_hour: int = Field(default=20)
    trusted_proxy_count: int = Field(default=1, ge=0, le=8)
    allow_open_signup: bool = Field(default=True)
    refresh_rotation_grace_seconds: int = Field(default=60, ge=0, le=600)
    password_reset_ttl_seconds: int = Field(default=3600, ge=300, le=86400)
    email_verification_ttl_hours: int = Field(default=24, ge=1, le=168)
    device_token_ttl_days: int = Field(default=365, ge=1, le=3650)
    mfa_issuer: str = Field(default="Blade DLP")
    mfa_encryption_key: SecretStr | None = Field(default=None)
    audit_hmac_key: SecretStr | None = Field(default=None)

    api_rate_limit_per_minute: int = Field(default=600, ge=1)
    api_rate_limit_exempt_paths: list[str] = Field(
        default_factory=lambda: ["/api/v1/scan", "/api/v1/events"]
    )
    api_rate_limit_max_keys: int = Field(default=10_000, ge=100)

    mailer_backend: Literal["console", "smtp"] = Field(default="console")
    smtp_host: str = Field(default="localhost")
    smtp_port: int = Field(default=587)
    smtp_user: str | None = Field(default=None)
    smtp_password: SecretStr | None = Field(default=None)
    smtp_from: str = Field(default="Blade DLP <no-reply@localhost>")
    smtp_tls: bool = Field(default=True)
    app_base_url: str = Field(default="http://localhost:5173")
    access_request_notify_email: str | None = Field(default=None)
    platform_admin_emails: list[str] = Field(default_factory=list)

    attachment_temp_dir: Path = Field(default=Path("/tmp/blade-attachments"))
    quarantine_storage_dir: Path = Field(default=Path("/tmp/blade-quarantine"))
    scan_deep_scan_threshold_bytes: int = Field(default=10 * 1024 * 1024)
    storage_backend: Literal["local", "s3"] = Field(default="local")
    s3_bucket: str = Field(default="blade-attachments")
    s3_prefix: str = Field(default="queued-scans")
    s3_region: str = Field(default="ap-south-1")
    s3_endpoint_url: str | None = Field(default=None)
    s3_access_key_id: str | None = Field(default=None)
    s3_secret_access_key: SecretStr | None = Field(default=None)
    s3_server_side_encryption: str | None = Field(default="AES256")

    scan_max_concurrency: int = Field(default=4, ge=1, le=64)
    scan_process_pool_enabled: bool = Field(default=True)
    scan_deadline_seconds: float = Field(default=8.0, gt=0, le=600)
    scan_worker_max_tasks: int = Field(default=100, ge=1, le=100_000)
    scan_max_attachment_bytes: int = Field(default=26_214_400, ge=1024, le=1_073_741_824)
    scan_rate_limit_per_device_per_minute: int = Field(default=60, ge=1)
    scan_rate_limit_per_org_per_minute: int = Field(default=600, ge=1)

    api_max_request_bytes: int = Field(default=2_097_152, ge=1024, le=1_073_741_824)

    celery_task_soft_time_limit: int = Field(default=300, ge=1, le=86_400)
    celery_task_time_limit: int = Field(default=330, ge=2, le=86_400)
    token_retention_days: int = Field(default=30, ge=1, le=3650)
    access_request_retention_days: int = Field(default=365, ge=0, le=3650)

    sentry_dsn: str | None = Field(default=None)

    @field_validator(
        "cors_origins",
        "api_rate_limit_exempt_paths",
        "landing_origins",
        "platform_admin_emails",
        mode="before",
    )
    @classmethod
    def parse_csv_list(cls, value: object) -> list[str] | object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator(
        "sentry_dsn",
        "s3_endpoint_url",
        "s3_access_key_id",
        "s3_server_side_encryption",
        "smtp_user",
        "access_request_notify_email",
        "audit_hmac_key",
        "mfa_encryption_key",
        mode="before",
    )
    @classmethod
    def empty_string_as_none(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            return value if value.get_secret_value().strip() else None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("refresh_cookie_samesite", mode="before")
    @classmethod
    def normalize_samesite(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @model_validator(mode="after")
    def enforce_task_time_limits(self) -> Settings:
        if self.celery_task_time_limit <= self.celery_task_soft_time_limit:
            raise ValueError(
                "CELERY_TASK_TIME_LIMIT must be greater than CELERY_TASK_SOFT_TIME_LIMIT: "
                "the soft limit exists so the task can record a failure before it is killed"
            )
        return self

    @model_validator(mode="after")
    def enforce_token_retention_outlives_refresh_tokens(self) -> Settings:
        if self.token_retention_days < self.jwt_refresh_ttl_days:
            raise ValueError(
                "TOKEN_RETENTION_DAYS must be at least JWT_REFRESH_TTL_DAYS "
                f"({self.jwt_refresh_ttl_days}): the daily purge would otherwise delete the "
                "rotated row that a replayed refresh token is matched against, and a stolen "
                "token would read as unknown instead of revoking its whole family"
            )
        return self

    @model_validator(mode="after")
    def enforce_deployed_security(self) -> Settings:
        if not self.hardened_config_required:
            return self
        deployment = f"APP_ENV={self.app_env}"
        if not self.refresh_cookie_secure:
            raise ValueError(f"REFRESH_COOKIE_SECURE must be true when {deployment}")
        if self.jwt_secret_value.startswith("change-me"):
            raise ValueError(f"JWT_SECRET must be changed when {deployment}")
        if self.storage_backend == "local":
            raise ValueError(
                f"STORAGE_BACKEND must be 's3' when {deployment}: local storage requires "
                "the API and the Celery worker to share a filesystem"
            )
        if self.mailer_backend == "console":
            raise ValueError(
                f"MAILER_BACKEND must be 'smtp' when {deployment}: invites and password "
                "resets are undeliverable when mail only goes to the log"
            )
        if self.audit_hmac_key is None:
            raise ValueError(
                f"AUDIT_HMAC_KEY must be set when {deployment}: without a deployment key "
                "the audit row signatures can be recomputed by anyone who can write rows"
            )
        if len(self.audit_hmac_key_value) < MINIMUM_DEPLOYED_SECRET_LENGTH:
            raise ValueError(
                f"AUDIT_HMAC_KEY must be at least {MINIMUM_DEPLOYED_SECRET_LENGTH} characters "
                f"when {deployment}: it is the only part of an audit row that someone with "
                "write access to the table cannot recompute"
            )
        mfa_key = self.mfa_encryption_key
        if mfa_key is not None and len(mfa_key.get_secret_value()) < MINIMUM_DEPLOYED_SECRET_LENGTH:
            raise ValueError(
                f"MFA_ENCRYPTION_KEY must be at least {MINIMUM_DEPLOYED_SECRET_LENGTH} "
                f"characters when {deployment}: it encrypts every stored TOTP seed"
            )
        if not self.cors_origins:
            raise ValueError(
                f"CORS_ORIGINS must list the dashboard origin when {deployment}: it is the "
                "allow-list the cross-site guard on /auth/refresh and /auth/logout checks, "
                "so an empty list answers every browser refresh and every sign-out with 403 "
                '(for example CORS_ORIGINS=["https://app.example"])'
            )
        if self.trusted_proxy_count < 1:
            raise ValueError(
                f"TRUSTED_PROXY_COUNT must be at least 1 when {deployment}: uvicorn is "
                "started with --proxy-headers --forwarded-allow-ips '*', so at 0 every "
                "IP-keyed limit is keyed on an address the caller writes itself in "
                "X-Forwarded-For. Count the proxies that append to that header: 1 for a "
                "single reverse proxy, 2 for Cloudflare in front of the platform proxy"
            )
        if self.allow_open_signup:
            raise ValueError(
                f"ALLOW_OPEN_SIGNUP must be false when {deployment}: open signup mints an "
                "active owner for any address that is typed in, and the platform operator "
                "gate is keyed on owning an organization with a listed address"
            )
        return self

    @property
    def jwt_secret_value(self) -> str:
        return self.jwt_secret.get_secret_value()

    @property
    def audit_hmac_key_value(self) -> str:
        key = self.audit_hmac_key
        return key.get_secret_value() if key is not None else DEV_AUDIT_HMAC_KEY

    @property
    def mfa_encryption_key_value(self) -> str:
        key = self.mfa_encryption_key
        return key.get_secret_value() if key is not None else self.jwt_secret_value

    @property
    def platform_admin_email_set(self) -> frozenset[str]:
        return frozenset(
            entry.strip().lower() for entry in self.platform_admin_emails if entry.strip()
        )

    @property
    def hardened_config_required(self) -> bool:
        return self.app_env.strip().lower() not in _RELAXED_ENVS

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del env_settings, dotenv_settings
        return (
            init_settings,
            CsvOrJsonEnvSource(settings_cls),
            CsvOrJsonDotEnvSource(
                settings_cls,
                env_file=settings_cls.model_config.get("env_file"),
                env_file_encoding="utf-8",
            ),
            file_secret_settings,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
