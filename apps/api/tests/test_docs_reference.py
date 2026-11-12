"""The generated reference pages in docs/reference/ match the code (workmind.docs_gen)."""

import json
from pathlib import Path

import pytest

from workmind import docs_gen
from workmind.core.config import Settings

STALE = "{path} is out of date: run `make docs-gen` and commit the result."


def _committed(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"{path.relative_to(docs_gen.ROOT)} is missing: run `make docs-gen`.")
    return path.read_text(encoding="utf-8")


def test_the_configuration_reference_is_up_to_date() -> None:
    page = docs_gen.CONFIGURATION_PAGE
    if _committed(page) != docs_gen.configuration_reference():
        pytest.fail(STALE.format(path=page.relative_to(docs_gen.ROOT)))


def test_the_openapi_schema_is_up_to_date() -> None:
    schema = docs_gen.OPENAPI_FILE
    if _committed(schema) != docs_gen.openapi_json():
        pytest.fail(STALE.format(path=schema.relative_to(docs_gen.ROOT)))


def test_every_setting_is_in_the_configuration_reference() -> None:
    page = docs_gen.configuration_reference()
    for name in Settings.model_fields:
        assert f'<a id="{name}"></a>`{name.upper()}`' in page


def test_notes_describe_settings_that_exist() -> None:
    """A renamed or removed setting must not leave its description behind."""
    assert set(docs_gen.NOTES) <= set(Settings.model_fields)


def test_the_schema_does_not_depend_on_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODUCT_NAME", "Renamed")
    monkeypatch.setenv("APP_ENV", "production")
    schema = docs_gen.openapi_schema()
    assert schema["info"]["title"] == "WorkMind AI API"
    assert "/api/v1/conversations" in schema["paths"]
    # Community: Enterprise-only routes (retention, the SIEM export) are not part of it.
    assert not any(path.startswith("/api/v1/admin/retention") for path in schema["paths"])


def test_field_comments_skip_section_headers_and_names() -> None:
    source = (
        "class Settings:\n"
        "    # --- Business intelligence. Datasets are DuckDB files under\n"
        "    # <path>; every query is read-only. ---\n"
        "    # Row cap per query.\n"
        "    analytics_max_rows: int = 1000\n"
        "    anthropic_api_key: str | None = None  # ANTHROPIC_API_KEY\n"
        "    llm_base_url: str = 'x'  # openai_compatible: Ollama\n"
    )
    assert docs_gen._field_comments(source) == {  # pyright: ignore[reportPrivateUsage]
        "analytics_max_rows": "Row cap per query.",
        "anthropic_api_key": "",
        "llm_base_url": "openai_compatible: Ollama",
    }


def test_table_cells_keep_pipes_and_markup_inert() -> None:
    cell = docs_gen._table_cell  # pyright: ignore[reportPrivateUsage]
    assert cell("a | b") == "a \\| b"
    assert cell("`x | y`") == "<code>x &#124; y</code>"
    assert cell("`plain`") == "`plain`"
    text = docs_gen._markdown_text("<PUBLIC_URL> and *stars*, `<kept>`")  # pyright: ignore[reportPrivateUsage]
    assert text == "&lt;PUBLIC_URL> and \\*stars\\*, `<kept>`"


def test_the_committed_schema_is_valid_json() -> None:
    schema = json.loads(_committed(docs_gen.OPENAPI_FILE))
    assert schema["openapi"].startswith("3.")
