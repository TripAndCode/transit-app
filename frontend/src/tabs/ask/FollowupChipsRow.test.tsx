import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import { FollowupChipsRow } from "./FollowupChipsRow";
import { useTranslation } from "react-i18next";
import type { ConvMessage } from "../../api/types";

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
  onFollowup: (ctxMsgId: number, question: string) => void;
  draftValue: string;
  onDraftChange: (next: string) => void;
  error?: string | null;
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
    expect(onFollowup).toHaveBeenCalledWith(1, "What about route 12?");
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

  it("shows an error message when passed", () => {
    renderWithProviders(
      <Wrapper
        messages={messagesWithResult}
        onFollowup={vi.fn()}
        draftValue=""
        onDraftChange={vi.fn()}
        error="Couldn't answer that question. Please try again."
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't answer that question");
  });
});
