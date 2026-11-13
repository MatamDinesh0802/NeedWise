import { promises as fs } from "node:fs";
import path from "node:path";

import { expect, test, type Browser, type Locator, type Page } from "@playwright/test";

import { caption, card, clearOverlay, installOverlay } from "./overlay";

/**
 * The 2-minute demo: a captioned walkthrough recorded against a running, seeded stack that has a
 * chat model (README.md in this folder). It writes out/raw.webm and out/marks.json; render.mjs
 * turns them into docs/media/demo.mp4 and docs/media/demo.gif.
 *
 * Every conversation it starts, the agent it creates and the email it sends are deleted at the
 * end, even when a step fails, and an approval left pending is rejected. What cannot be deleted
 * is listed in README.md.
 */

const OUT = path.resolve(__dirname, "out");
const PASSWORD = process.env.DEMO_PASSWORD ?? "needwise-demo";
const MAILPIT = process.env.DEMO_MAILPIT_URL ?? "http://localhost:8025";
const MODEL = 4 * 60_000; // the longest model step (research, then an approval) gets this long
const TYPING = 28; // ms per character: readable, not slow
// While working on the script: DEMO_STOP_AFTER=4 records scenes 1 to 4 only (cleanup still runs).
const STOP_AFTER = Number(process.env.DEMO_STOP_AFTER ?? 8);

const QUESTIONS = {
  salary: "What is the salary band for an L3 engineer?",
  crossDepartment:
    "For my on-call week: how do I restart the fleet controller, what P1 response time do customers get, and how close may I get to a moving AMR-500?",
  report: "Prepare this week's operations report and email it to leadership@acme.test.",
  insights: "Monthly revenue trend this year and the top 3 drivers",
};

const AGENT = {
  name: "Contractor onboarding agent",
  description:
    "Onboarding for external contractors: the NDA, the first week, VPN access and accounts, and who to ask. Not for employees' HR questions.",
  collection: "Contractor Onboarding",
  example: "When do contractors get repository access?",
};

/** Points where the playback speed changes, in seconds from the start of the video. */
interface Mark {
  t: number;
  speed: number;
  label: string;
}

class Timeline {
  readonly marks: Mark[] = [];
  private readonly t0 = Date.now();
  private speed = 1;

  /** From now on, play at `speed` (unchanged when omitted). */
  mark(label: string, speed?: number): void {
    if (speed !== undefined) this.speed = speed;
    this.marks.push({ t: (Date.now() - this.t0) / 1000, speed: this.speed, label });
  }
}

/** What the recording made, to delete afterwards. */
class Created {
  readonly conversations = new Map<string, string[]>();
  agentKey: string | null = null;

  conversation(email: string, page: Page): void {
    const id = page.url().split("/").pop()!;
    this.conversations.set(email, [...(this.conversations.get(email) ?? []), id]);
  }
}

function sameOrigin(baseURL: string): Record<string, string> {
  return { Origin: new URL(baseURL).origin, "X-CSRF-Protection": "1" };
}

async function typeSlowly(target: Locator, text: string): Promise<void> {
  await target.click();
  await target.pressSequentially(text, { delay: TYPING });
}

const accountMenu = (page: Page) => page.getByRole("button", { name: /^Account menu for / });

