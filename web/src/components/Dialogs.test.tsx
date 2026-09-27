// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ConfirmationProvider, useConfirmation } from "./Dialogs";

let root: Root;
let container: HTMLDivElement;
const results: boolean[] = [];

function Harness() {
  const { confirm, cancel } = useConfirmation();
  return (
    <>
      <button
        onClick={async () => {
          results.push(
            await confirm({
              title: "Discard saved draft?",
              description: "Download recovery first.",
              actionLabel: "Discard saved draft",
              destructive: true,
            }),
          );
        }}
      >
        Ask
      </button>
      <button onClick={cancel}>Invalidate</button>
      <button>Outside</button>
    </>
  );
}

beforeEach(async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  results.length = 0;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <ConfirmationProvider>
        <Harness />
      </ConfirmationProvider>,
    ),
  );
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

async function ask() {
  const button = container.querySelector("button")!;
  button.focus();
  await act(async () => button.click());
}
async function click(label: string) {
  await act(async () =>
    [
      ...document.querySelectorAll<HTMLButtonElement>(
        '[role="alertdialog"] button',
      ),
    ]
      .find((button) => button.textContent === label)!
      .click(),
  );
}

it("focuses Cancel, describes the action, and only accepts the explicit action", async () => {
  await ask();
  expect(document.activeElement?.textContent).toBe("Cancel");
  const dialog = document.querySelector('[role="alertdialog"]')!;
  expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();
  expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
  await click("Discard saved draft");
  expect(results).toEqual([true]);
  expect(document.querySelector('[role="alertdialog"]')).toBeNull();
});

it("cancels on Escape, contains focus, and restores the triggering button", async () => {
  await ask();
  await act(async () => container.querySelectorAll("button")[2].focus());
  expect(
    document
      .querySelector('[role="alertdialog"]')
      ?.contains(document.activeElement),
  ).toBe(true);
  await act(async () =>
    document.activeElement!.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    ),
  );
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  expect(results).toEqual([false]);
  expect(document.activeElement?.textContent).toBe("Ask");
});

it("cancels on invalidation without leaving unresolved callers", async () => {
  await ask();
  await act(async () => container.querySelectorAll("button")[1].click());
  expect(results).toEqual([false]);
});

it("cancels unresolved confirmations on provider unmount", async () => {
  await ask();
  await act(async () => root.render(<div />));
  expect(results).toEqual([false]);
});
