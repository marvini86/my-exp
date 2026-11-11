"""Tenant (account) context for PostgreSQL Row-Level Security.

The `tenant_isolation` policies filter rows by `app_current_account_id()`,
set via `set_config(..., is_local => true)`, i.e. valid only until
the end of the transaction. There are two ways to set the context:

- API:    `app_set_account_context(account, user)` requires the user to be a member;
- worker: `app_set_worker_context(job, worker)` requires the job to be
          processing by that worker (role myexp_worker).

So that the context survives `commit()`s on the same session, it is kept
in `session.info` and reapplied at the start of every new transaction by the
`after_begin` listener.
"""

from uuid import UUID

from sqlalchemy import Connection, TextClause, event, text
from sqlalchemy.orm import Session, SessionTransaction

_CONTEXT_KEY = "tenant_context"

_SET_MEMBER_CONTEXT = text("SELECT app_set_account_context(:account_id, :user_id)")
_SET_WORKER_CONTEXT = text("SELECT app_set_worker_context(:job_id, :worker_id)")


def set_account_context(session: Session, account_id: UUID, user_id: UUID) -> None:
    """API context: the database function revalidates the user's membership."""
    _set(session, _SET_MEMBER_CONTEXT, {"account_id": account_id, "user_id": user_id})


def set_worker_context(session: Session, job_id: UUID, worker_id: str) -> None:
    """Worker context: the account of the job this worker is processing."""
    _set(session, _SET_WORKER_CONTEXT, {"job_id": job_id, "worker_id": worker_id})


def _set(session: Session, statement: TextClause, params: dict) -> None:
    session.info[_CONTEXT_KEY] = (statement, params)

    # Transaction already open: apply now. Otherwise the listener applies it
    # when the transaction begins.
    if session.in_transaction():
        session.connection().execute(statement, params)


@event.listens_for(Session, "after_begin")
def _reapply_tenant_context(
    session: Session,
    transaction: SessionTransaction,
    connection: Connection,
) -> None:
    context = session.info.get(_CONTEXT_KEY)

    if context is not None:
        statement, params = context
        connection.execute(statement, params)
