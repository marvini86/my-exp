from datetime import datetime

from sqlalchemy import Boolean, func, text
from sqlalchemy.orm import Mapped, mapped_column


class Activatable:

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("TRUE"),
    )

    activated_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        server_default=func.now(),
    )

    deactivated_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )
