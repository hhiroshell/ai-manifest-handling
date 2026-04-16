"""
Shared verification helpers for Helm vs Kustomize experiment.
"""

from __future__ import annotations

import subprocess
import os
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def load_rendered_yaml(tool: str, env: str, working_dir: str) -> list[dict]:
    """
    Render manifests for the given tool + environment and return a list of
    parsed Kubernetes resource dicts.

    Args:
        tool: "helm" or "kustomize"
        env: "dev", "staging", or "prod"
        working_dir: root of the repository

    Returns:
        List of dicts, one per Kubernetes resource document.
    """
    wd = Path(working_dir)

    if tool == "helm":
        chart_dir = wd / "helm" / "bookstore"
        cmd = ["helm", "template", "bookstore", str(chart_dir)]
        if env != "dev":
            values_file = chart_dir / f"values-{env}.yaml"
            cmd += ["-f", str(values_file)]
    elif tool == "kustomize":
        overlay_dir = wd / "kustomize" / "bookstore" / "overlays" / env
        cmd = ["kubectl", "kustomize", str(overlay_dir)]
    else:
        raise ValueError(f"Unknown tool: {tool!r}")

    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=working_dir
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Render failed ({tool}/{env}): {result.stderr}"
        )

    docs = list(yaml.safe_load_all(result.stdout))
    return [d for d in docs if d is not None]


# ---------------------------------------------------------------------------
# Resource lookup
# ---------------------------------------------------------------------------

def find_resource(
    docs: list[dict], kind: str, name: str
) -> dict | None:
    """
    Find a Kubernetes resource by kind and metadata.name.

    Returns None if not found.
    """
    for doc in docs:
        if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name:
            return doc
    return None


# ---------------------------------------------------------------------------
# Field access
# ---------------------------------------------------------------------------

def get_field(resource: dict, field_path: str) -> Any:
    """
    Navigate a dot-path with bracket notation support.

    Examples:
        get_field(doc, "spec.replicas")
        get_field(doc, "spec.template.spec.containers[0].image")
        get_field(doc, "spec.metrics[0].resource.target.averageUtilization")
    """
    import re

    parts = re.split(r'\.(?![^\[]*\])', field_path)
    obj = resource
    for part in parts:
        m = re.match(r'^(\w+)\[(\d+)\]$', part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            obj = obj[key][idx]
        else:
            obj = obj[part]
    return obj


def field_exists(resource: dict, field_path: str) -> bool:
    """Return True if the field_path can be resolved without KeyError/IndexError."""
    try:
        get_field(resource, field_path)
        return True
    except (KeyError, IndexError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Environment variable checks
# ---------------------------------------------------------------------------

def _container_env_vars(container: dict) -> dict[str, str]:
    """Return a flat dict of env var name→value from a container spec."""
    result: dict[str, str] = {}
    for ev in container.get("env", []):
        if "value" in ev:
            result[ev["name"]] = ev["value"]
    return result


def get_container(resource: dict, container_name: str) -> dict | None:
    """Return the named container from a Deployment/Pod spec."""
    containers = resource.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    for c in containers:
        if c.get("name") == container_name:
            return c
    return None


def env_var_exists(resource: dict, container_name: str, var_name: str, expected_value: str | None = None) -> bool:
    """Check whether an env var exists (and optionally has the expected value)."""
    container = get_container(resource, container_name)
    if container is None:
        return False
    env_vars = _container_env_vars(container)
    if var_name not in env_vars:
        return False
    if expected_value is not None:
        return env_vars[var_name] == expected_value
    return True


def env_var_absent(resource: dict, container_name: str, var_name: str) -> bool:
    """Check that an env var does NOT appear in a container."""
    container = get_container(resource, container_name)
    if container is None:
        return True  # container missing → env var certainly absent
    env_vars = _container_env_vars(container)
    return var_name not in env_vars


# ---------------------------------------------------------------------------
# ConfigMap reference checks
# ---------------------------------------------------------------------------

def references_configmap(resource: dict, container_name: str, configmap_name: str) -> bool:
    """
    Return True if the named container references the given ConfigMap via
    envFrom or individual env[].valueFrom.configMapKeyRef.
    """
    container = get_container(resource, container_name)
    if container is None:
        return False

    # Check envFrom
    for ef in container.get("envFrom", []):
        ref = ef.get("configMapRef", {})
        if ref.get("name") == configmap_name:
            return True

    # Check env[].valueFrom.configMapKeyRef
    for ev in container.get("env", []):
        ref = ev.get("valueFrom", {}).get("configMapKeyRef", {})
        if ref.get("name") == configmap_name:
            return True

    return False


# ---------------------------------------------------------------------------
# Label checks
# ---------------------------------------------------------------------------

def label_exists(resource: dict, label_key: str) -> bool:
    return label_key in resource.get("metadata", {}).get("labels", {})


def selector_uses_label(resource: dict, label_key: str) -> bool:
    selector = resource.get("spec", {}).get("selector", {})
    match_labels = selector.get("matchLabels", selector)  # Service uses selector directly
    return label_key in match_labels


def pod_template_label_exists(resource: dict, label_key: str) -> bool:
    labels = resource.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})
    return label_key in labels


