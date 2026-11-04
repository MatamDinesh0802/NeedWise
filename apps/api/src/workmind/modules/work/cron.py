"""Five-field cron expressions (minute hour day-of-month month day-of-week), evaluated in UTC.

Supports *, lists (1,15), ranges (1-5), steps (*/15, 0-30/10) and day or month names (MON, JAN).
As in classic cron, when both day fields are restricted a day matches if either does."""

from datetime import datetime

DAYS = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}
MONTHS = {
    m: i
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
        start=1,
    )
}
BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


class CronError(ValueError):
    pass


def _value(token: str, names: dict[str, int]) -> int:
    upper = token.upper()
    if upper in names:
        return names[upper]
    if not token.isdigit():
        raise CronError(f"not a number or name: {token!r}")
    return int(token)


def _field(spec: str, low: int, high: int, names: dict[str, int]) -> set[int]:
    values: set[int] = set()
    for part in spec.split(","):
        base, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if step < 1:
            raise CronError(f"bad step in {part!r}")
        if base == "*":
            start, end = low, high
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = _value(a, names), _value(b, names)
        else:
            start = _value(base, names)
            end = high if step_text else start
        if not (low <= start <= high and low <= end <= high and start <= end):
            raise CronError(f"{part!r} is outside {low}-{high}")
        values.update(range(start, end + 1, step))
    return values


class Cron:
    def __init__(self, expression: str) -> None:
        fields = expression.split()
        if len(fields) != 5:
            raise CronError("a cron expression has five fields: minute hour day month weekday")
        names = [{}, {}, {}, MONTHS, DAYS]
        parsed = [
            _field(f, lo, hi, n) for f, (lo, hi), n in zip(fields, BOUNDS, names, strict=True)
        ]
        self.minutes, self.hours, self.days, self.months, weekdays = parsed
        self.weekdays = {d % 7 for d in weekdays}  # 7 is Sunday too
        self._day_any, self._weekday_any = fields[2] == "*", fields[4] == "*"

    def matches(self, when: datetime) -> bool:
        if (
            when.minute not in self.minutes
            or when.hour not in self.hours
            or when.month not in self.months
        ):
            return False
        day = when.day in self.days
        weekday = (when.isoweekday() % 7) in self.weekdays
        if self._day_any or self._weekday_any:
            return day and weekday
        return day or weekday
