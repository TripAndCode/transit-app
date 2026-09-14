import { describe, expect, it } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import type { ConvMessage } from "../../api/types";
import { investigationSteps } from "./investigationSteps";
import { InvestigationCanvas } from "./InvestigationCanvas";

function message(message_id: number, role: ConvMessage["role"], text: string): ConvMessage {
  return {
    message_id, conversation_id: "thread", role, chip_id: null, tool: null,
    args: null, signature_hash: null, result: null, rendered_summary: text,
    created_at: "2026-09-01T00:00:00Z",
  };
}
const messages = [message(1, "user", "Morning?"), message(2, "assistant", "First answer"),
  message(3, "user", "Evening?"), message(4, "assistant", "Second answer")];
const formatRoute = (code: string | null | undefined) => code ?? "";

describe("investigation steps", () => {
  it("groups questions with their answers without altering saved messages", () => {
    expect(investigationSteps(messages).map((step) => step.messages.length)).toEqual([2, 2]);
    expect(investigationSteps(messages)[0].messages[0]).toBe(messages[0]);
  });
  it("preserves orphan results and unanswered questions", () => {
    const steps = investigationSteps([messages[1], messages[2]]);
    expect(steps.map((step) => step.question)).toEqual(["", "Evening?"]);
    expect(investigationSteps([])).toEqual([]);
  });
});

describe("investigation canvas", () => {
  it("defaults to latest, hides follow-ups on older steps, and preserves the full log", () => {
    renderWithProviders(<InvestigationCanvas agencyId={9} messages={messages} formatRoute={formatRoute}>
      <button>Follow up</button>
    </InvestigationCanvas>);
    expect(screen.getByRole("button", { name: "2. Evening?" })).toHaveAttribute("aria-current", "step");
    const log = screen.getByText("Full conversation").closest("details");
    expect(log).not.toHaveAttribute("open");
    fireEvent.click(screen.getByRole("button", { name: "1. Morning?" }));
    expect(screen.queryByRole("button", { name: "Follow up" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1. Morning?" })).toHaveAttribute("aria-current", "step");
    fireEvent.click(screen.getByRole("button", { name: "Return to latest" }));
    expect(screen.getByRole("button", { name: "Follow up" })).toBeInTheDocument();
  });
  it("returns to the latest step when a new question arrives", () => {
    const { rerender } = renderWithProviders(<InvestigationCanvas agencyId={9} messages={messages} formatRoute={formatRoute} />);
    fireEvent.click(screen.getByRole("button", { name: "1. Morning?" }));
    rerender(<InvestigationCanvas agencyId={9} messages={[...messages, message(5, "user", "Weekends?")]} formatRoute={formatRoute} />);
    expect(screen.getByRole("button", { name: "3. Weekends?" })).toHaveAttribute("aria-current", "step");
  });
});
