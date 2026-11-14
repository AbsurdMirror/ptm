# Operations

```bash
uv run ptm init
uv run ptm check
uv run ptm backup /path/to/backup.sqlite3
uv run ptm restore /path/to/backup.sqlite3
uv run ptm export /path/to/export.json
```

- SQLite: `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`
- Backups use the SQLite Backup API
- Restore checks integrity and required tables; previous DB is copied aside
- Clear-all is intentionally not exposed via MCP tools
