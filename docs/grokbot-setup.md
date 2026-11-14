# Grok Bot / MCP setup

## stdio registration

```bash
uv run --directory /home/ubuntu/ptm ptm-mcp
```

Set `PTM_DATA_DIR` to a durable absolute path. Do not use a repo-relative data directory.

## Reminder polling

Preferred: platform timer every 5–15 minutes calling `ptm_reminder_dispatch` (`claim_due` then deliver then `ack`).

Fallback host cron:

```bash
*/10 * * * * cd /home/ubuntu/ptm && uv run ptm reminder-poll >>/tmp/ptm-reminder.log 2>&1
```

## Verification status

Custom Grok Bot MCP registration and outbound notification delivery are **not verified** in the build environment. Do not mark them complete until tested on the target platform.
