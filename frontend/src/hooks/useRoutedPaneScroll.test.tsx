import { describe, it, expect } from "vitest";
import { useRef } from "react";
import { act, fireEvent, render } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider, useMatches } from "react-router-dom";
import { routedPage, useRoutedPaneScroll } from "./useRoutedPaneScroll";

function Pane() {
  const ref = useRef<HTMLDivElement>(null);
  useRoutedPaneScroll(ref, routedPage(useMatches()));
  return (
    <div ref={ref} data-testid="pane">
      <Outlet />
    </div>
  );
}

const routes = [
  {
    path: "/",
    element: <Pane />,
    children: [
      { path: "a" },
      { path: "b" },
      { path: "c" },
      { path: "users", element: <Outlet />, children: [{ path: ":uid", handle: { overlay: true } }] },
    ],
  },
];

function renderPane(path = "/a") {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const { getByTestId } = render(<RouterProvider router={router} />);
  const go = async (to: string | number, options?: { replace?: boolean }) => {
    await act(async () => {
      await (typeof to === "number" ? router.navigate(to) : router.navigate(to, options));
    });
  };
  return { pane: getByTestId("pane"), go };
}

function scrollTo(pane: HTMLElement, top: number) {
  pane.scrollTop = top;
  fireEvent.scroll(pane);
}

describe("useRoutedPaneScroll", () => {
  it("opens a new page at the top", async () => {
    const { pane, go } = renderPane();
    scrollTo(pane, 300);
    await go("/b");
    expect(pane.scrollTop).toBe(0);
  });

  it("returns to where a page was left on a step back and forward", async () => {
    const { pane, go } = renderPane();
    scrollTo(pane, 300);
    await go("/b");
    scrollTo(pane, 40);
    await go(-1);
    expect(pane.scrollTop).toBe(300);
    await go(1);
    expect(pane.scrollTop).toBe(40);
  });

  it("keeps the place when only the query string changes, and files it under the new entry", async () => {
    const { pane, go } = renderPane("/a?x=1");
    scrollTo(pane, 300);
    await go("/a?x=2", { replace: true });
    expect(pane.scrollTop).toBe(300);
    await go("/b");
    await go(-1);
    expect(pane.scrollTop).toBe(300);
  });

  it("opens a page reached by a redirect at the top", async () => {
    const { pane, go } = renderPane();
    scrollTo(pane, 300);
    await go("/c", { replace: true });
    expect(pane.scrollTop).toBe(0);
  });

  it("keeps the list's place while an overlay route opens over it and closes again", async () => {
    const { pane, go } = renderPane("/users");
    scrollTo(pane, 300);
    await go("/users/5");
    expect(pane.scrollTop).toBe(300);
    await go("/users/6");
    expect(pane.scrollTop).toBe(300);
    await go("/users");
    expect(pane.scrollTop).toBe(300);
  });

  it("still opens another page at the top from an overlay", async () => {
    const { pane, go } = renderPane("/users/5");
    scrollTo(pane, 300);
    await go("/b");
    expect(pane.scrollTop).toBe(0);
  });
});
