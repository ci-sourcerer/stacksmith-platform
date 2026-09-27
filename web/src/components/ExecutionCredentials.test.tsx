// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../api";
import { ExecutionCredentials } from "./ExecutionCredentials";

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  api: vi.fn(),
}));

let root: Root;
let container: HTMLDivElement;
const saved = vi.fn();
const accessError = vi.fn();

beforeEach(() => {
  (
    globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  saved.mockReset();
  accessError.mockReset();
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path, body) => {
    if (path.endsWith("/execution-credential-providers"))
      return {
        providers: [{ name: "production", provider: "aws-assume-role" }],
      } as never;
    if (path.endsWith("/execution-credential-bindings") && !body)
      return {
        bindings: [{ provider: "production", phases: ["plan"] }],
      } as never;
    return { ok: true } as never;
  });
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render() {
  await act(async () =>
    root.render(
      <ExecutionCredentials
        repository="acme/service"
        environment="dev"
        onSaved={saved}
        onError={accessError}
      />,
    ),
  );
}

it("selects an approved identity, edits its phases, saves, and revokes it", async () => {
  await render();
  expect(container.textContent!.indexOf("production")).toBeLessThan(
    container.textContent!.indexOf("Assign an execution identity"),
  );
  const select = container.querySelector("select")!;
  await act(async () => {
    select.value = "production";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(
    container.querySelector<HTMLInputElement>('input[value="plan"]')!.checked,
  ).toBe(true);
  await act(async () =>
    container.querySelector<HTMLInputElement>('input[value="apply"]')!.click(),
  );
  await act(async () =>
    container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(api).toHaveBeenCalledWith(
    "/v1/repositories/acme/service/environments/dev/execution-credential-bindings",
    { provider: "production", phases: ["plan", "apply"] },
  );
  expect(saved).toHaveBeenCalled();
  await act(async () =>
    Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === "Revoke execution identity")!
      .click(),
  );
  expect(api).toHaveBeenCalledWith(
    "/v1/repositories/acme/service/environments/dev/execution-credential-bindings/production/remove",
    {},
  );
});

it("shows an empty identity list before the assignment form", async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.endsWith("/execution-credential-providers"))
      return {
        providers: [{ name: "production", provider: "aws-assume-role" }],
      } as never;
    return { bindings: [] } as never;
  });
  await render();
  expect(container.textContent).toContain(
    "No execution identities assigned yet.",
  );
  expect(
    container.querySelector(".credential-form-heading h4")?.textContent,
  ).toBe("Assign an execution identity");
});

it("reports access failures without treating them as an empty catalog", async () => {
  vi.mocked(api).mockRejectedValue(new ApiError("Access revoked", 403));
  await render();
  expect(accessError).toHaveBeenCalledWith("Access revoked");
});

it("keeps saving disabled until an identity and phase are selected", async () => {
  await render();
  expect(
    container.querySelector<HTMLButtonElement>('button[type="submit"]')!
      .disabled,
  ).toBe(true);
  const select = container.querySelector("select")!;
  await act(async () => {
    select.value = "production";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () =>
    container.querySelector<HTMLInputElement>('input[value="plan"]')!.click(),
  );
  expect(
    container.querySelector<HTMLButtonElement>('button[type="submit"]')!
      .disabled,
  ).toBe(true);
});
