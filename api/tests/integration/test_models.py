"""Ensures the models match the schema created by the SQL migrations."""

import pytest
from sqlalchemy import inspect

from models import (
    Account,
    AccountMember,
    AccountRole,
    Category,
    Document,
    DocumentItem,
    FinancialEntry,
    IngestionJob,
    User,
)
from repositories.account_member import AccountMemberRepository

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "model",
    [User, Account, AccountMember, Category, Document, IngestionJob, FinancialEntry, DocumentItem],
)
def test_model_columns_exist_in_database(session, model):
    table = model.__table__
    db_columns = {
        column["name"]
        for column in inspect(session.connection()).get_columns(table.name)
    }

    missing = {column.name for column in table.columns} - db_columns

    assert not missing, f"{table.name}: colunas ausentes no banco: {missing}"


def test_account_member_roundtrip(session):
    user = User(name="Ana", email="ana@example.com")
    account = Account(name="Família", slug="familia")
    session.add_all([user, account])
    session.flush()

    repository = AccountMemberRepository(session)
    repository.add(AccountMember(account_id=account.id, user_id=user.id, role=AccountRole.OWNER))
    session.expire_all()

    member = repository.get(account.id, user.id)

    assert member is not None
    assert member.role is AccountRole.OWNER
    assert member.user.email == "ana@example.com"
    assert [m.user_id for m in repository.list_by_account(account.id)] == [user.id]
    assert member.created_at.tzinfo is not None


def test_account_defaults_from_database(session):
    account = Account(name="Conta", slug="conta-defaults")
    session.add(account)
    session.flush()

    assert account.active is True
    assert account.activated_at is not None
    assert account.deactivated_at is None
    assert account.deleted_at is None
