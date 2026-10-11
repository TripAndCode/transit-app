import { describe, it, expect, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import { LimitPill } from "./LimitPill";

function openPill(value: number) {
  const onChange = vi.fn();
  renderWithProviders(<LimitPill label="Top" value={value} min={3} max={20} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /Top/ }));
  return { onChange, input: screen.getByRole("spinbutton", { name: "Top" }) as HTMLInputElement };
}

describe("LimitPill draft", () => {
  it.each([
    ["above max", 20, "50", "20"],
    ["below min", 3, "2", "3"],
    ["fractional", 5, "5.4", "5"],
  ])("snaps the field back to the clamped value on Enter when the value is unchanged (%s)", async (_name, value, typed, shown) => {
    const { onChange, input } = openPill(value);
    const user = userEvent.setup();
    await user.clear(input);
    await user.type(input, typed);
    await user.type(input, "{Enter}");
    expect(onChange).toHaveBeenCalledWith(Number(shown));
    expect(input.value).toBe(shown);
  });

  it("snaps the field back on blur too", async () => {
    const { input } = openPill(20);
    const user = userEvent.setup();
    await user.clear(input);
    await user.type(input, "50");
    fireEvent.blur(input);
    expect(input.value).toBe("20");
  });
});
