import type { Page } from "@playwright/test";

/**
 * What the recording draws on top of the app, from page scripts only (nothing in the app
 * changes): a caption over the header's empty middle, a title card, and a cursor, which
 * Playwright's video does not show. Captions are kept in sessionStorage, so they survive the
 * navigations of a sign-in (Keycloak is on the same origin, behind Caddy).
 *
 * `installOverlay` runs in the page (context.addInitScript): it must not use anything from
 * this module's scope.
 */
export function installOverlay(): void {
  const KEY = "nw-demo-overlay";
  const Z = "2147483647";
  let host: HTMLDivElement | null = null;
  let shown = "";

  function el(tag: string, css: string, parent: HTMLElement): HTMLElement {
    const node = document.createElement(tag);
    node.style.cssText = css;
    parent.appendChild(node);
    return node;
  }

  function build(): HTMLDivElement {
    const root = document.createElement("div");
    root.id = "nw-demo-overlay";
    root.style.cssText = `position:fixed;inset:0;pointer-events:none;z-index:${Z};font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;`;
    // The caption: centred over the header's free space (between the page title and the
    // account controls), two lines at most.
    el(
      "div",
      "position:absolute;top:5px;left:calc(50% + 35px);transform:translateX(-50%);max-width:560px;" +
        "background:rgba(17,24,39,.92);color:#fff;border-radius:12px;padding:6px 14px;font-size:16px;" +
        "line-height:21px;font-weight:550;letter-spacing:-.005em;box-shadow:0 6px 24px rgba(0,0,0,.25);" +
        "text-align:center;display:none;",
      root,
    ).id = "nw-demo-caption";
    const card = el(
      "div",
      "position:absolute;inset:0;background:linear-gradient(135deg,#0b1220 0%,#16213a 100%);color:#fff;" +
        "display:none;flex-direction:column;align-items:center;justify-content:center;gap:18px;text-align:center;padding:48px;",
      root,
    );
    card.id = "nw-demo-card";
    const cursor = el(
      "div",
      "position:absolute;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;" +
        "background:rgba(37,99,235,.28);border:2px solid rgba(37,99,235,.95);box-shadow:0 0 0 2px rgba(255,255,255,.8);" +
        "transition:transform .28s cubic-bezier(.2,.7,.3,1);transform:translate(-100px,-100px);",
      root,
    );
    cursor.id = "nw-demo-cursor";
    return root;
  }

  function render(): void {
    if (!document.documentElement) return;
    if (!host || !host.isConnected) {
      host = build();
      document.documentElement.appendChild(host);
      shown = "";
    }
    let state = "";
    try {
      state = sessionStorage.getItem(KEY) ?? "";
    } catch {
      state = "";
    }
    if (state === shown) return;
    shown = state;
    const data = (state ? JSON.parse(state) : {}) as { caption?: string; step?: string; card?: { title: string; lines: string[] } };
    const caption = host.querySelector<HTMLElement>("#nw-demo-caption")!;
    if (data.caption) {
      caption.innerHTML = "";
      if (data.step) {
        const step = document.createElement("span");
        step.textContent = data.step;
        step.style.cssText = "color:#93c5fd;font-weight:600;margin-right:8px;";
        caption.appendChild(step);
      }
      caption.appendChild(document.createTextNode(data.caption));
      caption.style.display = "block";
    } else {
      caption.style.display = "none";
    }
    const card = host.querySelector<HTMLElement>("#nw-demo-card")!;
    card.innerHTML = "";
    if (data.card) {
      const title = document.createElement("div");
      title.textContent = data.card.title;
      title.style.cssText = "font-size:48px;font-weight:700;letter-spacing:-.02em;";
      card.appendChild(title);
      for (const line of data.card.lines) {
        const p = document.createElement("div");
        p.textContent = line;
        p.style.cssText = "font-size:22px;line-height:32px;color:#cbd5e1;max-width:900px;";
        card.appendChild(p);
      }
      card.style.display = "flex";
    } else {
      card.style.display = "none";
    }
    host.querySelector<HTMLElement>("#nw-demo-cursor")!.style.visibility = data.card ? "hidden" : "visible";
  }

  function moveCursor(x: number, y: number): void {
    const cursor = host?.querySelector<HTMLElement>("#nw-demo-cursor");
    if (cursor) cursor.style.transform = `translate(${x}px,${y}px)`;
  }

  window.addEventListener("mousemove", (e) => moveCursor(e.clientX, e.clientY), true);
  window.addEventListener(
    "mousedown",
    (e) => {
      if (!host) return;
      const ring = document.createElement("div");
      ring.style.cssText =
        `position:absolute;left:${e.clientX - 18}px;top:${e.clientY - 18}px;width:36px;height:36px;border-radius:50%;` +
        "border:3px solid rgba(37,99,235,.9);opacity:1;transform:scale(.4);transition:transform .45s ease-out,opacity .45s ease-out;";
      host.appendChild(ring);
      requestAnimationFrame(() => {
        ring.style.transform = "scale(1.4)";
        ring.style.opacity = "0";
      });
      setTimeout(() => ring.remove(), 600);
    },
    true,
  );
  (window as unknown as { __wmDemoRender: () => void }).__wmDemoRender = render;
  document.addEventListener("DOMContentLoaded", render);
  setInterval(render, 250);
}

interface OverlayState {
  caption?: string;
  step?: string;
  card?: { title: string; lines: string[] };
}

async function setState(page: Page, state: OverlayState): Promise<void> {
  await page.evaluate((value) => {
    sessionStorage.setItem("nw-demo-overlay", value);
    (window as unknown as { __wmDemoRender?: () => void }).__wmDemoRender?.();
  }, JSON.stringify(state));
}

/** Shows a caption ("2/8" is drawn in a lighter colour before the text). */
export async function caption(page: Page, step: string, text: string): Promise<void> {
  await setState(page, { step, caption: text });
}

/** A full-screen title card over the page. */
export async function card(page: Page, title: string, lines: string[]): Promise<void> {
  await setState(page, { card: { title, lines } });
}

export async function clearOverlay(page: Page): Promise<void> {
  await setState(page, {});
}
