#!/usr/bin/env python3
"""cc-worker: Self-contained Claude Code Worker CLI.

Invokes Claude Code in non-interactive mode for analysis/planning tasks.
Returns structured JSON output for consumption by other CLI tools.

Usage:
    python cc_worker.py exec "task description" [--cwd DIR] [--model MODEL]
    python cc_worker.py continue SESSION_ID "follow-up message"
    python cc_worker.py sessions [--limit N]
    python cc_worker.py status SESSION_ID
    python cc_worker.py delete SESSION_ID
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
WORKER_PROMPT_FILE = SCRIPTS_DIR / "worker_system.md"
SESSION_DIR = Path.home() / ".cc-worker" / "sessions"

# ---------------------------------------------------------------------------
# Client: Claude Code CLI wrapper
# ---------------------------------------------------------------------------

DEFAULT_ALLOWED_TOOLS = [
    "Read",
    "Grep",
    "Glob",
    "Task",
    "WebSearch",
    "mcp__contextweaver__codebase-retrieval",
]

# Tools explicitly removed from Claude's context — hard restriction
DISALLOWED_TOOLS = [
    "Edit",
    "Write",
    "Bash",
    "NotebookEdit",
]


def _find_claude_bin() -> str:
    import shutil
    import platform

    is_windows = platform.system() == "Windows"

    if is_windows:
        cmd_path = shutil.which("claude.cmd")
        if cmd_path:
            return cmd_path

    path = shutil.which("claude")
    if path:
        return path

    candidates = [
        Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd",
        Path.home() / ".claude" / "local" / "claude.exe",
        Path.home() / ".claude" / "local" / "claude",
        Path("/usr/local/bin/claude"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "claude"


def _build_exec_cmd(
    task: str,
    *,
    model: str | None = None,
    max_turns: int = 15,
    allowed_tools: list[str] | None = None,
) -> list[str]:
    tools = allowed_tools or DEFAULT_ALLOWED_TOOLS
    cmd = [
        _find_claude_bin(),
        "-p",
        task,
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--append-system-prompt-file",
        str(WORKER_PROMPT_FILE),
        "--allowedTools",
        ",".join(tools),
        "--disallowedTools",
        ",".join(DISALLOWED_TOOLS),
    ]
    if model:
        cmd.extend(["--model", model])
    return cmd


def _build_continue_cmd(
    session_id: str,
    message: str,
    *,
    model: str | None = None,
    max_turns: int = 15,
) -> list[str]:
    cmd = [
        _find_claude_bin(),
        "-p",
        message,
        "--resume",
        session_id,
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--disallowedTools",
        ",".join(DISALLOWED_TOOLS),
    ]
    if model:
        cmd.extend(["--model", model])
    return cmd


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def _extract_worker_json(text: str) -> dict | None:
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "status" in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass

    pattern = r"```(?:json)?\s*\n(.*?)\n```"
    matches = re.findall(pattern, text, re.DOTALL)
    for match in matches:
        try:
            data = json.loads(match)
            if isinstance(data, dict) and "status" in data:
                return data
        except json.JSONDecodeError:
            continue

    return None


def _fallback_extract_questions(text: str) -> list[str]:
    questions = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 10 or line.startswith("#"):
            continue
        cleaned = re.sub(r"^[\d]+[.)]\s*", "", line)
        cleaned = re.sub(r"^[-*]\s*", "", cleaned)
        if cleaned.endswith("?") and len(cleaned) > 10:
            questions.append(cleaned)
    return questions


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 600) -> dict:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=_clean_env(),
            timeout=timeout,
        )
    except FileNotFoundError:
        return {
            "error": "claude CLI not found. Install Claude Code first.",
            "exit_code": -1,
        }
    except subprocess.TimeoutExpired:
        return {
            "error": f"Task timed out after {timeout} seconds",
            "exit_code": -2,
        }

    if proc.returncode != 0 and not proc.stdout.strip():
        return {
            "error": proc.stderr.strip() or f"claude exited with code {proc.returncode}",
            "exit_code": proc.returncode,
        }

    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "error": "Failed to parse claude output as JSON",
            "raw_output": proc.stdout[:2000],
            "exit_code": proc.returncode,
        }

    result = {
        "session_id": raw.get("session_id", ""),
        "model": raw.get("model", ""),
        "cost_usd": raw.get("total_cost_usd", 0),
        "duration_ms": raw.get("duration_ms", 0),
        "num_turns": raw.get("num_turns", 0),
        "is_error": raw.get("is_error", False),
        "subtype": raw.get("subtype", ""),
    }

    raw_result = raw.get("result", "")
    worker_data = _extract_worker_json(raw_result)

    if worker_data:
        result["status"] = worker_data.get("status", "completed")
        result["summary"] = worker_data.get("summary", "")
        result["analysis"] = worker_data.get("analysis", "")
        result["questions"] = worker_data.get("questions", [])
        result["files_analyzed"] = worker_data.get("files_analyzed", [])
    else:
        is_truncated = raw.get("subtype") == "error_max_turns"
        questions = _fallback_extract_questions(raw_result)
        result["status"] = (
            "incomplete" if is_truncated
            else "needs_clarification" if questions
            else "completed"
        )
        result["summary"] = ""
        result["analysis"] = raw_result
        result["questions"] = questions
        result["files_analyzed"] = []

    return result


def exec_task(
    task: str,
    *,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int = 15,
    allowed_tools: list[str] | None = None,
    timeout: int = 600,
) -> dict:
    cmd = _build_exec_cmd(
        task,
        model=model,
        max_turns=max_turns,
        allowed_tools=allowed_tools,
    )
    return _run(cmd, cwd=cwd, timeout=timeout)


def continue_session(
    session_id: str,
    message: str,
    *,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int = 15,
    timeout: int = 600,
) -> dict:
    cmd = _build_continue_cmd(
        session_id,
        message,
        model=model,
        max_turns=max_turns,
    )
    return _run(cmd, cwd=cwd, timeout=timeout)


# ---------------------------------------------------------------------------
# Session Store
# ---------------------------------------------------------------------------


def _ensure_session_dir() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def _session_path(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def session_save(
    session_id: str,
    task: str,
    result: dict,
    cwd: str,
    model: str,
) -> dict:
    _ensure_session_dir()
    data = {
        "session_id": session_id,
        "task": task,
        "cwd": cwd,
        "model": model,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "turns": [
            {"role": "user", "content": task, "timestamp": _now_iso()},
            {
                "role": "assistant",
                "content": result.get("result", ""),
                "status": result.get("status"),
                "timestamp": _now_iso(),
            },
        ],
        "last_status": result.get("status", "completed"),
        "last_summary": result.get("summary", ""),
    }
    _session_path(session_id).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return data


def session_update(session_id: str, message: str, result: dict) -> dict:
    path = _session_path(session_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    now = _now_iso()
    data["turns"].append({"role": "user", "content": message, "timestamp": now})
    data["turns"].append(
        {
            "role": "assistant",
            "content": result.get("result", ""),
            "status": result.get("status"),
            "timestamp": now,
        }
    )
    data["updated_at"] = now
    data["last_status"] = result.get("status", "completed")
    data["last_summary"] = result.get("summary", "")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def session_list() -> list[dict]:
    _ensure_session_dir()
    sessions = []
    for path in SESSION_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sessions.append(
                {
                    "session_id": data["session_id"],
                    "task": data["task"],
                    "cwd": data.get("cwd", ""),
                    "model": data.get("model", ""),
                    "created_at": data["created_at"],
                    "updated_at": data.get("updated_at", data["created_at"]),
                    "turns": len(data.get("turns", [])),
                    "last_status": data.get("last_status", ""),
                    "last_summary": data.get("last_summary", ""),
                }
            )
        except (json.JSONDecodeError, KeyError):
            continue
    sessions.sort(key=lambda s: s["updated_at"], reverse=True)
    return sessions


def session_get(session_id: str) -> dict | None:
    _ensure_session_dir()
    path = _session_path(session_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    for p in SESSION_DIR.glob("*.json"):
        if p.stem.startswith(session_id):
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def session_delete(session_id: str) -> bool:
    path = _session_path(session_id)
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_json(data: dict) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _print_error(message: str) -> None:
    print(json.dumps({"error": message}, indent=2), file=sys.stderr)


def cmd_exec(args: argparse.Namespace) -> int:
    allowed_tools = None
    if args.allowed_tools:
        allowed_tools = [t.strip() for t in args.allowed_tools.split(",")]

    result = exec_task(
        args.task,
        cwd=args.cwd,
        model=args.model or None,
        max_turns=args.max_turns,
        allowed_tools=allowed_tools,
    )

    if "error" in result:
        _print_json(result)
        return 1

    session_id = result.get("session_id", "")
    if session_id:
        session_save(
            session_id=session_id,
            task=args.task,
            result=result,
            cwd=args.cwd or ".",
            model=result.get("model", args.model or ""),
        )

    _print_json(result)
    return 0


def cmd_continue(args: argparse.Namespace) -> int:
    session = session_get(args.session_id)
    if session:
        full_id = session["session_id"]
        cwd = session.get("cwd", ".")
    else:
        full_id = args.session_id
        cwd = "."

    result = continue_session(
        full_id,
        args.message,
        cwd=cwd,
        model=args.model or None,
        max_turns=args.max_turns,
    )

    if "error" in result:
        _print_json(result)
        return 1

    if session:
        session_update(full_id, args.message, result)

    _print_json(result)
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    sessions = session_list()
    if args.limit:
        sessions = sessions[: args.limit]
    _print_json({"sessions": sessions, "count": len(sessions)})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    session = session_get(args.session_id)
    if not session:
        _print_error(f"Session not found: {args.session_id}")
        return 1
    _print_json(session)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    if session_delete(args.session_id):
        _print_json({"deleted": args.session_id})
        return 0
    _print_error(f"Session not found: {args.session_id}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cc-worker",
        description="Claude Code Worker - invoke Claude Code from other CLI tools",
    )
    parser.add_argument(
        "--version", action="version", version=f"cc-worker {__version__}"
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # exec
    p_exec = sub.add_parser("exec", help="Execute a new analysis/planning task")
    p_exec.add_argument("task", help="Task description")
    p_exec.add_argument("--cwd", help="Working directory for Claude Code")
    p_exec.add_argument("--model", default=None, help="Model override")
    p_exec.add_argument("--max-turns", type=int, default=15, help="Max agentic turns (default: 15)")
    p_exec.add_argument("--allowed-tools", help="Comma-separated tool list override")
    p_exec.set_defaults(func=cmd_exec)

    # continue
    p_cont = sub.add_parser("continue", help="Continue an existing session")
    p_cont.add_argument("session_id", help="Session ID (or prefix)")
    p_cont.add_argument("message", help="Follow-up message")
    p_cont.add_argument("--model", default=None, help="Model override")
    p_cont.add_argument("--max-turns", type=int, default=15, help="Max agentic turns (default: 15)")
    p_cont.set_defaults(func=cmd_continue)

    # sessions
    p_sessions = sub.add_parser("sessions", help="List tracked sessions")
    p_sessions.add_argument("--limit", type=int, help="Max sessions to show")
    p_sessions.set_defaults(func=cmd_sessions)

    # status
    p_status = sub.add_parser("status", help="Get session details")
    p_status.add_argument("session_id", help="Session ID (or prefix)")
    p_status.set_defaults(func=cmd_status)

    # delete
    p_delete = sub.add_parser("delete", help="Delete a session record")
    p_delete.add_argument("session_id", help="Session ID to delete")
    p_delete.set_defaults(func=cmd_delete)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
