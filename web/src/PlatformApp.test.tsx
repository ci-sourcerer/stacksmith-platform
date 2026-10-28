// @vitest-environment jsdom
import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import PlatformApp from "./PlatformApp";
import { api, ApiError } from "./api";
import { workspaceKey } from "./workspaceSelection";
import { runTargetKey } from "./runSelection";
import { knownGraph } from "./useAuthoredGraph";
import { relationshipKey } from "./model";
import type { Components, Platform, Relationship } from "./types";
import { recoveryKey, recoveryRecord } from "./recovery";
import { createDraftHistory } from "./history";
import type { Snapshot } from "./types";

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  api: vi.fn(),
}));
vi.mock("./components/Canvas", () => ({
  Canvas: ({
    components,
    relationships,
    onConnect,
    onUndo,
    onRedo,
    onSelect,
    onSelectRelationship,
  }: {
    components: Components;
    relationships: Relationship[];
    onConnect: (connection: {
      source: string;
      target: string;
      sourceHandle: string;
      targetHandle: string;
    }) => void;
    onUndo: () => void;
    onRedo: () => void;
    onSelect: (name: string | null) => void;
    onSelectRelationship: (key: string) => void;
  }) => (
    <>
      {Object.keys(components).map((name) => (
        <button key={name} onClick={() => onSelect(name)}>
          Select component {name}
        </button>
      ))}
      <div data-testid="draft-components">
        {Object.keys(components).join(",")}
      </div>
      <pre data-testid="draft-properties">{JSON.stringify(components)}</pre>
      <pre data-testid="draft-edges">{JSON.stringify(relationships)}</pre>
      {relationships.map((edge) => (
        <button
          key={relationshipKey(edge)}
          aria-label={`Select connection ${edge.source}.${edge.output} to ${edge.target}.${edge.path.join(".")}`}
          onClick={() => onSelectRelationship(relationshipKey(edge))}
        >
          Select {edge.kind} connection
        </button>
      ))}
      <button
        onClick={() =>
          onConnect({
            source: "data",
            target: "app",
            sourceHandle: "out:url",
            targetHandle: "in:url",
          })
        }
      >
        Test connect scalar
      </button>
      <button
        onClick={() =>
          onConnect({
            source: "data",
            target: "app",
            sourceHandle: "out:url",
            targetHandle: "in:settings",
          })
        }
      >
        Test connect nested
      </button>
      <button onClick={onUndo}>Test undo</button>
      <button onClick={onRedo}>Test redo</button>
    </>
  ),
}));

let root: Root;
let container: HTMLDivElement;
let execution: boolean;
let administrator: boolean;
let login: string;
let repositories: string[];
let paths: string[];
let mode: "github" | "demo";
let signedIn: boolean;
let capabilities: string[];
let initialComponents: Components;
let initialDisabled: string[];
let environmentNames: string[];
let associationRules: Platform["associations"];
let reviewRelationships: Relationship[];
const catalog = {
  service: {
    display_name: "Service",
    inputs: [
      { name: "url" },
      { name: "settings", type: "object({ url = string, replicas = number })" },
    ],
    outputs: [{ name: "url" }],
    module_source: "",
    module_version: "1",
  },
};
const workspace = {
  repository: "acme/service",
  path: "stack.yaml",
  environment: "dev",
};

class MemoryStorage {
  values = new Map<string, string>();
  getItem(key: string) {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
  removeItem(key: string) {
    this.values.delete(key);
  }
  clear() {
    this.values.clear();
  }
}

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("localStorage", new MemoryStorage());
  vi.stubGlobal("sessionStorage", new MemoryStorage());
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: () => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
    }),
  });
  vi.spyOn(window, "confirm").mockImplementation(() => {
    throw new Error("Browser confirmations must not be used");
  });
  localStorage.clear();
  sessionStorage.clear();
  mode = "github";
  signedIn = true;
  capabilities = [
    "configure_environment",
    "manage_credentials",
    "configure_repository",
    "plan_infrastructure",
  ];
  execution = true;
  administrator = true;
  login = "operator";
  repositories = ["acme/service", "acme/other"];
  paths = ["stack.yaml", "other.stack.yaml"];
  initialComponents = {};
  initialDisabled = [];
  environmentNames = ["dev"];
  associationRules = [];
  reviewRelationships = [];
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path, body) => {
    if (path.endsWith("/execution-credential-providers"))
      return { providers: [] } as never;
    if (path.endsWith("/execution-credential-bindings"))
      return { bindings: [] } as never;
    if (path === "/session")
      return {
        mode,
        login: mode === "demo" || !signedIn ? null : login,
        execution_enabled: execution,
      } as never;
    if (path === "/v1/me")
      return { administrator, provisioner: administrator, login } as never;
    if (path === "/repository-discovery")
      return {
        repositories: repositories.map((name) => ({ name, writable: true })),
      } as never;
    if (path === "/v1/repositories")
      return repositories.map((id) => ({
        id,
        deployment_enabled: true,
      })) as never;
    if (path.endsWith("/deployment-configuration"))
      return {
        deployment_enabled: true,
        settings: environmentNames.length
          ? {
              gitops_root: "",
              discovery_mode: "auto",
              resolved_discovery_mode: "env-files",
              lock_key: "",
              locked: false,
              automatic: true,
            }
          : null,
        inconsistent: false,
        environments: environmentNames.map((name) => ({
          name,
          active: true,
          revision: 1,
        })),
      } as never;
    if (path.endsWith("/deployment-discovery"))
      return {
        revision: "a".repeat(40),
        deployments: environmentNames.length
          ? [
              {
                gitops_root: "",
                requested_discovery_mode: "auto",
                discovery_mode: "env-files",
                all_environments: environmentNames,
                matrix: environmentNames.map((environment) => ({
                  environment,
                  runfile: "common/stacksmith.yaml",
                })),
                source_lockfiles: {
                  status: "missing",
                  present: [],
                  missing: ["common/stacksmith.lock.yaml"],
                  missing_environments: environmentNames,
                },
              },
            ]
          : [],
      } as never;
    if (path.endsWith("/stacks")) return { paths } as never;
    if (path === "/sample" || path.includes("/stack?"))
      return {
        snapshot: "snapshot",
        source: "components: {}",
        repository: path === "/sample" ? null : workspace.repository,
        path:
          new URLSearchParams(path.split("?")[1]).get("path") ?? "stack.yaml",
        branch: "main",
        head: "a".repeat(40),
        editable: true,
        document: {
          name: "Test stack",
          components: initialComponents,
          disabled_associations: initialDisabled,
        },
      } as never;
    if (path.endsWith("/environments"))
      return environmentNames.map((name) => ({
        id: name,
        name,
        can_inspect: capabilities.includes("plan_infrastructure"),
      })) as never;
    if (path.startsWith("/inspection-targets?"))
      return {
        targets: environmentNames.map((name) => ({
          name,
          can_inspect: capabilities.includes("plan_infrastructure"),
        })),
        availability: !environmentNames.length
          ? "unconfigured"
          : capabilities.includes("plan_infrastructure")
            ? "available"
            : "forbidden",
        can_configure: capabilities.includes("configure_environment"),
      } as never;
    if (path.includes("/access?"))
      return { capabilities, administrator, assignments: [] } as never;
    if (path === "/catalog" && body) return { catalog } as never;
    if (path === "/graph")
      return {
        edges: knownGraph(
          (body as { components: Components }).components,
          new Map(),
        ),
        diagnostics: [],
      } as never;
    if (path.startsWith("/catalog")) return catalog as never;
    if (path.startsWith("/platform"))
      return {
        description: "Test platform",
        scope: "repository",
        source: "managed",
        backend: { type: "remote" },
        tools: {},
        providers: [],
        components: [],
        associations: associationRules,
        policies: [],
        required_input_sets: [],
      } as never;
    if (path === "/github-connection")
      return {
        install_url: "https://github.com/apps/test/installations/new",
        installations: [],
      } as never;
    if (path === "/v1/repository-filter")
      return { mode: "all", topics: [], properties: [] } as never;
    if (path === "/review")
      return {
        catalog,
        source: "",
        diff: "",
        edges: reviewRelationships,
        associations: { rules: [] },
        validation: "Valid",
        context: {
          mode: (body as { environment: string | null }).environment
            ? "deployment"
            : "stack_preview",
          environment: (body as { environment: string | null }).environment,
          environment_revision: 1,
          repository: workspace.repository,
          revision: "a".repeat(40),
          path: "stack.yaml",
          config_revision: "config-revision",
        },
      } as never;
    if (path === "/source") return { source: "", diff: "" } as never;
    if (path === "/v1/tokens" && body) return { token: "test-token" } as never;
    if (path.startsWith("/v1/tokens?"))
      return {
        items: [],
        total: 0,
        page: 1,
        page_size: 20,
        counts: { active: 0, expired: 0, revoked: 0 },
      } as never;
    if (path.startsWith("/v1/runs?"))
      return { items: [], total: 0, page: 1, page_size: 20 } as never;
    if (path.startsWith("/v1/audit?"))
      return { items: [], total: 0, page: 1, page_size: 20 } as never;
    throw new Error(`Unexpected API request: ${path}`);
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function render(path = "/settings") {
  window.history.replaceState(null, "", path);
  await act(async () => root.render(<PlatformApp />));
}

async function inspect() {
  await act(async () => {
    if (vi.isFakeTimers()) vi.advanceTimersByTime(450);
    else await new Promise((resolve) => setTimeout(resolve, 450));
  });
}

async function click(selector: string, text?: string) {
  const element = [...document.querySelectorAll<HTMLElement>(selector)].find(
    (item) =>
      !item.closest("[hidden]") &&
      (text === undefined || item.textContent?.includes(text)),
  );
  expect(element, `${selector}: ${text ?? ""}`).toBeTruthy();
  await act(async () => element!.click());
}

async function choose(label: string, value: string) {
  const select = [...document.querySelectorAll("label")]
    .find((item) => item.textContent?.startsWith(label))
    ?.querySelector("select");
  expect(select).toBeTruthy();
  await act(async () => {
    select!.value = value;
    select!.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function mockRunExecution() {
  const original = vi.mocked(api).getMockImplementation()!;
  const run = {
    id: "run-test",
    repository: "acme/service",
    environment: "dev",
    revision: "a".repeat(40),
    action: "plan",
    status: "succeeded",
    phase: 1,
    actor: login,
    created: 1,
    updated: 2,
    expires: null,
    result: {},
    spec: { phases: ["prepare", "plan"], settings: {} },
  };
  vi.mocked(api).mockImplementation(async (...args) => {
    const [path] = args;
    if (path === "/v1/runs" || path === "/v1/runs/run-test")
      return run as never;
    if (path === "/v1/runs/run-test/artifacts") return [] as never;
    if (path.endsWith("/operations"))
      return { operations: [{ name: "deploy", stacks: ["app"] }] } as never;
    return original(...args);
  });
}

it("mounts and removes a single operations selector on repeated toggles", async () => {
  capabilities.push("plan_operations");
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].endsWith("/operations")) return { operations: [] } as never;
    return original(...args);
  });
  const errors = vi.spyOn(console, "error");
  await render("/runs");
  expect(
    vi.mocked(api).mock.calls.filter(([path]) => path.endsWith("/operations")),
  ).toHaveLength(1);
  for (let index = 0; index < 3; index++) {
    await click('input[name="include_operations"]');
    expect(document.querySelectorAll(".operation-selector")).toHaveLength(1);
    expect(document.body.textContent).toContain("No operations are declared");
    expect(document.querySelector(".platform-run-form")?.textContent).toContain(
      "No operations selected.",
    );
    await click('input[name="include_operations"]');
    expect(document.querySelectorAll(".operation-selector")).toHaveLength(0);
    expect(document.body.textContent).not.toContain("Choose stack operations");
  }
  expect(
    vi.mocked(api).mock.calls.filter(([path]) => path.endsWith("/operations")),
  ).toHaveLength(1);
  expect(errors.mock.calls.flat().join(" ")).not.toContain("same key");
});

