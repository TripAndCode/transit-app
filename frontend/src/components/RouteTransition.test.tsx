import { describe, it, expect } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useNavigate } from "react-router-dom";
import { RouteTransition } from "./RouteTransition";

function Navigator() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate("/b")}>
      go
    </button>
  );
}

function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/a"]}>
      <RouteTransition>
        <Navigator />
        <Routes>
          <Route path="/a" element={<p>page a</p>} />
          <Route path="/b" element={<p>page b</p>} />
        </Routes>
      </RouteTransition>
    </MemoryRouter>,
  );
}

describe("RouteTransition", () => {
  it("wraps the routed content in the shared enter animation", () => {
    const { container } = renderShell();
    const wrapper = container.querySelector(".route-enter");
    expect(wrapper).toBeTruthy();
    expect(wrapper).toContainElement(screen.getByText("page a"));
  });

  it("replays the animation on the next route without remounting the subtree", () => {
    const { container } = renderShell();
    const wrapper = container.querySelector(".route-enter")!;

    // Removing the class is how the restart is observable here: jsdom has no
    // animation engine, so a re-added class is the only visible effect.
    wrapper.classList.remove("route-enter");
    act(() => {
      screen.getByRole("button", { name: "go" }).click();
    });

    expect(screen.getByText("page b")).toBeInTheDocument();
    expect(container.firstElementChild).toBe(wrapper);
    expect(wrapper).toHaveClass("route-enter");
  });
});
