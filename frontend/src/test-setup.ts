// Vitest + jsdom setup for the console frontend.
// @testing-library/react renders into jsdom automatically; no explicit
// afterEach cleanup is needed for React 19 (it uses createRoot).
import "@testing-library/react";
