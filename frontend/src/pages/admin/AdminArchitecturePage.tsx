import { useState } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useArchitectureDoc, useArchitectureDocs } from "../../api/admin";
import { ErrorBanner } from "../../components/ErrorBanner";
import { mermaidMarkdownComponents } from "../../components/MarkdownMermaid";
import { SidebarNavList } from "../../components/SidebarNavList";

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

/** Developer/internal-only page at \`/admin/architecture\`: a Mermaid
 * rendering of CLAUDE.md's "Architecture pointers" (part A), plus a
 * sidebar-navigable index of \`docs/features/*.md\` (part B). Reuses the
 * existing \`RequireAdmin\` + \`AdminLayout\` gate exactly like
 * \`/admin/agencies\`, \`/admin/users\`, and \`/admin/ops\` -- reusing
 * \`role=admin\` rather than introducing a separate internal/developer
 * permission flag. */
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
            <SidebarNavList
              ariaLabel={t("admin.architecture.docs_nav_label")}
              width={220}
              items={docs.map((d) => ({ key: d.slug, label: d.title }))}
              activeKey={activeSlug}
              onSelect={setSelectedSlug}
            />
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
