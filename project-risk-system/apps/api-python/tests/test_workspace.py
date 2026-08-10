from importlib.metadata import version

import risk_platform


def test_workspace_package_is_importable() -> None:
  assert risk_platform.__all__ == ()


def test_approved_runtime_stack_is_installed() -> None:
  for distribution in (
    "alembic",
    "celery",
    "fastapi",
    "psycopg",
    "pydantic",
    "redis",
    "sqlalchemy",
  ):
    assert version(distribution)
