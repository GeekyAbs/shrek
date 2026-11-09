"""The ledger store. SQLModel over SQLite; Postgres is a later drop-in."""

from datetime import datetime, timedelta
from typing import Optional, Sequence

from sqlalchemy import func
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
        self._migrate()

    def _migrate(self) -> None:
        """One-time v0 -> v1: add provenance, delete legacy 'other' rows.

        Gated by PRAGMA user_version so rows labelled 'other' after the
        upgrade are never deleted. create_all cannot add columns to an
        existing table, hence the explicit ALTER.
        """
        with self.engine.begin() as conn:
            version = int(conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)
            if version >= 1:
                return
            cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(expense)")}
            if "category_source" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE expense ADD COLUMN category_source "
                    "VARCHAR NOT NULL DEFAULT 'unknown'"
                )
            conn.exec_driver_sql("DELETE FROM expense WHERE category = 'other'")
            conn.exec_driver_sql("PRAGMA user_version = 1")

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
        if limit is not None:
            stmt = stmt.limit(int(limit))
        with Session(self.engine) as session:
            return list(session.exec(stmt).all())

    def total(
        self,
        category: Optional[str] = None,
        period: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> dict[str, float]:
        """Sum per currency in SQL — never converted, never mixed (plan Q13)."""
        canon = normalize_period(period)
        stmt = select(Expense.currency, func.sum(Expense.amount)).group_by(
            Expense.currency
        )
        if category is not None:
            stmt = stmt.where(Expense.category == coerce_category(category))
        if canon is not None:
            stmt = stmt.where(Expense.ts >= period_start(canon, now))
        with Session(self.engine) as session:
            rows = session.exec(stmt).all()
        return {currency: round(float(total), 2) for currency, total in sorted(rows)}

    def update_category(
        self,
        expense_id: int,
        category: str,
        source: Optional[str] = None,
    ) -> Optional[Expense]:
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
            if source is not None:
                row.category_source = source
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def update_categories(
        self,
        updates: Sequence[tuple[int, str]],
        source: Optional[str] = None,
    ) -> int:
        """Apply many category changes in one transaction; all or nothing."""
        if not updates:
            return 0
        with Session(self.engine) as session:
            try:
                count = 0
                for expense_id, category in updates:
                    row = session.get(Expense, expense_id)
                    if row is None:
                        continue
                    row.category = coerce_category(category)
                    if source is not None:
                        row.category_source = source
                    session.add(row)
                    count += 1
                session.commit()
                return count
            except Exception:
                session.rollback()
                raise

    def latest(self) -> Optional[Expense]:
        with Session(self.engine) as session:
            stmt = select(Expense).order_by(Expense.id.desc()).limit(1)
            return session.exec(stmt).first()

    def get(self, expense_id: int) -> Optional[Expense]:
        with Session(self.engine) as session:
            return session.get(Expense, expense_id)
