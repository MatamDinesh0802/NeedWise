import pytest

from workmind.modules.identity.tokens import (
    display_prefix,
    generate_token,
    hash_token,
    tenant_slug_of,
)


def test_generated_tokens_carry_the_tenant_and_256_bits_of_randomness() -> None:
    first, second = generate_token("acme"), generate_token("acme")

    assert first != second
    assert tenant_slug_of(first) == "acme"
    assert len(first.removeprefix("wm_pat_acme_")) >= 43  # 32 random bytes, base64url
    assert display_prefix(first) == first[: len("wm_pat_acme_") + 4]
    assert hash_token(first) != hash_token(second)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "wm_pat_",
        "wm_pat_acme",
        "wm_pat_acme_short",
        "Bearer wm_pat_acme_" + "x" * 43,
        "wm_pat_Acme_" + "x" * 43,
        "wm_pat_acme_" + "x" * 400,
    ],
)
def test_malformed_tokens_have_no_tenant(token: str) -> None:
    assert tenant_slug_of(token) is None


def test_slugs_with_hyphens_parse_and_secrets_may_contain_underscores() -> None:
    token = "wm_pat_acme-robotics_" + "a_b-c" * 10
    assert tenant_slug_of(token) == "acme-robotics"
