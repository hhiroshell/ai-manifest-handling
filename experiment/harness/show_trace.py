"""
Pretty-print a saved debug trace file.

Usage:
    python experiment/harness/show_trace.py results/helm/T2-M1/001_trace.json
    python experiment/harness/show_trace.py results/helm/T2-M1/001_trace.json --turn 3
    python experiment/harness/show_trace.py results/helm/T2-M1/001_trace.json --summary
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

# ANSI color codes
RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
GRAY   = "\033[90m"
RED    = "\033[91m"


def _role_color(role: str) -> str:
    return CYAN if role == "user" else GREEN


def _truncate(text: str, max_chars: int = 500) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n{GRAY}... ({len(text) - max_chars} more chars){RESET}"


def print_summary(data: dict) -> None:
    tool = data["tool"]
    task_id = data["task_id"]
    run = data["run"]
    turns = data["turns"]

    print(f"\n{BOLD}=== Trace Summary ==={RESET}")
    print(f"  Tool:    {tool}")
    print(f"  Task:    {task_id}")
    print(f"  Run:     {run}")
    print(f"  Turns:   {len(turns)}")
    print()

    # Per-assistant-turn token table
    print(f"{BOLD}{'Turn':>5}  {'Role':<12} {'In tokens':>10} {'Out tokens':>11}  Content summary{RESET}")
    print("-" * 72)

    cumulative_in = 0
    cumulative_out = 0

    for t in turns:
        role = t["role"]
        in_tok = t.get("input_tokens") or 0
        out_tok = t.get("output_tokens") or 0
        cumulative_in += in_tok
        cumulative_out += out_tok

        # One-line content summary
        summary_parts = []
        for block in t.get("content", []):
            btype = block.get("type", "?")
            if btype == "text":
                snippet = block.get("text", "").strip().replace("\n", " ")[:60]
                summary_parts.append(f'text("{snippet}")')
            elif btype == "tool_use":
                summary_parts.append(f'tool_use({block.get("name")})')
            elif btype == "tool_result":
                content = str(block.get("content", "")).strip().replace("\n", " ")[:40]
                summary_parts.append(f'tool_result("{content}")')
        summary = ", ".join(summary_parts) if summary_parts else "-"

        in_str  = f"{in_tok:,}"  if in_tok  else "-"
        out_str = f"{out_tok:,}" if out_tok else "-"
        color = _role_color(role)
        print(f"  {t['turn']:>3}  {color}{role:<12}{RESET} {in_str:>10} {out_str:>11}  {GRAY}{summary[:60]}{RESET}")

    print("-" * 72)
    print(f"  {'Total':>3}  {'':12} {cumulative_in:>10,} {cumulative_out:>11,}")
    print()


def print_turn(turn: dict, verbose: bool = False) -> None:
    role = turn["role"]
    color = _role_color(role)
    in_tok  = turn.get("input_tokens")
    out_tok = turn.get("output_tokens")
    stop    = turn.get("stop_reason", "")

    token_info = ""
    if in_tok is not None:
        token_info = f"  {GRAY}in={in_tok:,}  out={out_tok:,}  stop={stop}{RESET}"

    print(f"\n{BOLD}{color}── Turn {turn['turn']}  [{role.upper()}]{RESET}{token_info}")

    for block in turn.get("content", []):
        btype = block.get("type", "?")

        if btype == "text":
            text = block.get("text", "")
            if not verbose:
                text = _truncate(text)
            print(f"\n{text}")

        elif btype == "tool_use":
            name  = block.get("name", "?")
            inp   = block.get("input", {})
            print(f"\n  {YELLOW}▶ tool_use: {name}{RESET}")
            for k, v in inp.items():
                v_str = str(v)
                if not verbose:
                    v_str = _truncate(v_str, 300)
                indented = textwrap.indent(v_str, "      ")
                print(f"    {k}:\n{indented}")

        elif btype == "tool_result":
            tool_id = block.get("tool_use_id", "?")
            content = str(block.get("content", ""))
            if not verbose:
                content = _truncate(content, 400)
            print(f"\n  {GRAY}◀ tool_result (id={tool_id}){RESET}")
            print(textwrap.indent(content, "    "))


def main() -> None:
    parser = argparse.ArgumentParser(description="Show a debug trace file")
    parser.add_argument("trace_file", help="Path to *_trace.json")
    parser.add_argument("--turn", type=int, default=None,
                        help="Show only this turn number (0-indexed)")
    parser.add_argument("--summary", action="store_true",
                        help="Show token summary table only")
    parser.add_argument("--verbose", action="store_true",
                        help="Do not truncate long content")
    args = parser.parse_args()

    path = Path(args.trace_file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path) as f:
        data = json.load(f)

    print_summary(data)

    if args.summary:
        return

    turns = data["turns"]
    if args.turn is not None:
        matching = [t for t in turns if t["turn"] == args.turn]
        if not matching:
            print(f"Error: turn {args.turn} not found.", file=sys.stderr)
            sys.exit(1)
        for t in matching:
            print_turn(t, verbose=args.verbose)
    else:
        for t in turns:
            print_turn(t, verbose=args.verbose)


if __name__ == "__main__":
    main()
