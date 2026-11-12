#!/usr/bin/env python3
"""Helper for scripts/backup.sh and scripts/restore.sh: Qdrant snapshots, checksums, the
manifest and the comparisons. Standard library only (Python 3.9+), so it runs on any host
with python3 and never depends on the version of WorkMind that is deployed.

    backup_tool.py qdrant-backup  --url URL --out DIR [--include RE] [--exclude RE]
    backup_tool.py qdrant-restore --url URL --backup DIR
    backup_tool.py qdrant-counts  --url URL [--include RE] [--exclude RE]
    backup_tool.py hash-tree DIR                  per-file SHA-256 list (to stdout)
    backup_tool.py verify-tree DIR LIST           compare a tree against such a list
    backup_tool.py rows-json [--strings]          `key|value` lines (stdin) to JSON
    backup_tool.py kv-json key=text key:=json ... a JSON object (to stdout)
    backup_tool.py finish --backup DIR            manifest.json (from DIR/tmp) and SHA256SUMS
    backup_tool.py check --backup DIR             verify SHA256SUMS
    backup_tool.py get --backup DIR [--keys] KEY  a value from manifest.json (dotted key)
    backup_tool.py compare --expected F --actual F --what NAME
    backup_tool.py http-json URL                  GET a JSON document (to stdout)

QDRANT_API_KEY, when set, is sent as the api-key header.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

CHUNK = 1024 * 1024
DEFAULT_INCLUDE = r"^chunks_"
# Integration tests create chunks_test_* collections in a shared development Qdrant.
DEFAULT_EXCLUDE = r"^chunks_test_"
MANIFEST = "manifest.json"
SUMS = "SHA256SUMS"


def log(message: str) -> None:
    print(f"  {message}", file=sys.stderr)


def die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


# --- HTTP ------------------------------------------------------------------------------------


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if os.environ.get("QDRANT_API_KEY"):
        headers["api-key"] = os.environ["QDRANT_API_KEY"]
    headers.update(extra or {})
    return headers


def _request(url: str, **kwargs: Any) -> urllib.request.Request:
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        die(f"not an http(s) URL: {url}")
    return urllib.request.Request(url, **kwargs)  # noqa: S310 (checked just above)


def call(method: str, url: str, body: Any = None, timeout: float = 600) -> Any:
    data = None if body is None else json.dumps(body).encode()
    headers = _headers({"Content-Type": "application/json"} if data is not None else None)
    request = _request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read()
    except urllib.error.HTTPError as exc:
        die(f"{method} {url}: HTTP {exc.code} {exc.read()[:300].decode(errors='replace')}")
    except urllib.error.URLError as exc:
        die(f"{method} {url}: {exc.reason}")
    return json.loads(payload) if payload else None


def download(url: str, target: Path) -> tuple[int, str]:
    """Streams to disk; returns (bytes, sha256)."""
    digest, size = hashlib.sha256(), 0
    request = _request(url, headers=_headers())
    try:
        response = urllib.request.urlopen(request, timeout=3600)  # noqa: S310
        with response, target.open("wb") as out:
            while block := response.read(CHUNK):
                out.write(block)
                digest.update(block)
                size += len(block)
    except urllib.error.HTTPError as exc:
        die(f"GET {url}: HTTP {exc.code}")
    return size, digest.hexdigest()


def upload_snapshot(base: str, collection: str, path: Path, sha256: str) -> None:
    """POST /collections/{name}/snapshots/upload as multipart, streamed from disk. Qdrant
    checks the checksum and replaces the collection with the snapshot's contents."""
    parts = urllib.parse.urlsplit(base)
    query = urllib.parse.urlencode({"priority": "snapshot", "wait": "true", "checksum": sha256})
    target = f"{parts.path.rstrip('/')}/collections/{collection}/snapshots/upload?{query}"
    boundary = uuid.uuid4().hex
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="snapshot"; '
        f'filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    connection_type = (
        http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(parts.hostname or "localhost", parts.port, timeout=3600)
    headers = _headers(
        {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(head) + path.stat().st_size + len(tail)),
        }
    )
    connection.putrequest("POST", target)
    for name, value in headers.items():
        connection.putheader(name, value)
    connection.endheaders()
    connection.send(head)
    with path.open("rb") as source:
        while block := source.read(CHUNK):
            connection.send(block)
    connection.send(tail)
    response = connection.getresponse()
    body = response.read()
    connection.close()
    if response.status >= 300:
        die(f"snapshot upload for {collection}: HTTP {response.status} {body[:300]!r}")


