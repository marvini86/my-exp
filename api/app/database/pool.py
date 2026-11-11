"""Connection check only when the connection has been idle.

`pool_pre_ping=True` runs a SELECT before EVERY use: with a distant database
(~145 ms per round trip) that weighs on every request. A connection used a
few seconds ago is alive; only an idle one (the network/server may have
dropped it) is checked. If the check fails, the pool discards it and opens another.
"""

import time

from sqlalchemy import Engine, event, exc

IDLE_PING_AFTER_SECONDS = 30.0
_LAST_USED = "last_used_at"


def ping_when_idle(engine: Engine, idle_seconds: float = IDLE_PING_AFTER_SECONDS) -> None:
    @event.listens_for(engine, "checkin")
    def _remember_last_use(dbapi_connection, connection_record) -> None:
        connection_record.info[_LAST_USED] = time.monotonic()

    @event.listens_for(engine, "checkout")
    def _ping_if_idle(dbapi_connection, connection_record, connection_proxy) -> None:
        last_used = connection_record.info.get(_LAST_USED)
        if last_used is None or time.monotonic() - last_used < idle_seconds:
            return  # freshly opened or recently used
        try:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SELECT 1")
            finally:
                cursor.close()
            dbapi_connection.rollback()
        except Exception as error:
            # The pool handles DisconnectionError: it discards this connection and tries another.
            raise exc.DisconnectionError("Idle connection is dead") from error
