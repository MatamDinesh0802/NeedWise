import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Department, Group } from "@/lib/admin/api";
import { describeExternal, isExternal, normalizeExternal, type IdentityMappings } from "@/lib/admin/identity-mappings";

import { IdentityMappingsView } from "./identity-mappings-section";

const groups: Group[] = [{ id: "g-lead", slug: "leadership", name: "Leadership", description: null, source: "local", member_count: 5 }];
const departments: Department[] = [{ id: "d-eng", slug: "engineering", name: "Engineering", member_count: 4, managers: [] }];

const data: IdentityMappings = {
  unmapped: [
    { external: "group:eng-leads@acme.test", documents: 3, sources: ["eng-drive"] },
    { external: "github-user:octocat", documents: 1, sources: [] },
  ],
  mappings: [
    { id: "m1", external: "github-team:acme/platform", principal: "group:g-lead", principal_name: "Leadership", created_by: null, updated_at: "2026-11-13T09:00:00Z" },
    { id: "m2", external: "domain:partner.example", principal: "group:gone", principal_name: null, created_by: null, updated_at: "2026-11-13T09:00:00Z" },
  ],
};

function render(overrides: Partial<Parameters<typeof IdentityMappingsView>[0]> = {}): string {
  return renderToStaticMarkup(
    <IdentityMappingsView
      data={data}
      groups={groups}
      departments={departments}
      tenantId="t-1"
      busy={false}
      onMap={() => undefined}
      onRemove={() => undefined}
      {...overrides}
    />,
  );
}

describe("IdentityMappingsView", () => {
  it("lists what is unmatched with counts and sources, never document titles", () => {
    const html = render();
    expect(html).toContain("Not matched: they give nobody access");
    expect(html).toContain("eng-leads@acme.test");
    expect(html).toContain("Group in the source · named on 3 documents from eng-drive");
    expect(html).toContain("GitHub account · named on 1 document<");
  });

  it("shows who each mapping names, and a mapping whose target is gone grants nothing", () => {
    const html = render();
    expect(html).toContain("github-team:acme/platform");
    expect(html).toContain("is Leadership");
    expect(html).toContain("no longer exists: grants nothing");
    expect(html).toContain('aria-label="Remove the mapping for domain:partner.example"');
  });

  it("says so when everything is matched", () => {
    expect(render({ data: { unmapped: [], mappings: [] } })).toContain("Every account the sources name is matched to someone.");
  });

  it("prefills the form from an unmatched account and waits for a target", () => {
    const html = render({ initialExternal: "group:eng-leads@acme.test" });
    expect(html).toMatch(/<input[^>]*value="group:eng-leads@acme.test"/);
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled=""[^>]*>Save mapping<\/button>/);
  });
});

describe("source accounts", () => {
  it("are recognised like the API does, in lower case", () => {
    expect(isExternal("Group:Eng-Leads@acme.test")).toBe(true);
    expect(normalizeExternal(" GitHub-User:Octocat ")).toBe("github-user:octocat");
    expect(isExternal("anyone")).toBe(true);
    expect(isExternal("user:alice")).toBe(false);
    expect(isExternal("group:has space")).toBe(false);
    expect(describeExternal("slack-user:u123")).toEqual({ kind: "Slack member", value: "u123" });
  });
});
