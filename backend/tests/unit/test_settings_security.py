from __future__ import annotations

import pytest
from pydantic import SecretStr

from blade_backend.settings import DEV_AUDIT_HMAC_KEY, Settings

PLACEHOLDER_SECRET = SecretStr("change-me-change-me-change-me-32!")
REAL_SECRET = SecretStr("6c2f8a1d4e7b09c3f5a8d2b6e1c47f90")


def _deployed(app_env: str, **overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "app_env": app_env,
        "refresh_cookie_secure": True,
        "jwt_secret": REAL_SECRET,
        "storage_backend": "s3",
        "mailer_backend": "smtp",
        "audit_hmac_key": REAL_SECRET,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # pyright: ignore[reportArgumentType]


@pytest.mark.unit
@pytest.mark.parametrize("app_env", ["staging", "uat", "prod", "production", "Preview"])
def test_a_deployed_env_refuses_the_placeholder_jwt_secret(app_env: str) -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        _deployed(app_env, jwt_secret=PLACEHOLDER_SECRET)


@pytest.mark.unit
@pytest.mark.parametrize("app_env", ["staging", "uat", "prod", "production"])
def test_a_deployed_env_refuses_an_insecure_refresh_cookie(app_env: str) -> None:
    with pytest.raises(ValueError, match="REFRESH_COOKIE_SECURE"):
        _deployed(app_env, refresh_cookie_secure=False)


@pytest.mark.unit
def test_a_deployed_env_refuses_the_console_mailer() -> None:
    with pytest.raises(ValueError, match="MAILER_BACKEND"):
        _deployed("staging", mailer_backend="console")


@pytest.mark.unit
def test_a_deployed_env_refuses_a_missing_audit_hmac_key() -> None:
    with pytest.raises(ValueError, match="AUDIT_HMAC_KEY"):
        _deployed("staging", audit_hmac_key=None)


@pytest.mark.unit
def test_a_relaxed_env_falls_back_to_the_development_audit_key() -> None:
    relaxed = Settings(app_env="local")

    assert relaxed.audit_hmac_key is None
    assert relaxed.audit_hmac_key_value == DEV_AUDIT_HMAC_KEY


@pytest.mark.unit
def test_a_configured_audit_hmac_key_is_used() -> None:
    assert _deployed("staging").audit_hmac_key_value == REAL_SECRET.get_secret_value()


@pytest.mark.unit
def test_a_deployed_env_refuses_local_storage() -> None:
    with pytest.raises(ValueError, match="STORAGE_BACKEND"):
        _deployed("staging", storage_backend="local")


@pytest.mark.unit
@pytest.mark.parametrize("app_env", ["local", "test", "TEST", " local "])
def test_only_local_and_test_may_relax(app_env: str) -> None:
    relaxed = Settings(
        app_env=app_env,
        refresh_cookie_secure=False,
        jwt_secret=PLACEHOLDER_SECRET,
        storage_backend="local",
        mailer_backend="console",
    )

    assert relaxed.hardened_config_required is False


@pytest.mark.unit
def test_a_deployed_env_with_everything_set_is_accepted() -> None:
    assert _deployed("staging").hardened_config_required is True