it("waits for background operation discovery before allowing an operations run", async () => {
  capabilities.push("plan_operations");
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const original = vi.mocked(api).getMockImplementation()!;
  let resolveOperations!: (value: unknown) => void;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].endsWith("/operations"))
      return new Promise((resolve) => {
        resolveOperations = resolve;
      }) as never;
    return original(...args);
  });
  await render("/runs");
  await click('input[name="include_operations"]');
  expect(document.body.textContent).toContain("Discovering operations");
  expect(
    document.querySelector<HTMLButtonElement>(".run-start-button")?.disabled,
  ).toBe(true);
  await act(async () => resolveOperations({ operations: [] }));
  expect(document.body.textContent).toContain("No operations are declared");
  expect(
    document.querySelector<HTMLButtonElement>(".run-start-button")?.disabled,
  ).toBe(false);
});

it("ignores discovery results for an old target and retries background failures", async () => {
  capabilities.push("plan_operations");
  environmentNames = ["dev", "prod"];
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const original = vi.mocked(api).getMockImplementation()!;
  let resolveOld!: (value: unknown) => void;
  let prodCalls = 0;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].includes("/environments/dev/operations"))
      return new Promise((resolve) => {
        resolveOld = resolve;
      }) as never;
    if (args[0].includes("/environments/prod/operations")) {
      prodCalls++;
      if (prodCalls === 1) throw new Error("Discovery unavailable");
      return {
        operations: [{ name: "deploy-prod", stacks: ["app"] }],
      } as never;
    }
    return original(...args);
  });
  await render("/runs");
  await choose("Environment", "prod");
  await act(async () =>
    resolveOld({ operations: [{ name: "stale", stacks: ["app"] }] }),
  );
  expect(document.body.textContent).not.toContain("Discovery unavailable");
  await click('input[name="include_operations"]');
  expect(document.body.textContent).toContain("Could not load operations");
  expect(
    document.querySelector<HTMLButtonElement>(".run-start-button")?.disabled,
  ).toBe(true);
  await click(".operation-actions button", "Refresh");
  expect(document.body.textContent).toContain("deploy-prod");
  expect(document.body.textContent).not.toContain("stale");
  expect(
    document.querySelector<HTMLButtonElement>(".run-start-button")?.disabled,
  ).toBe(false);
});

it.each([
  [
    {
      "plan-summary.json": {
        complete: true,
        totals: { create: 2, update: 0, replace: 1, destroy: 3, read: 4 },
      },
    },
    ["2", "0", "1", "3"],
    false,
  ],
  [
    { "plan-summary.json": { complete: false, totals: { create: 0 } } },
    ["0", "—", "—", "—"],
    true,
  ],
  [{}, [], false],
  [
    {
      "plan-summary.json": { totals: { create: 9 } },
      "apply-summary.json": { totals: { create: 5, destroy: 2 } },
    },
    ["5", "—", "—", "2"],
    false,
  ],
])(
  "shows resource changes from available reports",
  async (reports, counts, incomplete) => {
    localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
    mockRunExecution();
    const original = vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation(async (...args) => {
      if (args[0] === "/v1/runs/run-test") {
        return {
          ...((await original(...args)) as object),
          result: { reports },
        } as never;
      }
      if (args[0] === "/v1/runs/run-test/artifacts")
        return [
          { id: "file", name: "plan-summary.json", digest: "x" },
        ] as never;
      return original(...args);
    });
    await render("/runs?repo=acme/service&environment=dev&run=run-test");
    const summary = document.querySelector(".run-change-summary")!;
    expect(
      [...summary.querySelectorAll("dd")].map((item) => item.textContent),
    ).toEqual(counts);
    expect(summary.textContent?.includes("Incomplete report")).toBe(incomplete);
    if (!counts.length)
      expect(summary.textContent).toContain("Change counts unavailable");
    expect(document.body.textContent).toContain("Reports (1)");
    expect(document.body.textContent).toContain("Phase logs");
    if (
      "plan-summary.json" in reports &&
      "read" in reports["plan-summary.json"].totals
    ) {
      expect(
        document.querySelector(".run-report-details")?.textContent,
      ).toContain("read");
      expect(
        document.querySelector(".run-report-details")?.textContent,
      ).not.toContain("create");
    }
  },
);

it("submits infrastructure tags and explains mixed-run operation scope", async () => {
  capabilities.push("plan_operations");
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  mockRunExecution();
  await render("/runs");
  await click('input[name="include_operations"]');
  expect(document.body.textContent).toContain("Tags do not filter operations.");
  const input = document.querySelector<HTMLInputElement>('input[name="tags"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(input, "prod, shared");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click(".platform-run-form > button");
  expect(
    vi.mocked(api).mock.calls.find(([path]) => path === "/v1/runs")?.[1],
  ).toMatchObject({ tags: ["prod", "shared"] });
});

it("shows component tags in the approval review", async () => {
  capabilities.push("approve_infrastructure", "approve_operations");
  mockRunExecution();
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0] === "/v1/runs/run-test") {
      const run = (await original(...args)) as { spec: object };
      return {
        ...run,
        status: "awaiting_approval",
        spec: {
          ...run.spec,
          tags: ["prod", "shared"],
          phases: ["prepare", "plan", "operation"],
        },
      } as never;
    }
    return original(...args);
  });
  await render("/runs?repo=acme/service&environment=dev&run=run-test");
  expect(document.body.textContent).toContain("prod, shared");
  expect(document.body.textContent).toContain(
    "Component tags do not filter operations.",
  );
});

it.each(["succeeded", "failed", "running", "awaiting_approval", "uncertain"])(
  "shows an icon and text for %s history rows",
  async (status) => {
    localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
    mockRunExecution();
    const original = vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation(async (...args) => {
      if (args[0].startsWith("/v1/runs?")) {
        return {
          items: [
            { ...((await original("/v1/runs/run-test")) as object), status },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        } as never;
      }
      return original(...args);
    });
    await render("/runs");
    const row = document.querySelector(".platform-history button")!;
    expect(
      row
        .querySelector(`.run-status-icon.${status}`)
        ?.getAttribute("aria-hidden"),
    ).toBe("true");
    expect(row.textContent).toContain(status.replaceAll("_", " "));
  },
);

it("remembers Runs targets separately from Composer and preserves explicit All environments", async () => {
  environmentNames = ["dev", "prod"];
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/runs");
  const launchPanel = document.querySelector(".run-launch-panel")!;
  expect(launchPanel.querySelector(".platform-run-form")).not.toBeNull();
  expect(launchPanel.querySelector(".run-target-toolbar")).not.toBeNull();
  expect(
    launchPanel.querySelector(".run-target-toolbar")?.closest("form"),
  ).toBeNull();
  expect(window.location.search).toBe("?repo=acme%2Fservice&environment=dev");
  await choose("Environment", "prod");
  expect(JSON.parse(localStorage.getItem(runTargetKey(login))!)).toEqual({
    repository: "acme/service",
    environment: "prod",
  });
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!)).toEqual(
    workspace,
  );
  expect(window.location.search).toContain("environment=prod");
  await act(async () => {
    window.history.pushState(
      null,
      "",
      "/runs?repo=acme%2Fservice&environment=dev",
    );
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(
    document.querySelector<HTMLSelectElement>(".run-target-toolbar select")
      ?.value,
  ).toBe("dev");
  await choose("Environment", "");
  expect(document.querySelector(".platform-run-form")).toBeNull();
  expect(
    document.querySelector(".run-launch-panel .run-target-guidance")
      ?.textContent,
  ).toContain("Choose a deployment environment");
  expect(
    document.querySelector(".run-launch-panel .run-target-toolbar"),
  ).not.toBeNull();
  expect(document.body.textContent).toContain("Repository run history");
  environmentNames = ["dev"];
  await click('a[href="/settings"]');
  await click('a[href="/runs"]');
  expect(
    document.querySelector<HTMLSelectElement>(".run-target-toolbar select")
      ?.value,
  ).toBe("");
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!)).toEqual(
    workspace,
  );
});

it("uses saved Runs selection before the workspace and isolates other accounts", async () => {
  environmentNames = ["dev", "prod"];
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  localStorage.setItem(
    runTargetKey("another-account"),
    JSON.stringify({ repository: "acme/other", environment: "dev" }),
  );
  localStorage.setItem(
    runTargetKey(login),
    JSON.stringify({ repository: "acme/service", environment: "prod" }),
  );
  await render("/runs");
  expect(
    document.querySelector<HTMLSelectElement>(".run-target-toolbar select")
      ?.value,
  ).toBe("prod");
  expect(document.body.textContent).toContain("Run history: prod");
});

it("selects a sole environment for a newly chosen repository and resets run options", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/runs");
  await click('input[name="apply_after_plan"]');
  await click('input[name="destroy_plan"]');
  await click("summary", "Advanced run options");
  const revision = document.querySelector<HTMLInputElement>(
    'input[name="revision"]',
  )!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(revision, "b".repeat(40));
    revision.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(
    document.querySelector(".advanced-run-options")?.textContent,
  ).toContain("b".repeat(10));
  await click(".repository-selector-trigger");
  await click('[role="option"]', "acme/other");
  expect(
    document.querySelector<HTMLSelectElement>(".run-target-toolbar select")
      ?.value,
  ).toBe("dev");
  expect(
    document.querySelector<HTMLInputElement>('input[name="apply_after_plan"]')
      ?.checked,
  ).toBe(false);
  expect(
    document.querySelector<HTMLInputElement>('input[name="destroy_plan"]')
      ?.checked,
  ).toBe(false);
  expect(
    document.querySelector<HTMLInputElement>('input[name="revision"]')?.value,
  ).toBe("");
  expect(
    document.querySelector(".advanced-run-options")?.textContent,
  ).not.toContain("b".repeat(40));
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!)).toEqual(
    workspace,
  );
});

it("shows the local backend override within advanced options", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/runs");
  await click("summary", "Advanced run options");
  await click('input[name="allow_local_backend"]');
  expect(
    document.querySelector(".advanced-run-options")?.textContent,
  ).toContain("Local-backend test");
});

