import type { Page } from "@playwright/test";

import { expect, json, sameOrigin, test } from "./support/fixtures";
import { settled } from "./support/pages";
import { E2E_PREFIX, USERS } from "./support/users";

// Permissions in the Knowledge Hub, from the seeded collections (seed/acme/company.yaml).
// Collections other tests or people create may appear too, so the lists are checked for what
// must and must not be there, except for the guest, whose list is exact.

interface Collection {
  id: string;
  key: string;
  name: string;
}
interface KnowledgeDocument {
  id: string;
  title: string;
  filename: string;
  status: string;
}

async function collectionCards(page: Page): Promise<string[]> {
  await page.goto("/knowledge");
  await expect(page.getByRole("heading", { level: 2, name: "Knowledge Hub" })).toBeVisible();
  await settled(page);
  return (await page.getByRole("list").getByRole("heading", { level: 3 }).allTextContents()).map((t) => t.trim());
}

async function collectionsOf(page: Page): Promise<Collection[]> {
  return (await json<{ items: Collection[] }>(await page.request.get("/api/v1/collections"))).items;
}

async function documentsOf(page: Page, collectionId: string): Promise<KnowledgeDocument[]> {
  return (await json<{ items: KnowledgeDocument[] }>(await page.request.get(`/api/v1/collections/${collectionId}/documents`))).items;
}

const BOB_ONLY = ["Engineering Docs", "Support Knowledge Base", "Contractor Onboarding"];
const HANNAH_ONLY = ["Finance Reports", "Payroll", "Leadership Strategy"];
const NEITHER = ["Employee Records", "HR Guidance (internal)"];
const EVERYONE_IN_THE_COMPANY = ["Company Handbook", "Operations SOPs", "Legal & Contracts", "Slack: #general"];

test.describe("who sees which collections and documents", () => {
  test.use({ user: USERS.bob });

  test("bob (engineering) and hannah (finance manager, leadership) see different collections", async ({ page, pageAs }) => {
    const bobs = await collectionCards(page);
    for (const name of [...EVERYONE_IN_THE_COMPANY, ...BOB_ONLY]) expect(bobs).toContain(name);
    for (const name of [...HANNAH_ONLY, ...NEITHER]) expect(bobs).not.toContain(name);

    const hannah = await pageAs(USERS.hannah);
    const hannahs = await collectionCards(hannah.page);
    for (const name of [...EVERYONE_IN_THE_COMPANY, ...HANNAH_ONLY]) expect(hannahs).toContain(name);
    for (const name of [...BOB_ONLY, ...NEITHER]) expect(hannahs).not.toContain(name);
  });

  test("the guest sees only the contractor collection, and none of the company's", async ({ pageAs }) => {
    const mallory = await pageAs(USERS.mallory);
    expect(await collectionCards(mallory.page)).toEqual(["Contractor Onboarding"]);
  });

  test("a collection or document someone may not see is 'not found', never a hint that it exists", async ({ page, guard, pageAs }) => {
    const hannah = await pageAs(USERS.hannah);
    const finance = (await collectionsOf(hannah.page)).find((c) => c.key === "finance-reports")!;
    const engineering = (await collectionsOf(page)).find((c) => c.key === "engineering-docs")!;
    const [financeDoc] = await documentsOf(hannah.page, finance.id);
    const [engineeringDoc] = await documentsOf(page, engineering.id);
    expect(financeDoc, "the seeded Finance Reports have documents").toBeDefined();
    expect(engineeringDoc, "the seeded Engineering Docs have documents").toBeDefined();

    // Each opens their own.
    await page.goto(`/knowledge/documents/${engineeringDoc.id}`);
    await expect(page.getByRole("heading", { level: 2, name: engineeringDoc.title })).toBeVisible();
    await hannah.page.goto(`/knowledge/collections/${finance.id}`);
    await expect(hannah.page.getByRole("heading", { level: 2, name: "Finance Reports" })).toBeVisible();
    await expect(hannah.page.getByRole("link", { name: financeDoc.title })).toBeVisible();

    // And neither opens the other's, even with the link. The API answers 404 for both.
    guard.allow(404, new RegExp(`/api/v1/(collections/${finance.id}|documents/${financeDoc.id})`));
    hannah.guard.allow(404, new RegExp(`/api/v1/(collections/${engineering.id}|documents/${engineeringDoc.id})`));
    await page.goto(`/knowledge/collections/${finance.id}`);
    await expect(page.getByText("Collection not found")).toBeVisible();
    await expect(page.getByText(financeDoc.title)).toHaveCount(0);
    await page.goto(`/knowledge/documents/${financeDoc.id}`);
    await expect(page.getByText("Document not found")).toBeVisible();
    await hannah.page.goto(`/knowledge/collections/${engineering.id}`);
    await expect(hannah.page.getByText("Collection not found")).toBeVisible();
    await hannah.page.goto(`/knowledge/documents/${engineeringDoc.id}`);
    await expect(hannah.page.getByText("Document not found")).toBeVisible();
  });
});

