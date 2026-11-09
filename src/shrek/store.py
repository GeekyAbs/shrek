"""The ledger store. SQLModel over SQLite; Postgres is a later drop-in."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, SQLModel, create_engine, select

from shrek.models import Expense, coerce_category

#: Timezone-naive local period helper — totals follow the user's wall clock.
_PERIODS = ("today", "week", "month")


def period_start(period: str, now: Optional[datetime] = None) -> datetime:
    """Local-calendar start for a period keyword."""
    now = now or datetime.now()
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        days_back = (now.weekday() + 1) % 7  # weeks start Monday
        start = (now - timedelta(days=days_back)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"unknown period: {period!r}; expected one of {_PERIODS}")


def normalize_period(period: Optional[str]) -> Optional[str]:
    """Accept tiny variants ('this week', 'This Month') -> canonical keyword."""
    if period is None:
        return None
    if not isinstance(period, str):
        raise ValueError(f"period must be a string, got {type(period).__name__}")
    p = period.strip().lower()
    for canon in _PERIODS:
        if p == canon or p == f"this {canon}":
            return canon  # 'last <kw>' is a DIFFERENT window — deliberately rejected
    raise ValueError(f"unknown period: {period!r}; expected one of {_PERIODS}")


class Store:
    """Owns one SQLite file; safe to instantiate twice over the same path."""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SQLModel.metadata.create_all(self.engine)

    # -- writes ---------------------------------------------------------

    def add(self, expense: Expense) -> int:
        expense.category = coerce_category(expense.category)
        with Session(self.engine) as session:
            session.add(expense)
            session.commit()
            session.refresh(expense)
            return expense.id

    def delete(self, expense_id: int) -> bool:
        with Session(self.engine) as session:
            row = session.get(Expense, expense_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    # -- reads ----------------------------------------------------------

    def _filtered(
        self,
        category: Optional[str] = None,
        period: Optional[str] = None,
        now: Optional[datetime] = None,
    ):
        stmt = select(Expense).order_by(Expense.ts.desc(), Expense.id.desc())
        if category is not None:
            stmt = stmt.where(Expense.category == coerce_category(category))
        canon = normalize_period(period)
        if canon is not None:
            stmt = stmt.where(Expense.ts >= period_start(canon, now))
        return stmt

    def list(
        self,
        limit: Optional[int] = None,
        category: Optional[str] = None,
        period: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> list[Expense]:
        stmt = self._filtered(category=category, period=period, now=now)
        with Session(self.engine) as session:
            rows = session.exec(stmt).all()
            if limit is not None:
                rows = rows[: int(limit)]
            return list(rows)

    def total(
        self,
        category: Optional[str] = None,
        period: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> dict[str, float]:
        """Sum per currency — never converted, never mixed (plan Q13)."""
        stmt = self._filtered(category=category, period=period, now=now)
        sums: dict[str, float] = defaultdict(float)
        with Session(self.engine) as session:
            for row in session.exec(stmt).all():
                sums[row.currency] += row.amount
        return {currency: round(total, 2) for currency, total in sorted(sums.items())}

    def update_category(self, expense_id: int, category: str) -> Optional[Expense]:
        """Re-chew one row's category in place: id, timestamp and currency
        untouched (moving a 3-day-old row must not move its date)."""
        if expense_id is None:
            return None
        category = coerce_category(category)
        with Session(self.engine) as session:
            row = session.get(Expense, expense_id)
            if row is None:
                return None
            row.category = category
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def latest(self) -> Optional[Expense]:
        with Session(self.engine) as session:
            stmt = select(Expense).order_by(Expense.id.desc()).limit(1)
            return session.exec(stmt).first()

    def get(self, expense_id: int) -> Optional[Expense]:
        with Session(self.engine) as session:
            return session.get(Expense, expense_id)
