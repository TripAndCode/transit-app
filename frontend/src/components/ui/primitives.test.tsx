import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { Card } from "./Card";
import { PageHeader } from "./PageHeader";
import { Section } from "./Section";
import { Toolbar } from "./Toolbar";

const css = readFileSync(resolve(process.cwd(), "src/components/ui/ui.css"), "utf8");

/** The stylesheet is the contract here: vitest runs with `css: false`, so a
 *  class-driven declaration can never be read back off a rendered node. */
function ruleBody(selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at === -1) throw new Error(`selector not found: ${selector}`);
  return css.slice(at, css.indexOf("}", at));
}

describe("Card", () => {
  it("renders its children inside a .ui-card surface", () => {
    render(<Card>body</Card>);
    const card = screen.getByText("body");
    expect(card).toHaveClass("ui-card");
    expect(card.tagName).toBe("DIV");
  });

  it("is padded by default and can be rendered unpadded", () => {
    const { rerender } = render(<Card>padded</Card>);
    expect(screen.getByText("padded")).toHaveClass("ui-card--padded");
    rerender(<Card padded={false}>bare</Card>);
    expect(screen.getByText("bare")).not.toHaveClass("ui-card--padded");
  });

  it("keeps caller classes and can render as another element", () => {
    render(
      <Card as="section" className="login-card">
        inner
      </Card>,
    );
    const card = screen.getByText("inner");
    expect(card.tagName).toBe("SECTION");
    expect(card).toHaveClass("ui-card", "login-card");
  });

  it("paints from the card tokens, not literals", () => {
    const body = ruleBody(".ui-card");
    expect(body).toContain("var(--card-border)");
    expect(body).toContain("var(--radius-lg)");
    expect(body).toContain("var(--el-1)");
  });
});

describe("PageHeader", () => {
  it("renders the title as the page's h1", () => {
    render(<PageHeader title="Account" />);
    const heading = screen.getByRole("heading", { level: 1, name: "Account" });
    expect(heading).toHaveClass("ui-page-header__title");
  });

  it("renders an optional eyebrow, subtitle and actions slot", () => {
    render(
      <PageHeader
        eyebrow="Admin"
        title="Users"
        subtitle="Everyone with access"
        actions={<button type="button">Invite</button>}
      />,
    );
    expect(screen.getByText("Admin")).toBeInTheDocument();
    expect(screen.getByText("Everyone with access")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Invite" })).toBeInTheDocument();
  });

  it("omits the optional slots entirely when they are not given", () => {
    const { container } = render(<PageHeader title="Account" />);
    expect(container.querySelector(".ui-page-header__eyebrow")).toBeNull();
    expect(container.querySelector(".ui-page-header__subtitle")).toBeNull();
    expect(container.querySelector(".ui-page-header__actions")).toBeNull();
  });

  it("sizes the title from the type scale", () => {
    expect(ruleBody(".ui-page-header__title")).toContain("var(--text-2xl)");
  });
});

describe("Section", () => {
  it("renders an h2 title with an optional eyebrow and description", () => {
    render(
      <Section eyebrow="Security" title="Active sessions" description="Signed in devices">
        <p>rows</p>
      </Section>,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Active sessions" })).toBeInTheDocument();
    expect(screen.getByText("Security")).toBeInTheDocument();
    expect(screen.getByText("Signed in devices")).toBeInTheDocument();
    expect(screen.getByText("rows")).toBeInTheDocument();
  });

  it("renders as a <section> landmark and takes an actions slot", () => {
    const { container } = render(
      <Section title="Trend" actions={<button type="button">CSV</button>}>
        body
      </Section>,
    );
    expect(container.querySelector("section.ui-section")).not.toBeNull();
    expect(screen.getByRole("button", { name: "CSV" })).toBeInTheDocument();
  });
});

describe("Toolbar", () => {
  it("wraps its controls in one row", () => {
    const { container } = render(
      <Toolbar>
        <button type="button">A</button>
        <button type="button">B</button>
      </Toolbar>,
    );
    const bar = container.querySelector(".ui-toolbar");
    expect(bar).not.toBeNull();
    expect(bar!.children).toHaveLength(2);
    expect(ruleBody(".ui-toolbar")).toContain("flex-wrap: wrap");
  });

  it("can push its controls to the end of the row", () => {
    const { container } = render(<Toolbar align="end">x</Toolbar>);
    expect(container.querySelector(".ui-toolbar")).toHaveClass("ui-toolbar--end");
  });

  it("takes an accessible label when it is a real toolbar of controls", () => {
    render(<Toolbar label="Report actions">x</Toolbar>);
    expect(screen.getByRole("toolbar", { name: "Report actions" })).toBeInTheDocument();
  });
});