it.each([
  [true, false, false, false, "plan"],
  [true, false, true, false, "apply"],
  [false, true, false, false, "plan-operation"],
  [false, true, true, false, "apply-operation"],
  [true, true, true, false, "apply"],
  [true, false, false, true, "destroy-plan"],
  [true, true, true, true, "destroy"],
])(
  "preserves launch payload for infrastructure=%s operations=%s apply=%s destroy=%s",
  async (infrastructure, operations, apply, destroy, action) => {
    capabilities.push(
      "execute_infrastructure",
      "plan_operations",
      "execute_operations",
    );
    localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
    mockRunExecution();
    await render("/runs");
    if (operations) await click('input[name="include_operations"]');
    if (!infrastructure) await click('input[name="include_infrastructure"]');
    if (apply) await click('input[name="apply_after_plan"]');
    if (destroy) {
      await click('input[name="destroy_plan"]');
    }
    expect(document.querySelectorAll(".operation-selector")).toHaveLength(
      operations ? 1 : 0,
    );
    await click(".platform-run-form > button");
    expect(
      vi.mocked(api).mock.calls.find(([path]) => path === "/v1/runs")?.[1],
    ).toMatchObject({
      repository: "acme/service",
      environment: "dev",
      action,
      include_infrastructure: infrastructure,
      include_operations: operations,
      apply_after_plan: apply,
      destroy_plan: destroy,
    });
    expect(
      vi.mocked(api).mock.calls.some(([path]) => path.endsWith("/audit")),
    ).toBe(false);
  },
);

it.each([true, false])(
  "shows the audit link only for administrators: %s",
  async (admin) => {
    administrator = admin;
    mockRunExecution();
    await render("/runs?repo=acme/service&run=run-test");
    expect(document.body.textContent.includes("View audit log")).toBe(admin);
    expect(document.body.textContent).not.toContain("Audit history");
    expect(
      vi.mocked(api).mock.calls.some(([path]) => path.endsWith("/audit")),
    ).toBe(false);
  },
);

it("loads admin audit filters from the URL and applies and clears them", async () => {
  await render(
    "/settings?section=audit&actor=operator&action=run.created&page=2",
  );
  expect(
    document.querySelector<HTMLInputElement>('input[name="actor"]')?.value,
  ).toBe("operator");
  expect(
    vi
      .mocked(api)
      .mock.calls.some(
        ([path]) =>
          path.includes("/v1/audit?") &&
          path.includes("page=2") &&
          path.includes("actor=operator"),
      ),
  ).toBe(true);
  const repository = document.querySelector<HTMLInputElement>(
    'input[name="repository"]',
  )!;
  repository.value = "acme/service";
  await click("button", "Apply filters");
  expect(window.location.search).toContain("repository=acme%2Fservice");
  expect(window.location.search).not.toContain("page=2");
  await click("button", "Clear filters");
  expect(window.location.search).toBe("?section=audit");
  expect(document.body.textContent).toContain("No audit events match");
});

it("does not fetch or expose platform audit events to non-admins", async () => {
  administrator = false;
  await render("/settings?section=audit");
  expect(document.body.textContent).toContain("Access unavailable");
  expect(
    document.querySelector('a[href="/settings?section=audit"]'),
  ).toBeNull();
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path.startsWith("/v1/audit")),
  ).toBe(false);
});

it("blocks launch until permissions for the newly selected environment arrive", async () => {
  environmentNames = ["dev", "prod"];
  capabilities.push("execute_infrastructure");
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const original = vi.mocked(api).getMockImplementation()!;
  let resolveAccess: (value: unknown) => void = () => {};
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].endsWith("/access?environment=prod"))
      return (await new Promise<unknown>((resolve) => {
        resolveAccess = resolve;
      })) as never;
    return original(...args);
  });
  await render("/runs");
  await click('input[name="apply_after_plan"]');
  await click('input[name="destroy_plan"]');
  await choose("Environment", "prod");
  expect(document.querySelector(".platform-run-form")).toBeNull();
  expect(
    document.querySelector('.run-launch-content [role="status"]')?.textContent,
  ).toBe("Loading permissions…");
  expect(
    document.querySelector(".run-launch-content")?.textContent,
  ).not.toContain("does not allow");
  await act(async () =>
    resolveAccess({ capabilities, administrator: true, assignments: [] }),
  );
  expect(
    document.querySelector<HTMLInputElement>('input[name="apply_after_plan"]')
      ?.checked,
  ).toBe(false);
  expect(
    document.querySelector<HTMLInputElement>('input[name="destroy_plan"]')
      ?.checked,
  ).toBe(false);
});

it("reports repository and environment loading before showing availability", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const original = vi.mocked(api).getMockImplementation()!;
  let resolveRepositories!: (value: unknown) => void;
  let resolveEnvironments!: (value: unknown) => void;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0] === "/v1/repositories")
      return (await new Promise<unknown>((resolve) => {
        resolveRepositories = resolve;
      })) as never;
    if (args[0].endsWith("/environments"))
      return (await new Promise<unknown>((resolve) => {
        resolveEnvironments = resolve;
      })) as never;
    return original(...args);
  });
  await render("/runs");
  expect(
    document.querySelector('.run-launch-content [role="status"]')?.textContent,
  ).toBe("Loading repositories…");
  expect(
    document.querySelector(".run-launch-panel")?.textContent,
  ).not.toContain("unavailable");
  await act(async () =>
    resolveRepositories(await original("/v1/repositories")),
  );
  expect(
    document.querySelector('.run-launch-content [role="status"]')?.textContent,
  ).toBe("Loading deployment environments…");
  expect(
    document.querySelector(".run-launch-panel")?.textContent,
  ).not.toContain("unavailable");
  await act(async () => resolveEnvironments([]));
  expect(document.querySelector(".run-target-guidance")?.textContent).toContain(
    "Environment unavailable",
  );
});

it("shows a permission error instead of reporting access denied", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].includes("/access?"))
      throw new Error("Permissions could not be loaded");
    return original(...args);
  });
  await render("/runs");
  expect(
    document.querySelector('.run-launch-content [role="alert"]')?.textContent,
  ).toContain("Permissions could not be loaded");
  expect(
    document.querySelector(".run-launch-content")?.textContent,
  ).not.toContain("does not allow");
  expect(document.querySelector(".platform-run-form")).toBeNull();
});

it.each([
  ["/v1/repositories", "Repository lookup failed", "Retry repositories"],
  ["/environments", "Environment lookup failed", "Retry environments"],
])(
  "reports a failed %s request without claiming a target is unavailable",
  async (path, message, retry) => {
    localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
    const original = vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation(async (...args) => {
      if (args[0].endsWith(path)) throw new Error(message);
      return original(...args);
    });
    await render("/runs");
    const panel = document.querySelector(".run-launch-panel")!;
    expect(panel.textContent).toContain(message);
    expect(panel.textContent).toContain(retry);
    expect(panel.textContent).not.toContain(
      "The selected repository is unavailable",
    );
    expect(panel.textContent).not.toContain("Environment unavailable");
  },
);

it("keeps inactive target history accessible without offering launch", async () => {
  await render("/runs?repo=acme/service&environment=retired");
  expect(document.body.textContent).toContain("retired (unavailable)");
  expect(document.body.textContent).toContain("Run history: retired");
  expect(document.querySelector(".platform-run-form")).toBeNull();
  expect(
    vi
      .mocked(api)
      .mock.calls.some(
        ([path]) =>
          path.startsWith("/v1/runs?") && path.includes("environment=retired"),
      ),
  ).toBe(true);
});

it("retries audit failures, renders expandable details, and preserves filters during pagination", async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  let failing = true;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].startsWith("/v1/audit?")) {
      if (failing) throw new Error("Audit unavailable");
      return {
        items: [
          {
            id: "event-1",
            repository: "acme/service",
            run: "run-test",
            action: "run.created",
            actor: "operator",
            created: 1,
            detail: { revision: "a".repeat(40) },
          },
        ],
        total: 41,
        page: 1,
        page_size: 20,
      } as never;
    }
    return original(...args);
  });
  await render("/settings?section=audit&actor=operator");
  expect(document.body.textContent).toContain("Audit unavailable");
  failing = false;
  await click("button", "Retry");
  expect(document.body.textContent).toContain("run created");
  expect(document.querySelector('a[href*="run=run-test"]')).toBeTruthy();
  expect(
    document.querySelector(".audit-table details")?.hasAttribute("open"),
  ).toBe(false);
  await click("summary", "View details");
  expect(
    document.querySelector(".audit-table details")?.hasAttribute("open"),
  ).toBe(true);
  await click('button[aria-label="Next audit events page"]');
  expect(window.location.search).toContain("page=2");
  expect(window.location.search).toContain("actor=operator");
});

it("centralizes setup and uses explicit workspace application", async () => {
  await render();
  expect(document.querySelectorAll("header.topbar")).toHaveLength(1);
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(document.body.textContent).not.toContain("Connect GitHub");
  await click(".repository-selector-trigger");
  await click('[role="option"]', "acme/service");
  await choose("Composer stack", "stack.yaml");
  await choose("Environment", "dev");
  expect(
    JSON.parse(localStorage.getItem(workspaceKey(login))!).repository,
  ).toBe("");
  await click("button", "Apply workspace");
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!)).toEqual(
    workspace,
  );
  await click('a[href="/runs"]');
  expect(
    document.querySelectorAll(".repository-selector-trigger"),
  ).toHaveLength(1);
  expect(document.querySelector(".run-target-toolbar")?.textContent).toContain(
    "acme/service",
  );
  expect(
    document.querySelector<HTMLSelectElement>(".run-target-toolbar select")
      ?.value,
  ).toBe("dev");
  await click('a[href="/settings"]');
  await click('a[href="/settings?section=github"]');
  expect(document.body.textContent).toContain("Connect GitHub");
  expect(
    Array.from(
      document.querySelectorAll(
        ".github-settings > .settings-panel .settings-panel-heading h3",
      ),
    ).map((heading) => heading.textContent),
  ).toEqual(["GitHub access", "Repository discovery"]);
  expect(document.body.textContent).not.toContain("Discovery filter");
  expect(
    document.querySelectorAll(
      'a[href="https://github.com/apps/test/installations/new"]',
    ),
  ).toHaveLength(1);
});

it("preserves the live Composer draft while visiting Settings and confirms stack changes once", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await click('button[aria-label="Add Service"]');
  const components = document.querySelector(
    '[data-testid="draft-components"]',
  )?.textContent;
  expect(components).toBeTruthy();
  await click('a[href="/settings"]');
  await click('a[href="/settings?section=workspace"]');
  await choose("Composer stack", "other.stack.yaml");
  await click("button", "Apply workspace");
  expect(document.querySelectorAll('[role="alertdialog"]')).toHaveLength(1);
  await click('[role="alertdialog"] button', "Cancel");
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!).path).toBe(
    "stack.yaml",
  );
  await click('a[href="/"]');
  expect(
    document.querySelector('[data-testid="draft-components"]')?.textContent,
  ).toBe(components);
  expect(
    vi.mocked(api).mock.calls.filter(([path]) => path.includes("/stack?")),
  ).toHaveLength(1);
});

