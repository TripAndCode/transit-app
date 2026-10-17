import { useEffect, useEffectEvent } from "react";
import { useTranslation } from "react-i18next";
import { onApiKeyRejected } from "../api/client";
import { useToast } from "./ui/toastContext";

/** Says, once, that a stored API key the server rejected was removed. Mounted
 *  outside the auth gate, so it also speaks on the reload where the key first
 *  fails and nothing else of the app has rendered yet. */
export function ApiKeyRejectedNotice() {
  const { t } = useTranslation();
  const toast = useToast();
  const announce = useEffectEvent(() => {
    toast.show(t("header.settings_api_key_rejected"));
  });
  useEffect(() => onApiKeyRejected(announce), []);
  return null;
}