# --- Qdrant ----------------------------------------------------------------------------------


def collections(base: str, include: str, exclude: str) -> list[str]:
    names = [c["name"] for c in call("GET", f"{base}/collections")["result"]["collections"]]
    return sorted(n for n in names if re.search(include, n) and not re.search(exclude, n))


def count(base: str, name: str) -> int:
    result = call("POST", f"{base}/collections/{name}/points/count", {"exact": True})
    return int(result["result"]["count"])


def dense_size(base: str, name: str) -> int | None:
    vectors = call("GET", f"{base}/collections/{name}")["result"]["config"]["params"]["vectors"]
    if isinstance(vectors, dict) and "size" in vectors:
        return int(vectors["size"])
    sizes = [v.get("size") for v in vectors.values() if isinstance(v, dict)]
    return int(sizes[0]) if sizes and sizes[0] else None


def qdrant_backup(args: argparse.Namespace) -> None:
    base, out = args.url.rstrip("/"), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {}
    for name in collections(base, args.include, args.exclude):
        before = count(base, name)
        started = time.monotonic()
        created = call("POST", f"{base}/collections/{name}/snapshots?wait=true")["result"]
        snapshot = created["name"]
        try:
            target = out / f"{name}.snapshot"
            size, sha256 = download(
                f"{base}/collections/{name}/snapshots/{urllib.parse.quote(snapshot)}", target
            )
        finally:
            # The snapshot also takes disk space inside Qdrant: never leave it there.
            call("DELETE", f"{base}/collections/{name}/snapshots/{urllib.parse.quote(snapshot)}")
        after = count(base, name)
        result[name] = {
            "file": f"qdrant/{target.name}",
            "points": before,
            "points_after_snapshot": after,
            "dense_size": dense_size(base, name),
            "bytes": size,
            "sha256": sha256,
            "seconds": round(time.monotonic() - started, 2),
        }
        note = "" if before == after else f" (changed to {after} during the snapshot)"
        log(f"qdrant {name}: {before} points, {size} bytes{note}")
    (out / "collections.json").write_text(json.dumps(result, indent=2) + "\n")


def qdrant_restore(args: argparse.Namespace) -> None:
    base, backup = args.url.rstrip("/"), Path(args.backup)
    manifest = json.loads((backup / MANIFEST).read_text())
    failed = False
    for name, info in sorted(manifest["qdrant"]["collections"].items()):
        started = time.monotonic()
        upload_snapshot(base, name, backup / info["file"], info["sha256"])
        points = count(base, name)
        ok = points == info["points"]
        failed |= not ok
        log(
            f"qdrant {name}: {points} points restored, {info['points']} in the manifest "
            f"{'OK' if ok else 'MISMATCH'} ({time.monotonic() - started:.1f}s)"
        )
    if failed:
        raise SystemExit(1)


def qdrant_counts(args: argparse.Namespace) -> None:
    base = args.url.rstrip("/")
    print(json.dumps({n: count(base, n) for n in collections(base, args.include, args.exclude)}))


# --- Files -----------------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def tree(root: Path) -> list[tuple[str, int, str]]:
    return [
        (p.relative_to(root).as_posix(), p.stat().st_size, sha256_file(p))
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
    ]


def hash_tree(args: argparse.Namespace) -> None:
    for relative, _size, digest in tree(Path(args.dir)):
        print(f"{digest}  {relative}")


def verify_tree(args: argparse.Namespace) -> None:
    expected = {}
    for line in Path(args.list).read_text().splitlines():
        if line.strip():
            digest, relative = line.split("  ", 1)
            expected[relative] = digest
    actual = {relative: digest for relative, _size, digest in tree(Path(args.dir))}
    missing = sorted(set(expected) - set(actual))
    changed = sorted(r for r in expected if r in actual and actual[r] != expected[r])
    extra = sorted(set(actual) - set(expected))
    log(
        f"objects: {len(expected)} in the backup, {len(expected) - len(missing) - len(changed)} "
        f"restored with the same SHA-256, {len(missing)} missing, {len(changed)} different, "
        f"{len(extra)} not in the backup"
    )
    for relative in (missing + changed)[:20]:
        log(f"  {relative}")
    if missing or changed:
        raise SystemExit(1)


def rows_json(args: argparse.Namespace) -> None:
    rows: dict[str, Any] = {}
    for line in sys.stdin.read().splitlines():
        if "|" in line:
            key, value = line.rsplit("|", 1)
            rows[key.strip()] = value.strip() if args.strings else int(value)
    print(json.dumps(dict(sorted(rows.items()))))