test.describe("uploading a document", () => {
  test.use({ user: USERS.hannah });

  test("hannah uploads a text file to Finance Reports, it gets indexed, bob can't open it, and she deletes it", async ({
    page,
    pageAs,
    baseURL,
  }) => {
    const finance = (await collectionsOf(page)).find((c) => c.key === "finance-reports")!;
    const stamp = `${Date.now()}-${test.info().workerIndex}`;
    const filename = `${E2E_PREFIX.toLowerCase().replace(/ /g, "-")}-${stamp}.txt`;
    const body = `${E2E_PREFIX} upload check ${stamp}.\n\nThe browser end-to-end tests create this file and delete it again. Nothing in it is true.\n`;

    try {
      await page.goto(`/knowledge/collections/${finance.id}`);
      await expect(page.getByRole("heading", { level: 2, name: "Finance Reports" })).toBeVisible();
      await expect(page.getByText("You can edit")).toBeVisible();
      await page.locator('input[type="file"]').setInputFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from(body) });

      const uploads = page.getByRole("region", { name: "Upload documents" });
      await expect(uploads.getByText(/Uploaded\. Indexing runs in the background/)).toBeVisible();

      // The table refreshes itself while a document is processing (hash embeddings in CI work too).
      const row = page.getByRole("row").filter({ hasText: filename });
      await expect(row).toBeVisible();
      await expect(row.getByText("Ready")).toBeVisible({ timeout: 60_000 });
      const [uploaded] = (await documentsOf(page, finance.id)).filter((d) => d.filename === filename);
      expect(uploaded.status).toBe("ready");

      // Its page shows its chunks, with the text that was uploaded.
      await row.getByRole("link").first().click();
      await expect(page).toHaveURL(new RegExp(`/knowledge/documents/${uploaded.id}$`));
      await expect(page.getByText(`${E2E_PREFIX} upload check ${stamp}.`).first()).toBeVisible();

      // Bob is not in Finance and not in leadership: the new document is not found for him.
      const bob = await pageAs(USERS.bob);
      bob.guard.allow(404, new RegExp(`/api/v1/documents/${uploaded.id}`));
      await bob.page.goto(`/knowledge/documents/${uploaded.id}`);
      await expect(bob.page.getByText("Document not found")).toBeVisible();

      // Delete it through its row's menu.
      await page.goto(`/knowledge/collections/${finance.id}`);
      await page.getByRole("button", { name: `Actions for ${uploaded.title}` }).click();
      await page.getByRole("menuitem", { name: "Delete…" }).click();
      const confirm = page.getByRole("alertdialog");
      await confirm.getByRole("button", { name: "Delete document" }).click();
      // While the dialog is open the page behind it is hidden from the accessibility tree.
      await expect(confirm).toBeHidden();
      await expect(page.getByRole("heading", { level: 3, name: /^Documents/ })).toBeVisible();
      await expect(page.getByRole("row").filter({ hasText: filename })).toHaveCount(0);
      expect((await documentsOf(page, finance.id)).filter((d) => d.filename === filename)).toEqual([]);
    } finally {
      // A failed run must not leave the file behind.
      for (const leftover of (await documentsOf(page, finance.id)).filter((d) => d.filename === filename)) {
        await page.request.delete(`/api/v1/documents/${leftover.id}`, { headers: sameOrigin(baseURL!) });
      }
    }
  });
});