it("honors Back/Forward navigation and URL context without falling back to another repository", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/runs?repo=acme/missing&environment=prod");
  expect(document.querySelector(".run-target-toolbar")?.textContent).toContain(
    "acme/missing",
  );
  expect(
    document.querySelector<HTMLSelectElement>(".run-target-toolbar select")
      ?.value,
  ).toBe("prod");
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!)).toEqual(
    workspace,
  );
  await act(async () => {
    window.history.pushState(null, "", "/settings?section=github");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(
    document.querySelector('a[aria-current="page"][href="/settings"]'),
  ).toBeTruthy();
  expect(document.body.textContent).toContain("Connect GitHub");
  await act(async () => {
    window.history.pushState(
      null,
      "",
      "/runs?repo=acme/service&environment=dev",
    );
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(document.querySelector(".run-target-toolbar")?.textContent).toContain(
    "acme/service",
  );
  expect(
    document.querySelector<HTMLSelectElement>(".run-target-toolbar select")
      ?.value,
  ).toBe("dev");
});

it("clears dependent selections when choosing another repository and accepts repositories without stacks", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render();
  paths = [];
  await click(".repository-selector-trigger");
  await click('[role="option"]', "acme/other");
  expect(document.body.textContent).toContain("No stack files found");
  await click("button", "Apply workspace");
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!)).toEqual({
    repository: "acme/other",
    path: "",
    environment: "dev",
  });
});

it("does not let token scope selection change the active workspace", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/settings?section=tokens");
  await click(".repository-selector-trigger");
  await click('[role="option"]', "acme/other");
  const form = document.querySelector("form")!;
  (form.querySelector('input[name="name"]') as HTMLInputElement).value =
    "Automation";
  await act(async () =>
    form.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    ),
  );
  expect(
    vi
      .mocked(api)
      .mock.calls.find(([path, body]) => path === "/v1/tokens" && body)?.[1],
  ).toMatchObject({ repository: "acme/other" });
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!)).toEqual(
    workspace,
  );
  expect(
    document.querySelector(".settings-panel-footer")?.textContent,
  ).toContain("test-token");
  await click("button", "Hide token");
  expect(document.body.textContent).not.toContain("test-token");
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path, body]) => path === "/v1/tokens" && body),
  ).toHaveLength(1);
});

it("hides administration from viewers and rejects unavailable settings sections", async () => {
  administrator = false;
  capabilities = [];
  await render("/settings?section=platform-config");
  expect(
    document.querySelector(".platform-admin-nav")?.textContent,
  ).not.toContain("Administration");
  expect(document.body.textContent).toContain("Settings unavailable");
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path === "/v1/configuration"),
  ).toBe(false);
});

it("keeps GitHub access available to viewers without discovery administration", async () => {
  administrator = false;
  capabilities = [];
  await render("/settings?section=github");
  expect(document.body.textContent).toContain("Connect GitHub");
  expect(document.body.textContent).not.toContain("Repository discovery");
  expect(document.body.textContent).not.toContain("Refresh repositories");
  expect(
    vi
      .mocked(api)
      .mock.calls.some(([path]) => path === "/v1/repository-filter"),
  ).toBe(false);
});

it("supports authoring-only Settings without calling execution APIs", async () => {
  execution = false;
  await render("/settings?section=github");
  expect(document.body.textContent).toContain("Connect GitHub");
  expect(document.querySelector('a[href="/runs"]')).toBeNull();
  expect(
    document.querySelector(".platform-admin-nav")?.textContent,
  ).not.toContain("API tokens");
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path.startsWith("/v1")),
  ).toBe(false);
});

it("keeps another account's remembered workspace separate", async () => {
  localStorage.setItem(workspaceKey("someone-else"), JSON.stringify(workspace));
  await render("/");
  expect(window.location.pathname).toBe("/settings");
  expect(
    vi
      .mocked(api)
      .mock.calls.some(
        ([path]) => path.startsWith("/catalog") || path.startsWith("/platform"),
      ),
  ).toBe(false);
  expect(
    JSON.parse(localStorage.getItem(workspaceKey(login))!).repository,
  ).toBe("");
});

it("retains repository controls for provisioners without exposing administration", async () => {
  administrator = false;
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/settings");
  expect(document.querySelector(".platform-admin-nav")?.textContent).toContain(
    "Deployment settings",
  );
  expect(
    document.querySelector(".platform-admin-nav")?.textContent,
  ).not.toContain("Platform defaults");
});

it("blocks stale stack choices and keeps the saved workspace until a valid selection is applied", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  paths = [];
  await render();
  expect(document.body.textContent).toContain(
    "The selected stack is unavailable",
  );
  expect(
    [...document.querySelectorAll("button")].find(
      (button) => button.textContent === "Apply workspace",
    )?.disabled,
  ).toBe(true);
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!)).toEqual(
    workspace,
  );
});

it("offers only one sign-in action for a signed-out visitor", async () => {
  signedIn = false;
  await render();
  expect(document.querySelectorAll('a[href="/api/auth/github"]')).toHaveLength(
    1,
  );
  expect(vi.mocked(api).mock.calls).toHaveLength(1);
});

it("keeps the local example usable through Settings", async () => {
  mode = "demo";
  execution = false;
  await render();
  expect(document.body.textContent).toContain("Active workspace");
  expect(document.body.textContent).not.toContain("GitHub connection");
  await click('a[href="/"]');
  expect(window.location.hash).toBe("#/example");
  expect(
    document.querySelector('[data-testid="draft-components"]'),
  ).toBeTruthy();
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path.startsWith("/v1")),
  ).toBe(false);
});

it("updates explicit Settings context when applying a different workspace", async () => {
  await render("/settings?section=workspace&repo=acme/service&environment=dev");
  await click(".repository-selector-trigger");
  await click('[role="option"]', "acme/other");
  await click("button", "Apply workspace");
  expect(new URL(window.location.href).searchParams.has("repo")).toBe(false);
  expect(
    JSON.parse(localStorage.getItem(workspaceKey(login))!).repository,
  ).toBe("acme/other");
});

it("retains the demo draft when returning from Settings", async () => {
  mode = "demo";
  execution = false;
  await render("/#/example");
  await inspect();
  await click('button[aria-label="Add Service"]');
  const draft = document.querySelector(
    '[data-testid="draft-components"]',
  )?.textContent;
  await click('a[href="/settings"]');
  await click('a[href="/"]');
  expect(
    document.querySelector('[data-testid="draft-components"]')?.textContent,
  ).toBe(draft);
  expect(
    vi.mocked(api).mock.calls.filter(([path]) => path === "/sample"),
  ).toHaveLength(1);
});

it("keeps workspace selection usable when browser storage is blocked", async () => {
  vi.stubGlobal("localStorage", {
    getItem: () => {
      throw new DOMException("Blocked", "SecurityError");
    },
    setItem: () => {
      throw new DOMException("Blocked", "SecurityError");
    },
  });
  await render();
  expect(document.body.textContent).toContain(
    "Workspace preferences could not be saved",
  );
  await click(".repository-selector-trigger");
  await click('[role="option"]', "acme/service");
  await click("button", "Apply workspace");
  expect(document.body.textContent).toContain("Workspace applied.");
  expect(
    document.querySelector(".repository-selector-trigger")?.textContent,
  ).toContain("acme/service");
});

it("changes inspection environment through Settings without reopening or rewriting the draft", async () => {
  vi.useFakeTimers();
  environmentNames = ["dev", "prod"];
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await click('button[aria-label="Add Service"]');
  await act(async () => {
    vi.advanceTimersByTime(450);
  });
  const sourceCalls = vi
    .mocked(api)
    .mock.calls.filter(([path]) => path === "/source").length;
  await click('a[href="/settings"]');
  await choose("Environment", "prod");
  await click("button", "Apply workspace");
  await click('a[href="/"]');
  expect(
    document.querySelector('[data-testid="draft-components"]')?.textContent,
  ).toBe("service");
  expect(
    vi.mocked(api).mock.calls.filter(([path]) => path.includes("/stack?")),
  ).toHaveLength(1);
  expect(window.confirm).not.toHaveBeenCalled();
  await act(async () => {
    vi.advanceTimersByTime(450);
  });
  expect(
    vi.mocked(api).mock.calls.filter(([path]) => path === "/source"),
  ).toHaveLength(sourceCalls);
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path]) => path === "/review")
      .at(-1)?.[1],
  ).toMatchObject({
    environment: "prod",
    components: { service: { type: "service" } },
  });
  await click("button", "Test undo");
  expect(
    document.querySelector('[data-testid="draft-components"]')?.textContent,
  ).toBe("");
});

it("opens the canvas before review and uses its catalog without duplicate inspections", async () => {
  vi.useFakeTimers();
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  expect(
    document.querySelector('[data-testid="draft-components"]'),
  ).toBeTruthy();
  expect(document.querySelector('button[aria-label="Add Service"]')).toBeNull();
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path.startsWith("/catalog")),
  ).toBe(false);
  await inspect();
  expect(
    document.querySelector('button[aria-label="Add Service"]'),
  ).toBeTruthy();
  expect(
    vi.mocked(api).mock.calls.filter(([path]) => path === "/review"),
  ).toHaveLength(1);
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path.startsWith("/catalog")),
  ).toBe(false);
  expect(vi.mocked(api).mock.calls.some(([path]) => path === "/source")).toBe(
    false,
  );
});

it("loads a fallback catalog after failed inspection so the draft remains editable", async () => {
  vi.useFakeTimers();
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const respond = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...args) =>
    path === "/review"
      ? Promise.reject(new Error("Invalid association"))
      : respond(path, ...args),
  );
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  expect(document.body.textContent).toContain("Invalid association");
  expect(
    document.querySelector('button[aria-label="Add Service"]'),
  ).toBeTruthy();
  expect(
    vi.mocked(api).mock.calls.filter(([path]) => path === "/catalog"),
  ).toHaveLength(1);
  expect(vi.mocked(api).mock.calls.some(([path]) => path === "/source")).toBe(
    false,
  );
});

it("makes unchanged source available immediately and renders edits independently of inspection", async () => {
  vi.useFakeTimers();
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml?view=review");
  const download = [
    ...document.querySelectorAll<HTMLButtonElement>("button"),
  ].find((button) =>
    button.textContent?.includes("Download unvalidated stack"),
  );
  expect(download?.disabled).toBe(false);
  expect(document.querySelector(".diff-heading")?.textContent).toContain(
    "No changes",
  );
  expect(vi.mocked(api).mock.calls.some(([path]) => path === "/source")).toBe(
    false,
  );
  await click("button", "Back to canvas");
  await inspect();
  await click('button[aria-label="Add Service"]');
  await inspect();
  expect(
    vi.mocked(api).mock.calls.filter(([path]) => path === "/source"),
  ).toHaveLength(1);
  await click("button", "Test undo");
  await inspect();
  expect(
    vi.mocked(api).mock.calls.filter(([path]) => path === "/source"),
  ).toHaveLength(1);
  expect(document.querySelector(".unsaved")).toBeNull();
});

