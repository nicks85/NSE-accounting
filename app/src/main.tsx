import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { GainsScreen } from "./screens/GainsScreen";
import { HoldingsScreen } from "./screens/HoldingsScreen";
import { ImportScreen } from "./screens/ImportScreen";
import { LossesScreen } from "./screens/LossesScreen";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App
      screens={{
        import: <ImportScreen />,
        holdings: <HoldingsScreen />,
        gains: <GainsScreen />,
        losses: <LossesScreen />,
      }}
    />
  </StrictMode>,
);
