"""Checks rendered chart manifests for the rules the chart promises (docs/deploy/kubernetes.md).

    python deploy/helm/check-manifests.py rendered.yaml [...]    (needs PyYAML)

Every pod: no service account token, a RuntimeDefault seccomp profile, a non-root user.
Every container (init containers too): no privilege escalation, all capabilities dropped,
CPU and memory requests and limits, an image with a tag or digest, and a read-only root
filesystem (except Keycloak, whose stock image builds itself at start). Long-running
containers have liveness and readiness probes (except the Celery worker and beat, which have
none in compose either). ConfigMaps carry no credentials.
"""

import re
import sys
from typing import Any

import yaml

WRITABLE_ROOT = {"keycloak"}  # container names
NO_PROBES = {"worker", "beat"}  # Celery: compose has no healthcheck for them
SECRET_NAME = re.compile(r"(PASSWORD|SECRET|API_KEY|LICENSE_KEY|TOKEN)$")


def pod_spec(doc: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """(the pod spec, whether the pod runs for good)."""
    kind = doc.get("kind")
    if kind in ("Deployment", "StatefulSet", "DaemonSet"):
        return doc["spec"]["template"]["spec"], True
    if kind == "Job":
        return doc["spec"]["template"]["spec"], False
    if kind == "Pod":
        return doc["spec"], False
    return None, False


def check(doc: dict[str, Any]) -> list[str]:
    name = f"{doc.get('kind')}/{doc.get('metadata', {}).get('name')}"
    problems: list[str] = []
    if doc.get("kind") == "ConfigMap":
        for key in doc.get("data") or {}:
            if SECRET_NAME.search(key):
                problems.append(f"{name}: credential-like key {key} in a ConfigMap")
        return problems
    spec, long_running = pod_spec(doc)
    if spec is None:
        return problems
    if spec.get("automountServiceAccountToken") is not False:
        problems.append(f"{name}: automountServiceAccountToken is not false")
    pod_sc = spec.get("securityContext") or {}
    if (pod_sc.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
        problems.append(f"{name}: pod seccompProfile is not RuntimeDefault")
    containers = [(c, False) for c in spec.get("initContainers") or []]
    containers += [(c, long_running) for c in spec.get("containers") or []]
    for container, needs_probes in containers:
        where = f"{name} container {container['name']}"
        sc = container.get("securityContext") or {}
        if not (sc.get("runAsNonRoot") or pod_sc.get("runAsNonRoot")):
            problems.append(f"{where}: runAsNonRoot is not set")
        if sc.get("allowPrivilegeEscalation") is not False:
            problems.append(f"{where}: allowPrivilegeEscalation is not false")
        if "ALL" not in ((sc.get("capabilities") or {}).get("drop") or []):
            problems.append(f"{where}: capabilities are not all dropped")
        if sc.get("privileged"):
            problems.append(f"{where}: privileged")
        if not sc.get("readOnlyRootFilesystem") and container["name"] not in WRITABLE_ROOT:
            problems.append(f"{where}: root filesystem is writable")
        resources = container.get("resources") or {}
        for section in ("requests", "limits"):
            for resource in ("cpu", "memory"):
                if resource not in (resources.get(section) or {}):
                    problems.append(f"{where}: no {resource} {section}")
        image = container.get("image", "")
        if "@sha256:" not in image and (":" not in image.rsplit("/", 1)[-1] or image.endswith(":")):
            problems.append(f"{where}: image {image!r} has no tag")
        if needs_probes and container["name"] not in NO_PROBES:
            for probe in ("livenessProbe", "readinessProbe"):
                if not container.get(probe):
                    problems.append(f"{where}: no {probe}")
    return problems


def main(paths: list[str]) -> int:
    problems: list[str] = []
    pods = 0
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for doc in yaml.safe_load_all(handle):
                if not doc:
                    continue
                pods += pod_spec(doc)[0] is not None
                problems.extend(f"{path}: {p}" for p in check(doc))
    for problem in problems:
        print(problem)
    print(f"{pods} pod specs checked in {len(paths)} files: {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
