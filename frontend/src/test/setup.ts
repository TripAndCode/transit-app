import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Ensure React Testing Library unmounts components and clears the DOM between
// tests so state never leaks across cases.
afterEach(() => {
  cleanup();
});
