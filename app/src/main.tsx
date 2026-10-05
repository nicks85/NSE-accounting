import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { GainsScreen } from "./screens/GainsScreen";
import { ImportScreen } from "./screens/ImportScreen";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App screens={{ import: <ImportScreen />, gains: <GainsScreen /> }} />
  </StrictMode>,
);
