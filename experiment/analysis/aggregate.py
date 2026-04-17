"""
Aggregate experiment results and compute summary statistics.

Usage:
    python experiment/analysis/aggregate.py
    python experiment/analysis/aggregate.py --output results/summary.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).parent.parent.parent
RESULTS_DIR = REPO_ROOT / "results"

TIERS = {
    "T1-R1": 1, "T1-R2": 1,
    "T2-M1": 2, "T2-M2": 2, "T2-M3": 2,
    "T3-C1": 3, "T3-C2": 3, "T3-C3": 3,
    "T4-S1": 4, "T4-S2": 4,
}


class TaskStats(NamedTuple):
    tool: str
    task_id: str
    tier: int
    n: int
    success_rate: float
    success_ci_low: float
    success_ci_high: float
    mean_total_tokens: float
    std_total_tokens: float
    mean_tool_calls: float
    mean_llm_calls: float
    mean_wall_time: float
    validation_pass_rate: float


def wilson_ci(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion."""
    if n == 0:
        return 0.0, 0.0
    denominator = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    n = len(values)
    m = sum(values) / n
    if n < 2:
        return m, 0.0
    variance = sum((x - m) ** 2 for x in values) / (n - 1)
    return m, math.sqrt(variance)


def load_results() -> dict[tuple[str, str], list[dict]]:
    """Load all result JSONs into {(tool, task_id): [result, ...]} dict."""
    data: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for tool_dir in RESULTS_DIR.iterdir():
        if not tool_dir.is_dir():
            continue
        tool = tool_dir.name
        for task_dir in tool_dir.iterdir():
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            for result_file in sorted(task_dir.glob("[0-9][0-9][0-9].json")):
                with open(result_file) as f:
                    data[(tool, task_id)].append(json.load(f))
    return data


def compute_stats(data: dict[tuple[str, str], list[dict]]) -> list[TaskStats]:
    stats: list[TaskStats] = []

    for (tool, task_id), results in sorted(data.items()):
        n = len(results)
        successes = [r["task_success"] for r in results]
        tokens = [r["total_tokens"] for r in results]
        tool_calls = [r["tool_calls"] for r in results]
        llm_calls = [r["llm_calls"] for r in results]
        wall_times = [r["wall_time_sec"] for r in results]

        # Validation pass rate: fraction of runs where all 3 env dry-runs passed
        def validation_passed(r: dict) -> bool:
            vd = r.get("verifier_detail", {})
            return all(vd.get(f"dry_run_{env}", True) for env in ("dev", "staging", "prod"))

        val_rate = sum(1 for r in results if validation_passed(r)) / n if n > 0 else 0.0

        p = sum(successes) / n if n > 0 else 0.0
        ci_low, ci_high = wilson_ci(p, n)
        mean_tok, std_tok = mean_std(tokens)
        mean_tc, _ = mean_std(tool_calls)
        mean_lc, _ = mean_std(llm_calls)
        mean_wt, _ = mean_std(wall_times)

        stats.append(TaskStats(
            tool=tool,
            task_id=task_id,
            tier=TIERS.get(task_id, 0),
            n=n,
            success_rate=p,
            success_ci_low=ci_low,
            success_ci_high=ci_high,
            mean_total_tokens=mean_tok,
            std_total_tokens=std_tok,
            mean_tool_calls=mean_tc,
            mean_llm_calls=mean_lc,
            mean_wall_time=mean_wt,
            validation_pass_rate=val_rate,
        ))

    return stats


def print_summary(stats: list[TaskStats]) -> None:
    print(f"\n{'Task':<10} {'Tool':<12} {'N':>4} {'Success':>8} {'95% CI':>14} "
          f"{'Tokens':>8} {'ToolCalls':>10} {'WallTime':>10}")
    print("-" * 80)

    current_tier = None
    for s in sorted(stats, key=lambda x: (x.tier, x.task_id, x.tool)):
        if s.tier != current_tier:
            current_tier = s.tier
            print(f"\n--- Tier {s.tier} ---")

        ci = f"[{s.success_ci_low:.2f}, {s.success_ci_high:.2f}]"
        print(
            f"{s.task_id:<10} {s.tool:<12} {s.n:>4} {s.success_rate:>7.1%} "
            f" {ci:>14} {s.mean_total_tokens:>8.0f} {s.mean_tool_calls:>10.1f} "
            f"{s.mean_wall_time:>9.1f}s"
        )

    # Tier-level aggregates
    print("\n\n=== Tier-level Success Rates ===")
    tier_data: dict[tuple[str, int], list[float]] = defaultdict(list)
    for s in stats:
        tier_data[(s.tool, s.tier)].extend([s.success_rate] * s.n)

    for tier in sorted(set(s.tier for s in stats)):
        for tool in ("helm", "kustomize"):
            rates = [s.success_rate for s in stats if s.tool == tool and s.tier == tier]
            if rates:
                avg = sum(rates) / len(rates)
                print(f"  Tier {tier} | {tool:<12} | mean success: {avg:.1%}")


def write_csv(stats: list[TaskStats], output_path: str) -> None:
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "task_id", "tool", "tier", "n",
            "success_rate", "success_ci_low", "success_ci_high",
            "mean_total_tokens", "std_total_tokens",
            "mean_tool_calls", "mean_llm_calls", "mean_wall_time",
            "validation_pass_rate",
        ])
        for s in stats:
            writer.writerow([
                s.task_id, s.tool, s.tier, s.n,
                f"{s.success_rate:.4f}", f"{s.success_ci_low:.4f}", f"{s.success_ci_high:.4f}",
                f"{s.mean_total_tokens:.1f}", f"{s.std_total_tokens:.1f}",
                f"{s.mean_tool_calls:.2f}", f"{s.mean_llm_calls:.2f}", f"{s.mean_wall_time:.2f}",
                f"{s.validation_pass_rate:.4f}",
            ])
    print(f"\nCSV saved to: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="Path to save CSV summary")
    args = parser.parse_args()

    data = load_results()
    if not data:
        print("No results found. Run the experiment first.")
        sys.exit(1)

    stats = compute_stats(data)
    print_summary(stats)

    if args.output:
        write_csv(stats, args.output)


if __name__ == "__main__":
    main()
