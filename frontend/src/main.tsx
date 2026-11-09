import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
// Bundled locally rather than loaded from Google Fonts, which would send visitors' IP addresses to Google.
import "@fontsource/atkinson-hyperlegible/latin-400.css";
import "@fontsource/atkinson-hyperlegible/latin-700.css";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
