"""The audit log is append-only for the runtime and tamper-evident for everyone."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from needwise.core.tenancy import tenant_transaction
from needwise.modules.governance.audit import Actor, record_event, verify_chain
from tests.integration.conftest import SeededTenant, SessionFactory

TenantPair = tuple[SeededTenant, SeededTenant]


async def _record(sessions: SessionFactory, tenant: SeededTenant, count: int) -> None:
    for i in range(count):
        async with tenant_transaction(sessions, tenant.id) as session:
            await record_event(
                session,
                tenant_id=tenant.id,
                actor=Actor(type="user", id=tenant.user_ids[0]),
                action="auth.login",
                details={"attempt": i, "method": "oidc"},
                ip="203.0.113.7",
                user_agent="pytest",
            )


async def test_chain_verifies_and_sequences_are_per_tenant(
    app_sessions: SessionFactory, two_tenants: TenantPair
) -> None:
    acme, globex = two_tenants
    await _record(app_sessions, acme, 3)
    await _record(app_sessions, globex, 1)

    async with tenant_transaction(app_sessions, acme.id) as session:
        acme_result = await verify_chain(session, acme.id)
    async with tenant_transaction(app_sessions, globex.id) as session:
        globex_result = await verify_chain(session, globex.id)

    assert (acme_result.ok, acme_result.checked) == (True, 3)
    assert (globex_result.ok, globex_result.checked) == (True, 1)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'nothing.happened'",
        "DELETE FROM audit_events",
    ],
)
async def test_runtime_role_cannot_rewrite_history(
    app_sessions: SessionFactory, two_tenants: TenantPair, statement: str
) -> None:
    acme, _ = two_tenants
    await _record(app_sessions, acme, 1)

    async def rewrite() -> None:
        async with tenant_transaction(app_sessions, acme.id) as session:
            await session.execute(text(statement))

    with pytest.raises(DBAPIError, match="permission denied"):
        await rewrite()


async def test_tampering_with_privileged_access_is_detected(
    app_sessions: SessionFactory, owner_sessions: SessionFactory, two_tenants: TenantPair
) -> None:
    acme, _ = two_tenants
    await _record(app_sessions, acme, 3)

    # Someone with owner access quietly edits event #2.
    async with tenant_transaction(owner_sessions, acme.id) as session:
        await session.execute(
            text("UPDATE audit_events SET details = '{\"attempt\": 99}' WHERE seq = 2")
        )

    async with tenant_transaction(app_sessions, acme.id) as session:
        result = await verify_chain(session, acme.id)

    assert result.ok is False
    assert result.first_broken_seq == 2
