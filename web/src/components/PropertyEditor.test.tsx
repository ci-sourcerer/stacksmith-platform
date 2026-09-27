// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { PropertyEditor } from "./PropertyEditor";

it("shows nested connections without hiding siblings and disconnects only the nested value", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const change = vi.fn();
  try {
    await act(async () =>
      root.render(
        <PropertyEditor
          inputs={[{ name: "settings" }]}
          properties={{
            settings: { url: '{{ components["data"]["url"] }}', replicas: 3 },
          }}
          relationships={[
            {
              source: "data",
              output: "url",
              target: "app",
              property: "settings",
              path: ["settings", "url"],
              kind: "reference",
              rule: null,
            },
          ]}
          onChange={change}
        />,
      ),
    );
    expect(container.textContent).toContain("Connected to data.url");
    expect(
      container.querySelector('input[type="number"]')?.getAttribute("value"),
    ).toBe("3");
    await act(async () =>
      [...container.querySelectorAll("button")]
        .find(
          (button) =>
            button.getAttribute("aria-label") ===
            'Disconnect ["settings","url"]',
        )!
        .click(),
    );
    expect(change).toHaveBeenCalledWith({ settings: { replicas: 3 } });
  } finally {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  }
});
