"""
Git-based working directory reset.

The experiment runner calls reset_to_baseline() before each run to ensure
every agent starts from the same clean state.
"""

from __future__ import annotations

import subprocess


BASELINE_TAG = "experiment-baseline"


def create_baseline_tag(working_dir: str) -> None:
    """
    Create (or overwrite) the baseline git tag at the current HEAD.
    Call once before starting the experiment.
    """
    subprocess.run(
        ["git", "tag", "-f", BASELINE_TAG],
        cwd=working_dir, check=True
    )
    print(f"[reset] Baseline tag '{BASELINE_TAG}' set at HEAD in {working_dir}")


def reset_to_baseline(working_dir: str) -> None:
    """
    Reset the working directory to the baseline tag.

    - Restores all tracked files to the baseline state.
    - Removes untracked files and directories created by the agent.
      Directories listed in .gitignore (results/, .venv/) are preserved
      automatically because git clean respects .gitignore by default.
    """
    # Restore tracked files to baseline state
    subprocess.run(
        ["git", "checkout", BASELINE_TAG, "--", "."],
        cwd=working_dir, check=True
    )

    # Remove untracked files/dirs created by the agent.
    # -f: force, -d: include directories.
    # .gitignore entries (results/, .venv/) are automatically excluded.
    subprocess.run(
        ["git", "clean", "-fd"],
        cwd=working_dir, check=True
    )
