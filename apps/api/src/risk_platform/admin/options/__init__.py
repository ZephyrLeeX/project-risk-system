"""Administration option queries."""

from risk_platform.admin.options.api import router
from risk_platform.admin.options.service import AdminOptionsService

__all__ = ["AdminOptionsService", "router"]