def kv_json(args: argparse.Namespace) -> None:
    """key=value (a string) or key:=value (JSON), as in httpie."""
    result: dict[str, Any] = {}
    for item in args.items:
        if ":=" in item.split("=", 1)[0] + "=":
            key, raw = item.split(":=", 1)
            result[key] = json.loads(raw)
        else:
            key, value = item.split("=", 1)
            result[key] = value.strip()
    print(json.dumps(result))


def _load(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text()) if path.is_file() else default


def _manifest(backup: Path) -> dict[str, Any]:
    """Assembles what backup.sh left in tmp/ into the manifest."""
    tmp = backup / "tmp"
    info = _load(tmp / "info.json", {})
    app = _load(tmp / "app.json")
    status = _load(tmp / "status.json", {})
    collections = _load(backup / "qdrant" / "collections.json", {})
    owners = _load(tmp / "owners.json", {})
    objects = [line for line in _read_lines(backup / "objects.sha256")]
    if app is None:  # no api container to ask: the version from /system/status, the
        # embedding model from the collection names (chunks_<model>_<dimensions>)
        names = sorted(collections)
        named = re.match(r"^chunks_(.+)_(\d+)$", names[0]) if len(names) == 1 else None
        app = {
            "version": status.get("version"),
            "app_env": status.get("environment"),
            "embedding_model": f"{named.group(1)} (from the collection name)" if named else None,
            "embedding_dimensions": collections[names[0]]["dense_size"] if names else None,
            "qdrant_collection": names[0] if len(names) == 1 else None,
            "from": "system status and Qdrant (no api container to ask)",
        }
    databases = info.get("databases", "").split()
    return {
        "format": 1,
        "created_at": info.get("created_at"),
        "finished_at": info.get("finished_at"),
        "consistency": {
            "mode": info.get("mode"),
            "stopped_services": info.get("stopped", "").split(),
            "postgres": f"{info.get('app_database')}: one exported snapshot for the dump and "
            "the counts; the other databases are dumped one after the other",
        },
        "source": {
            "host": info.get("host"),
            "deploy_dir": info.get("deploy_dir"),
            "compose_project": info.get("compose_project") or None,
            "postgres": info.get("pg_source") or "compose service postgres",
            "qdrant_url": info.get("qdrant_url"),
            "public_url": app.get("public_url"),
        },
        "app": {
            "version": app.get("version"),
            "app_env": app.get("app_env"),
            "git_commit": info.get("git_commit") or None,
            "git_describe": info.get("git_describe") or None,
            "api_image": info.get("api_image") or None,
            "settings_from": app.get("from"),
        },
        "alembic_revision": info.get("alembic_revision"),
        "embedding": {
            "model": app.get("embedding_model"),
            "dimensions": app.get("embedding_dimensions"),
            "collection": app.get("qdrant_collection"),
        },
        "postgres": {
            "server_version": info.get("pg_version"),
            "app_database": info.get("app_database"),
            "databases": {
                db: {"file": f"postgres/{db}.dump", "owner": owners.get(db)} for db in databases
            },
            "globals": "postgres/globals.sql",
        },
        "row_counts": _load(tmp / "rows.json", {}),
        "documents_by_status": _load(tmp / "documents.json", {}),
        "qdrant": {"collections": collections},
        "objects": {
            "archive": "objects.tar.gz" if objects else None,
            "list": "objects.sha256",
            "files": len(objects),
        },
        "license": {"files": info.get("license_files", "").split()},
        "seconds": {
            key.removeprefix("seconds_"): value
            for key, value in info.items()
            if key.startswith("seconds_")
        },
        "not_included": [
            "Valkey: sessions, sign-in flows, the job queue, run events, caches",
            "the beat schedule volume and the model caches (speech, embeddings)",
            ".env and every secret (SECRETS_ENCRYPTION_KEY, SECRETS_KEY, OIDC_CLIENT_SECRET, "
            "database passwords): keep them apart from the backups",
            "the development license signing key (.workmind/dev-signing.key)",
        ],
    }


