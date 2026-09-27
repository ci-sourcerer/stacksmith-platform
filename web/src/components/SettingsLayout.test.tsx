// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  SettingsPageHeader,
  SettingsPanel,
  SettingsField,
  SettingsFields,
  SettingsActions,
  SettingsButton,
  SettingsFeedback,
} from "./SettingsLayout";
import { useSettingsError } from "../useSettingsError";
import { ApiError } from "../api";

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

it("keeps headings, utility actions, and form submission in their semantic places", async () => {
  const submit = vi.fn();
  const refresh = vi.fn();
  await act(async () =>
    root.render(
      <>
        <h1>Settings</h1>
        <SettingsPageHeader
          title="Example settings"
          description="Configure the example."
          context="acme/service"
        />
        <SettingsPanel
          title="Example form"
          description="Update a value."
          form={{
            onSubmit: (event) => {
              event.preventDefault();
              submit();
            },
          }}
          toolbar={<SettingsButton onClick={refresh}>Refresh</SettingsButton>}
          footer={
            <SettingsActions
              feedback={<SettingsFeedback>Unsaved changes</SettingsFeedback>}
            >
              <SettingsButton type="submit" variant="primary">
                Save
              </SettingsButton>
            </SettingsActions>
          }
        >
          <SettingsFields>
            <SettingsField
              label="Value"
              description="Used by the next run."
              error="Enter a value."
            >
              <input required />
            </SettingsField>
          </SettingsFields>
          <h4>Details</h4>
        </SettingsPanel>
      </>,
    ),
  );
  expect(
    [...container.querySelectorAll("h1,h2,h3,h4")].map(
      (heading) => heading.tagName,
    ),
  ).toEqual(["H1", "H2", "H3", "H4"]);
  expect(container.querySelectorAll("form")).toHaveLength(1);
  expect(container.querySelector("form form")).toBeNull();
  expect(
    container
      .querySelector(".settings-panel-footer .settings-button-primary")
      ?.getAttribute("type"),
  ).toBe("submit");
  const input = container.querySelector("input")!;
  expect(container.querySelector("label")?.htmlFor).toBe(input.id);
  expect(input.getAttribute("aria-invalid")).toBe("true");
  expect(
    input
      .getAttribute("aria-describedby")
      ?.split(" ")
      .map((id) => document.getElementById(id)?.textContent),
  ).toEqual(["Used by the next run.", "Enter a value."]);
  await act(async () =>
    container
      .querySelector<HTMLButtonElement>(".settings-toolbar button")!
      .click(),
  );
  expect(refresh).toHaveBeenCalledOnce();
  expect(submit).not.toHaveBeenCalled();
  input.value = "configured";
  await act(async () =>
    container
      .querySelector<HTMLButtonElement>(".settings-button-primary")!
      .click(),
  );
  expect(submit).toHaveBeenCalledOnce();
});

function ErrorHarness({
  failure,
  onAccessError,
}: {
  failure: unknown;
  onAccessError: (message: string) => void;
}) {
  const { error, reportError } = useSettingsError(onAccessError);
  return (
    <SettingsPanel title="Example">
      <SettingsButton onClick={() => reportError(failure)}>Load</SettingsButton>
      {error && <SettingsFeedback tone="error">{error}</SettingsFeedback>}
    </SettingsPanel>
  );
}

it.each(["primary", "secondary", "text", "danger"] as const)(
  "provides the %s action variant without implicit submission",
  async (variant) => {
    await act(async () =>
      root.render(
        <SettingsButton variant={variant} compact>
          Action
        </SettingsButton>,
      ),
    );
    expect(container.querySelector("button")?.classList).toContain(
      `settings-button-${variant}`,
    );
    expect(container.querySelector("button")?.classList).toContain(
      "settings-button-compact",
    );
    expect(container.querySelector("button")?.type).toBe("button");
  },
);

it.each([
  new Error("Service unavailable"),
  new ApiError("Sign in again", 401),
  new ApiError("Access denied", 403),
])("places request failures correctly: %s", async (failure) => {
  const onAccessError = vi.fn();
  await act(async () =>
    root.render(
      <ErrorHarness failure={failure} onAccessError={onAccessError} />,
    ),
  );
  await act(async () => container.querySelector("button")!.click());
  if (failure instanceof ApiError) {
    expect(onAccessError).toHaveBeenCalledWith(failure.message);
    expect(container.querySelector('[role="alert"]')).toBeNull();
  } else {
    expect(
      container.querySelector('.settings-panel [role="alert"]')?.textContent,
    ).toBe(failure.message);
    expect(onAccessError).not.toHaveBeenCalled();
  }
});
