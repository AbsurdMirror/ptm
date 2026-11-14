# Data model (V0.3)

Core objects: Category → Matter → Stage? → Plan[] / Execution[].

- Plan and Execution are independent; one Plan may have many Executions; Execution may omit Plan.
- Observation distinguishes `user` / `ai` / `statistics` sources.
- Event is append-only audit history.
- Reminder uses status machine: `pending → claimed → submitted|delivered|failed` (or `cancelled`).
- All timestamps stored UTC; display uses IANA timezone from request / `PTM_TIMEZONE`.
- `version` starts at 1 and increments on each successful update.

See the V0.3 technical spec for field-level contracts. Schema is applied via `src/ptm/migrations/001_initial.sql`.