it("keeps authored connections visible before inspection and after inspection fails", async () => {
  vi.useFakeTimers();
  initialComponents = { data: { type: "service" }, app: { type: "service" } };
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const respond = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...args) =>
    path === "/review"
      ? Promise.reject(
          new ApiError("Inspection failed", 422, {
            stage: "info associations",
            code: "command_failed",
            exit_code: 2,
            stdout: "",
            stderr: "Missing region",
          }),
        )
      : respond(path, ...args),
  );
  await render("/#/stacks/acme/service/stack.yaml");
  await click("button", "Test connect scalar");
  expect(
    document.querySelector('[data-testid="draft-edges"]')?.textContent,
  ).toContain('"source":"data"');
  expect(vi.mocked(api).mock.calls.some(([path]) => path === "/review")).toBe(
    false,
  );
  await act(async () => {
    vi.advanceTimersByTime(450);
  });
  expect(
    document.querySelector('[data-testid="draft-edges"]')?.textContent,
  ).toContain('"source":"data"');
  expect(document.body.textContent).toContain("Missing region");
  await click("button", "Test undo");
  expect(
    document.querySelector('[data-testid="draft-edges"]')?.textContent,
  ).toBe("[]");
  await click("button", "Test redo");
  expect(
    document.querySelector('[data-testid="draft-edges"]')?.textContent,
  ).toContain('"source":"data"');
});

it("chooses a nested destination and preserves sibling values", async () => {
  initialComponents = {
    data: { type: "service" },
    app: {
      type: "service",
      properties: { settings: { url: "old", replicas: 3 } },
    },
  };
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  await click("button", "Test connect nested");
  expect(
    document.querySelector('[data-testid="draft-properties"]')?.textContent,
  ).toContain('"url":"old"');
  expect(document.querySelector('[role="dialog"]')).toBeTruthy();
  await choose("Destination", '["settings","url"]');
  expect(
    document.querySelector('[data-testid="draft-properties"]')?.textContent,
  ).toContain('"url":"old"');
  await click('[role="dialog"] button', "Replace value");
  const draft = JSON.parse(
    document.querySelector('[data-testid="draft-properties"]')!.textContent!,
  );
  expect(draft.app.properties.settings).toEqual({
    url: '{{ components["data"]["url"] }}',
    replicas: 3,
  });
  expect(window.confirm).not.toHaveBeenCalled();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});

it("inspects managed connections without a deployment environment", async () => {
  vi.useFakeTimers();
  environmentNames = [];
  localStorage.setItem(
    workspaceKey(login),
    JSON.stringify({ ...workspace, environment: "" }),
  );
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await click('button[aria-label="Add Service"]');
  await act(async () => {
    vi.advanceTimersByTime(450);
  });
  expect(vi.mocked(api).mock.calls.some(([path]) => path === "/source")).toBe(
    true,
  );
  expect(
    vi.mocked(api).mock.calls.find(([path]) => path === "/review")?.[1],
  ).toMatchObject({ environment: null });
  expect(document.body.textContent).not.toContain("Choose an environment");
});

it("automatically inspects the sole deployment on a direct Composer link", async () => {
  vi.useFakeTimers();
  localStorage.setItem(
    workspaceKey(login),
    JSON.stringify({ ...workspace, environment: "" }),
  );
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  expect(
    vi.mocked(api).mock.calls.find(([path]) => path === "/review")?.[1],
  ).toMatchObject({ environment: "dev" });
});

it("preserves an explicit stack preview even when a sole deployment exists", async () => {
  vi.useFakeTimers();
  localStorage.setItem(
    workspaceKey(login),
    JSON.stringify({ ...workspace, environment: "", preview: true }),
  );
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  expect(
    vi.mocked(api).mock.calls.find(([path]) => path === "/review")?.[1],
  ).toMatchObject({ environment: null });
  await click('a[href="/settings"]');
  expect((document.querySelector("select") as HTMLSelectElement)?.value).toBe(
    "stack.yaml",
  );
  expect(document.body.textContent).toContain("Stack preview (no deployment)");
});

it("keeps preview available and reports target discovery failures", async () => {
  vi.useFakeTimers();
  const respond = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].startsWith("/inspection-targets?"))
      throw new Error("Service unavailable");
    return respond(...args);
  });
  localStorage.setItem(
    workspaceKey(login),
    JSON.stringify({ ...workspace, environment: "" }),
  );
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  expect(document.body.textContent).toContain(
    "Deployment targets could not be loaded",
  );
  expect(
    vi.mocked(api).mock.calls.find(([path]) => path === "/review")?.[1],
  ).toMatchObject({ environment: null });
});

it.each([
  {
    names: [],
    access: ["configure_environment"],
    message: "No deployment targets are configured for this repository",
    configure: true,
  },
  {
    names: ["dev"],
    access: ["view"],
    message:
      "You do not have Infrastructure planning access to a deployment target",
    configure: false,
  },
])(
  "explains workspace target availability: $message",
  async ({ names, access, message, configure }) => {
    environmentNames = names;
    capabilities = access;
    localStorage.setItem(
      workspaceKey(login),
      JSON.stringify({ ...workspace, environment: "", preview: true }),
    );
    await render("/settings?section=workspace");
    expect(document.body.textContent).toContain(message);
    expect(document.body.textContent).toContain(
      "Stack preview (no deployment)",
    );
    expect(document.body.textContent.includes("Configure deployments")).toBe(
      configure,
    );
  },
);

it("reports failed deployment inspection without falling back to preview", async () => {
  vi.useFakeTimers();
  const respond = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0] === "/review")
      throw new ApiError("Missing deployment input", 422);
    return respond(...args);
  });
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  expect(document.body.textContent).toContain(
    "Deployment dev inspection failed: Missing deployment input",
  );
  expect(document.body.textContent).toContain(
    "only authored connections are shown",
  );
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path]) => path === "/review")
      .every(
        ([, body]) => (body as { environment: string }).environment === "dev",
      ),
  ).toBe(true);
});

it("shows preview completion and review context for resolved managed connections", async () => {
  vi.useFakeTimers();
  environmentNames = [];
  initialComponents = { data: { type: "service" }, app: { type: "service" } };
  reviewRelationships = [
    {
      source: "data",
      target: "app",
      output: "url",
      property: "url",
      path: ["url"],
      kind: "association",
      rule: "managed-url",
    },
  ];
  localStorage.setItem(
    workspaceKey(login),
    JSON.stringify({ ...workspace, environment: "" }),
  );
  await render("/#/stacks/acme/service/stack.yaml");
  expect(document.body.textContent).toContain(
    "diagram is incomplete until inspection finishes",
  );
  await inspect();
  expect(document.body.textContent).toContain(
    "Stack preview: references and managed connections inspected",
  );
  expect(
    document.querySelector('[data-testid="draft-edges"]')?.textContent,
  ).toContain("managed-url");
  await click('button[aria-label="Add Service"]');
  await inspect();
  await click("button", "Review changes");
  expect(document.body.textContent).toContain(
    "Inspection context: stack preview",
  );
  expect(document.body.textContent).toContain("config-revision");
});

it("retains standalone authoring review without execution APIs", async () => {
  vi.useFakeTimers();
  execution = false;
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  await act(async () => {
    vi.advanceTimersByTime(450);
  });
  expect(
    vi.mocked(api).mock.calls.find(([path]) => path === "/review")?.[1],
  ).toMatchObject({ environment: null });
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path.startsWith("/v1")),
  ).toBe(false);
});

it("ignores late catalog and review responses from a previous environment", async () => {
  vi.useFakeTimers();
  environmentNames = ["dev", "prod"];
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const respond = vi.mocked(api).getMockImplementation()!;
  let finishReview: (value: never) => void = () => {
    throw new Error("Review request missing");
  };
  vi.mocked(api).mockImplementation((path, body, ...args) => {
    if (
      path === "/review" &&
      (body as { environment?: string })?.environment === "dev"
    )
      return new Promise<never>((resolve) => {
        finishReview = resolve;
      });
    return respond(path, body, ...args);
  });
  await render("/#/stacks/acme/service/stack.yaml");
  await act(async () => {
    vi.advanceTimersByTime(450);
  });
  await click('a[href="/settings"]');
  await choose("Environment", "prod");
  await click("button", "Apply workspace");
  await click('a[href="/"]');
  await act(async () => {
    vi.advanceTimersByTime(450);
  });
  await act(async () => {
    finishReview({
      catalog: {
        service: { ...catalog.service, display_name: "Wrong catalog" },
      },
      source: "",
      diff: "",
      edges: [],
      associations: { rules: [] },
      validation: "Wrong review",
    } as never);
  });
  expect(document.body.textContent).not.toContain("Wrong catalog");
  expect(document.body.textContent).not.toContain("Wrong review");
  expect(document.querySelector(".breadcrumb")?.textContent).toContain("prod");
});

function stackLink(path: string) {
  const link = document.createElement("a");
  link.href = `/#/stacks/acme/service/${path}`;
  document.body.append(link);
  return link;
}

async function traverseHistory(delta: number) {
  await act(async () => {
    await new Promise<void>((resolve) => {
      window.addEventListener("popstate", () => resolve(), { once: true });
      window.history.go(delta);
    });
  });
}

it("restores the accepted history entry when Back is cancelled without adding entries", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  const link = stackLink("other.stack.yaml");
  await act(async () => link.click());
  link.remove();
  await inspect();
  await click('button[aria-label="Add Service"]');
  const length = window.history.length;
  await traverseHistory(-1);
  expect(document.querySelectorAll('[role="alertdialog"]')).toHaveLength(1);
  const restored = new Promise<void>((resolve) =>
    window.addEventListener("popstate", () => resolve(), { once: true }),
  );
  await click('[role="alertdialog"] button', "Cancel");
  await act(async () => {
    await restored;
  });
  expect(window.location.hash).toContain("other.stack.yaml");
  expect(window.history.length).toBe(length);
  expect(
    document.querySelector('[data-testid="draft-components"]')?.textContent,
  ).toBe("service");
  await traverseHistory(-1);
  await click('[role="alertdialog"] button', "Leave draft");
  expect(window.location.hash).toBe("#/stacks/acme/service/stack.yaml");
  await traverseHistory(1);
  expect(window.location.hash).toContain("other.stack.yaml");
  expect(document.querySelector('[role="alertdialog"]')).toBeNull();
});

it("only applies the latest navigation attempt after a shared confirmation", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await click('button[aria-label="Add Service"]');
  const first = stackLink("other.stack.yaml");
  const second = stackLink("third.stack.yaml");
  await act(async () => {
    first.click();
    second.click();
  });
  first.remove();
  second.remove();
  expect(document.querySelectorAll('[role="alertdialog"]')).toHaveLength(1);
  await click('[role="alertdialog"] button', "Leave draft");
  expect(window.location.hash).toContain("third.stack.yaml");
  expect(
    vi
      .mocked(api)
      .mock.calls.some(([path]) => path.includes("path=other.stack.yaml")),
  ).toBe(false);
});

function seedRecovery() {
  const snapshot = {
    snapshot: "snapshot",
    repository: workspace.repository,
    path: workspace.path,
    branch: "main",
    head: "a".repeat(40),
    source: "components: {}",
    editable: true,
    reason: null,
    document: { name: "Test stack", components: {} },
  } satisfies Snapshot;
  const key = recoveryKey(snapshot, login);
  localStorage.setItem(
    key,
    JSON.stringify(
      recoveryRecord(
        snapshot,
        createDraftHistory({ components: { saved: { type: "service" } } })
          .present,
      ),
    ),
  );
  return key;
}

