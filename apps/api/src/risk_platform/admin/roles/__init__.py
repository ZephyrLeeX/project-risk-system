"""Role and permission administration bounded context."""

from risk_platform.admin.roles.api import router
from risk_platform.admin.roles.service import AdminRolesService

__all__ = ["AdminRolesService", "router"]