# ---------------------------------------------------------------------------
# kubectl dry-run validation
# ---------------------------------------------------------------------------

def kubectl_dry_run(manifest_yaml: str) -> tuple[bool, str]:
    """
    Run kubectl apply --dry-run=client on the given YAML string.

    Returns (success: bool, error_text: str).
    """
    result = subprocess.run(
        ["kubectl", "apply", "--dry-run=client", "-f", "-"],
        input=manifest_yaml,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stderr


def validate_all_rendered(tool: str, working_dir: str) -> dict[str, tuple[bool, str]]:
    """
    Render all three environments and validate each with kubectl dry-run.

    Returns dict: env → (success, error_text)
    """
    results: dict[str, tuple[bool, str]] = {}
    for env in ("dev", "staging", "prod"):
        try:
            docs = load_rendered_yaml(tool, env, working_dir)
            manifest_yaml = yaml.dump_all(docs)
            ok, err = kubectl_dry_run(manifest_yaml)
            results[env] = (ok, err)
        except RuntimeError as exc:
            results[env] = (False, str(exc))
    return results


# ---------------------------------------------------------------------------
# git diff helpers
# ---------------------------------------------------------------------------

def git_diff_stat(working_dir: str) -> list[str]:
    """Return list of files changed relative to HEAD."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True, text=True, cwd=working_dir
    )
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    # Also include untracked files that are new
    result2 = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True, cwd=working_dir
    )
    new_files = [l.strip() for l in result2.stdout.splitlines() if l.strip()]
    return list(set(lines + new_files))


# ---------------------------------------------------------------------------
# Generic check runner
# ---------------------------------------------------------------------------

def run_checks(checks: list[dict], tool: str, working_dir: str) -> dict[str, bool]:
    """
    Execute a list of check dicts from a task YAML and return pass/fail for each.

    Each check dict must have at minimum:
        - desc: str
        - env: str
        - resource_kind: str
        - resource_name: str
        - (check_type or field_path + expected)
    """
    results: dict[str, bool] = {}

    # Cache rendered docs per env to avoid redundant re-renders
    rendered: dict[str, list[dict]] = {}

    for check in checks:
        desc = check["desc"]
        env = check["env"]

        if env not in rendered:
            try:
                rendered[env] = load_rendered_yaml(tool, env, working_dir)
            except RuntimeError:
                results[desc] = False
                continue

        docs = rendered[env]
        resource = find_resource(docs, check["resource_kind"], check["resource_name"])

        if resource is None:
            results[desc] = False
            continue

        check_type = check.get("check_type", "field_value")

        try:
            if check_type == "field_value":
                actual = get_field(resource, check["field_path"])
                expected = check["expected"]
                # Normalize numeric comparisons
                results[desc] = str(actual) == str(expected) or actual == expected

            elif check_type == "field_contains":
                actual = get_field(resource, check["field_path"])
                results[desc] = check["expected_contains"] in str(actual)

            elif check_type == "env_var_exists":
                container_name = _infer_container_name(check["resource_name"])
                results[desc] = env_var_exists(
                    resource, container_name,
                    check["env_var_name"], check.get("env_var_value")
                )

            elif check_type == "env_var_absent":
                container_name = _infer_container_name(check["resource_name"])
                results[desc] = env_var_absent(resource, container_name, check["env_var_name"])

            elif check_type == "field_absent":
                results[desc] = not field_exists(resource, check["field_path"])

            elif check_type == "references_configmap":
                container_name = _infer_container_name(check["resource_name"])
                results[desc] = references_configmap(resource, container_name, check["configmap_name"])

            elif check_type == "label_exists":
                results[desc] = label_exists(resource, check["label_key"])

            elif check_type == "selector_uses_label":
                results[desc] = selector_uses_label(resource, check["label_key"])

            elif check_type == "pod_template_label_exists":
                results[desc] = pod_template_label_exists(resource, check["label_key"])

            elif check_type == "selector_label_absent":
                results[desc] = not selector_uses_label(resource, check["label_key"])

            else:
                results[desc] = False

        except (KeyError, IndexError, TypeError):
            results[desc] = False

    return results


def _infer_container_name(resource_name: str) -> str:
    """Infer container name from resource name (e.g. bookstore-api → api)."""
    return resource_name.split("-")[-1]
