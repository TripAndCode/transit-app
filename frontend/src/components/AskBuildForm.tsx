import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useAskBuildSchema } from "../api/hooks";
import type { BuildField, BuildTool, IntentSignature } from "../api/types";
import { RoutePicker } from "./RoutePicker";

type Props = {
  agencyId: number;
  initialValue?: IntentSignature | null;
  onSubmit: (tool: string, args: Record<string, unknown>) => void;
  onCancel?: () => void;
};

type Locale = "ja" | "en";

function defaultsForTool(tool: BuildTool): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const field of tool.fields) {
    if (field.default !== undefined) {
      result[field.key] = field.default;
    } else {
      switch (field.type) {
        case "enum":
          result[field.key] = field.options[0] ?? "";
          break;
        case "int":
          result[field.key] = field.min ?? 0;
          break;
        case "bool":
          result[field.key] = false;
          break;
        case "string":
          result[field.key] = "";
          break;
      }
    }
  }
  return result;
}

function FieldInput({
  field,
  value,
  onChange,
  t,
}: {
  field: BuildField;
  value: unknown;
  onChange: (val: unknown) => void;
  t: (key: string, opts?: { defaultValue: string }) => string;
}) {
  switch (field.type) {
    case "enum": {
      const strValue = (value as string) ?? field.options[0] ?? "";
      return (
        <select
          value={strValue}
          onChange={(e) => onChange(e.target.value)}
          style={{ width: "100%" }}
        >
          {field.options.map((opt) => (
            <option key={opt} value={opt}>
              {t(`ask.build_labels.values.${opt}`, { defaultValue: opt })}
            </option>
          ))}
        </select>
      );
    }
    case "int": {
      const numValue = value as number;
      return (
        <input
          type="number"
          value={numValue}
          min={field.min}
          max={field.max}
          onChange={(e) => onChange(Number(e.target.value))}
          onBlur={(e) => {
            let v = Number(e.target.value);
            if (field.min !== undefined && v < field.min) v = field.min;
            if (field.max !== undefined && v > field.max) v = field.max;
            onChange(v);
          }}
          style={{ width: "100%" }}
        />
      );
    }
    case "bool": {
      const boolValue = value as boolean;
      return (
        <input
          type="checkbox"
          checked={boolValue}
          onChange={(e) => onChange(e.target.checked)}
          style={{ width: "auto", marginTop: 4 }}
        />
      );
    }
    case "string": {
      const strValue = (value as string) ?? "";
      return (
        <input
          type="text"
          value={strValue}
          onChange={(e) => onChange(e.target.value)}
          style={{ width: "100%" }}
        />
      );
    }
    default: {
      return (
        <input
          type="text"
          disabled
          placeholder="(unsupported)"
          style={{ width: "100%", opacity: 0.5 }}
        />
      );
    }
  }
}

