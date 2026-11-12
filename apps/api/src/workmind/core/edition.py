"""Editions: Community (the Apache-2.0 core, always complete) and Enterprise (ADR 0011).

Enterprise needs a valid license. A license is a token of two base64url parts:

    base64url(payload JSON) "." base64url(Ed25519 signature of the payload JSON bytes)

The payload names the licensee, the licensed features, when it was issued and when it
expires, and optionally the one deployment it is for. The token is checked against the
trusted public keys: PRODUCTION_PUBLIC_KEYS, plus a development key that is trusted only
when APP_ENV=dev (`wm license dev` makes one). A missing, malformed, tampered, expired or
unknown-key license means Community, with a warning in the log that never contains the token.

Enterprise code (workmind_ee) is loaded by workmind.core.extensions, never imported here.
"""

import base64
import binascii
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Literal, Self

import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from workmind.core.config import Settings
from workmind.core.errors import ApiError

log = structlog.get_logger("workmind.edition")

Feature = Literal[
    "retention_policies", "department_budgets", "chargeback_reports", "advanced_audit_export"
]
# Every Enterprise feature the core knows about (the Feature names), with a name people read.
FEATURES: Final[Mapping[str, str]] = {
    "retention_policies": "Retention policies",
    "department_budgets": "Department budgets",
    "chargeback_reports": "Chargeback reports",
    "advanced_audit_export": "Advanced audit export",
}

# Ed25519 public keys (raw 32 bytes, base64url) that sign official licenses, by key id.
# Empty: no official licenses are issued yet, so only development licenses exist.
PRODUCTION_PUBLIC_KEYS: Final[Mapping[str, str]] = {}
DEV_KEY_ID: Final = "development"

LICENSE_VERSION: Final = 1
MAX_TOKEN_BYTES: Final = 16_384
# Tolerated difference between the issuer's clock and ours for issued_at.
CLOCK_SKEW: Final = timedelta(minutes=5)

EditionName = Literal["community", "enterprise"]


class LicenseError(ValueError):
    """Why a license token was rejected. The message never contains the token."""


@dataclass(frozen=True)
class License:
    licensee: str
    features: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    deployment_id: str | None = None
    key_id: str = ""  # the trusted key that signed it

    def valid_at(self, now: datetime) -> bool:
        return self.issued_at - CLOCK_SKEW <= now < self.expires_at


class LicensePayload(BaseModel):
    """The signed JSON. Unknown fields are refused: a newer license format gets a new `v`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Literal[1]
    licensee: str = Field(min_length=1, max_length=200)
    features: list[str] = Field(max_length=64)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    deployment_id: str | None = Field(default=None, min_length=1, max_length=200)


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Strict base64url: anything outside the alphabet is an error, not silently dropped."""
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LicenseError("not base64url") from exc


def public_key_from_text(text: str) -> Ed25519PublicKey:
    """A raw Ed25519 public key (32 bytes) written as base64url."""
    try:
        raw = b64url_decode(text.strip())
        return Ed25519PublicKey.from_public_bytes(raw)
    except (LicenseError, ValueError) as exc:
        raise LicenseError("not an Ed25519 public key (32 bytes, base64url)") from exc


def public_key_text(key: Ed25519PublicKey) -> str:
    return b64url_encode(key.public_bytes_raw())


def sign_license(payload: LicensePayload, private_key: Ed25519PrivateKey) -> str:
    """Issues a token. Only the CLI (development licenses) and tests sign here."""
    body = json.dumps(payload.model_dump(mode="json", exclude_none=True), sort_keys=True)
    data = body.encode()
    return f"{b64url_encode(data)}.{b64url_encode(private_key.sign(data))}"


def verify_license(
    token: str, trusted: Mapping[str, Ed25519PublicKey], *, now: datetime
) -> License:
    """Checks the signature against every trusted key, then the payload and its dates."""
    token = token.strip()
    if len(token) > MAX_TOKEN_BYTES:
        raise LicenseError("too long to be a license")
    parts = token.split(".")
    if len(parts) != 2 or not all(parts):
        raise LicenseError("not a license token (expected two base64url parts)")
    data, signature = b64url_decode(parts[0]), b64url_decode(parts[1])
    key_id = _signed_by(data, signature, trusted)
    if key_id is None:
        raise LicenseError("signature does not match a trusted key")
    try:
        payload = LicensePayload.model_validate(json.loads(data))
    except (ValueError, ValidationError) as exc:
        raise LicenseError(f"invalid payload: {_describe(exc)}") from exc
    license_ = License(
        licensee=payload.licensee,
        features=frozenset(payload.features),
        issued_at=payload.issued_at,
        expires_at=payload.expires_at,
        deployment_id=payload.deployment_id,
        key_id=key_id,
    )
    if now >= license_.expires_at:
        raise LicenseError(f"expired on {license_.expires_at.isoformat()}")
    if now < license_.issued_at - CLOCK_SKEW:
        raise LicenseError(f"not valid before {license_.issued_at.isoformat()}")
    return license_


