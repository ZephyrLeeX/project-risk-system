"""Agent layer-1 scope rule administration bounded context."""

from risk_platform.admin.agent_scope.api import router
from risk_platform.admin.agent_scope.service import AdminAgentScopeRulesService

__all__ = ["AdminAgentScopeRulesService", "router"]
