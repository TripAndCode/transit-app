import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import { FollowupChipsRow } from "./FollowupChipsRow";
import { useTranslation } from "react-i18next";
import type { ConvMessage } from "../../api/types";
import { ApiError } from "../../api/client";

const messagesWithResult: ConvMessage[] = [
  {
    message_id: 1,
    role: "assistant",
    tool: "route_ranking",
    args: {},
    result: { kind: "table", columns: [], rows: [] },
    rendered_summary: null,
    created_at: "2026-01-01T00:00:00Z",
  } as unknown as ConvMessage,
];

function Wrapper(props: {
  messages: ConvMessage[];
  onFollowup: (ctxMsgId: number, question: string, isDraft: boolean) => void;
  draftValue: string;
  onDraftChange: (next: string) => void;
  error?: unknown;
  maxChars?: number;
}) {
  const { t } = useTranslation();
  return <FollowupChipsRow t={t} {...props} />;
}

describe("FollowupChipsRow free-text input", () => {
  it("renders nothing when there is no tool result to ground on", () => {
    const { container } = renderWithProviders(
      <Wrapper messages={[]} onFollowup={vi.fn()} draftValue="" onDraftChange={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("disables send until non-whitespace text is entered", async () => {
    const user = userEvent.setup();
    const onDraftChange = vi.fn();
    renderWithProviders(
      <Wrapper
        messages={messagesWithResult}
        onFollowup={vi.fn()}
        draftValue=""
        onDraftChange={onDraftChange}
      />,
    );
    expect(screen.getByText("Send")).toBeDisabled();
    await user.type(screen.getByPlaceholderText("Ask about this result..."), "x");
    expect(onDraftChange).toHaveBeenCalled();
  });

  it("submits the typed question grounded on the last result message", async () => {
    const user = userEvent.setup();
    const onFollowup = vi.fn();
    renderWithProviders(
      <Wrapper
        messages={messagesWithResult}
        onFollowup={onFollowup}
        draftValue="What about route 12?"
        onDraftChange={vi.fn()}
      />,
    );
    await user.click(screen.getByText("Send"));
    expect(onFollowup).toHaveBeenCalledWith(1, "What about route 12?", true);
  });

  it("passes isDraft=false for a canned chip, even if its prompt text matches the current draft", async () => {
    const user = userEvent.setup();
    const onFollowup = vi.fn();
    const chipPrompt = "Explain the pattern in this result in 3 sentences or fewer.";
    renderWithProviders(
      <Wrapper
        messages={messagesWithResult}
        onFollowup={onFollowup}
        draftValue={chipPrompt}
        onDraftChange={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Why this pattern?" }));
    expect(onFollowup).toHaveBeenCalledWith(1, chipPrompt, false);
  });

  it("does not submit a whitespace-only draft", async () => {
    const user = userEvent.setup();
    const onFollowup = vi.fn();
    renderWithProviders(
      <Wrapper
        messages={messagesWithResult}
        onFollowup={onFollowup}
        draftValue="   "
        onDraftChange={vi.fn()}
      />,
    );
    expect(screen.getByText("Send")).toBeDisabled();
    await user.click(screen.getByText("Send"));
    expect(onFollowup).not.toHaveBeenCalled();
  });

  it("shows a generic error message for an unrecognized LLM error", () => {
    renderWithProviders(
      <Wrapper
        messages={messagesWithResult}
        onFollowup={vi.fn()}
        draftValue=""
        onDraftChange={vi.fn()}
        error={new ApiError(502, JSON.stringify({ detail: "llm_error:unexpected" }))}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't answer that question");
  });

  it("shows a too-long-specific message for a 400 question_too_long error", () => {
    renderWithProviders(
      <Wrapper
        messages={messagesWithResult}
        onFollowup={vi.fn()}
        draftValue=""
        onDraftChange={vi.fn()}
        error={new ApiError(400, JSON.stringify({ detail: "question_too_long" }))}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("too long");
  });

  it("shows a rate-limit-specific message for an llm_error:rate_limit error", () => {
    renderWithProviders(
      <Wrapper
        messages={messagesWithResult}
        onFollowup={vi.fn()}
        draftValue=""
        onDraftChange={vi.fn()}
        error={new ApiError(502, JSON.stringify({ detail: "llm_error:rate_limit" }))}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("High traffic");
  });

  it("caps the input at the server-supplied maxChars", () => {
    renderWithProviders(
      <Wrapper
        messages={messagesWithResult}
        onFollowup={vi.fn()}
        draftValue=""
        onDraftChange={vi.fn()}
        maxChars={10}
      />,
    );
    expect(screen.getByPlaceholderText("Ask about this result...")).toHaveAttribute(
      "maxlength",
      "10",
    );
  });
});
