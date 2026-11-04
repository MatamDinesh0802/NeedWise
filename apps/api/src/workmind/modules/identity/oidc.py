"""OpenID Connect client for the Backend-for-Frontend login (ADR 0003).

Browser-facing endpoints (authorize, end-session) come from discovery as public URLs;
server-to-server endpoints (token, JWKS) resolve on the internal network.
"""

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
ALGORITHMS = ["RS256"]
JWKS_CACHE_S = 600
CLOCK_LEEWAY_S = 30


class OidcError(Exception):
    pass


@dataclass(frozen=True)
class LoginRequest:
    url: str
    state: str
    nonce: str
    code_verifier: str


def pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OidcClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        discovery_url: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: str = "openid email profile organization",
    ) -> None:
        self._http = http
        self._discovery_url = discovery_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scopes = scopes
        self._metadata: dict[str, Any] | None = None
        self._jwks: jwt.PyJWKSet | None = None
        self._jwks_loaded_at = 0.0

    async def metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            metadata: dict[str, Any] = (await self._get(self._discovery_url)).json()
            self._metadata = metadata
            return metadata
        return self._metadata

    async def begin_login(self, *, login_hint: str | None = None) -> LoginRequest:
        metadata = await self.metadata()
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        # Switching to a named account (the demo "view as" menu): prefill it and ask for the
        # password even if Keycloak still has a session for someone else.
        hint = {"login_hint": login_hint, "prompt": "login"} if login_hint else {}
        query = urlencode(
            {
                **hint,
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "scope": self._scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": pkce_challenge(code_verifier),
                "code_challenge_method": "S256",
            }
        )
        return LoginRequest(
            url=f"{metadata['authorization_endpoint']}?{query}",
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
        )

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        metadata = await self.metadata()
        try:
            response = await self._http.post(
                metadata["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                    "code_verifier": code_verifier,
                },
                auth=(self._client_id, self._client_secret),
            )
        except httpx.HTTPError as exc:
            raise OidcError(f"identity provider unreachable: {type(exc).__name__}") from exc
        if response.is_error:
            raise OidcError(f"token exchange failed: HTTP {response.status_code}")
        tokens: dict[str, Any] = response.json()
        if "id_token" not in tokens:
            raise OidcError("token response has no id_token")
        return tokens

    async def verify_id_token(self, token: str, *, nonce: str) -> dict[str, Any]:
        claims = await self._decode(token, required=["exp", "iat", "sub", "nonce"])
        if not hmac.compare_digest(str(claims["nonce"]), nonce):
            raise OidcError("nonce mismatch")
        return claims

    async def verify_logout_token(self, token: str) -> dict[str, Any]:
        """Back-channel logout token (OpenID Connect Back-Channel Logout 1.0, section 2.6)."""
        claims = await self._decode(token, required=["iat", "events"])
        events = claims.get("events")
        if not isinstance(events, dict) or BACKCHANNEL_LOGOUT_EVENT not in events:
            raise OidcError("not a back-channel logout token")
        if "nonce" in claims:
            raise OidcError("logout tokens must not contain a nonce")
        if not claims.get("sid") and not claims.get("sub"):
            raise OidcError("logout token identifies no session")
        return claims

    async def end_session_url(self, *, id_token_hint: str, post_logout_redirect_uri: str) -> str:
        metadata = await self.metadata()
        query = urlencode(
            {
                "id_token_hint": id_token_hint,
                "post_logout_redirect_uri": post_logout_redirect_uri,
                "client_id": self._client_id,
            }
        )
        return f"{metadata['end_session_endpoint']}?{query}"

    async def _decode(self, token: str, *, required: list[str]) -> dict[str, Any]:
        metadata = await self.metadata()
        try:
            key = await self._signing_key(jwt.get_unverified_header(token).get("kid"))
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key.key,
                algorithms=ALGORITHMS,
                audience=self._client_id,
                issuer=metadata["issuer"],
                leeway=CLOCK_LEEWAY_S,
                options={"require": required},
            )
        except jwt.PyJWTError as exc:
            raise OidcError(f"invalid token: {exc}") from exc
        return claims

    async def _signing_key(self, kid: object) -> jwt.PyJWK:
        if not isinstance(kid, str):
            raise OidcError("token has no key id")
        for force_refresh in (False, True):  # refresh once: the provider may have rotated keys
            keys = await self._load_jwks(force=force_refresh)
            try:
                return keys[kid]
            except KeyError:
                continue
        raise OidcError("token signed with an unknown key")

    async def _load_jwks(self, *, force: bool) -> jwt.PyJWKSet:
        fresh = time.monotonic() - self._jwks_loaded_at < JWKS_CACHE_S
        if self._jwks is None or force or not fresh:
            metadata = await self.metadata()
            self._jwks = jwt.PyJWKSet.from_dict((await self._get(metadata["jwks_uri"])).json())
            self._jwks_loaded_at = time.monotonic()
        return self._jwks

    async def _get(self, url: str) -> httpx.Response:
        """Network errors and error statuses from the provider surface as OidcError, so routes
        can show a friendly message instead of failing with a 500."""
        try:
            response = await self._http.get(url)
        except httpx.HTTPError as exc:
            raise OidcError(f"identity provider unreachable: {type(exc).__name__}") from exc
        if response.is_error:
            raise OidcError(f"identity provider returned HTTP {response.status_code}")
        return response
