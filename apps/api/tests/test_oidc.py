"""Token verification is where login security lives, so every rejection path is tested.
A fake identity provider (httpx.MockTransport) serves discovery and signing keys."""

import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from workmind.modules.identity.oidc import (
    BACKCHANNEL_LOGOUT_EVENT,
    OidcClient,
    OidcError,
    pkce_challenge,
)

ISSUER = "https://login.example.test/realms/workmind"
CLIENT_ID = "workmind-api"


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


PROVIDER_KEY, ROGUE_KEY = _key(), _key()


def _jwks() -> dict[str, Any]:
    jwk: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(PROVIDER_KEY.public_key()))
    return {"keys": [{**jwk, "kid": "provider-key", "alg": "RS256", "use": "sig"}]}


def _provider(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/.well-known/openid-configuration"):
        return httpx.Response(
            200,
            json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
                "token_endpoint": "http://keycloak/token",
                "jwks_uri": "http://keycloak/certs",
                "end_session_endpoint": f"{ISSUER}/protocol/openid-connect/logout",
            },
        )
    if request.url.path == "/certs":
        return httpx.Response(200, json=_jwks())
    return httpx.Response(404)


@pytest.fixture
async def oidc() -> OidcClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(_provider))
    return OidcClient(
        http,
        discovery_url="http://keycloak/realms/workmind/.well-known/openid-configuration",
        client_id=CLIENT_ID,
        client_secret="secret",
        redirect_uri="http://localhost:8480/api/auth/callback",
    )


def _token(
    claims: dict[str, Any], *, key: rsa.RSAPrivateKey = PROVIDER_KEY, kid: str = "provider-key"
) -> str:
    now = int(time.time())
    base = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "user-1", "iat": now, "exp": now + 300}
    return jwt.encode({**base, **claims}, key, algorithm="RS256", headers={"kid": kid})


@pytest.mark.parametrize("failure", ["timeout", "http-500"])
async def test_provider_outages_raise_oidc_error_not_http_errors(failure: str) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(500)

    client = OidcClient(
        httpx.AsyncClient(transport=httpx.MockTransport(broken)),
        discovery_url="http://keycloak/.well-known/openid-configuration",
        client_id=CLIENT_ID,
        client_secret="secret",
        redirect_uri="http://localhost:8480/api/auth/callback",
    )

    with pytest.raises(OidcError):
        await client.begin_login()


async def test_token_exchange_timeouts_raise_oidc_error(oidc: OidcClient) -> None:
    await oidc.metadata()  # discovery succeeded; the token endpoint then times out
    oidc._http = httpx.AsyncClient(  # pyright: ignore[reportPrivateUsage]
        transport=httpx.MockTransport(
            lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("x", request=r))
        )
    )

    with pytest.raises(OidcError, match="unreachable"):
        await oidc.exchange_code("code", "verifier")


def test_pkce_challenge_matches_rfc_7636_example() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


async def test_login_url_uses_pkce_s256_and_fresh_state(oidc: OidcClient) -> None:
    first, second = await oidc.begin_login(), await oidc.begin_login()
    params = httpx.URL(first.url).params

    assert first.url.startswith(f"{ISSUER}/protocol/openid-connect/auth?")
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"] == pkce_challenge(first.code_verifier)
    assert "organization" in params["scope"].split()
    assert first.state != second.state
    assert first.nonce != second.nonce


async def test_valid_id_token_is_accepted(oidc: OidcClient) -> None:
    claims = await oidc.verify_id_token(_token({"nonce": "n-1"}), nonce="n-1")
    assert claims["sub"] == "user-1"


@pytest.mark.parametrize(
    ("claims", "kwargs", "reason"),
    [
        ({"nonce": "other"}, {}, "nonce mismatch"),
        ({"nonce": "n-1", "aud": "another-app"}, {}, "invalid token"),
        ({"nonce": "n-1", "iss": "https://evil.example.test"}, {}, "invalid token"),
        ({"nonce": "n-1", "exp": int(time.time()) - 3600}, {}, "invalid token"),
        ({}, {}, "invalid token"),  # nonce missing entirely
        ({"nonce": "n-1"}, {"key": ROGUE_KEY}, "invalid token"),  # forged signature
        ({"nonce": "n-1"}, {"kid": "unknown"}, "unknown key"),
    ],
    ids=["nonce", "audience", "issuer", "expired", "no-nonce", "forged", "unknown-kid"],
)
async def test_id_token_rejections(
    oidc: OidcClient, claims: dict[str, Any], kwargs: dict[str, Any], reason: str
) -> None:
    with pytest.raises(OidcError, match=reason):
        await oidc.verify_id_token(_token(claims, **kwargs), nonce="n-1")


async def test_unsigned_tokens_are_rejected(oidc: OidcClient) -> None:
    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": CLIENT_ID, "nonce": "n-1"},
        None,  # pyright: ignore[reportArgumentType]  (an unsigned token is the point)
        algorithm="none",
    )
    with pytest.raises(OidcError):
        await oidc.verify_id_token(unsigned, nonce="n-1")


async def test_logout_token_is_accepted(oidc: OidcClient) -> None:
    token = _token({"sid": "s-1", "events": {BACKCHANNEL_LOGOUT_EVENT: {}}})
    assert (await oidc.verify_logout_token(token))["sid"] == "s-1"


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"sid": "s-1", "events": {"other-event": {}}}, "not a back-channel logout token"),
        ({"sid": "s-1", "events": {BACKCHANNEL_LOGOUT_EVENT: {}}, "nonce": "x"}, "nonce"),
        ({"sid": "s-1"}, "invalid token"),  # no events claim at all
    ],
    ids=["wrong-event", "has-nonce", "no-events"],
)
async def test_logout_token_rejections(
    oidc: OidcClient, claims: dict[str, Any], reason: str
) -> None:
    with pytest.raises(OidcError, match=reason):
        await oidc.verify_logout_token(_token(claims))


async def test_switching_accounts_prefills_the_name_and_asks_for_a_password(
    oidc: OidcClient,
) -> None:
    plain = httpx.URL((await oidc.begin_login()).url).params
    hinted = httpx.URL((await oidc.begin_login(login_hint="bob@acme.test")).url).params

    assert "login_hint" not in plain
    assert "prompt" not in plain
    assert (hinted["login_hint"], hinted["prompt"]) == ("bob@acme.test", "login")
