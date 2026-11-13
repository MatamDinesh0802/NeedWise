from pathlib import Path
from typing import Any

import pytest
import yaml

from needwise.seed.company import CompanySpec, load_company

ACME = Path(__file__).resolve().parents[3] / "seed" / "acme" / "company.yaml"


def _acme_raw() -> dict[str, Any]:
    return yaml.safe_load(ACME.read_text())


def test_the_acme_company_file_is_valid() -> None:
    spec = load_company(ACME)

    assert spec.company.slug == "acme"
    people = ("alice", "bob", "carol", "dan", "erin", "mallory")
    leaders = ("priya.raman", "hannah.schmidt", "olivia.grant")  # CEO, CFO, Head of Operations
    assert {p.email for p in spec.demo_people} == {f"{name}@acme.test" for name in people + leaders}


def test_reports_every_broken_reference_at_once() -> None:
    raw = _acme_raw()
    raw["people"][0]["department"] = "legal"
    raw["people"][0]["groups"] = ["board"]
    raw["collections"][2]["viewers"] = ["group:nobody"]

    with pytest.raises(ValueError, match="invalid company file") as error:
        CompanySpec.model_validate(raw)

    message = str(error.value)
    assert "unknown department 'legal'" in message
    assert "unknown group 'board'" in message
    assert "unknown viewer 'group:nobody'" in message


def test_rejects_people_outside_the_company_domain() -> None:
    raw = _acme_raw()
    raw["people"][1]["email"] = "bob@gmail.com"

    with pytest.raises(ValueError, match=r"must use @acme\.test"):
        CompanySpec.model_validate(raw)


def test_rejects_typos_in_field_names() -> None:
    raw = _acme_raw()
    raw["people"][0]["deparment"] = "hr"

    with pytest.raises(ValueError, match="deparment"):
        CompanySpec.model_validate(raw)


def test_restricted_collections_need_explicit_viewers() -> None:
    raw = _acme_raw()
    restricted = next(c for c in raw["collections"] if c["scope"] == "restricted")
    restricted["viewers"] = []

    with pytest.raises(ValueError, match="restricted needs viewers"):
        CompanySpec.model_validate(raw)
