"""ASGI entry point."""

from risk_platform.app import create_app

app = create_app()

__all__ = ["app"]
