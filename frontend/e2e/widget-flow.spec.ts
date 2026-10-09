/**
 * Phase 5's goal, in a real browser: upload a file in the dashboard, then chat with it
 * from another website through the widget. Needs the running stack: `make up` with the Groq
 * key (one question), or the stack with the fake LLM (CI does that).
 */
import { expect, test } from "@playwright/test";

const WEBSITE = "http://localhost:5500"; // widget/demo.html, served by playwright.config.ts
const API = process.env.PUBLIC_API_URL ?? "http://localhost:8000";

const RULES = [
  "Library rules. Books can be borrowed for two weeks. A late fee of 5 rupees per day is",
  "charged for each book returned after the due date.",
  "",
  "Hostel rules. The hostel gates close at 10 pm every night.",
].join("\n");

test("a file uploaded in the dashboard answers questions in the widget on another website", async ({
  page,
}) => {
  await test.step("sign up", async () => {
    await page.goto("/signup");
    await page.getByLabel("Company name").fill("E2E College");
    await page.getByLabel("Email").fill(`e2e-${Date.now()}@example.com`);
    await page.getByLabel("Password").fill("e2e-password-123");
    await page.getByRole("button", { name: "Sign up" }).click();
    await expect(page).toHaveURL(/\/documents$/);
  });

  await test.step("upload a file and wait until it is ready", async () => {
    await page.getByLabel("Choose files to upload").setInputFiles({
      name: "college-rules.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(RULES),
    });
    const row = page.getByRole("row", { name: /college-rules\.txt/ });
    await expect(row.getByText("ready", { exact: true })).toBeVisible({ timeout: 60_000 });
  });

  let key = "";
  await test.step("create a public key for the website", async () => {
    await page.getByRole("link", { name: "API keys" }).click();
    await page.getByLabel("Name", { exact: true }).fill("Website chat");
    await page.getByLabel("Public (chat widget)").check();
    await page.getByLabel("Allowed websites").fill(WEBSITE);
    await page.getByRole("button", { name: "Create key" }).click();
    key = (await page.getByLabel("Public key").textContent())?.trim() ?? "";
    expect(key).toMatch(/^rf_pub_/);
    await expect(page.getByLabel("Embed code")).toContainText(`data-api-key="${key}"`);
  });

  await test.step("ask the widget on the website", async () => {
    const query = new URLSearchParams({ key, api: API });
    await page.goto(`${WEBSITE}/demo.html?${query}`);
    // Locators look inside the widget's (open) Shadow DOM.
    await page.getByRole("button", { name: "Open chat" }).click();
    await page.getByLabel("Your question").fill("What is the late fee for library books?");
    await page.getByLabel("Your question").press("Enter");

    const answer = page.locator("#ragforge-widget .bot .text").last();
    await expect(answer).toContainText(/5 rupees|₹\s?5/i, { timeout: 60_000 });
    const source = page.locator("#ragforge-widget .sources li").first();
    await expect(source).toContainText("college-rules.txt");
  });
});
