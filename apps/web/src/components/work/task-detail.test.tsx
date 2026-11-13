import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TaskBody } from "./task-detail";

describe("TaskBody", () => {
  it("lists a stale-document reminder's documents as links, under the description's first paragraph", () => {
    const html = renderToStaticMarkup(
      <TaskBody
        task={{
          description: "These documents come from a connected source.\n\n- Travel policy (Handbook site): … /knowledge/documents/d1",
          origin: {
            via: "connectors",
            kind: "stale_documents",
            documents: [
              { id: "d1", title: "Travel policy", reason: "missing_upstream" },
              { id: "d2", title: "Leave policy", reason: "not_updated" },
            ],
            more: 4,
          },
        }}
      />,
    );
    expect(html).toContain("These documents come from a connected source.");
    expect(html).not.toContain("- Travel policy (Handbook site)");
    expect(html).toContain('aria-label="Documents to check"');
    expect(html).toContain('href="/knowledge/documents/d1"');
    expect(html).toContain("no longer listed by the source");
    expect(html).toContain("unchanged in the source for a long time");
    expect(html).toContain("…and 4 more");
  });

  it("shows any other task's description as it is", () => {
    const html = renderToStaticMarkup(<TaskBody task={{ description: "Restock spare parts.\n\nBy Friday.", origin: { via: "web" } }} />);
    expect(html).toContain("Restock spare parts.\n\nBy Friday.");
  });
});
