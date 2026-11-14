# PTM — AI Personal Time Manager (V0.3)

独立持久化的个人时间管理程序，通过 **MCP stdio** 向 Grok Bot（或其他 MCP 客户端）提供 11 个工具。程序负责存储、校验、事务、统计与提醒状态机；不内置 LLM 决策。

规格依据：`AI个人时间管理系统_技术实现规格_V0.3`。

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
cd /home/ubuntu/ptm   # or your checkout path
uv sync
export PTM_DATA_DIR="$HOME/.local/share/ptm"   # optional; default as shown
export PTM_TIMEZONE="Asia/Shanghai"            # optional
uv run ptm init
```

## Run MCP (stdio)

Stdout is reserved for the MCP protocol; logs go to stderr.

```bash
uv run ptm-mcp
```

Example MCP client config:

```json
{
  "mcpServers": {
    "ptm": {
      "command": "uv",
      "args": ["run", "--directory", "/home/ubuntu/ptm", "ptm-mcp"],
      "env": {
        "PTM_DATA_DIR": "/home/ubuntu/.local/share/ptm",
        "PTM_TIMEZONE": "Asia/Shanghai"
      }
    }
  }
}
```

## CLI

```bash
uv run ptm init
uv run ptm check
uv run ptm backup ./backup.sqlite3
uv run ptm restore ./backup.sqlite3
uv run ptm export ./export.json
uv run ptm reminder-poll   # host-timer fallback for due reminders
```

## Tests

```bash
uv run pytest -q
```

Includes unit, SQLite integration, and real MCP stdio protocol e2e tests. Acceptance is asserted against DB rows, not chat prose.

## Tools (11)

| Tool | Role |
|---|---|
| `ptm_capture` | Record matter / observation |
| `ptm_agenda` | Daily agenda + backlog candidates |
| `ptm_record_execution` | Record actual effort |
| `ptm_plan` | Create / update / cancel / complete plans |
| `ptm_review` | Period review facts |
| `ptm_query` | Controlled read-only query |
| `ptm_mutate` | Atomic batch write + dry-run preview |
| `ptm_schema` | Capability discovery |
| `ptm_stats` | Descriptive statistics |
| `ptm_reminder` | Reminder CRUD |
| `ptm_reminder_dispatch` | Claim / ack due reminders |

## Layout

See `src/ptm/` for domain services; business logic is independent of the MCP transport (`mcp_server.py`).

## Not verified in this environment

- Grok Bot custom MCP registration / notification delivery
- Exactly-once reminder delivery (explicitly out of scope; at-least-once with lease/ack)
