import type { ReactElement } from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "../i18n";

// Force a deterministic language for assertions. The language detector would
// otherwise pick up the host environment; tests assert against English copy.
void i18n.changeLanguage("en");

/**
 * Renders a component tree wrapped in the real i18n provider and a fresh
 * React Query client so that `useTranslation()` and data hooks resolve. A new
 * client is created per render to keep query caches isolated between tests.
 * Returns the standard Testing Library result.
 */
export function renderWithProviders(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      // No retries/refetches in tests — keep behaviour deterministic.
      queries: { retry: false, gcTime: 0 },
    },
  });
  return render(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>
        <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
      </QueryClientProvider>
    ),
    ...options,
  });
}
