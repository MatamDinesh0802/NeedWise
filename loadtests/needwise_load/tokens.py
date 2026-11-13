"""Access tokens for the load test, and cleaning up after it (docs/load-testing.md).

    uv run python -m needwise_load.tokens create   # sign in as each demo user, create a token
    uv run python -m needwise_load.tokens check    # GET /api/v1/me with each token
    uv run python -m needwise_load.tokens revoke   # clean up, revoke every token, delete the file
    uv run python -m needwise_load.tokens sweep    # revoke any loadtest-* token left behind

Tokens are created through the API the way Settings > Access tokens does it: a browser session
(Keycloak sign-in), then POST /api/v1/me/tokens. Each gets every grantable scope and the
shortest lifetime (1 day), so a token that is somehow left behind expires tomorrow.

The plain tokens are written to loadtests/.secrets/tokens.json (mode 0600, git-ignored) and are
never printed. `revoke` first deletes what the run created with them ("[load] " conversations),
then revokes each token and deletes the file. `sweep` needs no file: it signs in as each user
and revokes every unrevoked token whose name starts with "loadtest-", deletes "[load] "
conversations, and, with --upload-collection, deletes leftover "loadtest-upload-" documents.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from needwise_load.browser import browser_like, same_origin, sign_in, sign_out
from needwise_load.settings import (
    ADMIN_USER,
    CONVERSATION_PREFIX,
    DEFAULT_DEMO_PASSWORD,
    DEFAULT_HOST,
    DEMO_PASSWORD_ENV,
    DEMO_USERS,
    TOKEN_FILE,
    TOKEN_NAME_PREFIX,
    UPLOAD_PREFIX,
)

SCOPES = ["profile:read", "knowledge:read", "assistant:ask"]
UPLOAD_COLLECTION = "loadtest-uploads"
TIMEOUT = 30


@dataclass(frozen=True)
class IssuedToken:
    email: str
    id: str
    token: str

    def __repr__(self) -> str:  # never show the secret, even in a traceback
        return f"IssuedToken(email={self.email!r}, id={self.id!r})"


@dataclass(frozen=True)
class TokenFile:
    host: str
    name: str
    created_at: str
    tokens: list[IssuedToken]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "host": self.host,
            "name": self.name,
            "created_at": self.created_at,
            "tokens": [t.__dict__ for t in self.tokens],
        }
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(body, handle, indent=2)
        path.chmod(0o600)

    @classmethod
    def load(cls, path: Path) -> "TokenFile":
        body = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            host=body["host"],
            name=body["name"],
            created_at=body["created_at"],
            tokens=[IssuedToken(**t) for t in body["tokens"]],
        )


def password() -> str:
    return os.environ.get(DEMO_PASSWORD_ENV, DEFAULT_DEMO_PASSWORD)


def signed_in(host: str, email: str) -> requests.Session:
    session = browser_like(requests.Session())
    sign_in(session, host, email, password(), timeout=TIMEOUT)
    return session


def _check(response: requests.Response, what: str) -> requests.Response:
    if response.status_code >= 400:
        raise RuntimeError(f"{what}: HTTP {response.status_code} {response.text[:200]}")
    return response


def create(host: str, users: list[str], path: Path, *, days: int = 1) -> TokenFile:
    if path.exists():
        raise RuntimeError(f"{path} exists: revoke those tokens first (tokens revoke)")
    name = f"{TOKEN_NAME_PREFIX}{datetime.now(UTC):%Y%m%d-%H%M%S}"
    issued = TokenFile(host, name, datetime.now(UTC).isoformat(), [])
    try:
        for email in users:
            session = signed_in(host, email)
            try:
                body = _check(
                    session.post(
                        urljoin(host, "/api/v1/me/tokens"),
                        json={"name": name, "scopes": SCOPES, "expires_in_days": days},
                        headers=same_origin(host),
                        timeout=TIMEOUT,
                    ),
                    f"creating a token for {email}",
                ).json()
                issued.tokens.append(IssuedToken(email, body["id"], body["token"]))
            finally:
                sign_out(session, host)
            print(f"  created {name} for {email}")
    finally:
        if issued.tokens:  # even a partial set must be revocable
            issued.save(path)
    return issued


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def check(issued: TokenFile) -> bool:
    ok = True
    for token in issued.tokens:
        response = requests.get(
            urljoin(issued.host, "/api/v1/me"), headers=bearer(token.token), timeout=TIMEOUT
        )
        email = response.json().get("email") if response.ok else None
        good = response.ok and email == token.email
        ok &= good
        print(f"  {token.email}: {'ok' if good else f'HTTP {response.status_code}'}")
    return ok


def delete_load_conversations(host: str, headers: dict[str, str]) -> int:
    """Deletes the caller's conversations whose title starts with "[load] "."""
    found: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 100, **({"cursor": cursor} if cursor else {})}
        page = _check(
            requests.get(
                urljoin(host, "/api/v1/conversations"),
                params=params,
                headers=headers,
                timeout=TIMEOUT,
            ),
            "listing conversations",
        ).json()
        found += [c["id"] for c in page["items"] if c["title"].startswith(CONVERSATION_PREFIX)]
        cursor = page.get("next_cursor")
        if not cursor:
            break
    for conversation_id in found:
        response = requests.delete(
            urljoin(host, f"/api/v1/conversations/{conversation_id}"),
            headers=headers,
            timeout=TIMEOUT,
        )
        if response.status_code not in (204, 404):
            _check(response, f"deleting conversation {conversation_id}")
    return len(found)


