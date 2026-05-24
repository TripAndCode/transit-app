import { useTranslation } from "react-i18next";

type Props = { service_split: Record<string, number> };

export function ServiceSplit({ service_split }: Props) {
  const { t } = useTranslation();
  const keys = Object.keys(service_split);
  if (keys.length === 0) return null;
  return (
    <div>
      <div className="ov-label">{t("overview.section_service_split")}</div>
      <div className="ov-svc">
        {keys.map((k) => (
          <div key={k}>
            <div className="ov-sub" style={{ fontSize: 12, marginBottom: 4 }}>
              {t(`overview.service_split_label.${k}`, { defaultValue: k })}
            </div>
            <div>
              <span className="ov-svc-num ov-anim-fade">
                {service_split[k].toFixed(1)}
                {t("overview.hero_unit_min")}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
