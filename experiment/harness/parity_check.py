"""
Parity check: verify that Helm and Kustomize produce semantically identical
manifests for all three environments.

Normalizes field ordering with PyYAML round-trip (stable dump).
Ignores Helm-specific labels (helm.sh/chart, app.kubernetes.io/managed-by).

Usage:
    python experiment/harness/parity_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Allow importing verifiers lib
sys.path.insert(0, str(Path(__file__).parent.parent / "verifiers"))
from lib import load_rendered_yaml, find_resource

HELM_ONLY_LABELS = {"helm.sh/chart", "app.kubernetes.io/managed-by"}
ENVS = ["dev", "staging", "prod"]


def normalize_resource(doc: dict) -> dict:
    """
    Normalize a resource dict for comparison:
    - Remove Helm-specific labels
    - Remove empty labels/annotations dicts (Helm emits labels: {} when all
      tool labels are stripped; Kustomize omits the field entirely)
    """
    import copy
    d = copy.deepcopy(doc)
    meta = d.get("metadata", {})
    labels = meta.get("labels", {})
    for key in HELM_ONLY_LABELS:
        labels.pop(key, None)
    if not labels:
        meta.pop("labels", None)
    annotations = meta.get("annotations", {})
    if not annotations:
        meta.pop("annotations", None)
    return d


def stable_dump(obj) -> str:
    """Deterministic YAML dump (sorted keys)."""
    return yaml.dump(obj, sort_keys=True, default_flow_style=False)


def index_docs(docs: list[dict]) -> dict[tuple[str, str], dict]:
    """Build a {(kind, name): resource} index from a list of docs."""
    idx: dict[tuple[str, str], dict] = {}
    for doc in docs:
        kind = doc.get("kind", "")
        name = doc.get("metadata", {}).get("name", "")
        idx[(kind, name)] = doc
    return idx


def check_parity(working_dir: str) -> bool:
    all_ok = True

    for env in ENVS:
        helm_docs = load_rendered_yaml("helm", env, working_dir)
        kust_docs = load_rendered_yaml("kustomize", env, working_dir)

        helm_idx = index_docs([normalize_resource(d) for d in helm_docs])
        kust_idx = index_docs([normalize_resource(d) for d in kust_docs])

        helm_keys = set(helm_idx.keys())
        kust_keys = set(kust_idx.keys())

        only_helm = helm_keys - kust_keys
        only_kust = kust_keys - helm_keys

        if only_helm:
            print(f"[{env}] FAIL: resources only in Helm: {sorted(only_helm)}")
            all_ok = False
        if only_kust:
            print(f"[{env}] FAIL: resources only in Kustomize: {sorted(only_kust)}")
            all_ok = False

        for key in helm_keys & kust_keys:
            helm_yaml = stable_dump(helm_idx[key])
            kust_yaml = stable_dump(kust_idx[key])
            if helm_yaml != kust_yaml:
                kind, name = key
                print(f"[{env}] DIFF: {kind}/{name}")
                # Print a simple diff
                import difflib
                diff = list(difflib.unified_diff(
                    helm_yaml.splitlines(), kust_yaml.splitlines(),
                    fromfile=f"helm/{env}/{kind}/{name}",
                    tofile=f"kustomize/{env}/{kind}/{name}",
                    lineterm="",
                ))
                for line in diff[:40]:
                    print("  ", line)
                if len(diff) > 40:
                    print(f"  ... ({len(diff) - 40} more lines)")
                all_ok = False
            else:
                print(f"[{env}] OK:  {key[0]}/{key[1]}")

    return all_ok


def main() -> None:
    working_dir = str(Path(__file__).parent.parent.parent)
    print(f"Running parity check in: {working_dir}\n")
    ok = check_parity(working_dir)
    if ok:
        print("\nParity check PASSED: Helm and Kustomize produce identical output for all environments.")
        sys.exit(0)
    else:
        print("\nParity check FAILED: differences found (see above).")
        sys.exit(1)


if __name__ == "__main__":
    main()
