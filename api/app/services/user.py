from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.exceptions import ConflictError, NotFoundError
from models.user import User
from repositories.user import UserRepository
from schemas.user import CreateUserRequest, UserResponse


class UserService:
    def __init__(self, session: Session, user_repository: UserRepository):
        self.session = session
        self.user_repository = user_repository

    def get_user(self, user_id: UUID, viewer: User) -> UserResponse:
        """Visible only to the user themselves or to someone sharing an account."""
        user = self.user_repository.get_visible(user_id, viewer.id)

        if user is None:
            raise NotFoundError("User not found")

        return UserResponse.model_validate(user)

    def list_users(self, viewer: User) -> list[UserResponse]:
        return [
            UserResponse.model_validate(user)
            for user in self.user_repository.list_visible(viewer.id)
        ]

    def create_user(self, request: CreateUserRequest) -> UserResponse:
        email = request.email.lower()

        if self.user_repository.get_by_email(email) is not None:
            raise ConflictError("Email already registered")

        user = User(name=request.name, email=email)

        try:
            self.user_repository.add(user)
            self.session.commit()
        except IntegrityError as exc:
            # Race between the check above and the INSERT (unique index).
            self.session.rollback()
            raise ConflictError("Email already registered") from exc

        return UserResponse.model_validate(user)
