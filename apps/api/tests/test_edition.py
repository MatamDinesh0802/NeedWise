"""Licenses and editions (ADR 0011): only a valid, trusted, unexpired license means Enterprise."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from workmind.core import config, edition
from workmind.core.config import Settings
from workmind.core.edition import (
    FEATURES,
    PRODUCTION_PUBLIC_KEYS,
    Edition,
    Feature,
    License,
    LicenseError,
    LicensePayload,
    b64url_decode,
    b64url_encode,
    enterprise_feature_error,
    load_edition,
    public_key_text,
    sign_license,
    verify_license,
)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
KEY = Ed25519PrivateKey.generate()
TRUSTED = {"test": KEY.public_key()}


def payload(**changes: object) -> LicensePayload:
    fields: dict[str, object] = {
        "v": 1,
        "licensee": "Acme Robotics",
        "features": ["retention_policies", "chargeback_reports"],
        "issued_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=30),
    }
    return LicensePayload.model_validate({**fields, **changes})


def token(key: Ed25519PrivateKey = KEY, **changes: object) -> str:
    return sign_license(payload(**changes), key)


def settings(**values: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "app_env": "dev", "authz_outbox_worker": False}
    return Settings(**{**base, **values})  # pyright: ignore[reportArgumentType]


Logs = list[dict[str, object]]


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> Logs:
    """What the edition module logs (structlog's capture_logs misses cached loggers)."""
    events: Logs = []

    class Recorder:
        def __getattr__(self, level: str) -> Callable[..., None]:
            def record(event: str, **fields: object) -> None:
                events.append({"event": event, "log_level": level, **fields})

            return record

    monkeypatch.setattr(edition, "log", Recorder())
    return events


@pytest.fixture(autouse=True)
def no_license_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A developer's own .workmind/ must not leak into these tests."""
    monkeypatch.setattr(config, "LICENSE_DIRS", (tmp_path / "none",))
    for name in ("LICENSE_KEY", "LICENSE_FILE", "LICENSE_DEV_PUBLIC_KEY", "DEPLOYMENT_ID"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)


# --- The token ------------------------------------------------------------------------------


def test_a_valid_license_is_accepted_with_its_fields() -> None:
    lic = verify_license(token(deployment_id="prod-eu-1"), TRUSTED, now=NOW)
    assert lic == License(
        licensee="Acme Robotics",
        features=frozenset({"retention_policies", "chargeback_reports"}),
        issued_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
        deployment_id="prod-eu-1",
        key_id="test",
    )


def test_a_tampered_payload_is_rejected() -> None:
    data, signature = token().split(".")
    body = json.loads(b64url_decode(data))
    body["features"] = list(FEATURES)  # grant yourself everything
    forged = f"{b64url_encode(json.dumps(body, sort_keys=True).encode())}.{signature}"
    with pytest.raises(LicenseError, match="signature does not match a trusted key"):
        verify_license(forged, TRUSTED, now=NOW)


def test_a_tampered_signature_is_rejected() -> None:
    data, signature = token().split(".")
    raw = bytearray(b64url_decode(signature))
    raw[0] ^= 0x01
    with pytest.raises(LicenseError, match="signature does not match a trusted key"):
        verify_license(f"{data}.{b64url_encode(bytes(raw))}", TRUSTED, now=NOW)


def test_an_expired_license_is_rejected() -> None:
    expired = token(issued_at=NOW - timedelta(days=60), expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(LicenseError, match="expired on"):
        verify_license(expired, TRUSTED, now=NOW)


def test_a_license_from_the_future_is_rejected() -> None:
    early = token(issued_at=NOW + timedelta(hours=1), expires_at=NOW + timedelta(days=30))
    with pytest.raises(LicenseError, match="not valid before"):
        verify_license(early, TRUSTED, now=NOW)


def test_a_license_signed_by_an_unknown_key_is_rejected() -> None:
    stranger = Ed25519PrivateKey.generate()
    with pytest.raises(LicenseError, match="signature does not match a trusted key"):
        verify_license(token(stranger), TRUSTED, now=NOW)
    with pytest.raises(LicenseError, match="signature does not match a trusted key"):
        verify_license(token(), {}, now=NOW)  # no trusted keys at all


@pytest.mark.parametrize(
    "bad",
    ["", "abc", "a.b.c", ".abc", "abc.", "not base64!.abc", "ab+c.ab/c", "a" * 20_000 + ".b"],
)
def test_malformed_tokens_are_rejected(bad: str) -> None:
    with pytest.raises(LicenseError):
        verify_license(bad, TRUSTED, now=NOW)


def test_a_signed_but_invalid_payload_is_rejected_without_echoing_it() -> None:
    body = json.dumps({"v": 1, "licensee": "Secret Corp", "features": "everything"}).encode()
    forged = f"{b64url_encode(body)}.{b64url_encode(KEY.sign(body))}"
    with pytest.raises(LicenseError, match="invalid payload") as caught:
        verify_license(forged, TRUSTED, now=NOW)
    assert "Secret Corp" not in str(caught.value)
    assert "everything" not in str(caught.value)


def test_unknown_payload_fields_and_versions_are_refused() -> None:
    for extra in ({"max_users": 10}, {"v": 2}):
        body = json.dumps({**payload().model_dump(mode="json"), **extra}).encode()
        signed = f"{b64url_encode(body)}.{b64url_encode(KEY.sign(body))}"
        with pytest.raises(LicenseError, match="invalid payload"):
            verify_license(signed, TRUSTED, now=NOW)


def test_dates_must_carry_a_time_zone() -> None:
    with pytest.raises(ValueError, match="timezone"):
        payload(expires_at=datetime(2027, 1, 1))


# --- The edition ----------------------------------------------------------------------------


def _license(features: set[str], expires_at: datetime = NOW + timedelta(days=30)) -> License:
    return License("Acme Robotics", frozenset(features), NOW - timedelta(days=1), expires_at)


def test_community_has_no_feature() -> None:
    community = Edition.community()
    assert community.name == "community"
    assert not community.enterprise
    assert community.license is None
    assert community.features == []
    assert not any(community.has(f) for f in FEATURES)


def test_enterprise_has_only_the_licensed_features() -> None:
    edition = Edition(_license({"chargeback_reports", "retention_policies"}), clock=lambda: NOW)
    assert edition.name == "enterprise"
    assert edition.has("retention_policies")
    assert edition.has("chargeback_reports")
    assert not edition.has("department_budgets")
    assert not edition.has("advanced_audit_export")
    assert not edition.has("anything_else")
    assert edition.features == ["retention_policies", "chargeback_reports"]  # FEATURES order


def test_features_switch_off_when_the_license_expires_while_running() -> None:
    clock = [NOW]
    edition = Edition(
        _license({"retention_policies"}, NOW + timedelta(hours=1)), clock=lambda: clock[0]
    )
    assert edition.has("retention_policies")
    clock[0] = NOW + timedelta(hours=1)
    assert not edition.has("retention_policies")
    assert edition.name == "community"
    assert edition.license is None


def test_the_feature_names_are_the_contract() -> None:
    assert list(FEATURES) == [
        "retention_policies",
        "department_budgets",
        "chargeback_reports",
        "advanced_audit_export",
    ]
    assert set(get_args(Feature)) == set(FEATURES)


def test_no_official_license_keys_exist_yet() -> None:
    # Documented in docs/enterprise.md: until keys are published, only dev licenses exist.
    assert PRODUCTION_PUBLIC_KEYS == {}


def test_the_402_names_the_feature_and_how_to_enable_it() -> None:
    error = enterprise_feature_error("retention_policies")
    assert (error.status, error.code) == (402, "enterprise_feature")
    assert error.detail is not None
    assert "Retention policies ('retention_policies')" in error.detail
    assert "docs/enterprise.md" in error.detail


# --- Loading from the settings --------------------------------------------------------------

DEV_PUBLIC = public_key_text(KEY.public_key())
PRODUCTION_SECRETS: dict[str, object] = {
    "oidc_client_secret": "s",
    "openfga_api_key": "k",
    "secrets_key": "c2VjcmV0LWtleS1mb3ItdGVzdHMtMzItYnl0ZXMtISE=",
    "secrets_encryption_key": "b3RoZXIta2V5LWZvci10ZXN0cy0zMi1ieXRlcy0hISE=",
}
# Outside dev and test, model provider keys need their own encryption key (docs/models.md).
DEMO_SECRETS: dict[str, object] = {
    "secrets_encryption_key": PRODUCTION_SECRETS["secrets_encryption_key"]
}


def test_without_a_license_the_deployment_is_community(logs: Logs) -> None:
    edition = load_edition(settings())
    assert edition.name == "community"
    assert logs == []  # nothing to warn about


def test_a_dev_license_is_accepted_in_development() -> None:
    edition = load_edition(
        settings(license_key=token(), license_dev_public_key=DEV_PUBLIC), now=NOW
    )
    assert edition.enterprise
    assert edition.license is not None
    assert edition.license.key_id == "development"
    assert edition.has("retention_policies")


@pytest.mark.parametrize("environment", ["test", "demo", "production"])
def test_the_dev_key_is_refused_outside_development(environment: str, logs: Logs) -> None:
    secret = token()
    extra = {"production": PRODUCTION_SECRETS, "demo": DEMO_SECRETS}.get(environment, {})
    edition = load_edition(
        settings(
            app_env=environment, license_key=secret, license_dev_public_key=DEV_PUBLIC, **extra
        ),
        now=NOW,
    )
    assert edition.name == "community"
    assert logs[0]["event"] == "license_rejected"
    assert logs[0]["log_level"] == "warning"
    assert "trusted only with APP_ENV=dev" in str(logs[0]["reason"])
    assert secret not in repr(logs)
    assert secret.split(".")[0] not in repr(logs)


@pytest.mark.parametrize(
    "case", ["tampered", "expired", "unknown_key", "garbage", "wrong_deployment"]
)
def test_any_bad_license_means_community_and_a_warning_without_the_token(
    case: str, logs: Logs
) -> None:
    secret = {
        "tampered": token()[:-4] + "AAAA",
        "expired": token(issued_at=NOW - timedelta(days=9), expires_at=NOW - timedelta(days=1)),
        "unknown_key": token(Ed25519PrivateKey.generate()),
        "garbage": "eyJ.not-a-license",
        "wrong_deployment": token(deployment_id="someone-else"),
    }[case]
    edition = load_edition(
        settings(license_key=secret, license_dev_public_key=DEV_PUBLIC, deployment_id="mine"),
        now=NOW,
    )
    assert edition.name == "community"
    assert [(e["event"], e["log_level"]) for e in logs] == [("license_rejected", "warning")]
    assert secret not in repr(logs)
    assert secret.split(".")[0] not in repr(logs)


def test_a_license_for_this_deployment_is_accepted() -> None:
    edition = load_edition(
        settings(
            license_key=token(deployment_id="mine"),
            license_dev_public_key=DEV_PUBLIC,
            deployment_id="mine",
        ),
        now=NOW,
    )
    assert edition.enterprise


def test_the_license_and_dev_key_can_come_from_files(tmp_path: Path) -> None:
    (tmp_path / "license.key").write_text(token() + "\n")
    (tmp_path / "dev-public.key").write_text(DEV_PUBLIC + "\n")
    edition = load_edition(
        settings(
            license_file=tmp_path / "license.key",
            license_dev_public_key_file=tmp_path / "dev-public.key",
        ),
        now=NOW,
    )
    assert edition.has("chargeback_reports")


def test_a_missing_license_file_means_community(tmp_path: Path, logs: Logs) -> None:
    edition = load_edition(settings(license_file=tmp_path / "nope.key"), now=NOW)
    assert edition.name == "community"
    assert logs[0]["event"] == "license_unreadable"


def test_the_settings_default_to_the_license_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mounted, repo = tmp_path / "run-workmind", tmp_path / "repo-workmind"
    mounted.mkdir()
    repo.mkdir()
    monkeypatch.setattr(config, "LICENSE_DIRS", (mounted, repo))
    assert settings().license_file is None
    assert settings().license_dev_public_key_file is None

    (repo / "license.key").write_text(token())
    (repo / "dev-public.key").write_text(DEV_PUBLIC)
    assert settings().license_file == repo / "license.key"
    (mounted / "license.key").write_text(token())
    (mounted / "dev-public.key").write_text(DEV_PUBLIC)
    found = settings()
    assert found.license_file == mounted / "license.key"  # the compose mount wins
    assert found.license_dev_public_key_file == mounted / "dev-public.key"
    assert load_edition(found, now=NOW).enterprise

    # Configured values win over the directory.
    assert settings(license_key="x").license_file is None
    assert settings(license_dev_public_key="y").license_dev_public_key_file is None


def test_an_invalid_dev_public_key_is_ignored(logs: Logs) -> None:
    edition = load_edition(settings(license_key=token(), license_dev_public_key="short"), now=NOW)
    assert edition.name == "community"
    assert [e["event"] for e in logs] == ["license_dev_key_invalid", "license_rejected"]
