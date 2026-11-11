from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column


class SoftDeletable:
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