def _read_lines(path: Path) -> list[str]:
    return (
        [line for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []
    )


def finish(args: argparse.Namespace) -> None:
    """The manifest, with the size and SHA-256 of every file, and SHA256SUMS (for
    `sha256sum -c` / `shasum -a 256 -c`)."""
    backup = Path(args.backup)
    manifest = _manifest(backup)
    files = [
        (relative, size, digest)
        for relative, size, digest in tree(backup)
        if relative not in (MANIFEST, SUMS) and not relative.startswith("tmp/")
    ]
    manifest["files"] = {relative: {"bytes": size, "sha256": d} for relative, size, d in files}
    manifest["total_bytes"] = sum(size for _r, size, _d in files)
    (backup / SUMS).write_text("".join(f"{d}  {r}\n" for r, _s, d in files))
    (backup / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")


def check(args: argparse.Namespace) -> None:
    backup = Path(args.backup)
    if not (backup / MANIFEST).is_file() or not (backup / SUMS).is_file():
        die(f"{backup} is not a WorkMind backup (no {MANIFEST} or {SUMS})")
    bad = []
    lines = [line for line in (backup / SUMS).read_text().splitlines() if line.strip()]
    for line in lines:
        digest, relative = line.split("  ", 1)
        path = backup / relative
        if not path.is_file() or sha256_file(path) != digest:
            bad.append(relative)
    if bad:
        die(f"checksum mismatch or missing file: {', '.join(bad[:10])}")
    log(f"backup checksums: {len(lines)} files OK")


def get(args: argparse.Namespace) -> None:
    value: Any = json.loads((Path(args.backup) / MANIFEST).read_text())
    for part in args.key.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    if value is None:
        return
    if args.keys:
        print("\n".join(value) if isinstance(value, dict) else "")
        return
    print(value if isinstance(value, (str, int, float)) else json.dumps(value))


def compare(args: argparse.Namespace) -> None:
    expected = json.loads(Path(args.expected).read_text())
    actual = json.loads(Path(args.actual).read_text())
    differences = {
        key: (expected.get(key), actual.get(key))
        for key in sorted(set(expected) | set(actual))
        if expected.get(key) != actual.get(key)
    }
    total = sum(v for v in expected.values() if isinstance(v, int))
    log(
        f"{args.what}: {len(expected)} expected, {len(expected) - len(differences)} equal "
        f"({total} in total), {len(differences)} different"
    )
    for key, (want, got) in differences.items():
        log(f"  {key}: backup {want}, now {got}")
    if differences:
        raise SystemExit(1)


def http_json(args: argparse.Namespace) -> None:
    print(json.dumps(call("GET", args.url, timeout=30)))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("qdrant-backup", qdrant_backup), ("qdrant-counts", qdrant_counts)):
        sub = commands.add_parser(name)
        sub.add_argument("--url", required=True)
        sub.add_argument("--include", default=os.environ.get("QDRANT_INCLUDE", DEFAULT_INCLUDE))
        sub.add_argument("--exclude", default=os.environ.get("QDRANT_EXCLUDE", DEFAULT_EXCLUDE))
        if name == "qdrant-backup":
            sub.add_argument("--out", required=True)
        sub.set_defaults(handler=handler)
    sub = commands.add_parser("qdrant-restore")
    sub.add_argument("--url", required=True)
    sub.add_argument("--backup", required=True)
    sub.set_defaults(handler=qdrant_restore)
    sub = commands.add_parser("hash-tree")
    sub.add_argument("dir")
    sub.set_defaults(handler=hash_tree)
    sub = commands.add_parser("verify-tree")
    sub.add_argument("dir")
    sub.add_argument("list")
    sub.set_defaults(handler=verify_tree)
    sub = commands.add_parser("rows-json")
    sub.add_argument("--strings", action="store_true", help="values are text, not counts")
    sub.set_defaults(handler=rows_json)
    sub = commands.add_parser("kv-json")
    sub.add_argument("items", nargs="*")
    sub.set_defaults(handler=kv_json)
    sub = commands.add_parser("finish")
    sub.add_argument("--backup", required=True)
    sub.set_defaults(handler=finish)
    sub = commands.add_parser("check")
    sub.add_argument("--backup", required=True)
    sub.set_defaults(handler=check)
    sub = commands.add_parser("get")
    sub.add_argument("--backup", required=True)
    sub.add_argument("--keys", action="store_true", help="the keys of an object, one per line")
    sub.add_argument("key")
    sub.set_defaults(handler=get)
    sub = commands.add_parser("compare")
    sub.add_argument("--expected", required=True)
    sub.add_argument("--actual", required=True)
    sub.add_argument("--what", required=True)
    sub.set_defaults(handler=compare)
    sub = commands.add_parser("http-json")
    sub.add_argument("url")
    sub.set_defaults(handler=http_json)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
