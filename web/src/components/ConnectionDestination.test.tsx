// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ConnectionDestination } from "./ConnectionDestination";

let root: Root;
let container: HTMLDivElement;
const choose = vi.fn();

function Harness({ value }: { value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Connect</button>
      {open && (
        <ConnectionDestination
          source="database.endpoint"
          target="api"
          property="settings"
          value={value}
          onChoose={(path) => {
            choose(path);
            setOpen(false);
          }}
          onCancel={() => setOpen(false)}
        />
      )}
    </>
  );
}
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  choose.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

async function open(value: unknown) {
  await act(async () => root.render(<Harness value={value} />));
  await act(async () => container.querySelector("button")!.click());
}
async function click(text: string) {
  await act(async () =>
    [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')]
      .find((button) => button.textContent === text)!
      .click(),
  );
}
async function select(label: string, value: string) {
  const input = [...document.querySelectorAll("label")]
    .find((item) => item.textContent?.startsWith(label))!
    .querySelector("select")!;
  await act(async () => {
    input.value = value;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
async function radio(text: string) {
  await act(async () =>
    [...document.querySelectorAll("label")]
      .find((item) => item.textContent?.includes(text))!
      .querySelector<HTMLInputElement>('input[type="radio"]')!
      .click(),
  );
}

it("previews list append and creates one connection only after submission", async () => {
  await open(["keep"]);
  expect(
    document.querySelector('[aria-label="Connection preview"]')?.textContent,
  ).toContain("Item 2");
  expect(choose).not.toHaveBeenCalled();
  await click("Add item");
  expect(choose).toHaveBeenCalledExactlyOnceWith(["settings", 1]);
});

it("supports nested field replacement with a preview and explicit commit", async () => {
  await open({ database: { url: "old" }, replicas: 3 });
  await select("Destination", '["settings","database","url"]');
  expect(
    document.querySelector('[aria-label="Connection preview"]')?.textContent,
  ).toContain('"old"');
  expect(choose).not.toHaveBeenCalled();
  await click("Replace value");
  expect(choose).toHaveBeenCalledExactlyOnceWith([
    "settings",
    "database",
    "url",
  ]);
});

it("makes whole-input replacement explicit and warns about replacing all items", async () => {
  await open(["first", "second"]);
  await radio("Replace the entire input");
  expect(
    document.querySelector('[aria-label="Connection preview"]')?.textContent,
  ).toContain("including all its current fields or items");
  await click("Replace input");
  expect(choose).toHaveBeenCalledExactlyOnceWith(["settings"]);
});

it("validates new object keys before connecting", async () => {
  await open({ url: "old" });
  await select("Destination", "new");
  const input = document.querySelector<HTMLInputElement>(
    '[role="dialog"] input[type="text"]',
  )!;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )!.set!;
  await act(async () => {
    setter.call(input, "url");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(document.querySelector('[role="alert"]')?.textContent).toContain(
    "already exists",
  );
  expect(
    document.querySelector<HTMLButtonElement>(
      '[role="dialog"] button[type="submit"]',
    )?.disabled,
  ).toBe(true);
  await act(async () => {
    setter.call(input, "new_url");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click("Connect field");
  expect(choose).toHaveBeenCalledExactlyOnceWith(["settings", "new_url"]);
});

it("cancels scalar replacement on Escape without committing", async () => {
  await open("old");
  expect(document.activeElement?.textContent).toBe("Cancel");
  await act(async () =>
    document.activeElement!.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    ),
  );
  expect(choose).not.toHaveBeenCalled();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});
