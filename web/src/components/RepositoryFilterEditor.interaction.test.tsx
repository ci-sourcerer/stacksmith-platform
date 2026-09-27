// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { RepositoryFilterEditor } from "./RepositoryFilterEditor";

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  api: vi.fn(),
}));
let container: HTMLDivElement;
let root: Root;
const onSaved = vi.fn();
const onError = vi.fn();
const rules = {
  properties: { stacksmith: "stack" },
  topics: ["stacksmith-stack"],
};

beforeEach(() => {
  vi.resetAllMocks();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  vi.mocked(api).mockImplementation(async (path, body) => {
    if (path === "/github-connection")
      return { installations: [{ account_type: "User" }] } as never;
    if (path === "/v1/repository-filter") return (body ?? rules) as never;
    if (path === "/v1/repositories/refresh") return {} as never;
    throw new Error(`Unexpected request: ${path}`);
  });
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render() {
  await act(async () =>
    root.render(<RepositoryFilterEditor onSaved={onSaved} onError={onError} />),
  );
}
async function click(text: string) {
  await act(async () =>
    Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === text)!
      .click(),
  );
}
async function changeTopic(value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(container.querySelector("input"), value);
    container
      .querySelector("input")!
      .dispatchEvent(new Event("input", { bubbles: true }));
  });
}

it("preserves unavailable property rules and supports topic add/remove and save", async () => {
  await render();
  expect(container.querySelectorAll("fieldset > legend")).toHaveLength(2);
  expect(container.querySelector('[name="property_name"]')).toBeNull();
  await click("Add topic");
  expect(container.querySelectorAll("input")).toHaveLength(2);
  await act(async () =>
    container
      .querySelector<HTMLButtonElement>('[aria-label="Remove topic 2"]')!
      .click(),
  );
  await changeTopic("updated-topic");
  await click("Save filter");
  expect(api).toHaveBeenCalledWith(
    "/v1/repository-filter",
    { properties: rules.properties, topics: ["updated-topic"] },
    undefined,
    undefined,
    "PUT",
  );
  expect(container.textContent).toContain("Repository filter saved.");
  expect(onSaved).toHaveBeenCalledOnce();
});

it("refreshes saved rules without submitting or discarding draft changes, including after failure", async () => {
  await render();
  await changeTopic("unsaved-topic");
  vi.mocked(api).mockRejectedValueOnce(new Error("Refresh unavailable"));
  await click("Refresh repositories");
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(
    "Refresh unavailable",
  );
  expect(container.querySelector("input")?.value).toBe("unsaved-topic");
  expect(onSaved).not.toHaveBeenCalled();
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([, body]) => body && "topics" in (body as object)),
  ).toHaveLength(0);
  await click("Refresh repositories");
  expect(container.querySelector('[role="alert"]')).toBeNull();
  expect(container.textContent).toContain(
    "Repositories refreshed using saved rules.",
  );
  expect(container.querySelector("input")?.value).toBe("unsaved-topic");
  expect(onSaved).toHaveBeenCalledOnce();
});

it.each(["Save filter", "Refresh repositories"])(
  "disables competing actions during %s",
  async (action) => {
    await render();
    let resolve!: (value: unknown) => void;
    vi.mocked(api).mockImplementationOnce(
      () =>
        new Promise((complete) => {
          resolve = complete;
        }),
    );
    await click(action);
    expect(container.textContent).toContain(
      action === "Save filter" ? "Saving…" : "Refreshing…",
    );
    expect(
      Array.from(container.querySelectorAll("button")).every(
        (button) => button.disabled || button.closest("fieldset[disabled]"),
      ),
    ).toBe(true);
    await act(async () => resolve(rules));
    expect(
      container.querySelector<HTMLButtonElement>(".settings-actions button")
        ?.disabled,
    ).toBe(false);
  },
);

it("reports save failure and allows retry without losing edits", async () => {
  await render();
  await changeTopic("retry-topic");
  vi.mocked(api).mockRejectedValueOnce(new Error("Save unavailable"));
  await click("Save filter");
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(
    "Save unavailable",
  );
  expect(onError).not.toHaveBeenCalled();
  expect(container.querySelector("input")?.value).toBe("retry-topic");
  await click("Save filter");
  expect(container.textContent).toContain("Repository filter saved.");
});

it("reports load failure instead of leaving a permanent loading message", async () => {
  vi.mocked(api).mockRejectedValueOnce(new Error("Filters unavailable"));
  await render();
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(
    "Filters unavailable",
  );
  expect(container.textContent).not.toContain("Loading");
});
