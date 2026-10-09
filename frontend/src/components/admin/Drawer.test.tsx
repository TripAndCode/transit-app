import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { Drawer } from "./Drawer";

function Harness({ onClose }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        open
      </button>
      <Drawer
        open={open}
        label="Agency detail"
        onClose={() => {
          setOpen(false);
          onClose?.();
        }}
      >
        <button type="button">inside</button>
      </Drawer>
    </div>
  );
}

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("Drawer", () => {
  it("renders nothing while closed", () => {
    wrap(<Harness />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("exposes an accessible name and takes focus when opened", async () => {
    const user = userEvent.setup();
    wrap(<Harness />);
    await user.click(screen.getByRole("button", { name: "open" }));
    const panel = screen.getByRole("dialog", { name: "Agency detail" });
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveFocus();
  });

  it("closes on Escape and restores focus to the opener", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    wrap(<Harness onClose={onClose} />);
    const opener = screen.getByRole("button", { name: "open" });
    await user.click(opener);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("closes on Escape pressed from a control inside the panel", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    wrap(<Harness onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "open" }));
    screen.getByRole("button", { name: "inside" }).focus();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("stays non-modal on the shared overlay base: no scrim, no aria-modal, page still scrolls", async () => {
    const user = userEvent.setup();
    wrap(<Harness />);
    await user.click(screen.getByRole("button", { name: "open" }));
    const panel = screen.getByRole("dialog", { name: "Agency detail" });
    expect(panel).toHaveClass("ui-overlay-panel");
    expect(panel).not.toHaveAttribute("aria-modal");
    expect(screen.queryByRole("presentation")).not.toBeInTheDocument();
    expect(document.body.style.overflow).not.toBe("hidden");
  });

  it("announces its heading, since a non-modal panel never takes the page over", async () => {
    const user = userEvent.setup();
    wrap(<Harness />);
    await user.click(screen.getByRole("button", { name: "open" }));
    const region = screen.getByRole("heading", { name: "Agency detail" }).closest("[aria-live]");
    expect(region).not.toBeNull();
    expect(region).toHaveAttribute("aria-live", "polite");
  });

  it("offers an explicit close control", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    wrap(<Harness onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "open" }));
    await user.click(screen.getByRole("button", { name: i18n.t("common.close") }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
