import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { useUrlState } from "./useUrlState";

function Probe({ paramKey, defaultValue }: { paramKey: string; defaultValue: string }) {
  const [value, setValue] = useUrlState(paramKey, defaultValue);
  const [params] = useSearchParams();
  return (
    <div>
      <span data-testid="value">{value}</span>
      <span data-testid="search">{params.toString()}</span>
      <button type="button" onClick={() => setValue("changed")}>
        change
      </button>
      <button type="button" onClick={() => setValue(defaultValue)}>
        reset
      </button>
    </div>
  );
}

function renderProbe(initialPath: string, paramKey = "sel", defaultValue = "trend") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Probe paramKey={paramKey} defaultValue={defaultValue} />
    </MemoryRouter>,
  );
}

describe("useUrlState", () => {
  it("reads the default value when the key is absent from the URL", () => {
    renderProbe("/agencies/1/route-analysis");
    expect(screen.getByTestId("value").textContent).toBe("trend");
    expect(screen.getByTestId("search").textContent).toBe("");
  });

  it("reads the existing query-string value when present", () => {
    renderProbe("/agencies/1/route-analysis?sel=byStop");
    expect(screen.getByTestId("value").textContent).toBe("byStop");
  });

  it("writes a non-default value into the query string", () => {
    renderProbe("/agencies/1/route-analysis");
    fireEvent.click(screen.getByText("change"));
    expect(screen.getByTestId("value").textContent).toBe("changed");
    expect(screen.getByTestId("search").textContent).toBe("sel=changed");
  });

  it("removes the key from the query string when set back to the default", () => {
    renderProbe("/agencies/1/route-analysis?sel=byStop");
    fireEvent.click(screen.getByText("reset"));
    expect(screen.getByTestId("value").textContent).toBe("trend");
    expect(screen.getByTestId("search").textContent).toBe("");
  });

  it("leaves other query-string params untouched", () => {
    renderProbe("/agencies/1/route-analysis?from=2026-01-01&to=2026-01-31");
    fireEvent.click(screen.getByText("change"));
    expect(screen.getByTestId("search").textContent).toBe("from=2026-01-01&to=2026-01-31&sel=changed");
  });
});