export function AskBuildForm({ agencyId, initialValue, onSubmit, onCancel }: Props) {
  const { t, i18n } = useTranslation();
  const locale = ((i18n.resolvedLanguage ?? "ja") === "en" ? "en" : "ja") as Locale;

  const { data: schema, isLoading, isError } = useAskBuildSchema(agencyId);

  const firstToolName = schema?.tools[0]?.name ?? "";
  const initToolName = initialValue?.tool ?? firstToolName;

  const [selectedToolName, setSelectedToolName] = useState<string>(initToolName);
  const [values, setValues] = useState<Record<string, unknown>>({});

  // Once the schema loads, initialize selectedToolName + values
  useEffect(() => {
    if (!schema) return;
    const tools = schema.tools;
    if (tools.length === 0) return;

    const targetTool = initialValue?.tool
      ? tools.find((tool) => tool.name === initialValue.tool)
      : null;
    const resolvedTool = targetTool ?? tools[0];

    setSelectedToolName(resolvedTool.name);

    if (initialValue?.tool === resolvedTool.name && initialValue.args) {
      // Pre-populate from initialValue, filling in any missing keys from defaults
      setValues({ ...defaultsForTool(resolvedTool), ...initialValue.args });
    } else {
      setValues(defaultsForTool(resolvedTool));
    }
    // Only run when schema becomes available or initialValue changes (not on every render)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema, initialValue]);

  function handleToolChange(name: string) {
    if (!schema) return;
    const tool = schema.tools.find((tool) => tool.name === name);
    if (!tool) return;
    setSelectedToolName(name);
    // Always reset values on manual tool switch
    setValues(defaultsForTool(tool));
  }

  function handleFieldChange(key: string, val: unknown) {
    setValues((prevValues) => ({ ...prevValues, [key]: val }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Drop empty-string values so optional fields (e.g. ``route``) are
    // omitted from the args dict — sending ``""`` vs. nothing produces
    // different canonical hashes server-side and silently mis-routes some
    // tools (route_stats with empty route was dispatching describe_data).
    const cleaned: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(values)) {
      if (v === "" || v === null || v === undefined) continue;
      cleaned[k] = v;
    }
    onSubmit(selectedToolName, cleaned);
  }

  if (isLoading) {
    return (
      <p style={{ color: "var(--text-secondary)", fontSize: 14 }}>
        {t("ask.build.loading")}
      </p>
    );
  }

  if (isError || !schema) {
    return (
      <p
        role="alert"
        style={{
          background: "var(--error-bg)",
          color: "var(--error-fg)",
          border: "1px solid #f0e2b6",
          padding: "10px 14px",
          borderRadius: "var(--radius)",
          fontSize: 14,
        }}
      >
        {t("ask.build.error")}
      </p>
    );
  }

  const activeTool: BuildTool | undefined = schema.tools.find(
    (tool) => tool.name === selectedToolName,
  );

  return (
    <form onSubmit={handleSubmit} noValidate>
      {/* Tool selector */}
      <div style={{ marginBottom: "var(--space-4)" }}>
        <label
          style={{
            display: "block",
            fontSize: 12,
            color: "var(--text-secondary)",
            marginBottom: "var(--space-1)",
          }}
        >
          {t("ask.build.tool")}
        </label>
        <select
          value={selectedToolName}
          onChange={(e) => handleToolChange(e.target.value)}
          style={{ width: "100%" }}
        >
          {schema.tools.map((tool) => (
            <option key={tool.name} value={tool.name}>
              {locale === "en" ? tool.label_en : tool.label_ja}
            </option>
          ))}
        </select>
      </div>

      {/* Fields grid */}
      {activeTool && activeTool.fields.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: "var(--space-3) var(--space-4)",
            marginBottom: "var(--space-4)",
          }}
          className="ask-build-grid"
        >
          {activeTool.fields.map((field) => {
            const labelBase = t(`ask.build_labels.${field.key}`, { defaultValue: field.key });
            const optionalSuffix = field.optional
              ? ` (${t("ask.build_labels.optional", { defaultValue: "任意" })})`
              : "";
            const label = labelBase + optionalSuffix;
            const isRoute = field.key === "route_id" || field.key === "route";
            return (
              <div key={field.key}>
                <label
                  style={{
                    display: "block",
                    fontSize: 12,
                    color: "var(--text-secondary)",
                    marginBottom: "var(--space-1)",
                  }}
                >
                  {label}
                </label>
                {isRoute ? (
                  <RoutePicker
                    agencyId={agencyId}
                    value={(values[field.key] as string | null) ?? null}
                    onChange={(route_id) => handleFieldChange(field.key, route_id ?? "")}
                  />
                ) : (
                  <FieldInput
                    field={field}
                    value={values[field.key]}
                    onChange={(val) => handleFieldChange(field.key, val)}
                    t={t}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Action buttons */}
      <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
        <button
          type="submit"
          style={{
            background: "var(--accent)",
            color: "#ffffff",
            border: "none",
            borderRadius: "var(--radius)",
            padding: "7px 18px",
            fontSize: 14,
            fontWeight: 600,
            cursor: "pointer",
            transition: "opacity var(--transition)",
          }}
        >
          ▶ {t("ask.build.run")}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            style={{
              background: "transparent",
              color: "var(--text-secondary)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius)",
              padding: "6px 14px",
              fontSize: 14,
              cursor: "pointer",
              transition: "background var(--transition)",
            }}
          >
            {t("ask.build.cancel")}
          </button>
        )}
      </div>

      {/* Responsive 1-col on narrow screens */}
      <style>{`
        @media (max-width: 480px) {
          .ask-build-grid {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
    </form>
  );
}
