// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { HelpTip } from "./HelpTip";

it("opens supplementary help on keyboard focus and links the description", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () =>
      root.render(
        <HelpTip label="About refresh">
          Refresh does not apply selections.
        </HelpTip>,
      ),
    );
    const trigger = container.querySelector("button")!;
    expect(trigger.getAttribute("aria-label")).toBe("About refresh");
    await act(async () => trigger.focus());
    expect(document.querySelector('[role="tooltip"]')?.textContent).toContain(
      "Refresh does not apply selections.",
    );
    expect(trigger.getAttribute("aria-describedby")).toBe(
      document.querySelector('[role="tooltip"]')?.id,
    );
    await act(async () =>
      trigger.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      ),
    );
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
  } finally {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  }
});
