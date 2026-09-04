from __future__ import annotations

import pytest
from pydantic import SecretStr

from blade_backend.main import warn_on_development_audit_key
from blade_backend.settings import DEV_AUDIT_HMAC_KEY, Settings

PLACEHOLDER_SECRET = SecretStr("change-me-change-me-change-me-32!")
REAL_SECRET = SecretStr("6c2f8a1d4e7b09c3f5a8d2b6e1c47f90")
SHORT_SECRET = SecretStr("too-short-for-a-deployment")


def _deployed(app_env: str, **overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "app_env": app_env,
        "refresh_cookie_secure": True,
        "jwt_secret": REAL_SECRET,
        "storage_backend": "s3",
        "mailer_backend": "smtp",
        "audit_hmac_key": REAL_SECRET,
        "allow_open_signup": False,
        "trusted_proxy_count": 1,
        "cors_origins": ["https://app.example"],
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
def test_token_retention_shorter_than_the_refresh_lifetime_is_refused() -> None:
    with pytest.raises(ValueError, match="TOKEN_RETENTION_DAYS"):
        Settings(app_env="local", token_retention_days=7, jwt_refresh_ttl_days=30)


@pytest.mark.unit
def test_token_retention_matching_the_refresh_lifetime_is_accepted() -> None:
    kept = Settings(app_env="local", token_retention_days=30, jwt_refresh_ttl_days=30)

    assert kept.token_retention_days == 30


@pytest.mark.unit
def test_a_deployed_env_refuses_an_empty_cors_allow_list() -> None:
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _deployed("staging", cors_origins=[])


@pytest.mark.unit
def test_a_deployed_env_refuses_an_untrusted_proxy_count() -> None:
    with pytest.raises(ValueError, match="TRUSTED_PROXY_COUNT"):
        _deployed("staging", trusted_proxy_count=0)


@pytest.mark.unit
def test_the_default_proxy_count_trusts_one_hop() -> None:
    assert Settings(app_env="local").trusted_proxy_count == 1


@pytest.mark.unit
def test_a_deployed_env_refuses_open_signup() -> None:
    with pytest.raises(ValueError, match="ALLOW_OPEN_SIGNUP"):
        _deployed("staging", allow_open_signup=True)


@pytest.mark.unit
@pytest.mark.parametrize("empty", ["", "   "])
def test_a_deployed_env_refuses_an_empty_audit_hmac_key(empty: str) -> None:
    with pytest.raises(ValueError, match="AUDIT_HMAC_KEY"):
        _deployed("staging", audit_hmac_key=empty)


@pytest.mark.unit
def test_a_deployed_env_refuses_a_short_audit_hmac_key() -> None:
    with pytest.raises(ValueError, match="AUDIT_HMAC_KEY"):
        _deployed("staging", audit_hmac_key=SHORT_SECRET)


@pytest.mark.unit
def test_a_deployed_env_refuses_a_short_mfa_encryption_key() -> None:
    with pytest.raises(ValueError, match="MFA_ENCRYPTION_KEY"):
        _deployed("staging", mfa_encryption_key=SHORT_SECRET)


@pytest.mark.unit
@pytest.mark.parametrize("empty", ["", "   ", SecretStr("")])
def test_an_empty_mfa_encryption_key_falls_back_instead_of_encrypting_with_nothing(
    empty: object,
) -> None:
    relaxed = Settings(app_env="local", mfa_encryption_key=empty)  # pyright: ignore[reportArgumentType]

    assert relaxed.mfa_encryption_key is None
    assert relaxed.mfa_encryption_key_value == relaxed.jwt_secret_value


@pytest.mark.unit
def test_an_empty_audit_hmac_key_is_warned_about_like_a_missing_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    warn_on_development_audit_key(Settings(app_env="local", audit_hmac_key=SecretStr("")))

    assert "development key" in capsys.readouterr().out


@pytest.mark.unit
def test_a_configured_audit_hmac_key_silences_the_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    warn_on_development_audit_key(Settings(app_env="local", audit_hmac_key=REAL_SECRET))

    assert capsys.readouterr().out == ""


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
