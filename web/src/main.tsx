import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./PlatformApp";
import { NotificationProvider } from "./components/ErrorBanner";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <NotificationProvider>
      <App />
    </NotificationProvider>
  </StrictMode>,
);
