import pytest

from workmind.core.config import Settings

REAL_KEY = "c2VjcmV0LWtleS1mb3ItdGVzdHMtMzItYnl0ZXMtISE="  # 32 bytes, base64


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "a-real-secret")
    monkeypatch.setenv("OPENFGA_API_KEY", "a-real-key")
    monkeypatch.setenv("SECRETS_KEY", REAL_KEY)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.app_env == "production"
    assert settings.qdrant_url == "http://qdrant:6333"


def test_api_docs_are_disabled_in_production() -> None:
    production = Settings(  # pyright: ignore[reportCallIssue]
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="production",
        oidc_client_secret="s3cret",  # pyright: ignore[reportArgumentType]
        openfga_api_key="k3y",  # pyright: ignore[reportArgumentType]
        secrets_key=REAL_KEY,  # pyright: ignore[reportArgumentType]
    )

    assert Settings(_env_file=None, app_env="dev").docs_enabled  # pyright: ignore[reportCallIssue]
    assert not production.docs_enabled


def test_production_refuses_development_secrets() -> None:
    with pytest.raises(ValueError, match="OIDC_CLIENT_SECRET must be set"):
        Settings(_env_file=None, app_env="production")  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValueError, match="OPENFGA_API_KEY must be set"):
        Settings(_env_file=None, app_env="production", oidc_client_secret="s")  # pyright: ignore[reportCallIssue, reportArgumentType]
    with pytest.raises(ValueError, match="SECRETS_KEY must be set"):
        Settings(_env_file=None, app_env="production", oidc_client_secret="s", openfga_api_key="k")  # pyright: ignore[reportCallIssue, reportArgumentType]


def test_invalid_environment_is_rejected() -> None:
    with pytest.raises(ValueError, match="app_env"):
        Settings(_env_file=None, app_env="staging")  # pyright: ignore[reportCallIssue, reportArgumentType]
