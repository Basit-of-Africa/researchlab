// End-to-end smoke test of the running application through the real UI.
// Usage: BASE_URL=http://localhost:3000 PDF=path/to/paper.pdf CSV=path/to/data.csv SHOTS=./shots node e2e/smoke.mjs
// Requires Playwright (npx playwright install chromium) and a running backend + frontend with an empty database.
import { createRequire } from "node:module";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const SHOTS = process.env.SHOTS ?? "./e2e-shots";
fs.mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && !m.text().includes("status of 4") && !m.text().includes("status of 503") && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));
const step = async (name, fn) => {
  process.stdout.write(`• ${name} … `);
  try {
    await fn();
  } catch (e) {
    await page.screenshot({ path: `${SHOTS}/FAILED_${name.replace(/\W+/g, "_")}.png` });
    throw e;
  }
  await page.screenshot({ path: `${SHOTS}/${name.replace(/\W+/g, "_")}.png`, fullPage: false });
  console.log("ok");
};

await step("first-run registration", async () => {
  await page.goto(`${BASE}/login`);
  await page.getByText("First run").waitFor();
  await page.getByLabel("Your name").fill("Test Researcher");
  await page.getByLabel("Email").fill("researcher@example.org");
  await page.getByLabel("Password").fill("correct horse battery staple");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByText("Research dashboard").waitFor();
});

await step("create project", async () => {
  await page.goto(`${BASE}/projects?new=1`);
  const dlg = page.getByRole("dialog");
  await dlg.getByLabel("Title").fill("Digital trust in African SMEs");
  await dlg.getByLabel("Discipline").fill("Information systems");
  await dlg.getByRole("button", { name: "Create" }).click();
  await page.getByRole("heading", { name: "Digital trust in African SMEs" }).waitFor();
  await page.getByRole("tab", { name: /Research questions/ }).click();
  await page.getByPlaceholder("Add a research question").fill("How do institutional voids shape digital trust adoption among SMEs?");
  await page.getByRole("button", { name: "Add" }).click();
  await page.getByText("proposed").first().waitFor();
  await page.getByRole("button", { name: "Approve" }).first().click();
  await page.getByText("approved", { exact: true }).first().waitFor();
});

await step("upload PDF and process", async () => {
  await page.goto(`${BASE}/knowledge?upload=1`);
  const dlg = page.getByRole("dialog");
  await dlg.locator('input[type="file"]').setInputFiles(process.env.PDF);
  await dlg.getByRole("button", { name: "Add", exact: true }).click();
  await dlg.getByText("succeeded").waitFor({ timeout: 60000 });
  await dlg.getByRole("button", { name: "Close", exact: true }).last().click();
  await page.getByText("Institutional Capacity and Digital Trust").first().waitFor();
});

await step("document detail", async () => {
  await page.getByText("Institutional Capacity and Digital Trust").first().click();
  await page.getByText("Bibliographic metadata").waitFor();
  await page.getByText("source: extracted").first().waitFor();
});

await step("hybrid search", async () => {
  await page.goto(`${BASE}/knowledge`);
  await page.getByRole("tab", { name: "Search", exact: true }).click();
  await page.getByPlaceholder("Search full text").fill("partial least squares structural equation modelling");
  await page.locator("form").getByRole("button", { name: "Search" }).click();
  await page.getByText(/§ .*2\. Methodology/).first().waitFor();
});

await step("literature discovery (live OpenAlex)", async () => {
  await page.goto(`${BASE}/literature`);
  await page.getByPlaceholder("Topic, keywords or title").fill("institutional voids entrepreneurship Africa");
  await page.locator("form").getByRole("button", { name: "Search" }).click();
  await page.getByText(/OpenAlex matches/).waitFor({ timeout: 45000 });
});

let manuscriptUrl;
await step("create 7000-word manuscript", async () => {
  await page.goto(`${BASE}/manuscripts?new=1`);
  const dlg = page.getByRole("dialog");
  await dlg.getByLabel("Project").selectOption({ label: "Digital trust in African SMEs" });
  await dlg.getByLabel("Working title").fill("Institutional capacity and digital trust adoption");
  await dlg.getByText("7,000 / 7,000").waitFor();
  await dlg.getByRole("button", { name: "Create manuscript" }).click();
  await page.getByText("Literature Review and Theoretical Framework").first().waitFor();
  manuscriptUrl = page.url();
});

await step("edit section and preview", async () => {
  await page.locator("textarea").first().fill("Digital trust infrastructure has become central to enterprise development [@missing2020].\n\nThis is a second paragraph.");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("unsaved").waitFor({ state: "detached" });
  await page.getByRole("button", { name: "Preview" }).click();
  await page.locator("span", { hasText: "[@missing2020]" }).waitFor();
});

await step("export DOCX", async () => {
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "DOCX" }).click()]);
  const path = `${SHOTS}/manuscript.docx`;
  await dl.saveAs(path);
  if (fs.statSync(path).size < 5000) throw new Error("DOCX too small");
});

await step("data analysis", async () => {
  await page.goto(`${BASE}/analysis`);
  await page.locator('input[type="file"]').setInputFiles(process.env.CSV);
  await page.getByText("120 rows × 3 variables").waitFor();
  await page.getByRole("button", { name: "Run analysis" }).click();
  await page.getByText(/Descriptive statistics for 2 numeric/).waitFor({ timeout: 60000 });
  await page.getByRole("button", { name: "Reproduce" }).click();
  await page.getByText(/produced identical results/).waitFor({ timeout: 60000 });
});

await step("AI unavailable is reported clearly", async () => {
  await page.goto(`${BASE}/workspace`);
  await page.getByPlaceholder("Ask, explain your thinking").fill("Which theories explain trust in mobile money?");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByText(/No AI provider could complete the request/).waitFor({ timeout: 30000 });
});

await step("settings and dashboard", async () => {
  await page.goto(`${BASE}/settings`);
  await page.getByText("Data policy for unpublished material").waitFor();
  await page.goto(`${BASE}/`);
  await page.getByText("Processing status").waitFor();
});

await browser.close();
if (consoleErrors.length) {
  console.error("Console errors:\n" + consoleErrors.join("\n"));
  process.exit(1);
}
console.log("All smoke steps passed.");
