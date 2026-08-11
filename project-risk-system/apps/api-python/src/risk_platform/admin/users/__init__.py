"""Admin user-management bounded context."""

from risk_platform.admin.users.api import router
from risk_platform.admin.users.service import AdminUsersService

__all__ = ["AdminUsersService", "router"]