def delete_uploads(session: requests.Session, host: str, collection_key: str) -> int:
    """Deletes "loadtest-upload-" documents from the collection (a browser session that may
    edit it)."""
    collections = _check(
        session.get(urljoin(host, "/api/v1/collections"), timeout=TIMEOUT), "listing collections"
    ).json()["items"]
    match = [c for c in collections if c["key"] == collection_key]
    if not match:
        return 0
    documents = _check(
        session.get(
            urljoin(host, f"/api/v1/collections/{match[0]['id']}/documents"), timeout=TIMEOUT
        ),
        "listing documents",
    ).json()["items"]
    leftovers = [d for d in documents if str(d.get("filename", "")).startswith(UPLOAD_PREFIX)]
    for document in leftovers:
        response = session.delete(
            urljoin(host, f"/api/v1/documents/{document['id']}"),
            headers=same_origin(host),
            timeout=TIMEOUT,
        )
        if response.status_code not in (204, 404):
            _check(response, f"deleting document {document['id']}")
    return len(leftovers)


def revoke(path: Path, *, keep_conversations: bool = False) -> None:
    issued = TokenFile.load(path)
    failures: list[str] = []
    for token in issued.tokens:
        try:
            if not keep_conversations:
                deleted = delete_load_conversations(issued.host, bearer(token.token))
                print(f"  {token.email}: deleted {deleted} load-test conversation(s)")
        except (requests.RequestException, RuntimeError) as exc:
            failures.append(f"{token.email}: cleanup failed ({exc})")
    by_user: dict[str, list[IssuedToken]] = {}
    for token in issued.tokens:
        by_user.setdefault(token.email, []).append(token)
    for email, tokens in by_user.items():
        try:
            session = signed_in(issued.host, email)
            try:
                for token in tokens:
                    revoke_one(session, issued.host, token.id)
            finally:
                sign_out(session, issued.host)
            print(f"  revoked {issued.name} for {email}")
        except (requests.RequestException, RuntimeError) as exc:
            failures.append(f"{email}: revoke failed ({exc})")
    if failures:
        raise RuntimeError("; ".join(failures) + f". {path} is kept: run revoke again.")
    path.unlink()


def revoke_one(session: requests.Session, host: str, token_id: str) -> None:
    response = session.delete(
        urljoin(host, f"/api/v1/me/tokens/{token_id}"),
        headers=same_origin(host),
        timeout=TIMEOUT,
    )
    if response.status_code not in (204, 404):
        _check(response, f"revoking token {token_id}")