it("confirms recovery replacement and keeps restoration undoable", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  seedRecovery();
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await click('button[aria-label="Add Service"]');
  await click('button[aria-label="Restore saved draft"]');
  await click('[role="alertdialog"] button', "Cancel");
  expect(
    document.querySelector('[data-testid="draft-components"]')?.textContent,
  ).toBe("service");
  await click('button[aria-label="Restore saved draft"]');
  await click('[role="alertdialog"] button', "Restore saved draft");
  expect(
    document.querySelector('[data-testid="draft-components"]')?.textContent,
  ).toBe("saved");
  await click("button", "Test undo");
  expect(
    document.querySelector('[data-testid="draft-components"]')?.textContent,
  ).toBe("service");
});

it("only discards saved recovery data after confirmation", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const key = seedRecovery();
  await render("/#/stacks/acme/service/stack.yaml");
  await click("summary", "Saved draft options");
  await click("button", "Discard saved draft");
  await click('[role="alertdialog"] button', "Cancel");
  expect(localStorage.getItem(key)).not.toBeNull();
  await click("summary", "Saved draft options");
  await click("button", "Discard saved draft");
  await click('[role="alertdialog"] button', "Discard saved draft");
  expect(localStorage.getItem(key)).toBeNull();
});

it("confirms before signing out with an edited draft", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const respond = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...args) =>
    path === "/auth/logout"
      ? new Promise<never>(() => {})
      : respond(path, ...args),
  );
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await click('button[aria-label="Add Service"]');
  await click("button", "Sign out");
  await click('[role="alertdialog"] button', "Cancel");
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path === "/auth/logout"),
  ).toBe(false);
  await click("button", "Sign out");
  await click('[role="alertdialog"] button', "Leave draft");
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path === "/auth/logout"),
  ).toBe(true);
});

it("applies a confirmed workspace change once and opens the chosen stack", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await click('button[aria-label="Add Service"]');
  await click('a[href="/settings"]');
  await choose("Composer stack", "other.stack.yaml");
  await click("button", "Apply workspace");
  await click('[role="alertdialog"] button', "Leave draft");
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!).path).toBe(
    "other.stack.yaml",
  );
  await click('a[href="/"]');
  expect(document.querySelector('[role="alertdialog"]')).toBeNull();
  expect(
    vi.mocked(api).mock.calls.filter(([path]) => path.includes("/stack?")),
  ).toHaveLength(2);
});

it("shows rule impact and only disables the rule after confirmation", async () => {
  vi.useFakeTimers();
  initialComponents = { data: { type: "service" }, app: { type: "service" } };
  associationRules = [
    {
      name: "service-rule",
      description: "Managed URL",
      source: { kind: "managed", label: "platform", detail: null, url: null },
    },
  ];
  reviewRelationships = [
    {
      source: "data",
      target: "app",
      output: "url",
      property: "url",
      path: ["url"],
      kind: "association",
      rule: "service-rule",
    },
  ];
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  await act(async () => {
    vi.advanceTimersByTime(450);
  });
  await click(".managed-connections-toolbar button", "Managed connections");
  await click("button", "Disable across entire stack…");
  expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain(
    "data.url",
  );
  await click('[role="alertdialog"] button', "Cancel");
  expect(
    document.querySelector('[data-testid="draft-edges"]')?.textContent,
  ).toContain("service-rule");
  await click("button", "Disable across entire stack…");
  await click('[role="alertdialog"] button', "Disable across entire stack");
  await act(async () => {
    vi.advanceTimersByTime(450);
  });
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path]) => path === "/review")
      .at(-1)?.[1],
  ).toMatchObject({ disabled_associations: ["service-rule"] });
  await click("button", "Test undo");
  expect(
    document.querySelector('[data-testid="draft-edges"]')?.textContent,
  ).toContain("service-rule");
});

function seedManagedConnections() {
  initialComponents = Object.fromEntries(
    ["data", "app", "worker", "downstream"].map((name) => [
      name,
      { type: "service" },
    ]),
  );
  associationRules = [
    {
      name: "service-rule",
      description: "Managed URL",
      source: { kind: "managed", label: "platform", detail: null, url: null },
    },
  ];
  reviewRelationships = [
    { source: "data", target: "app" },
    { source: "data", target: "worker" },
    { source: "app", target: "downstream" },
  ].map((endpoints) => ({
    ...endpoints,
    output: "url",
    property: "url",
    path: ["url"],
    kind: "association",
    rule: "service-rule",
  }));
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
}

async function selectManagedConnection() {
  await click('button[aria-label="Select connection data.url to app.url"]');
}

function currentComponents(): Components {
  return JSON.parse(
    document.querySelector('[data-testid="draft-properties"]')!.textContent!,
  );
}

it("disconnects the receiving component in both roles, retains its sidebar and exports only the component opt-out", async () => {
  vi.useFakeTimers();
  seedManagedConnections();
  initialComponents.app.properties = { keep: "authored value" };
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await selectManagedConnection();
  expect(
    document.querySelector(".connection-inspector")?.textContent,
  ).toContain("Managed by platform");
  expect(document.querySelector(".connection-impact")?.textContent).toContain(
    "Affected connections (2)",
  );
  expect(document.querySelector(".connection-impact")?.textContent).toContain(
    "app.url",
  );
  expect(document.querySelector(".connection-impact")?.textContent).toContain(
    "downstream.url",
  );
  await click("button", "Disconnect from app");
  expect(document.querySelector('[role="alertdialog"]')).toBeNull();
  expect(document.activeElement?.textContent).toContain("Undo disconnect");
  expect(
    document.querySelector(".connection-inspector")?.textContent,
  ).toContain("Disconnected from app");
  expect(currentComponents().app).toEqual({
    type: "service",
    properties: { keep: "authored value" },
    disabled_associations: ["service-rule"],
  });
  expect(
    JSON.parse(
      document.querySelector('[data-testid="draft-edges"]')!.textContent!,
    ),
  ).toEqual([reviewRelationships[1]]);
  reviewRelationships = [reviewRelationships[1]];
  await inspect();
  expect(
    document.querySelector(".connection-inspector")?.textContent,
  ).toContain("Affected connections (2)");
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path]) => path === "/source")
      .at(-1)?.[1],
  ).toMatchObject({
    disabled_associations: [],
    components: {
      app: {
        disabled_associations: ["service-rule"],
        properties: { keep: "authored value" },
      },
    },
  });
});

it("undoes a scoped disconnect without discarding an intervening authored reference", async () => {
  vi.useFakeTimers();
  seedManagedConnections();
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await selectManagedConnection();
  await click("button", "Disconnect from app");
  await click("button", "Test connect scalar");
  await click("button", "Undo disconnect");
  expect(currentComponents().app.disabled_associations).toBeUndefined();
  expect(currentComponents().app.properties?.url).toBe(
    '{{ components["data"]["url"] }}',
  );
  expect(
    document.querySelector('[data-testid="draft-edges"]')?.textContent,
  ).toContain('"kind":"reference"');
  await click("button", "Test undo");
  expect(currentComponents().app.disabled_associations).toEqual([
    "service-rule",
  ]);
  expect(currentComponents().app.properties?.url).toBe(
    '{{ components["data"]["url"] }}',
  );
  await click("button", "Test redo");
  expect(currentComponents().app.disabled_associations).toBeUndefined();
});

it("keeps disconnect restoration and Retry available after failed inspection and summarizes the scope in Review", async () => {
  vi.useFakeTimers();
  seedManagedConnections();
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await selectManagedConnection();
  await click("button", "Disconnect from app");
  const respond = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...args) =>
    path === "/review"
      ? Promise.reject(new Error("Missing input"))
      : respond(path, ...args),
  );
  await inspect();
  expect(
    document.querySelector(".connection-inspector")?.textContent,
  ).toContain("Missing input");
  expect(
    document.querySelector(".connection-inspector")?.textContent,
  ).toContain("Undo disconnect");
  expect(
    document.querySelector(".connection-inspector")?.textContent,
  ).toContain("Retry inspection");
  await click("button", "Review changes");
  expect(document.querySelector(".managed-summary")?.textContent).toContain(
    "Disabled for app",
  );
  await click("button", "Manage connections");
  expect(document.querySelector(".managed-connections")?.textContent).toContain(
    "Disabled for app",
  );
  await click("button", "Re-enable for app");
  expect(currentComponents().app.disabled_associations).toBeUndefined();
});

it("restores authored opt-outs without needing any edges and respects a stack-wide override", async () => {
  vi.useFakeTimers();
  seedManagedConnections();
  initialDisabled = ["service-rule"];
  initialComponents.app.disabled_associations = [
    "service-rule",
    "unknown-rule",
  ];
  reviewRelationships = [];
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await click(".managed-connections-toolbar button");
  expect(document.querySelector(".managed-connections")?.textContent).toContain(
    "Disabled scopes (3)",
  );
  await click("button", "Review stack-wide setting…");
  expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain(
    "Scope: Entire stack",
  );
  await click('[role="alertdialog"] button', "Re-enable across entire stack");
  expect(currentComponents().app.disabled_associations).toEqual([
    "service-rule",
    "unknown-rule",
  ]);
  await click(".managed-scope button", "Re-enable for app");
  expect(currentComponents().app.disabled_associations).toEqual([
    "unknown-rule",
  ]);
  await click(".managed-scope button", "Re-enable for app");
  expect(currentComponents().app.disabled_associations).toBeUndefined();
});

it("routes Delete to the component opt-out while keeping text input and read-only guards", async () => {
  vi.useFakeTimers();
  seedManagedConnections();
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await selectManagedConnection();
  await act(async () =>
    document
      .querySelector('input[aria-label="Search components"]')!
      .dispatchEvent(
        new KeyboardEvent("keydown", { key: "Delete", bubbles: true }),
      ),
  );
  expect(currentComponents().app.disabled_associations).toBeUndefined();
  await click(".toolbar-actions button", "Editing");
  expect(
    document.querySelector<HTMLButtonElement>(".connection-primary")?.disabled,
  ).toBe(true);
  await act(async () =>
    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Delete", bubbles: true }),
    ),
  );
  expect(currentComponents().app.disabled_associations).toBeUndefined();
  await click(".toolbar-actions button", "View only");
  await act(async () =>
    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Backspace", bubbles: true }),
    ),
  );
  expect(currentComponents().app.disabled_associations).toEqual([
    "service-rule",
  ]);
  expect(document.querySelector('[role="alertdialog"]')).toBeNull();
});

it("clears contextual connection details on deployment switches and supports authored reference removal", async () => {
  vi.useFakeTimers();
  seedManagedConnections();
  environmentNames = ["dev", "prod"];
  initialComponents.app.properties = {
    url: '{{ components["data"]["url"] }}',
    keep: true,
  };
  reviewRelationships = [];
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await click('button[aria-label="Select connection data.url to app.url"]');
  expect(
    document.querySelector(".connection-inspector")?.textContent,
  ).toContain("Authored reference");
  await click("button", "Remove reference");
  expect(currentComponents().app.properties).toEqual({ keep: true });
  expect(document.querySelector(".connection-inspector")).toBeNull();
  await click("button", "Test undo");
  await click('button[aria-label="Select connection data.url to app.url"]');
  await click('a[href="/settings"]');
  await choose("Environment", "prod");
  await click("button", "Apply workspace");
  await click('a[href="/"]');
  expect(document.querySelector(".connection-inspector")).toBeNull();
});

