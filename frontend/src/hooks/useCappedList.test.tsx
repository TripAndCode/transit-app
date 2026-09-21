import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useCappedList } from "./useCappedList";

function Probe({ items, cap, listKey }: { items: number[]; cap: number; listKey?: unknown }) {
  const { visible, remaining, showMore } = useCappedList(items, cap, listKey ?? "fixed");
  return (
    <div>
      <ul>
        {visible.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
      {remaining > 0 && (
        <button type="button" onClick={showMore}>
          show {remaining} more
        </button>
      )}
    </div>
  );
}

describe("useCappedList", () => {
  it("shows every item unchanged when under the cap", () => {
    render(<Probe items={[1, 2, 3]} cap={200} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("caps a long list to the given size and reports the remainder", () => {
    const items = Array.from({ length: 250 }, (_, i) => i);
    render(<Probe items={items} cap={200} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(200);
    expect(screen.getByRole("button", { name: "show 50 more" })).toBeInTheDocument();
  });

  it("raises the cap by the initial cap each time showMore is called, never dropping items", async () => {
    const items = Array.from({ length: 450 }, (_, i) => i);
    render(<Probe items={items} cap={200} />);
    await userEvent.click(screen.getByRole("button", { name: "show 250 more" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(400);
    expect(screen.getByRole("button", { name: "show 50 more" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "show 50 more" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(450);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("never shows a negative remainder when items shrink below the current cap", () => {
    render(<Probe items={[1, 2]} cap={200} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("re-caps when the list it is showing is replaced", async () => {
    // The cap exists to keep the *first* render of a long list cheap. Without
    // a reset it survives the data: after one "show more", switching report
    // type or agency renders the next list at the raised cap instead.
    const first = Array.from({ length: 500 }, (_, i) => i);
    const second = Array.from({ length: 500 }, (_, i) => i + 1000);
    const { rerender } = render(<Probe items={first} cap={200} listKey="first" />);
    await userEvent.click(screen.getByRole("button", { name: "show 300 more" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(400);

    rerender(<Probe items={second} cap={200} listKey="second" />);
    expect(screen.getAllByRole("listitem")).toHaveLength(200);
  });

  it("keeps the raised cap while the same list is being shown", async () => {
    const items = Array.from({ length: 500 }, (_, i) => i);
    const { rerender } = render(<Probe items={items} cap={200} listKey="same" />);
    await userEvent.click(screen.getByRole("button", { name: "show 300 more" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(400);

    // A re-render that is not a data change must not undo the user's click.
    rerender(<Probe items={items} cap={200} listKey="same" />);
    expect(screen.getAllByRole("listitem")).toHaveLength(400);
  });
});
