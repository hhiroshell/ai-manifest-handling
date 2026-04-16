"""
Claude SDK wrapper for the experiment harness.

Runs an agentic loop using the Anthropic API with tool use.
Collects metrics (token counts, tool calls, LLM calls).
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

ALLOWED_BASH_PREFIXES = (
    "kubectl", "helm", "kustomize", "cat", "diff", "git",
    "tree", "find", "ls", "yq", "echo",
)

TOOLS: list[dict] = [
    {
        "name": "read_file",
        "description": "Read the contents of a file at the given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative path of the file to read"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file, creating or overwriting it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file to write"},
                "content": {"type": "string", "description": "Full content to write to the file"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list (default: working dir)"},
            },
            "required": [],
        },
    },
    {
        "name": "bash",
        "description": (
            "Run a shell command. Only the following commands are allowed: "
            + ", ".join(ALLOWED_BASH_PREFIXES)
            + ". Multi-command pipelines using these commands are permitted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"}
            },
            "required": ["command"],
        },
    },
]


@dataclass
class AgentMetrics:
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    wall_time_sec: float = 0.0
    retry_count: int = 0
    final_text: str = ""
    success: bool = False  # True if DONE signal detected


@dataclass
class AgentResult:
    metrics: AgentMetrics
    messages: list[dict] = field(default_factory=list)


def _is_allowed_command(command: str) -> bool:
    """Check that the command starts with an allowed prefix."""
    stripped = command.strip()
    first_token = re.split(r'\s+', stripped)[0]
    # Allow pipes as long as each segment's first token is allowed
    segments = re.split(r'[|;&]', stripped)
    for seg in segments:
        tok = re.split(r'\s+', seg.strip())[0]
        if tok and tok not in ALLOWED_BASH_PREFIXES:
            return False
    return True


def _execute_tool(tool_name: str, tool_input: dict, working_dir: str) -> str:
    """Execute a tool call and return its result as a string."""
    if tool_name == "read_file":
        path = Path(working_dir) / tool_input["path"]
        try:
            return path.read_text()
        except FileNotFoundError:
            return f"Error: file not found: {path}"
        except Exception as e:
            return f"Error reading file: {e}"

    elif tool_name == "write_file":
        path = Path(working_dir) / tool_input["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tool_input["content"])
        return f"Written {len(tool_input['content'])} bytes to {path}"

    elif tool_name == "list_directory":
        list_path = tool_input.get("path", ".")
        path = Path(working_dir) / list_path
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
            lines = []
            for e in entries:
                suffix = "/" if e.is_dir() else ""
                lines.append(f"{'d' if e.is_dir() else 'f'}  {e.name}{suffix}")
            return "\n".join(lines) if lines else "(empty)"
        except FileNotFoundError:
            return f"Error: directory not found: {path}"

    elif tool_name == "bash":
        command = tool_input["command"]
        if not _is_allowed_command(command):
            return f"Error: command not allowed. Permitted commands: {', '.join(ALLOWED_BASH_PREFIXES)}"
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                cwd=working_dir, timeout=60
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]: {result.stderr}"
            return output if output.strip() else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 60s"
        except Exception as e:
            return f"Error running command: {e}"

    return f"Error: unknown tool {tool_name!r}"


def run_agent(
    prompt: str,
    system_prompt: str,
    model: str,
    temperature: float,
    max_turns: int,
    working_dir: str,
    api_key: str | None = None,
) -> AgentResult:
    """
    Run an agentic loop using the Anthropic SDK.

    Returns AgentResult with collected metrics and full message history.
    """
    client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    metrics = AgentMetrics()
    messages: list[dict] = [{"role": "user", "content": prompt}]
    start_time = time.time()

    for turn in range(max_turns):
        metrics.llm_calls += 1

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=temperature,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        metrics.input_tokens += response.usage.input_tokens
        metrics.output_tokens += response.usage.output_tokens

        # Extract text and tool uses from response
        tool_uses = []
        text_parts = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(block)
                metrics.tool_calls += 1

        full_text = "\n".join(text_parts)
        metrics.final_text = full_text

        # Append assistant message
        messages.append({"role": "assistant", "content": response.content})

        # Check for DONE signal in text
        if re.search(r'^\s*DONE\s*$', full_text, re.MULTILINE):
            metrics.success = True
            break

        # If stop_reason is end_turn with no tool use → agent is done
        if response.stop_reason == "end_turn" and not tool_uses:
            break

        # Execute tool calls and collect results
        if tool_uses:
            tool_results = []
            for tool_use in tool_uses:
                result_content = _execute_tool(tool_use.name, tool_use.input, working_dir)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result_content,
                })
            messages.append({"role": "user", "content": tool_results})

    metrics.wall_time_sec = time.time() - start_time
    return AgentResult(metrics=metrics, messages=messages)
