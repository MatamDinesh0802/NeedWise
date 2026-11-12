import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { answerFromRun } from "@/lib/chat/answer";
import { initialRunState, reduceRunEvent } from "@/lib/chat/run-reducer";
import { aliceCap } from "@/test/usage-fixtures";

import { AssistantMessage } from "./assistant-message";

function failed(data: Record<string, unknown>) {
  const started = reduceRunEvent(initialRunState("r1"), { id: "1730000000000-0", type: "run.started", data: {} });
  return answerFromRun(reduceRunEvent(started, { id: "1730000000001-0", type: "run.failed", data }));
}

const render = (answer: ReturnType<typeof failed>) =>
  renderToStaticMarkup(<AssistantMessage answer={answer} onOpenSources={() => {}} onRetry={() => {}} sourcesOpen={false} />);

describe("AssistantMessage when a run fails", () => {
  it("names the used-up budget from the stream's error event, and offers no retry", () => {
    // Chat, department agents and research runs all stream through here.
    const html = render(failed({ code: "budget_exceeded", message: "Your daily cap is used up.", budget: aliceCap }));
    expect(html).toContain('data-code="budget_exceeded"');
    expect(html).toContain("Your daily cap is used up");
    expect(html).toContain("$2.13 of $2 spent (107%)");
    expect(html).toContain("Resets ");
    expect(html).not.toContain("The answer failed");
    expect(html).not.toContain("Retry");
  });

  it("still explains a budget block without the budget, in the API's words", () => {
    const html = render(failed({ code: "budget_exceeded", message: "The organization's monthly budget is used up." }));
    expect(html).toContain("A usage budget is used up");
    expect(html).toContain("The organization&#x27;s monthly budget is used up.");
  });

  it("keeps the generic failure with retry for other errors", () => {
    const html = render(failed({ code: "model_unavailable", message: "The language model is unavailable." }));
    expect(html).toContain("The answer failed");
    expect(html).toContain("Retry");
  });
});
