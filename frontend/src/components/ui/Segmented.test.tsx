import { useState } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Segmented, type SegmentedOption } from "./Segmented";

const OPTIONS: SegmentedOption<string>[] = [
  { value: "a", label: "A" },
  { value: "b", label: "B" },
  { value: "c", label: "C" },
];

const GRID_OPTIONS: SegmentedOption<string>[] = [
  { value: "0", label: "0" },
  { value: "1", label: "1" },
  { value: "2", label: "2" },
  { value: "3", label: "3" },
  { value: "4", label: "4" },
];

function thumb(container: HTMLElement): HTMLElement {
  return container.querySelector(".seg-thumb")!;
}

/** A stateful wrapper, since the real control is driven by a `value` prop
 *  its caller owns -- arrow-key/roving-tabindex behaviour only shows up
 *  once the parent feeds the new value back in, as every call site does. */
function ControlledSegmented({
  options,
  initial,
  columns,
}: {
  options: SegmentedOption<string>[];
  initial: string | null;
  columns?: number;
}) {
  const [value, setValue] = useState<string | null>(initial);
  return <Segmented aria-label="Choice" options={options} value={value} onChange={setValue} columns={columns} />;
}

describe("Segmented", () => {
  it("positions the thumb at the selected index, in a single row", () => {
    const { container } = render(<Segmented aria-label="Choice" options={OPTIONS} value="b" onChange={() => {}} />);
    const span = thumb(container);
    expect(span.style.transform).toBe("translate(100%, 0%)");
    expect(span.style.opacity).toBe("1");
  });

  it("positions the thumb in 2D when the options wrap onto more than one row", () => {
    // 5 options, 2 columns -> index 3 sits at column 1, row 1.
    const { container } = render(
      <Segmented aria-label="Choice" options={GRID_OPTIONS} value="3" onChange={() => {}} columns={2} />,
    );
    const span = thumb(container);
    expect(span.style.transform).toBe("translate(100%, 100%)");
  });

  it("shows a choice's figure under its label, in its name but not its tooltip", () => {
    const options: SegmentedOption<string>[] = [
      { value: "a", label: "A", meta: "40%" },
      { value: "b", label: "B" },
    ];
    const { container } = render(<Segmented aria-label="Choice" options={options} value="a" onChange={() => {}} />);
    const radio = screen.getByRole("radio", { name: "A 40%" });
    expect(radio).toHaveAttribute("title", "A");
    expect(radio.querySelector(".seg-btn__meta")).toHaveTextContent("40%");
    expect(container.querySelector(".seg")).toHaveClass("seg--meta");
  });

  it("keeps the one-line track when no choice has a figure, or only an empty one", () => {
    const options: SegmentedOption<string>[] = [{ value: "a", label: "A", meta: "" }, ...OPTIONS.slice(1)];
    const { container } = render(<Segmented aria-label="Choice" options={options} value="a" onChange={() => {}} />);
    expect(container.querySelector(".seg")).not.toHaveClass("seg--meta");
    expect(container.querySelector(".seg-btn__meta")).toBeNull();
  });

  it("hides the thumb when no option matches", () => {
    const { container } = render(<Segmented aria-label="Choice" options={OPTIONS} value={null} onChange={() => {}} />);
    expect(thumb(container).style.opacity).toBe("0");
  });

  it("keeps a hidden thumb where it last stood, and fades it in at the next choice instead of sliding", () => {
    const seg = (value: string | null) => <Segmented aria-label="Choice" options={OPTIONS} value={value} onChange={() => {}} />;
    const { container, rerender } = render(seg("c"));
    rerender(seg(null));
    expect(thumb(container).style.opacity).toBe("0");
    expect(thumb(container).style.transform).toBe("translate(200%, 0%)");
    rerender(seg("a"));
    expect(thumb(container).style.transform).toBe("translate(0%, 0%)");
    expect(thumb(container)).toHaveClass("seg-thumb--appear");
    rerender(seg("b"));
    expect(thumb(container)).not.toHaveClass("seg-thumb--appear");
  });

  it("calls onChange with the clicked option's value", async () => {
    let picked: string | null = null;
    render(<Segmented aria-label="Choice" options={OPTIONS} value="a" onChange={(v) => (picked = v)} />);
    await userEvent.click(screen.getByRole("radio", { name: "C" }));
    expect(picked).toBe("c");
  });

  it("moves selection with arrow keys, wrapping at the ends", async () => {
    render(<ControlledSegmented options={OPTIONS} initial="a" />);
    screen.getByRole("radio", { name: "A" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "B" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "B" })).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}{ArrowRight}");
    // wraps past the last option back to the first
    expect(screen.getByRole("radio", { name: "A" })).toHaveAttribute("aria-checked", "true");
  });

  it("supports Home and End", async () => {
    render(<ControlledSegmented options={OPTIONS} initial="b" />);
    screen.getByRole("radio", { name: "B" }).focus();
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("radio", { name: "C" })).toHaveAttribute("aria-checked", "true");
    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("radio", { name: "A" })).toHaveAttribute("aria-checked", "true");
  });

  it("keeps a roving tabindex: only the selected option is tabbable", () => {
    render(<Segmented aria-label="Choice" options={OPTIONS} value="b" onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: "A" })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("radio", { name: "B" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: "C" })).toHaveAttribute("tabindex", "-1");
  });

  it("moves the tabbable option when the selection moves via the keyboard", async () => {
    render(<ControlledSegmented options={OPTIONS} initial="a" />);
    screen.getByRole("radio", { name: "A" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "B" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: "A" })).toHaveAttribute("tabindex", "-1");
  });

  it("falls back to the first option as the tab stop when nothing is selected", () => {
    render(<Segmented aria-label="Choice" options={OPTIONS} value={null} onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: "A" })).toHaveAttribute("tabindex", "0");
  });

  it("renders a radiogroup with its accessible name", () => {
    render(<Segmented aria-label="Choice" options={OPTIONS} value="a" onChange={() => {}} />);
    expect(screen.getByRole("radiogroup", { name: "Choice" })).toBeInTheDocument();
  });
});
