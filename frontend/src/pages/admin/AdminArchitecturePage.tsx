import { useState } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useArchitectureDoc, useArchitectureDocs } from "../../api/admin";
import { ErrorBanner } from "../../components/ErrorBanner";
import { mermaidMarkdownComponents } from "../../components/MarkdownMermaid";

// Rendered as a Mermaid flowchart rather than prose so it matches the
// approved mockup's data-flow box. This is a MANUAL-SYNC reminder, not an
// enforced one: CLAUDE.md's own "Architecture pointers" section is
// agent-facing prose, not a frontend asset, so there is no way to derive
// this diagram from it at build or run time. If that section's data path,
// DB split, or Ask-routing stages change, update the flowchart below by
// hand to match.
const ARCHITECTURE_DIAGRAM = `\`\`\`mermaid
flowchart LR
  gtfs["gtfs_pipeline.py"] --> analyze["pipeline/analyze.py"]
  analyze --> agg[("agg_* tables (Postgres)")]
  agg --> api["FastAPI routers"]
  api --> spa["React SPA"]

  ch[("ClickHouse: raw GTFS-RT updates")] -->|narrow filters| api
  pg[("Postgres: aggregates, OLTP, PostGIS, pgvector")] -->|default reports| api

  api --> rules["Ask Stage 1: rules"]
  rules --> ann["Ask Stage 2: embedding nearest-neighbour"]
  ann --> rag["Ask Stage 3: RAG LLM (only stage calling an LLM)"]
\`\`\`
`;

/** Developer/internal-only page at \`/admin/architecture\` (item 25): a
 * Mermaid rendering of CLAUDE.md's "Architecture pointers" (part A), plus a
 * sidebar-navigable index of \`docs/features/*.md\` (part B). Reuses the
 * existing \`RequireAdmin\` + \`AdminLayout\` gate exactly like
 * \`/admin/agencies\`, \`/admin/users\`, and \`/admin/ops\` -- no new
 * permission concept, per the explicit decision recorded in NEXT_TASK.md's
 * item 25 to keep \`role=admin\` a single, carefully-scoped concept. */
export function AdminArchitecturePage() {
  const { t } = useTranslation();
  const { data: docs, error: docsError } = useArchitectureDocs();
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  // Defaults to the first doc in the (server-sorted) list once it loads;
  // an explicit sidebar click always overrides that default afterward.
  const activeSlug = selectedSlug ?? docs?.[0]?.slug ?? null;
  const { data: doc, error: docError } = useArchitectureDoc(activeSlug);

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, marginBottom: 20 }}>{t("admin.architecture.title")}</h1>

      <section style={{ marginBottom: 32 }}>
        <h2 style={{ fontSize: 16, marginBottom: 12 }}>{t("admin.architecture.diagram_title")}</h2>
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={mermaidMarkdownComponents}>
          {ARCHITECTURE_DIAGRAM}
        </ReactMarkdown>
      </section>

      <section>
        <h2 style={{ fontSize: 16, marginBottom: 12 }}>{t("admin.architecture.docs_title")}</h2>
        {docsError != null && <ErrorBanner error={docsError} />}
        {docs != null && docs.length === 0 && (
          <p style={{ color: "var(--text-tertiary)" }}>{t("admin.architecture.docs_empty")}</p>
        )}
        {docs != null && docs.length > 0 && (
          <div style={{ display: "flex", gap: 32, alignItems: "flex-start" }}>
            <nav
              aria-label={t("admin.architecture.docs_nav_label")}
              style={{ width: 220, flexShrink: 0 }}
            >
              <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                {docs.map((d) => (
                  <li key={d.slug}>
                    <button
                      type="button"
                      onClick={() => setSelectedSlug(d.slug)}
                      aria-current={d.slug === activeSlug ? "true" : undefined}
                      style={{
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        background: d.slug === activeSlug ? "var(--accent-soft)" : "transparent",
                        border: "none",
                        borderLeft: `3px solid ${d.slug === activeSlug ? "var(--accent)" : "transparent"}`,
                        color: "var(--text-primary)",
                        fontWeight: d.slug === activeSlug ? 600 : 400,
                        fontSize: 13,
                        lineHeight: 1.4,
                        padding: "8px 12px",
                        cursor: "pointer",
                        borderRadius: "var(--radius)",
                      }}
                    >
                      {d.title}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
            <div style={{ flex: 1, minWidth: 0 }}>
              {docError != null && <ErrorBanner error={docError} />}
              {doc != null && (
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={mermaidMarkdownComponents}>
                  {doc.content}
                </ReactMarkdown>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
