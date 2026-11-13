"""scripts/lib/backup_tool.py: how scripts/backup.sh archives object storage while the API keeps
deleting and writing objects (docs/operations/backup-restore.md, "Consistency").

Before the fix the script listed the tree, then ran tar over the list: a file deleted in between
made tar exit 1 (bsdtar) or 2 (GNU tar) and the whole backup died; and an object deleted after
the database snapshot was neither listed nor archived, so a restore reported "objects OK" while
the restored document had no original."""

import argparse
import importlib.util
import tarfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOL = Path(__file__).resolve().parents[3] / "scripts" / "lib" / "backup_tool.py"


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("backup_tool", TOOL)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text)


def _archived(archive: Path) -> dict[str, str]:
    with tarfile.open(archive) as tar:
        return {
            m.name: tar.extractfile(m).read().decode()  # pyright: ignore[reportOptionalMemberAccess]
            for m in tar.getmembers()
        }


def _run(tool: ModuleType, name: str, **kwargs: Any) -> None:
    getattr(tool, name)(argparse.Namespace(**kwargs))


def test_a_file_deleted_while_archiving_is_skipped_not_fatal(
    tool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    objects, out = tmp_path / "objects", tmp_path / "out"
    out.mkdir()
    _tree(objects, {"tenants/t1/objects/ab/doc1": "one", "tenants/t1/objects/ab/doc2": "two"})
    listed = tool.files(objects)
    (objects / "tenants/t1/objects/ab/doc2").unlink()  # DELETE /api/v1/documents/{id}, now
    monkeypatch.setattr(tool, "files", lambda _root: listed)

    _run(tool, "archive_tree", dir=str(objects), out=str(out / "o.tgz"), list=str(out / "o.sha256"))

    assert _archived(out / "o.tgz") == {"tenants/t1/objects/ab/doc1": "one"}
    sums = (out / "o.sha256").read_text().splitlines()
    assert [line.split("  ", 1)[1] for line in sums] == ["tenants/t1/objects/ab/doc1"]


def test_an_object_deleted_after_the_snapshot_is_still_archived_from_the_pins(
    tool: ModuleType, tmp_path: Path
) -> None:
    objects, out = tmp_path / "objects", tmp_path / "out"
    out.mkdir()
    _tree(objects, {"tenants/t1/objects/ab/doc1": "one", "analytics/t1/v1.duckdb": "v1"})
    pin = objects / ".backup-pin-needwise-x"

    _run(tool, "pin_tree", dir=str(objects), pin=str(pin))  # before the database snapshot
    # During the dump: a document deleted, a dataset replaced, an upload half-written.
    (objects / "tenants/t1/objects/ab/doc1").unlink()
    _tree(objects, {"analytics/t1/v2.duckdb": "v2", "tenants/t1/objects/cd/new.tmp": "par"})
    (objects / "analytics/t1/v1.duckdb").unlink()
    _run(tool, "pin_tree", dir=str(objects), pin=str(pin))  # after it
    _run(tool, "archive_tree", dir=str(pin), out=str(out / "o.tgz"), list=str(out / "o.sha256"))

    assert _archived(out / "o.tgz") == {
        "analytics/t1/v1.duckdb": "v1",
        "analytics/t1/v2.duckdb": "v2",
        "tenants/t1/objects/ab/doc1": "one",
    }
    # The pins are never part of the tree itself, and temporary files never archived.
    assert tool.files(objects) == ["analytics/t1/v2.duckdb"]


def test_the_checksums_describe_the_archived_bytes(tool: ModuleType, tmp_path: Path) -> None:
    objects, out = tmp_path / "objects", tmp_path / "out"
    out.mkdir()
    _tree(objects, {"a/x": "x" * 3_000_000, "b/y": "y"})

    _run(tool, "archive_tree", dir=str(objects), out=str(out / "o.tgz"), list=str(out / "o.sha256"))
    restored = tmp_path / "restored"
    with tarfile.open(out / "o.tgz") as tar:
        tar.extractall(restored, filter="data")

    _run(tool, "verify_tree", dir=str(restored), list=str(out / "o.sha256"))  # exits 1 if not
