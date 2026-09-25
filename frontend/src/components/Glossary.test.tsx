import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Glossary } from "./Glossary";

describe("Glossary", () => {
  it("shows the term inline without needing interaction", () => {
    render(<Glossary term="GTFS" explanation="The standard transit-data format." />);
    expect(screen.getByText("GTFS")).toBeInTheDocument();
  });

  it("reveals the explanation as a tooltip on focus", () => {
    render(<Glossary term="GTFS" explanation="The standard transit-data format." />);
    const trigger = screen.getByText("GTFS");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    fireEvent.focus(trigger);
    expect(screen.getByRole("tooltip")).toHaveTextContent("The standard transit-data format.");

    fireEvent.blur(trigger);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
