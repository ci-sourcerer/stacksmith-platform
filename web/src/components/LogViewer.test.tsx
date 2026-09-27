// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { LogViewer } from "./LogViewer";

it("toggles prefixes and searches the displayed, filtered live output", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const logs =
    "[plan]\n20:53:27.337 INFO   tofu: guidance\n20:53:30.810 STDERR tofu: Error: missing credentials";
  await act(async () => root.render(<LogViewer logs={logs} />));
  const output = container.querySelector("pre")!;
  const controls = [...container.querySelectorAll("label")];
  const prefixes = controls
    .find((label) => label.textContent?.includes("Show log prefixes"))!
    .querySelector("input")!;
  const issues = controls
    .find((label) => label.textContent?.includes("Warnings and errors only"))!
    .querySelector("input")!;
  expect(output.textContent).toBe(
    "[plan]\nguidance\nError: missing credentials",
  );
  await act(async () => prefixes.click());
  expect(output.textContent).toBe(logs);
  await act(async () => issues.click());
  expect(output.textContent).toBe(
    "20:53:30.810 STDERR tofu: Error: missing credentials",
  );
  expect(container.textContent).toContain("Showing 1 of 3 lines");
  await act(async () => prefixes.click());
  expect(output.textContent).toBe("Error: missing credentials");
  const search = container.querySelector('input[type="search"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(search, "credentials");
    search.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(container.textContent).toContain("1 of 1");
  expect(output.querySelector("mark")?.textContent).toBe("credentials");
  await act(async () =>
    root.render(
      <LogViewer logs={`${logs}\n20:53:31.000 ERROR  another error`} />,
    ),
  );
  expect(output.textContent).toContain("another error");
  expect(container.textContent).toContain("Showing 2 of 4 lines");
  await act(async () => root.unmount());
  container.remove();
  delete (HTMLElement.prototype as unknown as { scrollIntoView?: unknown })
    .scrollIntoView;
  vi.unstubAllGlobals();
});
