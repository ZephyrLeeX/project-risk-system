import { expect, test } from "@playwright/test";

const username = process.env.E2E_USERNAME;
const password = process.env.E2E_PASSWORD;
const adminUsername = process.env.E2E_ADMIN_USERNAME;
const adminPassword = process.env.E2E_ADMIN_PASSWORD;

test("browser harness reaches the real Compose edge and exposes the V2 entry points", async ({ page }) => {
  await page.goto("/login");
  await expect(page).toHaveURL(/login/);
  await expect(page.getByRole("heading", { name: /登录平台|Login/ })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Company API");
  await expect(page.locator("body")).not.toContainText("自定义 DeepSeek endpoint");
});

test("authenticated V2 journey", async ({ page }) => {
  test.skip(!username || !password, "BLOCKED_EXTERNAL_INPUTS: E2E_USERNAME/E2E_PASSWORD not supplied");
  await page.goto("/login");
  await page.getByRole("textbox", { name: "账号" }).fill(username!);
  await page.getByRole("textbox", { name: "密码" }).fill(password!);
  await page.getByRole("button", { name: /登\s*录/ }).click();
  await expect(page).toHaveURL("/");
  await page.getByRole("button", { name: /Agent/ }).click();
  await expect(page.getByLabel("Agent智能对话")).toBeVisible();
  await expect(page.getByText("仅使用您有权访问的数据")).toBeVisible();
});

test("Admin V2 browser cutover", async ({ page }) => {
  test.skip(!adminUsername || !adminPassword, "BLOCKED_EXTERNAL_INPUTS: E2E_ADMIN_USERNAME/E2E_ADMIN_PASSWORD not supplied");
  await page.goto("/login");
  await page.getByRole("textbox", { name: "账号" }).fill(adminUsername!);
  await page.getByRole("textbox", { name: "密码" }).fill(adminPassword!);
  await page.getByRole("button", { name: /登\s*录/ }).click();
  await page.goto("/admin/api-keys");
  await expect(page.getByText("Provider Account & Model Config")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Company API");
  await expect(page.locator("body")).not.toContainText("自定义 DeepSeek endpoint");
});
