import { expect, test } from "@playwright/test";

/**
 * 主路径 1：登录 → 首页 → 分析详情。
 */
test("登录 → 首页 → 分析详情", async ({ page }) => {
  await page.goto("/auth/verify");
  await page.getByLabel("API 访问令牌").fill("test-token");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByTestId("masked-token")).toContainText("****");

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "市场概览" })).toBeVisible();

  await page.goto("/analysis/demo-id");
  await expect(page.getByRole("heading", { name: "分析详情" })).toBeVisible();
});

/**
 * 主路径 2：异动详情。
 */
test("异动详情", async ({ page }) => {
  await page.goto("/anomalies");
  await expect(page.getByRole("heading", { name: "异动事件" })).toBeVisible();

  await page.goto("/anomalies/demo-id");
  await expect(page.getByRole("heading", { name: "异动详情" })).toBeVisible();
});

/**
 * 主路径 3：推送偏好修改。
 */
test("推送偏好修改", async ({ page }) => {
  await page.goto("/notifications/prefs");
  await expect(page.getByRole("heading", { name: "推送偏好" })).toBeVisible();
});
