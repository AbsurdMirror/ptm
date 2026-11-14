# MCP tool contract

All tools return:

```json
{"ok": true, "data": {}, "warnings": []}
```

or

```json
{"ok": false, "error": {"code": "...", "message": "...", "retryable": false, "details": {}}}
```

Business failures set MCP `isError=true`.

Error codes: `INVALID_ARGUMENT`, `NOT_FOUND`, `CONFLICT`, `VERSION_CONFLICT`, `DUPLICATE_REQUEST`, `PREVIEW_EXPIRED`, `CONFIRMATION_REQUIRED`, `LIMIT_EXCEEDED`, `STORAGE_ERROR`, `TEMPORARY_FAILURE`, `INTERNAL_ERROR`.

Write tools accept optional `request_id` for idempotency. Updates require `expected_version`.
