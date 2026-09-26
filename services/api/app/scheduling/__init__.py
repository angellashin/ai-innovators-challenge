from .simulator import simulate, validate_tasks
from datetime import date


def calendar_shift_days(before: str, after: str) -> int:
    """Calculate calendar-day change between simulator finish dates."""
    return (date.fromisoformat(after[:10]) - date.fromisoformat(before[:10])).days

__all__ = ["simulate", "validate_tasks", "calendar_shift_days"]
