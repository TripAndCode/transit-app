import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useAgencyId } from "./useAgencyId";

function Probe() {
  const agencyId = useAgencyId();
  return <span data-testid="agency-id">{agencyId === null ? "null" : agencyId}</span>;
}

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Probe />
    </MemoryRouter>,
  );
  return screen.getByTestId("agency-id").textContent;
}

describe("useAgencyId", () => {
  it("parses a plain integer id from the route", () => {
    expect(renderAt("/agencies/12/overview")).toBe("12");
  });

  it("returns null for a non-numeric id", () => {
    expect(renderAt("/agencies/abc/overview")).toBe("null");
  });

  it("returns null for a non-integer id", () => {
    expect(renderAt("/agencies/1.5/overview")).toBe("null");
  });

  it("returns null when there is no agencyId route segment", () => {
    expect(renderAt("/")).toBe("null");
  });
});
