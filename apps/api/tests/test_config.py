import pytest

from workmind.core.config import DEV_SECRETS_ENCRYPTION_KEY, Settings

REAL_KEY = "c2VjcmV0LWtleS1mb3ItdGVzdHMtMzItYnl0ZXMtISE="  # 32 bytes, base64
OTHER_KEY = "b3RoZXIta2V5LWZvci10ZXN0cy0zMi1ieXRlcy0hISE="  # 32 bytes, base64


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "a-real-secret")
    monkeypatch.setenv("OPENFGA_API_KEY", "a-real-key")
    monkeypatch.setenv("SECRETS_KEY", REAL_KEY)
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", OTHER_KEY)
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
        secrets_encryption_key=OTHER_KEY,  # pyright: ignore[reportArgumentType]
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


def test_the_encryption_key_is_required_outside_development() -> None:
    real = {"oidc_client_secret": "s", "openfga_api_key": "k", "secrets_key": REAL_KEY}
    for env in ("demo", "production"):
        with pytest.raises(ValueError, match="SECRETS_ENCRYPTION_KEY must be set"):
            Settings(_env_file=None, app_env=env, **real)  # pyright: ignore[reportCallIssue, reportArgumentType]
        with pytest.raises(ValueError, match="SECRETS_ENCRYPTION_KEY must be set"):
            Settings(  # pyright: ignore[reportCallIssue]
                _env_file=None,  # pyright: ignore[reportCallIssue]
                app_env=env,  # pyright: ignore[reportArgumentType]
                secrets_encryption_key=DEV_SECRETS_ENCRYPTION_KEY,  # pyright: ignore[reportArgumentType]
                **real,  # pyright: ignore[reportArgumentType]
            )
    # Development and tests fall back to the clearly marked development key.
    dev = Settings(_env_file=None, app_env="dev")  # pyright: ignore[reportCallIssue]
    assert dev.encryption_keys == (DEV_SECRETS_ENCRYPTION_KEY, [])


def test_encryption_keys_must_be_32_bytes_of_base64() -> None:
    with pytest.raises(ValueError, match="base64 encodings of 32 bytes"):
        Settings(_env_file=None, app_env="dev", secrets_encryption_key="c2hvcnQ=")  # pyright: ignore[reportCallIssue, reportArgumentType]
    with pytest.raises(ValueError, match="base64 encodings of 32 bytes"):
        Settings(  # pyright: ignore[reportCallIssue]
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="dev",
            secrets_encryption_previous_keys=f"{REAL_KEY},not base64",  # pyright: ignore[reportArgumentType]
        )
    rotated = Settings(  # pyright: ignore[reportCallIssue]
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="dev",
        secrets_encryption_key=OTHER_KEY,  # pyright: ignore[reportArgumentType]
        secrets_encryption_previous_keys=f" {REAL_KEY} ,",  # pyright: ignore[reportArgumentType]
    )
    assert rotated.encryption_keys == (OTHER_KEY, [REAL_KEY])


async def test_the_database_pool_follows_its_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB_POOL_SIZE and DB_MAX_OVERFLOW size each API process's pool (one per uvicorn worker,
    docs/benchmarks/load.md). Nothing connects: the engine opens connections lazily."""
    from sqlalchemy.pool import QueuePool

    from workmind.core.resources import Resources

    monkeypatch.setenv("DB_POOL_SIZE", "3")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "4")
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    resources = Resources.create(settings)
    try:
        pool = resources.engine.sync_engine.pool
        assert isinstance(pool, QueuePool)
        assert pool.size() == 3
        assert pool.overflow() == -3  # nothing checked out yet: room for 3 + 4
        assert "Pool size: 3" in pool.status()
    finally:
        await resources.aclose()


async def test_the_analytics_workers_are_split_across_the_api_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: each uvicorn process had a pool of ANALYTICS_WORKERS, so two processes (the
    # default) doubled the container's query workers and their memory (the Helm limit is sized
    # for one pool).
    from workmind.core.resources import Resources

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    monkeypatch.setenv("ANALYTICS_WORKERS", "3")
    resources = Resources.create(Settings(_env_file=None))  # pyright: ignore[reportCallIssue]
    try:
        assert resources.analytics.pool.workers == 1
    finally:
        await resources.aclose()
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    resources = Resources.create(Settings(_env_file=None))  # pyright: ignore[reportCallIssue]
    try:
        assert resources.analytics.pool.workers == 3
    finally:
        await resources.aclose()


def test_the_database_pool_settings_are_bounded() -> None:
    with pytest.raises(ValueError, match="db_pool_size"):
        Settings(_env_file=None, db_pool_size=0)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValueError, match="db_max_overflow"):
        Settings(_env_file=None, db_max_overflow=-1)  # pyright: ignore[reportCallIssue]
    defaults = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert (defaults.db_pool_size, defaults.db_max_overflow) == (10, 10)
