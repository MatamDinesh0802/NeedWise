"""`nw license dev`: a development license, without touching .env (docs/enterprise.md).

    nw license dev [--features retention_policies ...] [--days 30] [--licensee NAME]

The first run creates a development signing key pair in <repo>/.needwise/ (git-ignored);
later runs reuse it. Each run writes:

    .needwise/license.key       the license token, signed with the development key
    .needwise/dev-public.key    the public key the API trusts, only with APP_ENV=dev
    .needwise/dev-signing.key   the private key (mode 0600; never printed)

compose mounts .needwise read-only at /run/needwise in api, worker and beat, and the settings
default to the files there. Restart those services to apply. Delete license.key to go back to
Community. The token and the private key are never printed.
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from needwise.core.config import DEV_PUBLIC_KEY_FILE_NAME, LICENSE_FILE_NAME
from needwise.core.edition import (
    FEATURES,
    LicensePayload,
    b64url_decode,
    b64url_encode,
    public_key_text,
    sign_license,
)

SIGNING_KEY_FILE_NAME = "dev-signing.key"
LICENSE_DIR_NAME = ".needwise"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="nw license", description="Development licenses for the Enterprise edition."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    dev = commands.add_parser(
        "dev", help="write a development license to <repo>/.needwise (APP_ENV=dev only)"
    )
    dev.add_argument(
        "--features",
        nargs="+",
        metavar="FEATURE",
        help=f"licensed features (default: all): {', '.join(FEATURES)}",
    )
    dev.add_argument("--days", type=int, default=30, help="valid for this many days (1-366)")
    dev.add_argument("--licensee", default="Local development", help="who the license is for")
    dev.add_argument("--deployment-id", help="bind the license to this DEPLOYMENT_ID")
    dev.add_argument("--dir", type=Path, help="where to write (default: <repo>/.needwise)")
    args = parser.parse_args(argv)

    features: list[str] = []
    for value in args.features or list(FEATURES):
        features += [f for f in value.split(",") if f]
    unknown = sorted(set(features) - set(FEATURES))
    if unknown:
        parser.error(f"unknown feature(s): {', '.join(unknown)}; known: {', '.join(FEATURES)}")
    if not 1 <= args.days <= 366:
        parser.error("--days must be between 1 and 366")
    directory: Path | None = args.dir or _default_dir(Path.cwd())
    if directory is None:
        parser.error("run inside the NeedWise repository, or pass --dir")

    features = sorted(set(features), key=list(FEATURES).index)
    expires_at = write_dev_license(
        directory,
        features=features,
        days=args.days,
        licensee=args.licensee,
        deployment_id=args.deployment_id,
    )
    print(
        f"Development license written to {directory / LICENSE_FILE_NAME}\n"
        f"  licensee: {args.licensee}\n"
        f"  features: {', '.join(features)}\n"
        f"  expires:  {expires_at.isoformat()}\n"
        "Accepted only with APP_ENV=dev (the compose default). To apply it:\n"
        "  docker compose restart api worker beat   (or restart make api-dev)\n"
        f"Back to Community: delete {directory / LICENSE_FILE_NAME} and restart.",
        file=sys.stderr,
    )
    return 0


def write_dev_license(
    directory: Path,
    *,
    features: list[str],
    days: int,
    licensee: str,
    deployment_id: str | None = None,
    now: datetime | None = None,
) -> datetime:
    """Writes license.key and dev-public.key (and the key pair on first use). Returns the
    expiry."""
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    key = _signing_key(directory / SIGNING_KEY_FILE_NAME)
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    payload = LicensePayload(
        v=1,
        licensee=licensee,
        features=features,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(days=days),
        deployment_id=deployment_id,
    )
    # Readable by the containers' user: neither file is a secret on its own.
    _write(directory / DEV_PUBLIC_KEY_FILE_NAME, public_key_text(key.public_key()) + "\n", 0o644)
    _write(directory / LICENSE_FILE_NAME, sign_license(payload, key) + "\n", 0o644)
    return payload.expires_at


def _signing_key(path: Path) -> Ed25519PrivateKey:
    if path.exists():
        return Ed25519PrivateKey.from_private_bytes(b64url_decode(path.read_text().strip()))
    key = Ed25519PrivateKey.generate()
    _write(path, b64url_encode(key.private_bytes_raw()) + "\n", 0o600)
    return key


def _write(path: Path, text: str, mode: int) -> None:
    """Atomically, with `mode` from the start (never briefly world-readable)."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "w") as file:
        file.write(text)
    os.chmod(temporary, mode)  # the umask may have removed bits
    temporary.replace(path)


def _default_dir(start: Path) -> Path | None:
    """<repo>/.needwise, the repository being the nearest folder with compose.yaml and apps/."""
    for folder in (start, *start.parents):
        if (folder / "compose.yaml").is_file() and (folder / "apps" / "api").is_dir():
            return folder / LICENSE_DIR_NAME
    return None
