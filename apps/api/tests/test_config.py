import pytest

from workmind.core.config import Settings


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.app_env == "production"
    assert settings.qdrant_url == "http://qdrant:6333"


def test_api_docs_are_disabled_in_production() -> None:
    assert Settings(_env_file=None, app_env="dev").docs_enabled  # pyright: ignore[reportCallIssue]
    assert not Settings(_env_file=None, app_env="production").docs_enabled  # pyright: ignore[reportCallIssue]


def test_invalid_environment_is_rejected() -> None:
    with pytest.raises(ValueError, match="app_env"):
        Settings(_env_file=None, app_env="staging")  # pyright: ignore[reportCallIssue, reportArgumentType]
