import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../test/renderWithProviders";
import { PlaybackToggle } from "./PlaybackToggle";

const PLAYBACK_ON = /1日の動きを再生|Play the day/i;
const PLAYBACK_OFF = /現在の運行に戻る|Back to current observations/i;

describe("PlaybackToggle", () => {
  it("switches the map into playback and reports the state it is in", async () => {
    const onToggle = vi.fn();
    renderWithProviders(<PlaybackToggle active={false} onToggle={onToggle} />);
    const toggle = screen.getByRole("button", { name: PLAYBACK_ON });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    await userEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("keeps its name in an element a narrow map can hide, leaving the icon", () => {
    renderWithProviders(<PlaybackToggle active={false} onToggle={vi.fn()} />);
    const toggle = screen.getByRole("button", { name: PLAYBACK_ON });
    expect(toggle.querySelector(".ops-dock__playback-label")?.textContent).toMatch(PLAYBACK_ON);
  });

  it("offers the way back out once playback is on", () => {
    renderWithProviders(<PlaybackToggle active onToggle={vi.fn()} />);
    expect(screen.getByRole("button", { name: PLAYBACK_OFF }).getAttribute("aria-pressed")).toBe("true");
  });
});
