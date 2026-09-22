import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { useUrlPatch, useUrlState } from "./useUrlState";

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

/** Two independent keys written from one handler, the shape every compound
 *  selection in the tabs uses. */
function PairProbe() {
  const [a, setA] = useUrlState<string>("a", "");
  const [b, setB] = useUrlState<string>("b", "");
  const patch = useUrlPatch();
  const [params] = useSearchParams();
  return (
    <div>
      <span data-testid="pair">{`${a}/${b}`}</span>
      <span data-testid="search">{params.toString()}</span>
      <button type="button" onClick={() => { setA("1"); setB("2"); }}>
        two setters
      </button>
      <button type="button" onClick={() => patch({ a: "1", b: "2" })}>
        one patch
      </button>
      <button type="button" onClick={() => patch({ a: "1", b: null, routes: ["R1", "R2"] })}>
        mixed patch
      </button>
    </div>
  );
}

describe("useUrlPatch", () => {
  function renderPair(path = "/agencies/1/map") {
    return render(
      <MemoryRouter initialEntries={[path]}>
        <PairProbe />
      </MemoryRouter>,
    );
  }

  // The reason the hook exists: setSearchParams builds from the params of
  // the render that made it, so the second per-key write starts from the
  // same snapshot as the first and replaces it.
  it("per-key setters called together lose all but the last write", () => {
    renderPair();
    fireEvent.click(screen.getByText("two setters"));
    expect(screen.getByTestId("search").textContent).toBe("b=2");
  });

  it("writes every key in the patch in one navigation", () => {
    renderPair();
    fireEvent.click(screen.getByText("one patch"));
    expect(screen.getByTestId("search").textContent).toBe("a=1&b=2");
    expect(screen.getByTestId("pair").textContent).toBe("1/2");
  });

  it("removes a key set to null and joins an array, alongside a plain write", () => {
    renderPair("/agencies/1/map?b=keep");
    fireEvent.click(screen.getByText("mixed patch"));
    const search = screen.getByTestId("search").textContent ?? "";
    expect(new URLSearchParams(search).get("a")).toBe("1");
    expect(new URLSearchParams(search).has("b")).toBe(false);
    expect(new URLSearchParams(search).get("routes")).toBe("R1,R2");
  });

  it("keeps query keys it was not given", () => {
    renderPair("/agencies/1/map?from=2026-01-01&b=old");
    fireEvent.click(screen.getByText("one patch"));
    const search = new URLSearchParams(screen.getByTestId("search").textContent ?? "");
    expect(search.get("from")).toBe("2026-01-01");
    expect(search.get("b")).toBe("2");
  });
});
