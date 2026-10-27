import { describe, it, expect, vi, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { LiveMapHero } from "./LiveMapHero";

vi.mock("./heroMapDraw", () => ({ drawHeroFrame: vi.fn() }));

function renderHero() {
  return render(
    <I18nextProvider i18n={i18n}>
      <LiveMapHero />
    </I18nextProvider>,
  );
}

function reduceMotion(reduce: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) => ({ matches: reduce && query.includes("prefers-reduced-motion"), media: query }) as MediaQueryList,
  );
}

describe("LiveMapHero", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders a decorative canvas, even without 2D canvas support", () => {
    // jsdom returns null from getContext("2d"), which the hook must tolerate.
    const { container, unmount } = renderHero();
    const canvas = container.querySelector("canvas");
    expect(canvas).toHaveAttribute("aria-hidden", "true");
    expect(() => unmount()).not.toThrow();
  });

  it("offers a pause while the scene plays, and a replay once it has ended", () => {
    reduceMotion(false);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      setTransform: () => {},
    } as unknown as CanvasRenderingContext2D);
    let pending: FrameRequestCallback | null = null;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      pending = cb;
      return 1;
    });
    renderHero();
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    let now = performance.now();
    for (let i = 0; i < 1000 && pending; i++) {
      const cb: FrameRequestCallback = pending;
      pending = null;
      now += 100;
      act(() => cb(now));
    }
    expect(screen.getByRole("button", { name: "Play again" })).toBeTruthy();
  });

  it("shows no playback control under reduced motion, where the scene stands still", () => {
    reduceMotion(true);
    renderHero();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