def _signed_by(
    data: bytes, signature: bytes, trusted: Mapping[str, Ed25519PublicKey]
) -> str | None:
    for key_id, key in trusted.items():
        try:
            key.verify(signature, data)
        except InvalidSignature:
            continue
        return key_id
    return None


def _describe(exc: Exception) -> str:
    """Field names and messages only: pydantic's own message would echo the input."""
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'payload'}: {error['msg']}"
            for error in exc.errors()
        )
    return "not JSON"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Edition:
    """What this deployment runs. `has(feature)` is the only question features should ask.

    A license that expires while the process runs turns its features off at that moment:
    the edition reads the clock on every question.
    """

    def __init__(
        self, license: License | None = None, *, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self._license = license
        self._clock = clock

    @classmethod
    def community(cls) -> Self:
        return cls(None)

    @property
    def license(self) -> License | None:
        """The license while it is valid, else None."""
        lic = self._license
        return lic if lic is not None and lic.valid_at(self._clock()) else None

    @property
    def enterprise(self) -> bool:
        return self.license is not None

    @property
    def name(self) -> EditionName:
        return "enterprise" if self.enterprise else "community"

    @property
    def features(self) -> list[str]:
        """Licensed features: the ones this core knows first (in FEATURES order), then others."""
        lic = self.license
        if lic is None:
            return []
        known = [f for f in FEATURES if f in lic.features]
        return known + sorted(lic.features - set(known))

    def has(self, feature: str) -> bool:
        lic = self.license
        return lic is not None and feature in lic.features

    def __repr__(self) -> str:
        return f"Edition({self.name}, features={self.features})"


def enterprise_feature_error(feature: str) -> ApiError:
    """402 for a request that needs a feature this deployment is not licensed for."""
    label = FEATURES.get(feature, feature)
    return ApiError(
        402,
        "enterprise_feature",
        "Enterprise feature",
        f"{label} ('{feature}') is part of WorkMind Enterprise and is not licensed on this "
        "deployment. To enable it, install a license that includes it: see docs/enterprise.md.",
    )


# --- Loading the license from the settings ---------------------------------------------------


def load_edition(settings: Settings, *, now: datetime | None = None) -> Edition:
    """The edition the settings describe. Never raises: any problem means Community."""
    source, token = _read_token(settings)
    if token is None:
        return Edition.community()
    try:
        license_ = _verify(token, settings, now=now or _utcnow())
    except LicenseError as exc:
        log.warning("license_rejected", source=source, reason=str(exc), edition="community")
        return Edition.community()
    log.info(
        "license_accepted",
        source=source,
        licensee=license_.licensee,
        features=sorted(license_.features),
        expires_at=license_.expires_at.isoformat(),
        key=license_.key_id,
    )
    return Edition(license_)


def _verify(token: str, settings: Settings, *, now: datetime) -> License:
    trusted = {
        key_id: public_key_from_text(text) for key_id, text in PRODUCTION_PUBLIC_KEYS.items()
    }
    dev_key = _dev_public_key(settings)
    if dev_key is not None and settings.app_env == "dev":
        trusted[DEV_KEY_ID] = dev_key
    try:
        license_ = verify_license(token, trusted, now=now)
    except LicenseError:
        # Say so plainly when it is a development license outside development.
        if dev_key is not None and settings.app_env != "dev":
            try:
                verify_license(token, {DEV_KEY_ID: dev_key}, now=now)
            except LicenseError:
                pass
            else:
                raise LicenseError(
                    "signed by the development key, which is trusted only with APP_ENV=dev"
                ) from None
        raise
    if license_.deployment_id is not None and license_.deployment_id != settings.deployment_id:
        raise LicenseError("issued for another deployment (DEPLOYMENT_ID does not match)")
    return license_


def _read_token(settings: Settings) -> tuple[str, str | None]:
    if settings.license_key is not None:
        token = settings.license_key.get_secret_value().strip()
        return "LICENSE_KEY", token or None
    if settings.license_file is None:
        return "none", None
    source = f"LICENSE_FILE {settings.license_file}"
    try:
        token = _read_small(settings.license_file)
    except OSError as exc:
        log.warning("license_unreadable", source=source, error=type(exc).__name__)
        return source, None
    return source, token or None


def _dev_public_key(settings: Settings) -> Ed25519PublicKey | None:
    text: str | None = settings.license_dev_public_key
    source = "LICENSE_DEV_PUBLIC_KEY"
    if text is None and settings.license_dev_public_key_file is not None:
        source = f"LICENSE_DEV_PUBLIC_KEY_FILE {settings.license_dev_public_key_file}"
        try:
            text = _read_small(settings.license_dev_public_key_file)
        except OSError as exc:
            log.warning("license_dev_key_unreadable", source=source, error=type(exc).__name__)
            return None
    if not text:
        return None
    try:
        return public_key_from_text(text)
    except LicenseError as exc:
        log.warning("license_dev_key_invalid", source=source, reason=str(exc))
        return None


def _read_small(path: Path) -> str:
    with path.open("rb") as file:
        data = file.read(MAX_TOKEN_BYTES + 1)
    return data.decode("utf-8", errors="replace").strip()