def sweep(host: str, users: list[str], *, upload_collection: str | None = None) -> int:
    """Revokes every unrevoked loadtest-* token of these users and deletes their "[load] "
    conversations (with a short-lived token of their own, revoked at once)."""
    revoked = 0
    for email in users:
        session = signed_in(host, email)
        try:
            listed = _check(
                session.get(urljoin(host, "/api/v1/me/tokens"), timeout=TIMEOUT), "listing tokens"
            ).json()["items"]
            stale = [
                t["id"]
                for t in listed
                if t["name"].startswith(TOKEN_NAME_PREFIX) and t["revoked_at"] is None
            ]
            cleanup = _check(
                session.post(
                    urljoin(host, "/api/v1/me/tokens"),
                    json={
                        "name": f"{TOKEN_NAME_PREFIX}sweep",
                        "scopes": ["assistant:ask"],
                        "expires_in_days": 1,
                    },
                    headers=same_origin(host),
                    timeout=TIMEOUT,
                ),
                "creating a cleanup token",
            ).json()
            try:
                deleted = delete_load_conversations(host, bearer(cleanup["token"]))
            finally:
                revoke_one(session, host, cleanup["id"])
            for token_id in stale:
                revoke_one(session, host, token_id)
            uploads = delete_uploads(session, host, upload_collection) if upload_collection else 0
        finally:
            sign_out(session, host)
        revoked += len(stale)
        print(
            f"  {email}: revoked {len(stale)} leftover token(s), deleted {deleted} "
            f"conversation(s)" + (f" and {uploads} upload(s)" if upload_collection else "")
        )
    return revoked


def ensure_upload_collection(host: str, admin: str, uploader: str, key: str) -> str:
    """A restricted collection only `uploader` can see and edit, for the upload scenario;
    created by an org admin unless it exists. Returns its id. There is no API to delete a
    collection: it stays (empty, once uploads are deleted) for the next run."""
    session = signed_in(host, admin)
    try:
        existing = _check(
            session.get(urljoin(host, "/api/v1/admin/collections"), timeout=TIMEOUT),
            "listing collections",
        ).json()["items"]
        found = [c["id"] for c in existing if c["key"] == key]
        if found:
            return found[0]
        people = _check(
            session.get(
                urljoin(host, "/api/v1/admin/users"), params={"q": uploader}, timeout=TIMEOUT
            ),
            "finding the uploader",
        ).json()["items"]
        match = [p["id"] for p in people if p["email"].lower() == uploader.lower()]
        if not match:
            raise RuntimeError(f"no person {uploader} in this organization")
        created = _check(
            session.post(
                urljoin(host, "/api/v1/admin/collections"),
                json={
                    "key": key,
                    "name": "Load test uploads",
                    "description": "Documents the load test uploads and deletes again.",
                    "scope": "restricted",
                    "editors": [f"user:{match[0]}"],
                },
                headers=same_origin(host),
                timeout=TIMEOUT,
            ),
            "creating the upload collection",
        ).json()
        return created["id"]
    finally:
        sign_out(session, host)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="needwise_load.tokens", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument(
        "command", choices=["create", "check", "revoke", "sweep", "upload-collection"]
    )
    parser.add_argument("--host", default=os.environ.get("NW_LOAD_HOST", DEFAULT_HOST))
    parser.add_argument("--users", default=",".join(DEMO_USERS), help="comma-separated emails")
    parser.add_argument(
        "--file", type=Path, default=Path(os.environ.get("NW_LOAD_TOKENS", TOKEN_FILE))
    )
    parser.add_argument("--days", type=int, default=1, help="token lifetime (create)")
    parser.add_argument("--keep-conversations", action="store_true", help="revoke only")
    parser.add_argument("--upload-collection", help="sweep: also delete leftover uploads here")
    parser.add_argument("--admin", default=ADMIN_USER, help="upload-collection: who creates it")
    parser.add_argument("--uploader", default=ADMIN_USER, help="upload-collection: who uploads")
    args = parser.parse_args(argv)
    users = [u.strip() for u in args.users.split(",") if u.strip()]
    try:
        if args.command == "create":
            issued = create(args.host, users, args.file, days=args.days)
            print(f"{len(issued.tokens)} token(s) named {issued.name} in {args.file}")
        elif args.command == "check":
            return 0 if check(TokenFile.load(args.file)) else 1
        elif args.command == "revoke":
            revoke(args.file, keep_conversations=args.keep_conversations)
            print(f"revoked; {args.file} deleted")
        elif args.command == "upload-collection":
            key = args.upload_collection or UPLOAD_COLLECTION
            ensure_upload_collection(args.host, args.admin, args.uploader, key)
            print(f"collection {key}: {args.uploader} may upload to it")
        else:
            count = sweep(args.host, users, upload_collection=args.upload_collection)
            print(f"swept: {count} leftover token(s) revoked")
    except (RuntimeError, requests.RequestException, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