/** The header's Sign in link, Keycloak's email page, then its password page. */
async function signIn(page: Page, email: string): Promise<void> {
  await page.getByRole("link", { name: "Sign in" }).first().click();
  await typeSlowly(page.getByLabel("Email"), email);
  await page.getByRole("button", { name: "Sign In" }).click();
  await typeSlowly(page.getByLabel("Password", { exact: true }), PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(accountMenu(page)).toBeVisible();
}

/** The user menu's View as… (demo stacks): Keycloak asks only for the password. */
async function viewAs(page: Page, name: string): Promise<void> {
  // From a page scrolled down, opening the View as… submenu scrolls the page, which closes the
  // submenu again (seen on Insights): start from the top.
  await page.evaluate(() => window.scrollTo({ top: 0 }));
  await page.waitForTimeout(400);
  for (let attempt = 1; ; attempt++) {
    try {
      await accountMenu(page).click();
      await page.getByRole("menuitem", { name: /View as/ }).click();
      await page.getByRole("menuitem", { name: new RegExp(`^${name}`) }).click({ timeout: 5000 });
      break;
    } catch (error) {
      if (attempt === 3) throw error;
      await page.keyboard.press("Escape");
      await page.keyboard.press("Escape");
      await page.waitForTimeout(500);
    }
  }
  await typeSlowly(page.getByLabel("Password", { exact: true }), PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(accountMenu(page)).toBeVisible();
}

/** Asks in the chat and waits until the answer has finished streaming. */
async function ask(page: Page, question: string, created: Created, email: string, onStreaming?: () => void): Promise<void> {
  await typeSlowly(page.getByRole("textbox", { name: "Ask a question" }), question);
  await page.getByRole("button", { name: "Send" }).click();
  await page.waitForURL(/\/chat\/[0-9a-f-]{36}$/);
  created.conversation(email, page);
  const stop = page.getByRole("button", { name: "Stop answering" });
  await expect(stop).toBeVisible();
  onStreaming?.();
  await expect(stop).toBeHidden({ timeout: MODEL });
}

test("record the demo walkthrough", async ({ browser, baseURL }) => {
  test.setTimeout(20 * 60_000);
  await fs.mkdir(OUT, { recursive: true });
  const context = await browser.newContext({
    baseURL,
    viewport: { width: 1280, height: 720 },
    recordVideo: { dir: OUT, size: { width: 1280, height: 720 } },
    colorScheme: "light",
    locale: "en-US",
    timezoneId: "UTC",
  });
  await context.addInitScript(installOverlay);
  const page = await context.newPage();
  const timeline = new Timeline();
  const created = new Created();

  try {
    // Title card over the signed-out start page.
    await page.goto("/");
    await card(page, "NeedWise", [
      "Answers from your company's knowledge, with citations,",
      "drawn only from what each person is allowed to read.",
      "Open source · self-hosted · fictional demo company: Acme Robotics",
    ]);
    timeline.mark("title", 1);
    await page.waitForTimeout(4000);
    await clearOverlay(page);

    // 1. Sign in.
    await caption(page, "1/8", "Alice, an HR manager, signs in with the company's single sign-on (Keycloak).");
    timeline.mark("alice-sign-in", 1);
    await page.waitForTimeout(1500);
    timeline.mark("alice-keycloak", 2);
    await signIn(page, "alice@acme.test");
    await page.getByRole("link", { name: "Assistant" }).click();
    await expect(page.getByRole("textbox", { name: "Ask a question" })).toBeVisible();

    if (STOP_AFTER < 2) return;
    // 2. A cited answer, and its source.
    await caption(page, "2/8", "She asks about salary bands. Every claim cites a source she is allowed to read.");
    timeline.mark("gif-start", 1);
    await page.waitForTimeout(1200);
    await ask(page, QUESTIONS.salary, created, "alice@acme.test");
    await page.waitForTimeout(1500);
    await page.getByRole("button", { name: /^Source \d+: / }).first().click();
    await expect(page.getByRole("complementary", { name: "Sources" })).toBeVisible();
    await caption(page, "2/8", "A citation opens its source beside the answer: Salary Bands 2026, a restricted document.");
    await page.waitForTimeout(4500);

    if (STOP_AFTER < 3) return;
    // 3. The same question, without access.
    await caption(page, "3/8", "Bob, an engineer, asks the same question. (View as… switches demo accounts.)");
    timeline.mark("view-as-bob", 2);
    await viewAs(page, "Bob");
    timeline.mark("bob-ask", 1);
    await page.waitForTimeout(800);
    await ask(page, QUESTIONS.salary, created, "bob@acme.test");
    await caption(page, "3/8", "Bob may not read salary data, so the model never sees it: no answer, and no hint that it exists.");
    await page.waitForTimeout(5000);
    timeline.mark("gif-end");

    if (STOP_AFTER < 4) return;
    // 4. Department agents.
    await page.getByRole("link", { name: "New chat" }).click();
    await caption(page, "4/8", "A question across three departments: Engineering, Support and Operations agents search at once.");
    await page.waitForTimeout(1000);
    await ask(page, QUESTIONS.crossDepartment, created, "bob@acme.test", () => timeline.mark("agents-working", 3));
    timeline.mark("agents-done", 1);
    await caption(page, "4/8", "Each agent searches only what Bob may read; the answer cites every part.");
    await page.waitForTimeout(2000);
    await page.mouse.move(900, 400);
    await page.mouse.wheel(0, 420);
    await page.waitForTimeout(3000);

    if (STOP_AFTER < 5) return;
    // 5. An action that needs approval.
    await caption(page, "5/8", "Olivia, the head of operations, signs in.");
    timeline.mark("olivia-sign-in", 2.5);
    await accountMenu(page).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page.getByRole("link", { name: "Sign in" }).first()).toBeVisible();
    await signIn(page, "olivia.grant@acme.test");
    await page.getByRole("link", { name: "Assistant" }).click();
    await page.getByRole("group", { name: "Assistant mode" }).getByRole("button", { name: "Research & act" }).click();
    await caption(page, "5/8", "Research & act: the assistant may search, draft and propose actions.");
    timeline.mark("research-ask", 1);
    await ask(page, QUESTIONS.report, created, "olivia.grant@acme.test", () => timeline.mark("research-working", 8));
    const approval = page.getByRole("article", { name: /^Send an email/ }).last();
    await expect(approval).toBeVisible({ timeout: MODEL });
    timeline.mark("approval", 1);
    await approval.scrollIntoViewIfNeeded();
    await caption(page, "5/8", "Nothing is sent without approval. Olivia edits the draft and adds her sign-off.");
    await page.waitForTimeout(2500);
    await approval.getByRole("button", { name: "Edit & approve" }).click();
    const body = approval.getByLabel("Body");
    await body.click();
    await body.evaluate((el: HTMLTextAreaElement) => {
      el.setSelectionRange(el.value.length, el.value.length);
      el.scrollTop = el.scrollHeight;
    });
    await page.keyboard.type("\n\nThanks,\nOlivia", { delay: 60 });
    await page.waitForTimeout(1200);
    await approval.getByRole("button", { name: "Approve with my changes" }).click();
    await expect(approval.getByRole("status").filter({ hasText: "Approved with changes." })).toBeVisible({ timeout: 60_000 });
    await caption(page, "5/8", "Approved with her changes: sent once, as Olivia, and recorded in the audit log.");
    await page.waitForTimeout(4000);

    if (STOP_AFTER < 6) return;
    // 6. Meetings.
    await caption(page, "6/8", "Meetings: decisions and action items link to the moment they were said.");
    timeline.mark("meeting", 1);
    await page.getByRole("link", { name: "Meetings" }).click();
    await page.getByRole("region", { name: "Recent meetings" }).getByRole("link").first().click();
    const decisions = page.getByRole("region", { name: /^Decisions/ });
    await expect(decisions).toBeVisible();
    await page.waitForTimeout(2000);
    const moments = decisions.getByRole("button", { name: /^(Play the recording from|Show) / });
    await moments.nth(0).click();
    await page.waitForTimeout(3500);
    await moments.nth(1).click();
    await page.waitForTimeout(3500);

    if (STOP_AFTER < 7) return;
    // 7. Insights.
    await page.getByRole("link", { name: "Insights" }).click();
    await caption(page, "7/8", "Insights: a question about company data becomes a chart, a short explanation and the SQL behind it.");
    timeline.mark("insights-ask", 1);
    await typeSlowly(page.getByLabel("Ask a question about the data"), QUESTIONS.insights);
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    timeline.mark("insights-working", 4);
    const chart = page.locator('[aria-roledescription="chart"]').first();
    await expect(chart).toBeVisible({ timeout: MODEL });
    timeline.mark("insights-done", 1);
    await page.waitForTimeout(3500);
    // The SQL of the query that answers the question is open from the start.
    const sql = page.locator("pre").filter({ hasText: /\bSELECT\b/i }).first();
    await sql.evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
    await page.waitForTimeout(800);
    await sql.hover();
    await caption(page, "7/8", "Every number in the text is checked against the query results. The SQL runs with Olivia's access.");
    await page.waitForTimeout(4500);

    if (STOP_AFTER < 8) return;
    // 8. An administrator adds an agent.
    await caption(page, "8/8", "Erin, the IT administrator, adds a department agent in the UI, without code.");
    timeline.mark("view-as-erin", 2);
    await viewAs(page, "Erin");
    await page.goto("/admin/agents");
    timeline.mark("agent", 1);
    await page.getByRole("button", { name: "New agent" }).click();
    const dialog = page.getByRole("dialog", { name: "New agent" });
    await typeSlowly(dialog.getByLabel("Name", { exact: true }), AGENT.name);
    created.agentKey = await dialog.getByLabel("Key", { exact: true }).inputValue();
    await typeSlowly(dialog.getByLabel("Description", { exact: true }), AGENT.description);
    await dialog.getByText(AGENT.collection, { exact: true }).click();
    const example = dialog.getByRole("textbox", { name: "Example question 1" });
    await example.scrollIntoViewIfNeeded();
    await typeSlowly(example, AGENT.example);
    await page.waitForTimeout(800);
    await dialog.getByRole("button", { name: "Create agent" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("button", { name: `Edit ${AGENT.name}` })).toBeVisible();
    await page.getByRole("button", { name: `Edit ${AGENT.name}` }).scrollIntoViewIfNeeded();
    await caption(page, "8/8", "Questions route to it from now on. Every change is versioned and can be restored.");
    await page.waitForTimeout(4000);

    await card(page, "NeedWise", [
      "Permission-aware, cited, measured. Apache-2.0, with an Enterprise edition.",
      "Try it: git clone … && make quickstart",
    ]);
    timeline.mark("end-card", 1);
    await page.waitForTimeout(4500);
    timeline.mark("end");
  } finally {
    if (timeline.marks.at(-1)?.label !== "end") timeline.mark("end");
    await context.close();
    await page.video()?.saveAs(path.join(OUT, "raw.webm"));
    await page.video()?.delete();
    await fs.writeFile(path.join(OUT, "marks.json"), `${JSON.stringify(timeline.marks, null, 2)}\n`);
    await cleanUp(browser, baseURL!, created);
  }
});

/** Signs `email` in, quietly, in a context of its own (not recorded). */
async function signedIn(browser: Browser, baseURL: string, email: string): Promise<Page> {
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  await page.goto("/");
  await page.getByRole("link", { name: "Sign in" }).first().click();
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Sign In" }).click();
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(accountMenu(page)).toBeVisible();
  return page;
}

function findMessageIds(value: unknown, into: string[] = []): string[] {
  if (value && typeof value === "object") {
    for (const [key, inner] of Object.entries(value)) {
      if (key === "message_id" && typeof inner === "string") into.push(inner);
      else findMessageIds(inner, into);
    }
  }
  return into;
}

/** Deletes what the recording created: conversations, the agent, the email in Mailpit. */
async function cleanUp(browser: Browser, baseURL: string, created: Created): Promise<void> {
  const headers = sameOrigin(baseURL);
  const messageIds: string[] = [];
  for (const [email, ids] of created.conversations) {
    const page = await signedIn(browser, baseURL, email);
    for (const id of ids) {
      const approvals = await page.request.get(`/api/v1/approvals?status=all&conversation_id=${id}`);
      if (approvals.ok()) {
        const body = (await approvals.json()) as unknown;
        findMessageIds(body, messageIds);
        // A recording stopped before its approval step must not leave an email waiting to be sent.
        const items = (Array.isArray(body) ? body : ((body as { items?: unknown[] }).items ?? [])) as { id: string; status: string }[];
        for (const pending of items.filter((a) => a.status === "pending")) {
          const rejected = await page.request.post(`/api/v1/approvals/${pending.id}/decision`, {
            headers,
            data: { decision: "reject", note: "Demo recording stopped before this step." },
          });
          console.log(`rejected approval ${pending.id}: HTTP ${rejected.status()}`);
        }
      }
      const deleted = await page.request.delete(`/api/v1/conversations/${id}`, { headers });
      console.log(`deleted conversation ${id} of ${email}: HTTP ${deleted.status()}`);
    }
    await page.context().close();
  }
  if (created.agentKey) {
    const page = await signedIn(browser, baseURL, "erin@acme.test");
    const deleted = await page.request.delete(`/api/v1/admin/agents/${created.agentKey}`, { headers });
    console.log(`deleted agent ${created.agentKey}: HTTP ${deleted.status()}`);
    await page.context().close();
  }
  for (const messageId of messageIds) {
    const id = messageId.replace(/^<|>$/g, "");
    try {
      const context = await browser.newContext();
      const search = await context.request.get(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`message-id:"${id}"`)}`);
      const found = ((await search.json()) as { messages?: { ID: string }[] }).messages ?? [];
      if (found.length > 0) {
        const deleted = await context.request.delete(`${MAILPIT}/api/v1/messages`, { data: { IDs: found.map((m) => m.ID) } });
        console.log(`deleted ${found.length} email(s) from Mailpit: HTTP ${deleted.status()}`);
      }
      await context.close();
    } catch (error) {
      console.log(`could not delete the email from Mailpit (${MAILPIT}): ${String(error)}`);
    }
  }
}