it("loads repository history without deployment targets", async () => {
  environmentNames = [];
  localStorage.setItem(
    workspaceKey(login),
    JSON.stringify({ ...workspace, environment: "", preview: true }),
  );
  await render("/runs");
  expect(document.body.textContent).toContain("Repository run history");
  expect(document.body.textContent).toContain(
    "No runs in this repository yet.",
  );
  expect(document.body.textContent).toContain("Configure deployments");
  expect(document.querySelector(".run-target-guidance")?.textContent).toContain(
    "Choose a deployment environment",
  );
  expect(document.body.textContent).not.toContain("Workspace unavailable");
  expect(
    vi
      .mocked(api)
      .mock.calls.some(
        ([path]) =>
          path.startsWith("/v1/runs?") && !path.includes("environment="),
      ),
  ).toBe(true);
  expect(
    [...document.querySelectorAll("button")].some(
      (button) => button.textContent === "Start run",
    ),
  ).toBe(false);
});

it("retries a failed history request and keeps environment filtering", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const original = vi.mocked(api).getMockImplementation()!;
  let fail = true;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].startsWith("/v1/runs?") && fail)
      throw new Error("History temporarily unavailable");
    return original(...args);
  });
  await render("/runs");
  expect(document.body.textContent).toContain(
    "History temporarily unavailable",
  );
  expect(document.body.textContent).not.toContain(
    "No runs in this environment yet.",
  );
  fail = false;
  await click("button", "Retry run history");
  expect(document.body.textContent).toContain(
    "No runs in this environment yet.",
  );
  expect(
    vi
      .mocked(api)
      .mock.calls.some(
        ([path]) =>
          path.startsWith("/v1/runs?") && path.includes("environment=dev"),
      ),
  ).toBe(true);
});

it("keeps discovered stacks available when environment discovery fails", async () => {
  localStorage.setItem(
    workspaceKey(login),
    JSON.stringify({ ...workspace, environment: "", preview: true }),
  );
  const original = vi.mocked(api).getMockImplementation()!;
  let fail = true;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].startsWith("/inspection-targets") && fail)
      throw new Error("Target discovery unavailable");
    return original(...args);
  });
  await render("/settings?section=workspace");
  expect(document.querySelector("select")?.textContent).toContain("stack.yaml");
  expect(document.body.textContent).toContain("Retry environment discovery");
  fail = false;
  await click("button", "Retry environment discovery");
  expect(document.body.textContent).not.toContain(
    "Retry environment discovery",
  );
});

it("ignores the cancelled target lookup during StrictMode effect replay", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  window.history.replaceState(null, "", "/runs");
  await act(async () =>
    root.render(
      <StrictMode>
        <PlatformApp />
      </StrictMode>,
    ),
  );
  const requests = vi
    .mocked(api)
    .mock.calls.filter(([path]) => path.startsWith("/inspection-targets?"));
  expect(requests.length).toBeGreaterThanOrEqual(2);
  expect(requests[0][2]?.aborted).toBe(true);
  expect(requests.at(-1)?.[2]?.aborted).toBe(false);
  expect(document.body.textContent).not.toContain(
    "Deployment targets could not be loaded",
  );
});

it("shows pending changes while navigation uses the applied workspace", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/settings?section=workspace");
  await choose("Composer stack", "");
  expect(document.body.textContent).toContain("Unapplied workspace changes.");
  expect(document.querySelector('a[href*="stack.yaml"]')?.textContent).toBe(
    "Open Composer",
  );
  expect(document.body.textContent).toContain("Open the applied workspace");
});

it("ignores history responses from a previous repository", async () => {
  environmentNames = [];
  localStorage.setItem(
    workspaceKey(login),
    JSON.stringify({ ...workspace, environment: "", preview: true }),
  );
  const original = vi.mocked(api).getMockImplementation()!;
  let resolveOld!: (value: never) => void;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].startsWith("/v1/runs?repository=acme%2Fservice"))
      return new Promise<never>((resolve) => {
        resolveOld = resolve;
      });
    return original(...args);
  });
  await render("/runs");
  await act(async () => {
    window.history.pushState(null, "", "/runs?repo=acme/other");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await act(async () =>
    resolveOld({
      items: [
        {
          id: "old-run",
          action: "plan",
          environment: "old-environment",
          status: "failed",
          revision: "a".repeat(40),
          created: 1,
          actor: "old-actor",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    } as never),
  );
  expect(document.body.textContent).not.toContain("old-actor");
  expect(document.body.textContent).toContain(
    "No runs in this repository yet.",
  );
});

it("keeps environments available when stack discovery fails and retries only stacks", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const original = vi.mocked(api).getMockImplementation()!;
  let fail = true;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].endsWith("/stacks") && fail)
      throw new Error("Stack discovery unavailable");
    return original(...args);
  });
  await render("/settings?section=workspace");
  expect([...document.querySelectorAll("select")][1].textContent).toContain(
    "dev",
  );
  expect(document.body.textContent).not.toContain("No stack files found");
  const targetsBefore = vi
    .mocked(api)
    .mock.calls.filter(([path]) =>
      path.startsWith("/inspection-targets"),
    ).length;
  fail = false;
  await click("button", "Retry stack discovery");
  expect(document.body.textContent).not.toContain("Retry stack discovery");
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path]) => path.startsWith("/inspection-targets"))
      .length,
  ).toBe(targetsBefore);
});

it("refreshes workspace choices without applying the draft", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/settings?section=workspace");
  await choose("Composer stack", "other.stack.yaml");
  const before = vi
    .mocked(api)
    .mock.calls.filter(([path]) => path === "/repository-discovery").length;
  await click("button", "Refresh choices");
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path]) => path === "/repository-discovery").length,
  ).toBeGreaterThan(before);
  expect(document.querySelector("select")?.value).toBe("other.stack.yaml");
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!).path).toBe(
    "stack.yaml",
  );
});

it("can apply a deployment workspace without a stack when stack discovery fails", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].endsWith("/stacks"))
      throw new Error("Stack discovery unavailable");
    return original(...args);
  });
  await render("/settings?section=workspace");
  await choose("Composer stack", "");
  await click("button", "Apply workspace");
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!)).toMatchObject({
    path: "",
    environment: "dev",
  });
});

it("can apply explicit preview when environment discovery fails", async () => {
  localStorage.setItem(
    workspaceKey(login),
    JSON.stringify({ ...workspace, environment: "", preview: true }),
  );
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].startsWith("/inspection-targets"))
      throw new Error("Environment discovery unavailable");
    return original(...args);
  });
  await render("/settings?section=workspace");
  await click("button", "Apply workspace");
  expect(document.body.textContent).toContain("Workspace applied.");
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!)).toMatchObject({
    environment: "",
    preview: true,
  });
});

it("labels environments in repository-wide history without granting run creation", async () => {
  environmentNames = [];
  capabilities = [];
  localStorage.setItem(
    workspaceKey(login),
    JSON.stringify({ ...workspace, environment: "", preview: true }),
  );
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].startsWith("/v1/runs?"))
      return {
        items: [
          {
            id: "run-one",
            environment: "production",
            action: "plan",
            status: "succeeded",
            revision: "a".repeat(40),
            created: 100,
            actor: "operator",
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      } as never;
    return original(...args);
  });
  await render("/runs");
  expect(document.querySelector(".platform-history")?.textContent).toContain(
    "production",
  );
  expect(document.querySelector(".platform-run-form")).toBeNull();
  expect(
    document.querySelector(".run-launch-panel .run-target-toolbar"),
  ).not.toBeNull();
});

const settingsPages = [
  ["workspace", "Workspace", ["Active workspace"]],
  ["github", "GitHub connection", ["GitHub access", "Repository discovery"]],
  ["tokens", "API tokens", ["Create a token", "Your tokens"]],
  ["environments", "Deployment settings", ["Deployment setup"]],
  [
    "repository-config",
    "Repository configuration",
    ["Configuration policy", "Effective layers"],
  ],
  ["effective-config", "Effective configuration", ["State & tooling"]],
  [
    "credentials",
    "Credentials",
    ["Execution identities", "Credential bindings"],
  ],
  ["permissions", "Stack roles", ["Role assignments"]],
  [
    "platform-config",
    "Platform defaults",
    ["Default configuration", "Effective layers"],
  ],
  ["health", "Execution health", ["Execution status", "Pending runs"]],
  ["audit", "Audit log", ["Platform activity"]],
] as const;

it("configures detected deployments without requiring a dot root", async () => {
  environmentNames = [];
  localStorage.setItem(
    workspaceKey(login),
    JSON.stringify({ ...workspace, environment: "" }),
  );
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    const [path, body, , , method] = args;
    if (path.endsWith("/deployment-configuration") && method === "PUT")
      return { applied: true } as never;
    if (path.endsWith("/deployment-configuration"))
      return {
        deployment_enabled: false,
        settings: null,
        inconsistent: false,
        environments: [],
      } as never;
    if (path.endsWith("/deployment-discovery"))
      return {
        revision: "a".repeat(40),
        deployments: [
          {
            gitops_root: "",
            requested_discovery_mode: "auto",
            discovery_mode: "env-files",
            all_environments: ["dev", "prod"],
            matrix: [],
            source_lockfiles: {
              status: "complete",
              present: ["common/stacksmith.lock.yaml"],
              missing: [],
              missing_environments: [],
            },
          },
        ],
      } as never;
    return original(path, body, args[2], args[3], method);
  });

  await render("/settings?section=environments");
  expect(document.body.textContent).toContain("Repository root");
  expect(document.body.textContent).toContain("dev, prod");
  const rootInput = document.querySelector<HTMLInputElement>(
    'input[placeholder="Repository root"]',
  );
  expect(rootInput?.value).toBe("");
  expect(
    document.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked,
  ).toBe(true);
  const eventChoices = document.querySelectorAll<HTMLInputElement>(
    'input[name="event-runs"]',
  );
  expect(eventChoices).toHaveLength(2);
  await act(async () => eventChoices[1].click());
  await click("button", "Configure and enable deployments");
  expect(
    vi.mocked(api).mock.calls.some(
      ([path, body, , , method]) =>
        path.endsWith("/deployment-configuration") &&
        method === "PUT" &&
        JSON.stringify(body) ===
          JSON.stringify({
            revision: "a".repeat(40),
            gitops_root: "",
            discovery_mode: "auto",
            lock_key: "",
            locked: true,
            automatic: false,
            deactivate_missing: false,
          }),
    ),
  ).toBe(true);
});

