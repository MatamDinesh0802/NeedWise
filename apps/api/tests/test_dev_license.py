"""`wm license dev`: a development license the API accepts only with APP_ENV=dev."""

import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from workmind.cli import main as wm
from workmind.core.config import Settings
from workmind.core.dev_license import write_dev_license
from workmind.core.edition import FEATURES, load_edition


def _settings(directory: Path, app_env: str = "dev") -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env=app_env,  # pyright: ignore[reportArgumentType]
        license_file=directory / "license.key",
        license_dev_public_key_file=directory / "dev-public.key",
    )


def test_wm_license_dev_writes_a_license_the_dev_api_accepts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / ".workmind"
    code = wm(
        ["license", "dev", "--dir", str(out), "--features", "retention_policies", "--days", "7"]
    )
    assert code == 0

    assert sorted(p.name for p in out.iterdir()) == [
        "dev-public.key",
        "dev-signing.key",
        "license.key",
    ]
    assert stat.S_IMODE((out / "dev-signing.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((out / "license.key").stat().st_mode) == 0o644

    edition = load_edition(_settings(out))
    assert edition.enterprise
    assert edition.features == ["retention_policies"]
    assert edition.license is not None
    assert (edition.license.expires_at - edition.license.issued_at).days == 7

    printed = capsys.readouterr()
    for secret in (
        (out / "license.key").read_text().strip(),
        (out / "dev-signing.key").read_text().strip(),
    ):
        assert secret not in printed.out + printed.err
    assert "APP_ENV=dev" in printed.err


def test_the_key_pair_is_created_once_and_reused(tmp_path: Path) -> None:
    write_dev_license(tmp_path, features=list(FEATURES), days=30, licensee="Dev")
    public, private = (
        (tmp_path / "dev-public.key").read_text(),
        (tmp_path / "dev-signing.key").read_text(),
    )
    write_dev_license(tmp_path, features=["chargeback_reports"], days=1, licensee="Dev")
    assert (tmp_path / "dev-public.key").read_text() == public
    assert (tmp_path / "dev-signing.key").read_text() == private
    assert load_edition(_settings(tmp_path)).features == ["chargeback_reports"]


def test_a_dev_license_is_refused_outside_development(tmp_path: Path) -> None:
    write_dev_license(tmp_path, features=list(FEATURES), days=30, licensee="Dev")
    for environment in ("test", "demo"):
        assert load_edition(_settings(tmp_path, environment)).name == "community"


def test_all_features_by_default_and_comma_lists(tmp_path: Path) -> None:
    assert wm(["license", "dev", "--dir", str(tmp_path / "a")]) == 0
    assert load_edition(_settings(tmp_path / "a")).features == list(FEATURES)
    assert (
        wm(
            [
                "license",
                "dev",
                "--dir",
                str(tmp_path / "b"),
                "--features",
                "chargeback_reports,retention_policies",
            ]
        )
        == 0
    )
    assert load_edition(_settings(tmp_path / "b")).features == [
        "retention_policies",
        "chargeback_reports",
    ]


@pytest.mark.parametrize("args", [["--features", "everything"], ["--days", "0"], ["--days", "400"]])
def test_bad_arguments_are_refused(tmp_path: Path, args: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        wm(["license", "dev", "--dir", str(tmp_path), *args])
    assert exit_.value.code == 2
    assert not (tmp_path / "license.key").exists()


def test_the_default_directory_is_the_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    (tmp_path / "apps" / "api").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "apps" / "api")
    assert wm(["license", "dev", "--days", "1"]) == 0
    assert (tmp_path / ".workmind" / "license.key").is_file()


def test_outside_a_repository_a_directory_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        wm(["license", "dev"])


def test_the_license_expires_after_the_chosen_days(tmp_path: Path) -> None:
    now = datetime(2026, 11, 12, 9, 30, 15, 123, tzinfo=UTC)
    expires = write_dev_license(
        tmp_path, features=["retention_policies"], days=30, licensee="Dev", now=now
    )
    assert expires == datetime(2026, 12, 12, 9, 30, 15, tzinfo=UTC)
