# CC-Worker Skill

Claude Code Skill，用于跨 CLI 调用独立的 Claude Code Worker 实例执行只读分析任务。

## 功能

- **代码库深度分析** — Worker 使用 Read、Grep、Glob、语义搜索等工具自主探索目标项目
- **实施方案生成** — 基于真实代码结构输出架构设计、文件规划和实施步骤
- **代码审查** — 输出带具体文件路径/行号引用的审查报告
- **会话管理** — 支持多轮对话，通过 `continue` 命令追问和补充

## 项目结构

```
cc-worker-skill/
├── README.md
└── cc-worker/
    ├── SKILL.md              # Skill 元数据和使用文档
    └── scripts/
        ├── cc_worker.py      # 自包含 CLI（无需 pip install）
        └── worker_system.md  # Worker 行为约束 prompt
```

## 前置条件

- Python >= 3.10
- Claude Code CLI 已安装并完成认证

## 使用方式

### 作为 Claude Code Skill 调用

将本仓库放置在 Skill 目录下，Claude Code 会自动识别 `SKILL.md` 并注册为可用 Skill。调用方式：

```
/cc-worker <task description> [--cwd <project_path>]
```

### 直接命令行调用

```bash
# 执行分析任务
python cc-worker/scripts/cc_worker.py exec "分析项目结构并生成重构方案" --cwd /path/to/project

# 继续已有会话
python cc-worker/scripts/cc_worker.py continue <session_id> "使用 JWT 方案"

# 查看会话列表
python cc-worker/scripts/cc_worker.py sessions [--limit N]

# 查看/删除会话
python cc-worker/scripts/cc_worker.py status <session_id>
python cc-worker/scripts/cc_worker.py delete <session_id>
```

### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--cwd` | Worker 的工作目录（跨项目分析时必须指定） | 当前目录 |
| `--model` | 覆盖默认模型 | 用户配置的模型 |
| `--max-turns` | 最大工具调用轮次 | 15 |
| `--allowed-tools` | 逗号分隔的工具白名单 | Read,Grep,Glob,Task,WebSearch,语义搜索 |

## 输出格式

所有命令输出 JSON 到 stdout：

```json
{
  "session_id": "uuid",
  "status": "completed | needs_clarification | incomplete",
  "summary": "一行摘要",
  "analysis": "完整的分析/方案/审查报告（Markdown）",
  "questions": [],
  "files_analyzed": ["file1", "file2"],
  "cost_usd": 0.12,
  "num_turns": 10,
  "duration_ms": 45000
}
```

| status | 含义 | 后续操作 |
|--------|------|----------|
| `completed` | 任务完成 | 读取 `analysis` |
| `needs_clarification` | Worker 有疑问 | 通过 `continue` 回答 `questions` |
| `incomplete` | 达到轮次上限 | 增加 `--max-turns` 或 `continue` |

## Worker 能力边界

Worker 以**只读模式**运行，可使用的工具：

- `Read` / `Grep` / `Glob` — 文件探索
- `Task` — 深度子代理探索
- `WebSearch` — 在线搜索
- `mcp__contextweaver__codebase-retrieval` — 语义代码搜索

Worker **不能**创建、编辑或删除文件，纯分析用途。

## 会话持久化

会话数据存储在 `~/.cc-worker/sessions/` 目录下，以 JSON 文件形式保存，支持跨调用恢复多轮对话。

## License

MIT
