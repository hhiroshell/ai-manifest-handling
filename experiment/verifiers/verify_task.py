"""
Generic task verifier.

Usage:
    python verify_task.py --task T2-M1 --tool helm --working-dir /path/to/repo
                          [--agent-output "agent text output for read tasks"]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

# Allow importing lib from same directory
sys.path.insert(0, str(Path(__file__).parent))
from lib import run_checks, validate_all_rendered, git_diff_stat


def load_task(task_id: str) -> dict:
    tasks_dir = Path(__file__).parent.parent / "tasks"
    task_file = tasks_dir / f"{task_id}.yaml"
    with open(task_file) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Scoring: text-based tasks (T1-Rx)
# ---------------------------------------------------------------------------

def score_read_task(task: dict, agent_output: str) -> dict:
    scoring = task.get("scoring", "exact_match")
    results: dict[str, bool] = {}

    if scoring == "exact_match":
        expected = str(task["expected_answer"]).strip()
        # Accept the answer anywhere in the output (agent may add context)
        found = expected in agent_output
        results[f"answer_contains_{expected!r}"] = found

    elif scoring == "precision_recall":
        expected_fields = task.get("expected_fields", [])
        for field in expected_fields:
            key = field["key"]
            staging_val = str(field["staging"])
            prod_val = str(field["prod"])
            # Both values should appear somewhere in the output
            both_present = (staging_val in agent_output) and (prod_val in agent_output)
            results[f"field_{key}"] = both_present

    task_success = all(results.values())
    partial = sum(results.values()) / len(results) if results else 0.0
    return {
        "task_success": task_success,
        "partial_credit": partial,
        "verifier_detail": results,
    }


# ---------------------------------------------------------------------------
# Scoring: manifest-modification tasks
# ---------------------------------------------------------------------------

def score_modify_task(task: dict, tool: str, working_dir: str) -> dict:
    checks = task.get("checks", [])
    if not checks:
        return {"task_success": False, "partial_credit": 0.0, "verifier_detail": {}}

    check_results = run_checks(checks, tool, working_dir)

    # Also check field_path checks that use "expected_contains"
    # (run_checks handles check_type dynamically, but default is "field_value";
    #  remap checks that have expected_contains to check_type field_contains)
    # This is already handled in lib.run_checks — checks with expected_contains
    # need check_type: field_contains. For T2-M2 we set it inline.

    # kubectl dry-run validation
    dry_run_results = validate_all_rendered(tool, working_dir)
    for env, (ok, _err) in dry_run_results.items():
        check_results[f"dry_run_{env}"] = ok

    # Unnecessary file changes (informational — doesn't affect success)
    changed_files = git_diff_stat(working_dir)

    task_success = all(check_results.values())
    partial = sum(check_results.values()) / len(check_results) if check_results else 0.0

    return {
        "task_success": task_success,
        "partial_credit": partial,
        "verifier_detail": check_results,
        "files_changed": changed_files,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a task result")
    parser.add_argument("--task", required=True, help="Task ID, e.g. T2-M1")
    parser.add_argument("--tool", required=True, choices=["helm", "kustomize"])
    parser.add_argument("--working-dir", required=True, help="Repository root")
    parser.add_argument("--agent-output", default="", help="Agent text output (for read tasks)")
    args = parser.parse_args()

    task = load_task(args.task)
    category = task.get("category", "modify")

    if category == "read":
        result = score_read_task(task, args.agent_output)
    else:
        result = score_modify_task(task, args.tool, args.working_dir)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
