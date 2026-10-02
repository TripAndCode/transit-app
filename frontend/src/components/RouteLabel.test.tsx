import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { RouteLabel } from "./RouteLabel";

function names(labels: Record<string, string>) {
  const data = new Map(Object.entries(labels));
  return { data, isLoading: false, format: (code: string | null | undefined) => data.get(String(code)) ?? `Route ${code}` };
}

describe("RouteLabel", () => {
  it("follows a route's label with its code, muted", () => {
    const { container } = render(<RouteLabel code="1021" names={names({ "1021": "A1 国道・古川線 · for 青森駅" })} />);
    expect(container).toHaveTextContent("A1 国道・古川線 · for 青森駅 1021");
    expect(container.querySelector(".route-label__code")).toHaveTextContent("1021");
  });

  it("shows the code once when the label already carries it", () => {
    // A route with no name fields is labelled by its route_id, which embeds the code.
    const { container } = render(<RouteLabel code="1021" names={names({ "1021": "国道・古川線(1021)" })} />);
    expect(container).toHaveTextContent(/^国道・古川線\(1021\)$/);
    expect(container.querySelector(".route-label__code")).toBeNull();
  });

  it("reads as Route <code> when nothing names it", () => {
    const { container } = render(<RouteLabel code="1021" names={names({})} />);
    expect(container).toHaveTextContent(/^Route 1021$/);
  });
});
