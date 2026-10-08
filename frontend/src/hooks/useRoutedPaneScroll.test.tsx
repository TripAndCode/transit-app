import { describe, it, expect } from "vitest";
import { useEffect, useRef } from "react";
import { act, fireEvent, render } from "@testing-library/react";
import { MemoryRouter, useNavigate, type NavigateFunction } from "react-router-dom";
import { useRoutedPaneScroll } from "./useRoutedPaneScroll";

const router: { navigate?: NavigateFunction } = {};

function Pane() {
  const ref = useRef<HTMLDivElement>(null);
  useRoutedPaneScroll(ref);
  const navigate = useNavigate();
  useEffect(() => {
    router.navigate = navigate;
  });
  return <div ref={ref} data-testid="pane" />;
}

function go(to: string | number, options?: { replace?: boolean }) {
  act(() => {
    if (typeof to === "number") void router.navigate!(to);
    else void router.navigate!(to, options);
  });
}

function renderPane(path = "/a") {
  const { getByTestId } = render(
    <MemoryRouter initialEntries={[path]}>
      <Pane />
    </MemoryRouter>,
  );
  return getByTestId("pane");
}

function scrollTo(pane: HTMLElement, top: number) {
  pane.scrollTop = top;
  fireEvent.scroll(pane);
}

describe("useRoutedPaneScroll", () => {
  it("opens a new page at the top", () => {
    const pane = renderPane();
    scrollTo(pane, 300);
    go("/b");
    expect(pane.scrollTop).toBe(0);
  });

  it("returns to where a page was left on a step back and forward", () => {
    const pane = renderPane();
    scrollTo(pane, 300);
    go("/b");
    scrollTo(pane, 40);
    go(-1);
    expect(pane.scrollTop).toBe(300);
    go(1);
    expect(pane.scrollTop).toBe(40);
  });

  it("keeps the place when only the query string changes, and files it under the new entry", () => {
    const pane = renderPane("/a?x=1");
    scrollTo(pane, 300);
    go("/a?x=2", { replace: true });
    expect(pane.scrollTop).toBe(300);
    go("/b");
    go(-1);
    expect(pane.scrollTop).toBe(300);
  });

  it("opens a page reached by a redirect at the top", () => {
    const pane = renderPane();
    scrollTo(pane, 300);
    go("/c", { replace: true });
    expect(pane.scrollTop).toBe(0);
  });
});
