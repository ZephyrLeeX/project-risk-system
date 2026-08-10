export interface PasswordPolicyContext {
  minLength: number;
  username?: string;
}

export function getPasswordPolicyViolations(
  password: string,
  context: PasswordPolicyContext,
): string[] {
  const violations: string[] = [];

  if (password.length < context.minLength) {
    violations.push(`密码长度至少为 ${context.minLength} 位`);
  }
  if (!/[a-z]/.test(password)) {
    violations.push("密码需包含小写字母");
  }
  if (!/[A-Z]/.test(password)) {
    violations.push("密码需包含大写字母");
  }
  if (!/\d/.test(password)) {
    violations.push("密码需包含数字");
  }
  if (!/[^A-Za-z0-9]/.test(password)) {
    violations.push("密码需包含特殊字符");
  }
  if (
    context.username &&
    password.toLocaleLowerCase().includes(context.username.toLocaleLowerCase())
  ) {
    violations.push("密码不能包含登录账号");
  }

  return violations;
}
