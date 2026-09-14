import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { useInvestigationLocation } from "./useInvestigationLocation";

function Harness() {
  const [id, select] = useInvestigationLocation();
  const location = useLocation();
  return <>
    <output data-testid="id">{id ?? "none"}</output>
    <output data-testid="search">{location.search}</output>
    <button onClick={() => select("thread-2")}>Select</button>
    <button onClick={() => select(null)}>New</button>
  </>;
}

describe("investigation URL", () => {
  it("restores a deep link, switches and clears selection without losing filters", () => {
    render(<MemoryRouter initialEntries={["/agencies/9/ask?from=2026-09-01&routes=C10&conversation=thread-1"]}><Harness /></MemoryRouter>);
    expect(screen.getByTestId("id")).toHaveTextContent("thread-1");
    fireEvent.click(screen.getByText("Select"));
    expect(screen.getByTestId("id")).toHaveTextContent("thread-2");
    expect(screen.getByTestId("search")).toHaveTextContent("from=2026-09-01&routes=C10&conversation=thread-2");
    fireEvent.click(screen.getByText("New"));
    expect(screen.getByTestId("id")).toHaveTextContent("none");
    expect(screen.getByTestId("search")).toHaveTextContent("?from=2026-09-01&routes=C10");
    expect(screen.getByTestId("search")).not.toHaveTextContent("conversation");
  });
  it.each(["..%2Fother", "..", ".", "%3Fadmin", "a".repeat(101)])("rejects unsafe conversation ID %s", (id) => {
    render(<MemoryRouter initialEntries={[`/ask?conversation=${id}`]}><Harness /></MemoryRouter>);
    expect(screen.getByTestId("id")).toHaveTextContent("none");
  });
});
