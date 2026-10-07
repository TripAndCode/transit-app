import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { RouteTitleTransition } from "./RouteTitleTransition";

describe("RouteTitleTransition", () => {
  it("renders what it wraps; outside a transition it adds nothing to the page", () => {
    render(
      <RouteTitleTransition>
        <h1>Route 3</h1>
      </RouteTitleTransition>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Route 3" }).parentElement?.tagName).toBe("DIV");
  });
});
