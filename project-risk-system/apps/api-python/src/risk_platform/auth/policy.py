"""Password and forced-change policies for authentication."""

from __future__ import annotations

import re


def password_policy_violations(
    password: str,
    *,
    minimum_length: int,
    username: str | None = None,
) -> tuple[str, ...]:
    violations: list[str] = []
    if len(password) < minimum_length:
        violations.append(f"密码长度至少为 {minimum_length} 位")
    if re.search(r"[a-z]", password) is None:
        violations.append("密码需包含小写字母")
    if re.search(r"[A-Z]", password) is None:
        violations.append("密码需包含大写字母")
    if re.search(r"\d", password) is None:
        violations.append("密码需包含数字")
    if re.search(r"[^A-Za-z0-9]", password) is None:
        violations.append("密码需包含特殊字符")
    if username and username.casefold() in password.casefold():
        violations.append("密码不能包含登录账号")
    return tuple(violations)


__all__ = ["password_policy_violations"]
