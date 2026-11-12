"""Business intelligence through Caddy, with real logins and the seeded Acme dataset.

Who may query and which rows they see are decided by the server from the person's principals;
these tests check that through the proxy as the browser sees it. They need `make seed` (it loads
the analytics model and tables). Set E2E_MODEL=1 to also ask the BI agent a real question
(a few cents).
"""

import os

import httpx
import pytest

from tests.e2e.conftest import same_origin_headers, sign_in


def _query(client: httpx.Client, sql: str) -> httpx.Response:
    return client.post("/api/v1/analytics/query", json={"sql": sql}, headers=same_origin_headers())


def test_an_account_executive_sees_only_his_own_accounts(browser: httpx.Client) -> None:
    sign_in(browser, "dan@acme.test")

    model = browser.get("/api/v1/analytics/model").json()
    owners = _query(browser, "SELECT DISTINCT account_owner FROM customers")
    deal_owners = _query(browser, "SELECT DISTINCT owner FROM opportunities")
    # Through a join and a subquery, too: the policy applies to every reference to a table.
    joined = _query(
        browser,
        "SELECT DISTINCT c.account_owner FROM invoice_lines i "
        "JOIN (SELECT * FROM customers) c USING (customer_id)",
    )

    assert model["available"] is True
    assert model["row_policy"].startswith("Own accounts only")
    assert owners.status_code == 200, owners.text
    assert owners.json()["rows"] == [["dan@acme.test"]]
    assert deal_owners.json()["rows"] == [["dan@acme.test"]]
    assert joined.json()["rows"] == [["dan@acme.test"]]


def test_finance_sees_every_account_and_the_guard_still_applies(browser: httpx.Client) -> None:
    sign_in(browser, "carol@acme.test")

    model = browser.get("/api/v1/analytics/model").json()
    owners = _query(browser, "SELECT count(DISTINCT account_owner) AS owners FROM customers")
    file_read = _query(browser, "SELECT * FROM read_csv('/etc/passwd')")
    drop = _query(browser, "DROP TABLE customers")
    after = _query(browser, "SELECT count(*) FROM customers")

    assert (model["available"], model["row_policy"]) == (True, None)
    assert {t["name"] for t in model["tables"]} == {
        "customers",
        "products",
        "invoice_lines",
        "opportunities",
    }
    assert owners.json()["rows"][0][0] > 1
    assert (file_read.status_code, file_read.json()["code"]) == (422, "sql_rejected")
    assert (drop.status_code, drop.json()["code"]) == (422, "sql_rejected")
    assert after.status_code == 200
    assert after.json()["rows"][0][0] > 0


def test_an_engineer_has_no_access_and_is_not_an_admin(browser: httpx.Client) -> None:
    sign_in(browser, "bob@acme.test")

    model = browser.get("/api/v1/analytics/model").json()
    query = _query(browser, "SELECT 1")
    admin = browser.get("/api/v1/admin/analytics")

    assert (model["available"], model["reason"]) == (False, "no_access")
    assert (query.status_code, query.json()["code"]) == (403, "no_access")
    assert admin.status_code == 403


def test_an_org_admin_manages_the_dataset_without_reading_it(browser: httpx.Client) -> None:
    sign_in(browser, "erin@acme.test")

    admin = browser.get("/api/v1/admin/analytics")
    query = _query(browser, "SELECT count(*) FROM invoice_lines")

    assert admin.status_code == 200, admin.text
    assert "Own accounts only" in admin.json()["yaml"]
    tables = {t["name"]: t["rows"] for t in admin.json()["dataset"]["tables"]}
    assert set(tables) == {"customers", "products", "invoice_lines", "opportunities"}
    assert all(rows > 0 for rows in tables.values())
    assert (query.status_code, query.json()["code"]) == (403, "no_access")


@pytest.mark.skipif(os.environ.get("E2E_MODEL") != "1", reason="set E2E_MODEL=1 (uses the model)")
def test_a_real_question_gets_a_chart_checked_numbers_and_rerunnable_sql(
    browser: httpx.Client,
) -> None:
    sign_in(browser, "hannah.schmidt@acme.test")

    asked = browser.post(
        "/api/v1/analytics/ask",
        json={"question": "Monthly revenue trend this year and the top 3 drivers"},
        headers=same_origin_headers(),
        timeout=300,
    )
    answer = asked.json()
    main = next(q for q in answer["queries"] if q["id"] == answer["main_query"])
    rerun = _query(browser, main["sql"])
    stored = browser.get(f"/api/v1/analytics/runs/{answer['id']}")
    runs = browser.get("/api/v1/analytics/runs").json()

    assert asked.status_code == 200, asked.text
    assert answer["status"] == "answered", answer["error"]
    assert answer["chart"] is not None
    assert answer["verification"]["total"] > 0
    assert answer["verification"]["verified"] == answer["verification"]["total"]
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["rows"] == main["rows"]  # the shown SQL reproduces the shown rows
    assert stored.json()["narrative"] == answer["narrative"]
    assert runs[0]["id"] == answer["id"]
