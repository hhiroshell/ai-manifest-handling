"""
Git-based working directory reset.

The experiment runner calls reset_to_baseline() before each run to ensure
every agent starts from the same clean state.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


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
    Discards all uncommitted changes and restores tracked files.
    Also removes untracked files that were created since the baseline.
    """
    wd = Path(working_dir)

    # Restore all tracked files
    subprocess.run(
        ["git", "checkout", BASELINE_TAG, "--", "."],
        cwd=working_dir, check=True
    )

    # Remove untracked files (new files created by the agent)
    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=working_dir, capture_output=True, text=True
    )
    untracked = [f.strip() for f in result.stdout.splitlines() if f.strip()]
    for f in untracked:
        file_path = wd / f
        if file_path.exists():
            file_path.unlink()

    # Remove empty directories left behind
    for dirpath in sorted(wd.rglob("*"), reverse=True):
        if dirpath.is_dir() and dirpath != wd:
            try:
                dirpath.rmdir()  # only removes if empty
            except OSError:
                pass
