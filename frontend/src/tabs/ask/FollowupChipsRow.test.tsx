import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import { FollowupChipsRow } from "./FollowupChipsRow";
import { useTranslation } from "react-i18next";
import type { ConvMessage } from "../../api/types";
import { ApiError } from "../../api/client";
import type { StopFocus } from "./stopEvidence";

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
  focus?: StopFocus | null;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  return <FollowupChipsRow t={t} {...props} />;
}

describe("FollowupChipsRow free-text input", () => {
  it("uses the selected source ID and visible sequence context, without sending on selection", async () => {
    const onFollowup = vi.fn();
    const source = { ...messagesWithResult[0], message_id: 22, tool: "segment_hotspots",
      result: { kind: "table", columns: ["stop_sequence", "stop_name", "avg_min", "samples"],
        rows: [[7, "Central", 4.2, 128]], summary: null, series: null, pairs: null } } as ConvMessage;
    renderWithProviders(<Wrapper messages={[source, ...messagesWithResult]} onFollowup={onFollowup}
      draftValue="Explain the sample count" onDraftChange={vi.fn()} compact
      focus={{ messageId: 22, sequence: 7, name: "Central" }} />);
    expect(onFollowup).not.toHaveBeenCalled();
    expect(screen.getByText(/Selected stop-sequence group: 7/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("Send"));
    expect(onFollowup).toHaveBeenCalledWith(22, "Explain the sample count\nSelected stop-sequence group: 7 (representative name: Central).", true);
  });
  it("does not fall back to another source when the selected row is unavailable", () => {
    renderWithProviders(<Wrapper messages={messagesWithResult} onFollowup={vi.fn()}
      draftValue="Explain" onDraftChange={vi.fn()} focus={{ messageId: 22, sequence: 7, name: "Central" }} />);
    expect(screen.queryByText("Send")).not.toBeInTheDocument();
  });
  it("counts the context prefix toward the server question limit", () => {
    const source = { ...messagesWithResult[0], tool: "segment_hotspots",
      result: { kind: "table", columns: ["stop_sequence", "stop_name", "avg_min", "samples"],
        rows: [[7, "Central", 4.2, 128]], summary: null, series: null, pairs: null } } as ConvMessage;
    renderWithProviders(<Wrapper messages={[source]} onFollowup={vi.fn()} draftValue="Explain" onDraftChange={vi.fn()}
      maxChars={20} focus={{ messageId: 1, sequence: 7, name: "Central" }} />);
    expect(screen.getByText("Send")).toBeDisabled();
  });
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

  it("shows a calm sign-in nudge for an anonymous Ask quota-exceeded 429, not a raw rate-limit error", () => {
    renderWithProviders(
      <MemoryRouter>
        <Wrapper
          messages={messagesWithResult}
          onFollowup={vi.fn()}
          draftValue=""
          onDraftChange={vi.fn()}
          error={new ApiError(429, JSON.stringify({ detail: "x", code: "ask_anon_quota_exceeded" }))}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/free AI question limit/i);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
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
