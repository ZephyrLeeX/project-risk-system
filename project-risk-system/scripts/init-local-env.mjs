import { randomBytes } from "node:crypto";
import {
  constants,
  copyFileSync,
  existsSync,
  readFileSync,
  chmodSync,
  writeFileSync,
} from "node:fs";
import { resolve } from "node:path";

const projectRoot = resolve(import.meta.dirname, "..");
const examplePath = resolve(projectRoot, ".env.example");
const environmentPath = resolve(projectRoot, ".env");

if (!existsSync(examplePath)) {
  throw new Error(`环境模板不存在：${examplePath}`);
}

if (existsSync(environmentPath)) {
  console.log("本地 .env 已存在，未覆盖任何配置。");
  process.exit(0);
}

copyFileSync(
  examplePath,
  environmentPath,
  constants.COPYFILE_EXCL,
);

const base64UrlSecret = (bytes) => randomBytes(bytes).toString("base64url");
const databasePassword = base64UrlSecret(24);
const sessionSecret = base64UrlSecret(48);
const encryptionKey = randomBytes(32).toString("base64");
const initialAdminPassword = `Risk!${base64UrlSecret(18)}aA1`;

const replacements = new Map([
  ["POSTGRES_PASSWORD", databasePassword],
  [
    "DATABASE_URL",
    `postgresql://project_risk:${databasePassword}@localhost:5432/project_risk?schema=public`,
  ],
  ["SESSION_SECRET", sessionSecret],
  ["DATA_ENCRYPTION_KEY", encryptionKey],
  ["INITIAL_ADMIN_PASSWORD", initialAdminPassword],
]);

const source = readFileSync(environmentPath, "utf8");
const output = source
  .split(/\r?\n/)
  .map((line) => {
    const separatorIndex = line.indexOf("=");
    if (separatorIndex < 0) return line;
    const key = line.slice(0, separatorIndex);
    const replacement = replacements.get(key);
    return replacement === undefined ? line : `${key}=${replacement}`;
  })
  .join("\n");

writeFileSync(environmentPath, output, {
  encoding: "utf8",
  mode: 0o600,
});
chmodSync(environmentPath, 0o600);

console.log(
  "本地 .env 已创建，敏感值已随机生成且未回显；文件权限已设置为 600。",
);