it("restores every saved repository deployment setting", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    const [path, body] = args;
    if (path.endsWith("/deployment-configuration"))
      return {
        deployment_enabled: true,
        settings: {
          gitops_root: "infrastructure",
          discovery_mode: "folders",
          resolved_discovery_mode: "folders",
          lock_key: "shared-production",
          locked: true,
          automatic: false,
        },
        inconsistent: false,
        environments: [{ name: "dev", active: true, revision: 4 }],
      } as never;
    if (path.endsWith("/deployment-discovery"))
      return {
        revision: "a".repeat(40),
        deployments: [
          {
            gitops_root: "infrastructure",
            requested_discovery_mode: "folders",
            discovery_mode: "folders",
            all_environments: ["dev"],
            matrix: [],
            source_lockfiles: {
              status: "complete",
              present: ["infrastructure/common/stacksmith.lock.yaml"],
              missing: [],
              missing_environments: [],
            },
          },
        ],
      } as never;
    return original(path, body, args[2], args[3], args[4]);
  });

  await render("/settings?section=environments");
  expect(document.body.textContent).toContain("Active environments");
  expect(
    document.querySelector<HTMLInputElement>(
      'input[placeholder="Repository root"]',
    )?.value,
  ).toBe("infrastructure");
  expect(document.querySelector<HTMLSelectElement>("select")?.value).toBe(
    "folders",
  );
  expect(
    document.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked,
  ).toBe(true);
  expect(
    document.querySelector<HTMLInputElement>(
      'input[placeholder="This repository"]',
    )?.value,
  ).toBe("shared-production");
  expect(
    document.querySelectorAll<HTMLInputElement>('input[name="event-runs"]')[1]
      .checked,
  ).toBe(true);
});

function mockSettingsData(
  state: "populated" | "empty" | "failure" | "loading" = "empty",
) {
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    const path = args[0];
    if (
      path.endsWith("/configuration") ||
      path.endsWith("/grants") ||
      path.endsWith("/secrets") ||
      path === "/v1/status"
    ) {
      if (state === "failure") throw new Error("Settings data unavailable");
      if (state === "loading") return new Promise<never>(() => {});
      if (path.endsWith("/configuration"))
        return {
          mode: "inherit",
          refs: [],
          platform_refs:
            state === "populated"
              ? [
                  `git+https://github.com/acme/config//platform.yaml@${"a".repeat(40)}`,
                ]
              : [],
        } as never;
      if (path.endsWith("/grants"))
        return (
          state === "populated"
            ? [
                {
                  id: "grant",
                  login: "operator",
                  environment: "dev",
                  role: "viewer",
                  workloads: [],
                },
              ]
            : []
        ) as never;
      if (path.endsWith("/secrets"))
        return (
          state === "populated"
            ? [
                {
                  id: "binding",
                  name: "EXAMPLE_TOKEN",
                  phases: ["plan"],
                  reference: null,
                  json_key: null,
                },
              ]
            : []
        ) as never;
      return {
        mode: "worker",
        observed_at: 1,
        workers:
          state === "populated"
            ? [{ id: "worker", driver: "docker", heartbeat: 1 }]
            : [],
        stale_workers: 0,
        pending:
          state === "populated"
            ? [{ id: "pending-run", status: "queued", created: 1 }]
            : [],
      } as never;
    }
    return original(...args);
  });
}

it.each(settingsPages)(
  "uses the shared layout for %s",
  async (section, title, panels) => {
    localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
    mockSettingsData("populated");
    await render(`/settings?section=${section}`);
    expect(
      document.querySelector(".settings-page-heading h2")?.textContent,
    ).toBe(title);
    expect(
      [...document.querySelectorAll(".settings-layout h2")].map(
        (heading) => heading.textContent,
      ),
    ).toEqual([title]);
    const headings = [
      ...document.querySelectorAll(".settings-panel-heading h3"),
    ].map((heading) => heading.textContent?.trim());
    for (const heading of panels) expect(headings).toContain(heading);
    expect(headings).not.toContain(title);
    expect(
      document.querySelector(".settings-layout .platform-card"),
    ).toBeNull();
    expect(document.querySelector("form form")).toBeNull();
    for (const button of document.querySelectorAll<HTMLButtonElement>(
      '.settings-button-primary[type="submit"]',
    )) {
      expect(button.closest("form")).not.toBeNull();
      expect(
        button.closest(".settings-panel-footer, .settings-actions"),
      ).not.toBeNull();
    }
  },
);

it("shows existing credential bindings before the inline form", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  mockSettingsData("populated");
  await render("/settings?section=credentials");
  const section = [...document.querySelectorAll(".settings-panel")].find(
    (panel) => panel.querySelector("h3")?.textContent === "Credential bindings",
  )!;
  expect(section.textContent!.indexOf("EXAMPLE_TOKEN")).toBeLessThan(
    section.textContent!.indexOf("Add credential binding"),
  );
  expect(section.querySelector('input[value="value"]')).not.toBeNull();
  expect(section.querySelector('input[value="reference"]')).not.toBeNull();
  expect(
    section.querySelectorAll(".credential-phase-options input"),
  ).toHaveLength(6);
});

it.each(["empty", "failure", "loading"] as const)(
  "keeps the configuration panel stable during %s",
  async (state) => {
    localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
    mockSettingsData(state);
    await render("/settings?section=repository-config");
    expect(
      document.querySelector(".settings-panel-heading h3")?.textContent,
    ).toBe("Configuration policy");
    if (state === "failure") {
      expect(
        document.querySelector('.settings-panel [role="alert"]')?.textContent,
      ).toBe("Settings data unavailable");
      expect(document.body.textContent).toContain("Reload configuration");
    }
    if (state === "loading")
      expect(
        document.querySelector('.settings-panel [role="status"]')?.textContent,
      ).toBe("Loading configuration…");
  },
);

it("keeps the shared layout for restricted and authoring-only accounts", async () => {
  administrator = false;
  capabilities = [];
  execution = false;
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/settings?section=github");
  expect(
    [...document.querySelectorAll(".settings-panel-heading h3")].map(
      (heading) => heading.textContent,
    ),
  ).toEqual(["GitHub access"]);
  expect(
    document.querySelector('a[href="/settings?section=permissions"]'),
  ).toBeNull();
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path.startsWith("/v1/")),
  ).toBe(false);
});

it("keeps the latest run independent of history pages and opens its details", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  mockRunExecution();
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].startsWith("/v1/runs?")) {
      return {
        items: [
          {
            id: "run-test",
            repository: "acme/service",
            environment: "dev",
            revision: "a".repeat(40),
            action: "plan",
            status: "succeeded",
            actor: login,
            created: 1,
          },
        ],
        total: 30,
      } as never;
    }
    return original(...args);
  });
  await render("/runs");
  expect(document.querySelector(".latest-run")?.textContent).toContain(
    "succeeded",
  );
  await click('button[aria-label="Next run history page"]');
  expect(
    vi
      .mocked(api)
      .mock.calls.some(([path]) => path.includes("page=2&page_size=20")),
  ).toBe(true);
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path]) => path.endsWith("page=1&page_size=1")),
  ).toHaveLength(1);
  expect(document.querySelector(".latest-run")?.textContent).toContain(
    "succeeded",
  );
  await click(".latest-run a");
  expect(new URLSearchParams(window.location.search).get("run")).toBe(
    "run-test",
  );
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path === "/v1/runs/run-test"),
  ).toBe(true);
});

it("keeps launch and history available when the latest run fails", async () => {
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].endsWith("page=1&page_size=1"))
      throw new Error("Latest unavailable");
    return original(...args);
  });
  await render("/runs");
  expect(document.querySelector(".latest-run")?.textContent).toContain(
    "Latest unavailable",
  );
  expect(
    document.querySelector<HTMLButtonElement>(".run-start-button")?.disabled,
  ).toBe(false);
  expect(document.body.textContent).toContain("Run history: dev");
});

it("explains a remembered missing deployment and inspects preview only when chosen", async () => {
  vi.useFakeTimers();
  environmentNames = [];
  initialComponents = { policy: { type: "service" } };
  const respond = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (
      (args[0] === "/review" || args[0] === "/catalog") &&
      (args[1] as { environment?: string } | undefined)?.environment
    )
      throw new ApiError("The selected environment is unavailable.", 404);
    return respond(...args);
  });
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  expect(document.body.textContent).toContain(
    "The selected deployment dev is unavailable",
  );
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path]) => path === "/review")
      .every(
        ([, body]) => (body as { environment: string }).environment === "dev",
      ),
  ).toBe(true);
  await click("button", "Inspect stack preview");
  await inspect();
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path]) => path === "/review")
      .at(-1)?.[1],
  ).toMatchObject({ environment: null, components: initialComponents });
  expect(document.body.textContent).not.toContain(
    "The selected deployment dev is unavailable",
  );
  expect(
    document.querySelector('[data-testid="draft-components"]')?.textContent,
  ).toBe("policy");
  expect(JSON.parse(localStorage.getItem(workspaceKey(login))!)).toMatchObject({
    ...workspace,
    environment: "",
    preview: true,
  });
  expect(document.body.textContent).toContain(
    "Stack preview: references and managed connections inspected",
  );
});

it("renames a component, preserves layout and references, and supports undo and redo", async () => {
  vi.useFakeTimers();
  const oldReference = '{{ components["data"]["url"] }}';
  const newReference = '{{ components["store"]["url"] }}';
  initialComponents = {
    data: { type: "service" },
    app: { type: "service", properties: { url: oldReference } },
  };
  const respond = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (...args) => {
    if (args[0].includes("/stack?")) {
      const snapshot = (await respond(...args)) as Snapshot;
      return {
        ...snapshot,
        document: {
          ...snapshot.document!,
          metadata: {
            stacksmith_composer: {
              positions: { data: { x: 80, y: 120 } },
              routes: [
                {
                  source: "data",
                  target: "app",
                  output: "url",
                  property: "url",
                  path: ["url"],
                  kind: "reference",
                  rule: null,
                  points: [{ x: 10, y: 20 }],
                },
              ],
            },
          },
        },
      } as never;
    }
    if (args[0] === "/rename-component")
      return {
        components: {
          store: { type: "service" },
          app: { type: "service", properties: { url: newReference } },
        },
      } as never;
    return respond(...args);
  });
  localStorage.setItem(workspaceKey(login), JSON.stringify(workspace));
  await render("/#/stacks/acme/service/stack.yaml");
  await inspect();
  await click("button", "Select component data");
  await click('button[aria-label="Rename data"]');
  const input = document.querySelector<HTMLInputElement>("#component-rename")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(input, "store");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click('button[type="submit"]', "Rename");
  expect(
    document.querySelector('[data-testid="draft-components"]')?.textContent,
  ).toBe("store,app");
  expect(
    document.querySelector('[data-testid="draft-properties"]')?.textContent,
  ).toContain("store");
  expect(document.body.textContent).toContain("Component details");
  await inspect();
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(([path]) => path === "/source")
      .at(-1)?.[1],
  ).toMatchObject({
    components: {
      store: { type: "service" },
      app: { properties: { url: newReference } },
    },
    renames: [{ old: "data", new: "store" }],
  });
  await click("button", "Test undo");
  expect(
    document.querySelector('[data-testid="draft-properties"]')?.textContent,
  ).toContain("data");
  await click("button", "Test redo");
  expect(
    document.querySelector('[data-testid="draft-components"]')?.textContent,
  ).toBe("store,app");
  await act(async () => vi.advanceTimersByTime(300));
  const saved = JSON.parse(
    localStorage.getItem(
      recoveryKey(
        {
          repository: "acme/service",
          path: "stack.yaml",
          branch: "main",
        } as Snapshot,
        login,
      ),
    )!,
  );
  expect(saved.draft.renames).toEqual([{ old: "data", new: "store" }]);
});
