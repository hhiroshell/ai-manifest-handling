"""
Experiment runner.

Iterates over (tool, task, repetition) triples, runs the agent,
collects metrics, and saves results to results/{tool}/{task_id}/{run}.json.

Usage:
    # Run all (300 total runs)
    python experiment/harness/runner.py

    # Run a single combination for debugging
    python experiment/harness/runner.py --task T2-M1 --tool helm --reps 1

    # Resume an interrupted run (skips completed result files)
    python experiment/harness/runner.py --resume
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from dataclasses import asdict
from pathlib import Path

import yaml

# Ensure verifiers and harness are importable
REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "experiment" / "verifiers"))
sys.path.insert(0, str(REPO_ROOT / "experiment" / "harness"))

from agent import run_agent, AgentMetrics
from reset import create_baseline_tag, reset_to_baseline
from verify_task import load_task, score_read_task, score_modify_task
from lib import git_diff_stat


def load_config() -> dict:
    config_path = REPO_ROOT / "experiment" / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def build_prompt(task: dict, tool: str, working_dir: str) -> str:
    """Construct the full task prompt with directory context."""
    # Include a tree of the relevant directory so the agent knows the structure
    try:
        if tool == "helm":
            tree_result = subprocess.run(
                ["tree", str(working_dir / "helm" / "bookstore")],
                capture_output=True, text=True, timeout=10
            )
            tree_output = tree_result.stdout
        else:
            tree_result = subprocess.run(
                ["tree", str(working_dir / "kustomize" / "bookstore")],
                capture_output=True, text=True, timeout=10
            )
            tree_output = tree_result.stdout
    except Exception:
        tree_output = "(tree not available)"

    tool_label = "Helm chart" if tool == "helm" else "Kustomize"
    task_prompt = task["prompt"].strip()

    return textwrap.dedent(f"""
        You are working in a Kubernetes manifest repository.
        The application is managed using {tool_label}.
        The repository root is: {working_dir}

        Repository structure:
        {tree_output}

        Task:
        {task_prompt}
    """).strip()


def result_path(tool: str, task_id: str, run: int) -> Path:
    return REPO_ROOT / "results" / tool / task_id / f"{run:03d}.json"


def save_result(tool: str, task_id: str, run: int, data: dict) -> None:
    path = result_path(tool, task_id, run)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def run_single(
    tool: str,
    task_id: str,
    run: int,
    config: dict,
    working_dir: Path,
) -> None:
    """Execute one (tool, task, run) combination."""
    exp = config["experiment"]
    task = load_task(task_id)

    print(f"\n{'='*60}")
    print(f"  Tool: {tool}  Task: {task_id}  Run: {run}")
    print(f"{'='*60}")

    # Reset working directory to baseline
    reset_to_baseline(str(working_dir))

    # Build prompt
    prompt = build_prompt(task, tool, working_dir)

    # Run agent
    agent_result = run_agent(
        prompt=prompt,
        system_prompt=exp["system_prompt"],
        model=exp["model"],
        temperature=exp["temperature"],
        max_turns=exp["max_turns"],
        working_dir=str(working_dir),
    )
    metrics = agent_result.metrics

    # Verify results
    category = task.get("category", "modify")
    if category == "read":
        verification = score_read_task(task, metrics.final_text)
    else:
        verification = score_modify_task(task, tool, str(working_dir))

    # Count file changes
    changed_files = git_diff_stat(str(working_dir))

    # Build result record
    result = {
        "tool": tool,
        "task_id": task_id,
        "run": run,
        "model": exp["model"],
        "task_success": verification["task_success"],
        "partial_credit": verification["partial_credit"],
        "llm_calls": metrics.llm_calls,
        "input_tokens": metrics.input_tokens,
        "output_tokens": metrics.output_tokens,
        "total_tokens": metrics.input_tokens + metrics.output_tokens,
        "tool_calls": metrics.tool_calls,
        "wall_time_sec": round(metrics.wall_time_sec, 2),
        "done_signal": metrics.success,
        "files_touched": len(changed_files),
        "files_changed": changed_files,
        "verifier_detail": verification.get("verifier_detail", {}),
    }

    save_result(tool, task_id, run, result)

    status = "PASS" if result["task_success"] else "FAIL"
    print(f"  Result: {status} | partial={result['partial_credit']:.2f} | "
          f"tokens={result['total_tokens']} | tool_calls={result['tool_calls']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Helm vs Kustomize experiment")
    parser.add_argument("--task", help="Run only this task ID (e.g. T2-M1)")
    parser.add_argument("--tool", choices=["helm", "kustomize"], help="Run only this tool")
    parser.add_argument("--reps", type=int, help="Number of repetitions (overrides config)")
    parser.add_argument("--resume", action="store_true", help="Skip already-completed runs")
    parser.add_argument("--skip-baseline-tag", action="store_true",
                        help="Skip creating the baseline git tag (use if already created)")
    args = parser.parse_args()

    config = load_config()
    exp = config["experiment"]
    working_dir = REPO_ROOT

    n_reps = args.reps if args.reps else exp["repetitions"]
    task_ids = [args.task] if args.task else [t["id"] for t in config["tasks"]]
    tools = [args.tool] if args.tool else ["helm", "kustomize"]

    # Create baseline git tag (once per experiment run)
    if not args.skip_baseline_tag:
        # Commit all current files to git if there are untracked files
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=str(working_dir)
        )
        if result.stdout.strip():
            subprocess.run(
                ["git", "add", "-A"], cwd=str(working_dir), check=True
            )
            subprocess.run(
                ["git", "commit", "-m", "experiment: add all manifest and harness files"],
                cwd=str(working_dir), check=True
            )
        create_baseline_tag(str(working_dir))

    total = len(tools) * len(task_ids) * n_reps
    completed = 0

    for tool in tools:
        for task_id in task_ids:
            for run in range(1, n_reps + 1):
                if args.resume and result_path(tool, task_id, run).exists():
                    print(f"  Skipping {tool}/{task_id}/{run:03d} (already done)")
                    completed += 1
                    continue

                run_single(tool, task_id, run, config, working_dir)
                completed += 1
                print(f"  Progress: {completed}/{total}")


if __name__ == "__main__":
    main()
