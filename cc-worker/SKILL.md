---
name: cc-worker
description: "Invoke a separate Claude Code instance to perform deep analysis, generate implementation plans, or review code. Use this when you need Claude Code's full tool access (Read, Grep, Glob, semantic search) to analyze a codebase and return structured results."
argument-hint: "<task description> [--cwd <project_path>]"
---

# CC-Worker: Cross-CLI Claude Code Invocation

## Overview

Spawns a **separate Claude Code process** in Worker Mode to perform analysis and planning tasks. The worker explores the target codebase using its own tool calls (Read, Grep, Glob, semantic search), then returns a structured JSON response with the complete analysis.

Use this when you need:
- Deep codebase analysis that requires reading many files
- Implementation plans grounded in actual code structure
- Code review reports with specific file/line references
- Architecture assessments based on exploring the full project

## Setup

The skill is self-contained. The script and prompt file are located in `scripts/` alongside this SKILL.md:

```
cc-worker/
├── SKILL.md              ← this file
└── scripts/
    ├── cc_worker.py      ← self-contained CLI (no pip install needed)
    └── worker_system.md  ← worker behavior prompt
```

**Prerequisites**: Python >= 3.10, Claude Code CLI installed and authenticated.

## How to Invoke

Run the script directly via `python`. The `SCRIPT_DIR` below refers to the `scripts/` directory next to this SKILL.md.

### Execute a new task

```bash
python SCRIPT_DIR/cc_worker.py exec "<task description>" --cwd <project_path>
```

Options:
- `--cwd <path>` — Working directory for the analysis (REQUIRED for cross-project use)
- `--model <model>` — Override model (default: user's configured model)
- `--max-turns <n>` — Max tool-use turns (default: 15)
- `--allowed-tools <tools>` — Comma-separated tool list override

### Continue an existing session

```bash
python SCRIPT_DIR/cc_worker.py continue <session_id> "<follow-up message>"
```

### List tracked sessions

```bash
python SCRIPT_DIR/cc_worker.py sessions [--limit N]
```

### View session details / Delete

```bash
python SCRIPT_DIR/cc_worker.py status <session_id>
python SCRIPT_DIR/cc_worker.py delete <session_id>
```

## Output Format

All commands return JSON to stdout:

```json
{
  "session_id": "uuid — use this for continue commands",
  "status": "completed | needs_clarification | incomplete",
  "summary": "One-line summary",
  "analysis": "COMPLETE analysis/plan/review in Markdown",
  "questions": ["Clarification questions if status is needs_clarification"],
  "files_analyzed": ["list/of/files/read"],
  "cost_usd": 0.12,
  "num_turns": 10,
  "duration_ms": 45000
}
```

### Status values

| Status | Meaning | Action |
|--------|---------|--------|
| `completed` | Task fully answered | Read `analysis` |
| `needs_clarification` | Worker has questions | Answer `questions` via `continue` |
| `incomplete` | Hit max_turns limit | Increase `--max-turns` or `continue` |

## Workflow

### Standard: execute → check → (optional) continue

1. Run `exec` with a clear task description and `--cwd` pointing to the target project.
2. Parse JSON output.
3. Check `status`:
   - `completed` → use `analysis` directly.
   - `needs_clarification` → read `questions`, then `continue <session_id> "<answers>"`.
   - `incomplete` → `continue <session_id> "please complete the analysis"`.

### Example: Generate implementation plan

```bash
SCRIPT_DIR="path/to/cc-worker/scripts"

result=$(python "$SCRIPT_DIR/cc_worker.py" exec \
  "分析项目结构，为用户认证功能出具实施方案" \
  --cwd /path/to/project)

session_id=$(echo "$result" | jq -r '.session_id')
status=$(echo "$result" | jq -r '.status')

if [ "$status" = "needs_clarification" ]; then
  result=$(python "$SCRIPT_DIR/cc_worker.py" continue "$session_id" \
    "使用 JWT，不需要 OAuth")
fi

echo "$result" | jq -r '.analysis'
```

### Example: Code review

```bash
python "$SCRIPT_DIR/cc_worker.py" exec \
  "审查 src/ 目录的代码质量，重点关注安全漏洞和性能问题" \
  --cwd /path/to/project
```

## Worker Capabilities

The worker runs with **read-only** tools only:
- `Read`, `Grep`, `Glob` — file exploration
- `Task` — deep sub-agent exploration
- `WebSearch` — online research
- `mcp__contextweaver__codebase-retrieval` — semantic code search

The worker **cannot** create, edit, or delete files. It is purely analytical.

## Session Persistence

Sessions are stored in `~/.cc-worker/sessions/` as JSON files. They survive across invocations, enabling multi-turn conversations via `continue`.

## When NOT to use this

- Simple questions that don't require exploring a codebase — just answer directly.
- Tasks that require writing code — use implementation skills instead.
- If you already have enough context from files you've read — no need to spawn a worker.
