from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Common declarative base.

    Audit timestamp fields live in mixins (Timestamped, SoftDeletable),
    because not every table has the same columns (e.g. messages only has
    created_at; only accounts has deleted_at).
    """

    # Every `Mapped[datetime]` becomes TIMESTAMPTZ, matching the migrations.
    type_annotation_map = {
        datetime: DateTime(timezone=True),
    }
