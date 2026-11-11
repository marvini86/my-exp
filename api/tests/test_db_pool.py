from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

import database.pool as pool_module
from database.pool import ping_when_idle


def make_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}", poolclass=QueuePool, pool_size=1, max_overflow=0)
    ping_when_idle(engine, idle_seconds=30)
    return engine


def raw_connection_id(engine):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        return id(connection.connection.dbapi_connection)


def test_recently_used_connection_is_reused_without_ping(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(pool_module.time, "monotonic", lambda: clock[0])
    engine = make_engine(tmp_path)

    first = raw_connection_id(engine)
    clock[0] += 5  # 5 s later: no check, same connection
    second = raw_connection_id(engine)

    assert first == second


def test_dead_idle_connection_is_replaced(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(pool_module.time, "monotonic", lambda: clock[0])
    engine = make_engine(tmp_path)

    with engine.connect() as connection:
        dead = connection.connection.dbapi_connection
    dead.close()  # simulates the network/server dropping the idle connection
    clock[0] += 60

    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1
        assert connection.connection.dbapi_connection is not dead
