import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { InsightHint } from "./InsightHint";

function renderHint() {
  return render(
    <I18nextProvider i18n={i18n}>
      <div data-testid="clipping-column" style={{ overflow: "hidden" }}>
        <InsightHint title="About the comparison" body="Compares two periods." />
      </div>
      <button type="button">elsewhere</button>
    </I18nextProvider>,
  );
}

describe("InsightHint", () => {
  it("opens outside any clipping column, placed against the viewport", async () => {
    renderHint();
    await userEvent.click(screen.getByRole("button", { name: "Hint" }));
    const popover = screen.getByRole("dialog", { name: "About the comparison" });
    expect(screen.getByTestId("clipping-column")).not.toContainElement(popover);
    expect(popover.style.position).toBe("fixed");
    expect(popover.style.left).not.toBe("");
  });

  it("stays open for a click inside it, and closes for a click elsewhere", async () => {
    renderHint();
    await userEvent.click(screen.getByRole("button", { name: "Hint" }));
    await userEvent.click(screen.getByText("Compares two periods."));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "elsewhere" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes on Escape and hands focus back to its button", async () => {
    renderHint();
    const trigger = screen.getByRole("button", { name: "Hint" });
    await userEvent.click(trigger);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("gives its button at least a 24 px target", () => {
    renderHint();
    const button = screen.getByRole("button", { name: "Hint" });
    expect(Number.parseFloat(button.style.minWidth)).toBeGreaterThanOrEqual(24);
    expect(Number.parseFloat(button.style.minHeight)).toBeGreaterThanOrEqual(24);
  });
});
