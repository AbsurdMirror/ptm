"""Domain enums and shared helpers (V0.3)."""

from __future__ import annotations

from enum import StrEnum


class CategoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MatterStatus(StrEnum):
    BACKLOG = "backlog"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class StageStatus(StrEnum):
    BACKLOG = "backlog"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PlanType(StrEnum):
    TIME_BLOCK = "time_block"
    TIME_BUDGET = "time_budget"


class PlanStatus(StrEnum):
    PLANNED = "planned"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    MISSED = "missed"


class Flexibility(StrEnum):
    FIXED = "fixed"
    FLEXIBLE = "flexible"


class DurationPrecision(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    UNKNOWN = "unknown"


class ExecutionResult(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class ObservationType(StrEnum):
    FEEDBACK = "feedback"
    PREFERENCE = "preference"
    PATTERN = "pattern"


class ObservationSource(StrEnum):
    USER = "user"
    AI = "ai"
    STATISTICS = "statistics"


class ObservationStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"


class ReminderStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SUBMITTED = "submitted"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CaptureKind(StrEnum):
    MATTER = "matter"
    OBSERVATION = "observation"


SEED_CATEGORIES = (
    ("工作", "工作与研究"),
    ("学习", "学习与个人成长"),
    ("运动健康", "运动与健康"),
    ("日常生活", "日常事务"),
    ("社交关系", "社交关系"),
    ("娱乐休闲", "休息娱乐"),
)

QUERYABLE_ENTITIES = frozenset(
    {
        "category",
        "matter",
        "stage",
        "plan",
        "execution",
        "observation",
        "event",
        "reminder",
    }
)

MUTABLE_ENTITIES = frozenset(
    {
        "category",
        "matter",
        "stage",
        "plan",
        "execution",
        "observation",
        "reminder",
    }
)

DURATION_MINUTE_TOLERANCE = 2
