"""Who can sign in to the platform and who can invite.

- Admin (AUTH_ADMIN_EMAILS): signs in and invites.
- Allowed by the admin (platform_access table): signs in and invites.
- Invited to an account (by someone above): signs in, but CANNOT invite anyone.
  This way access does not spread in a chain without the admin knowing.
"""

from core.config import AuthSettings
from repositories.platform_access import PlatformAccessRepository


class AccessPolicy:
    def __init__(self, settings: AuthSettings, repository: PlatformAccessRepository) -> None:
        self.settings = settings
        self.repository = repository

    def can_invite(self, email: str | None) -> bool:
        return bool(email) and (self.settings.is_admin(email) or self.repository.is_listed(email))

    def has_access(self, email: str | None) -> bool:
        return self.can_invite(email) or (bool(email) and self.repository.is_member_somewhere(email))
