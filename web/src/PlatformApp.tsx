import type { RunSummary } from "./types";
import { useSettingsError } from "./useSettingsError";
import {
  SettingsPageHeader,
  SettingsPanel,
  SettingsFields,
  SettingsField,
  SettingsActions,
  SettingsFeedback,
  SettingsButton,
} from "./components/SettingsLayout";
import { ExecutionHealth } from "./components/ExecutionHealth";
import { SecretReferenceSelector } from "./components/SecretReferenceSelector";
import { CredentialPhases } from "./components/CredentialPhases";
import { ExecutionCredentials } from "./components/ExecutionCredentials";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  FolderGit2,
  KeyRound,
  ShieldCheck,
  SlidersHorizontal,
  Layers,
  Globe2,
  CircleCheck,
  CircleX,
  CircleAlert,
  CirclePause,
  CircleDashed,
  LoaderCircle,
} from "lucide-react";
import Composer from "./App";
import { LogViewer } from "./components/LogViewer";
import { OperationSelector } from "./components/OperationSelector";
import { reportLabel } from "./reportLabel";
import { api, ApiError } from "./api";
import { ErrorBanner } from "./components/ErrorBanner";
import { PlatformHeader } from "./components/PlatformHeader";
import { Permissions } from "./components/Permissions";
import { GitHubConnection } from "./components/GitHubConnection";
import { RepositoryFilterEditor } from "./components/RepositoryFilterEditor";
import { RepositorySelector } from "./components/RepositorySelector";
import { ConfigurationEditor } from "./components/ConfigurationEditor";
import { AdvancedRunOptions } from "./components/AdvancedRunOptions";
import { LatestRun } from "./components/LatestRun";
import { TokenTable } from "./components/TokenTable";
import type { TokenCounts, TokenMetadata, TokenStatus } from "./tokenMetadata";
import { Pagination } from "./components/Pagination";
import { pageCount } from "./pagination";
import type { Page } from "./pagination";
import { ApplicationContext, useApplication } from "./ApplicationContext";
import {
  composerUrl,
  EMPTY_WORKSPACE,
  parseWorkspace,
  sameWorkspaceStack,
  selectSoleTarget,
  workspaceAtUrl,
  workspaceKey,
  type WorkspaceSelection,
  type InspectionTargets,
} from "./workspaceSelection";
import { rememberReturnRoute, restoreReturnRoute } from "./navigation";
import { WorkspaceSettings } from "./components/WorkspaceSettings";
import { EffectiveConfiguration } from "./components/EffectiveConfiguration";
import { AuditLog } from "./components/AuditLog";
import {
  parseRunTarget,
  runTargetAtUrl,
  runTargetKey,
  runTargetUrl,
  type RunTarget,
} from "./runSelection";
import type { Session } from "./types";
import { ConfirmationProvider, useConfirmation } from "./components/Dialogs";
import "./platform.css";
import "./settings.css";

type Repository = {
  id: string;
  branch: string;
  enabled: number;
  deployment_enabled: number;
};
type Environment = {
  id: string;
  name: string;
  settings?: DeploymentSettings;
};
type DeploymentSettings = {
  gitops_root: string;
  discovery_mode: string;
  resolved_discovery_mode?: string;
  detected_revision?: string;
  lock_key: string;
  locked: boolean;
  automatic: boolean;
};
type DeploymentConfiguration = {
  deployment_enabled: boolean;
  settings: DeploymentSettings | null;
  inconsistent: boolean;
  environments: { name: string; active: boolean; revision: number }[];
};
type DeploymentCandidate = {
  gitops_root: string;
  requested_discovery_mode: string;
  discovery_mode: string;
  all_environments: string[];
  matrix: { environment: string; runfile: string; stack?: string }[];
  source_lockfiles: {
    status: "complete" | "partial" | "missing";
    present: string[];
    missing: string[];
    missing_environments: string[];
  };
};
type DeploymentDiscovery = {
  revision: string;
  deployments: DeploymentCandidate[];
};
type Run = RunSummary & {
  spec: {
    phases: string[];
    settings?: { discovery_mode: string };
    revision_override?: boolean;
    allow_local_backend?: boolean;
    tags?: string[];
  };
  result: {
    message?: string;
    reports?: Record<string, unknown>;
    plan_digest?: string;
    logs?: string;
  };
};
type EffectiveAccess = {
  administrator: boolean;
  capabilities: string[];
  assignments: { environment: string; role: string; workloads: string[] }[];
};

type Artifact = { id: string; name: string; digest: string };
async function request<T>(
  path: string,
  body?: unknown,
  method?: "PUT" | "DELETE",
): Promise<T> {
  return api<T>(
    `/v1${path}`,
    body,
    undefined,
    path === "/runs" && body !== undefined
      ? { "Idempotency-Key": crypto.randomUUID() }
      : undefined,
    method,
  );
}

function message(error: unknown) {
  return error instanceof Error ? error.message : "The request failed.";
}
function fields(event: React.FormEvent<HTMLFormElement>) {
  event.preventDefault();
  return new FormData(event.currentTarget);
}
const text = (data: FormData, name: string) => String(data.get(name) ?? "");
const endpoint = (repository: string) => `/repositories/${repository}`;

function actionLabel(action: string) {
  return (
    {
      plan: "Plan",
      apply: "Plan and apply",
      destroy: "Plan and destroy",
      "destroy-plan": "Destroy plan",
      "plan-operation": "Operations plan",
      "apply-operation": "Plan and apply operations",
    }[action] ?? action
  );
}

export default function PlatformApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    api<Session>("/session")
      .then((value) => {
        if (active) setSession(value);
      })
      .catch((failure) => {
        if (active) setError(message(failure));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  if (!session && !loading)
    return (
      <div className="platform-root">
        <PlatformHeader session={session} active="runs" onSignOut={() => {}} />
        {error && (
          <ErrorBanner message={error} signIn onDismiss={() => setError("")} />
        )}
        <main className="empty-state">
          <h1>Unable to load the platform</h1>
          <button className="button primary" onClick={() => location.reload()}>
            Retry
          </button>
        </main>
      </div>
    );
  if (!session)
    return <div className="platform-loading">Loading Stacksmith Platform…</div>;
  return (
    <ConfirmationProvider>
      <PlatformShell session={session} />
    </ConfirmationProvider>
  );
}

function PlatformShell({ session }: { session: Session }) {
  const [composerVisited, setComposerVisited] = useState(false);
  const [location, setLocation] = useState(() => {
    restoreReturnRoute();
    return new URL(window.location.href);
  });
  const [storageError, setStorageError] = useState(
    () => new URL(window.location.href).searchParams.get("auth_error") ?? "",
  );
  const [workspace, setWorkspace] = useState(() => {
    try {
      return workspaceAtUrl(
        new URL(window.location.href),
        parseWorkspace(
          localStorage.getItem(workspaceKey(session.login ?? "local-demo")),
        ),
      );
    } catch (error) {
      if (!(error instanceof DOMException)) throw error;
      return workspaceAtUrl(new URL(window.location.href), EMPTY_WORKSPACE);
    }
  });
  const guard = useRef<() => Promise<boolean>>(async () => true);
  const [targetError, setTargetError] = useState("");
  useEffect(() => {
    setTargetError("");
    if (!session.execution_enabled || !workspace.repository) return;
    const abort = new AbortController();
    const repository = workspace.repository;
    api<InspectionTargets>(
      `/inspection-targets?repository=${encodeURIComponent(repository)}`,
      undefined,
      abort.signal,
    )
      .then(({ targets }) => {
        if (!abort.signal.aborted) {
          if (
            workspace.environment &&
            !targets.some((target) => target.name === workspace.environment)
          )
            setTargetError(
              `The selected deployment ${workspace.environment} is unavailable. Choose another deployment or inspect a stack preview.`,
            );
          setWorkspace((selection) =>
            selection.repository === repository
              ? selectSoleTarget(selection, targets)
              : selection,
          );
        }
      })
      .catch((failure: unknown) => {
        if (!abort.signal.aborted)
          setTargetError(
            `Deployment targets could not be loaded. ${message(failure)}`,
          );
      });
    return () => abort.abort();
  }, [workspace.repository, workspace.environment, session.execution_enabled]);
  const intent = useRef(0);
  const mounted = useRef(true);
  const historyPosition = useRef({
    scope:
      window.history.state?.stacksmithHistory?.scope ?? crypto.randomUUID(),
    index: window.history.state?.stacksmithHistory?.index ?? 0,
  });
  const restoringHistory = useRef(false);
  const pendingHistory = useRef("");
  const current = useRef({ location, workspace });
  current.current = { location, workspace };
  const registerGuard = useCallback((value: () => Promise<boolean>) => {
    guard.current = value;
    return () => {
      if (guard.current === value) guard.current = async () => true;
    };
  }, []);
  function historyState() {
    return {
      ...window.history.state,
      stacksmithHistory: { ...historyPosition.current },
    };
  }
  useEffect(() => {
    mounted.current = true;
    window.history.replaceState(historyState(), "", window.location.href);
    return () => {
      mounted.current = false;
      intent.current++;
    };
  }, []);
  async function applyWorkspace(next: WorkspaceSelection): Promise<boolean> {
    const attempt = ++intent.current;
    if (
      !sameWorkspaceStack(current.current.workspace, next) &&
      !(await guard.current())
    )
      return false;
    if (attempt !== intent.current || !mounted.current) return false;
    const nextLocation = new URL(window.location.href);
    nextLocation.searchParams.delete("repo");
    nextLocation.searchParams.delete("environment");
    window.history.replaceState(historyState(), "", nextLocation);
    current.current = { location: nextLocation, workspace: next };
    setLocation(nextLocation);
    setWorkspace(next);
    return true;
  }
  async function navigate(
    href: string,
    options?: { replace?: boolean },
  ): Promise<boolean> {
    const attempt = ++intent.current;
    const next = new URL(
      href === "/"
        ? session.mode === "demo"
          ? "/#/example"
          : composerUrl(current.current.workspace)
        : href,
      window.location.origin,
    );
    const selection = workspaceAtUrl(next, current.current.workspace);
    if (
      !sameWorkspaceStack(current.current.workspace, selection) &&
      !(await guard.current())
    )
      return false;
    if (attempt !== intent.current || !mounted.current) return false;
    if (next.href === current.current.location.href) return true;
    if (!options?.replace) historyPosition.current.index++;
    window.history[options?.replace ? "replaceState" : "pushState"](
      historyState(),
      "",
      next,
    );
    current.current = { location: next, workspace: selection };
    setWorkspace(selection);
    setLocation(next);
    window.dispatchEvent(new Event("platform:navigate"));
    return true;
  }
  useEffect(() => {
    if (
      session.mode === "github" &&
      session.login &&
      location.pathname === "/" &&
      !location.hash &&
      !workspace.repository
    )
      navigate("/settings?section=workspace");
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(
        workspaceKey(session.login ?? "local-demo"),
        JSON.stringify(workspace),
      );
    } catch (error) {
      if (!(error instanceof DOMException)) throw error;
      setStorageError(
        "Workspace preferences could not be saved in this browser. Your current selection is still available in this tab.",
      );
    }
  }, [session.login, workspace]);
  useEffect(() => {
    async function followLocation() {
      const next = new URL(window.location.href);
      if (
        restoringHistory.current &&
        next.href === current.current.location.href
      ) {
        restoringHistory.current = false;
        return;
      }
      if (next.href === current.current.location.href) return;
      if (pendingHistory.current === next.href) return;
      pendingHistory.current = next.href;
      const attempt = ++intent.current;
      const targetPosition = window.history.state?.stacksmithHistory;
      const selection = workspaceAtUrl(next, current.current.workspace);
      const allowed =
        sameWorkspaceStack(current.current.workspace, selection) ||
        (await guard.current());
      if (attempt !== intent.current || !mounted.current) {
        if (pendingHistory.current === next.href) pendingHistory.current = "";
        return;
      }
      pendingHistory.current = "";
      if (!allowed) {
        if (
          targetPosition?.scope === historyPosition.current.scope &&
          targetPosition.index !== historyPosition.current.index
        ) {
          restoringHistory.current = true;
          window.history.go(
            historyPosition.current.index - targetPosition.index,
          );
        } else
          window.history.replaceState(
            historyState(),
            "",
            current.current.location,
          );
        return;
      }
      if (targetPosition?.scope === historyPosition.current.scope)
        historyPosition.current.index = targetPosition.index;
      else {
        historyPosition.current.index++;
        window.history.replaceState(historyState(), "", next);
      }
      current.current = { location: next, workspace: selection };
      setWorkspace(selection);
      setLocation(next);
      window.dispatchEvent(new Event("platform:navigate"));
    }
    function followLink(event: MouseEvent) {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const link =
        event.target instanceof Element ? event.target.closest("a") : null;
      if (!link || link.target || link.hasAttribute("download")) return;
      const next = new URL(link.href);
      if (
        next.origin !== window.location.origin ||
        !["/", "/runs", "/settings"].includes(next.pathname)
      )
        return;
      event.preventDefault();
      navigate(next.pathname + next.search + next.hash);
    }
    window.addEventListener("popstate", followLocation);
    window.addEventListener("hashchange", followLocation);
    window.addEventListener("platform:location", followLocation);
    document.addEventListener("click", followLink);
    followLocation();
    return () => {
      window.removeEventListener("popstate", followLocation);
      window.removeEventListener("hashchange", followLocation);
      window.removeEventListener("platform:location", followLocation);
      document.removeEventListener("click", followLink);
    };
  }, []);
  const active =
    location.pathname === "/settings" ||
    (session.mode === "github" &&
      !!session.login &&
      location.pathname === "/" &&
      !location.hash &&
      !workspace.repository)
      ? "settings"
      : location.pathname === "/runs" && session.execution_enabled
        ? "runs"
        : "composer";
  useEffect(() => {
    if (active === "composer") setComposerVisited(true);
  }, [active]);
  return (
    <ApplicationContext.Provider
      value={{
        session,
        workspace,
        location,
        navigate,
        applyWorkspace,
        registerGuard,
        targetError,
      }}
    >
      <div className="platform-root">
        <PlatformHeader
          session={session}
          active={active}
          onSignOut={async () => {
            const attempt = ++intent.current;
            if (
              !(await guard.current()) ||
              attempt !== intent.current ||
              !mounted.current
            )
              return;
            void api("/auth/logout", {})
              .then(() => window.location.reload())
              .catch((error) => setStorageError(message(error)));
          }}
        />
        {storageError && (
          <ErrorBanner
            message={storageError}
            signIn={false}
            onDismiss={() => setStorageError("")}
          />
        )}
        {session.mode === "github" && !session.login ? (
          <main className="welcome">
            <h1>Stacksmith Platform</h1>
            <p>Sign in to choose your workspace and manage your settings.</p>
            <a
              className="button primary"
              href="/api/auth/github"
              onClick={rememberReturnRoute}
            >
              Continue with GitHub
            </a>
          </main>
        ) : (
          <>
            {(active === "composer" || composerVisited) && (
              <div hidden={active !== "composer"}>
                <Composer />
              </div>
            )}
            {active !== "composer" && (
              <Dashboard
                key={session.login ?? "local-demo"}
                session={session}
              />
            )}
          </>
        )}
      </div>
    </ApplicationContext.Provider>
  );
}

function Dashboard({ session }: { session: Session }) {
  const { workspace, location, navigate } = useApplication();
  const [savedRunTarget, setSavedRunTarget] = useState(() => {
    try {
      return parseRunTarget(
        localStorage.getItem(runTargetKey(session.login ?? "local-demo")),
      );
    } catch (failure) {
      if (!(failure instanceof DOMException)) throw failure;
      return null;
    }
  });
  const runTarget = runTargetAtUrl(location, savedRunTarget ?? workspace);
  const repository =
    location.pathname === "/runs" ? runTarget.repository : workspace.repository;
  const environment =
    location.pathname === "/runs"
      ? runTarget.environment
      : workspace.environment;
  const currentRunTarget = useRef("");
  currentRunTarget.current = `${repository}:${environment}`;
  const autoEnvironment = useRef<string | null>(
    !location.searchParams.has("repo") &&
      !savedRunTarget &&
      !workspace.environment
      ? workspace.repository
      : null,
  );
  const [environmentRepository, setEnvironmentRepository] = useState("");
  const [accessTarget, setAccessTarget] = useState("");
  useEffect(() => {
    if (location.pathname !== "/runs") return;
    if (!location.searchParams.has("repo"))
      void navigate(
        runTargetUrl(
          { repository, environment },
          location.searchParams.get("run") ?? undefined,
        ),
        { replace: true },
      );
    setSavedRunTarget((previous) =>
      previous?.repository === repository &&
      previous.environment === environment
        ? previous
        : { repository, environment },
    );
    try {
      localStorage.setItem(
        runTargetKey(session.login ?? "local-demo"),
        JSON.stringify({ repository, environment }),
      );
    } catch (failure) {
      if (!(failure instanceof DOMException)) throw failure;
    }
  }, [location.href, repository, environment, session.login]);
  function chooseRunTarget(target: RunTarget, selectSole = false) {
    if (selectSole && target.repository === repository) return;
    autoEnvironment.current = selectSole ? target.repository : null;
    void navigate(runTargetUrl(target));
  }
  const [identity, setIdentity] = useState<{
    login: string;
    administrator: boolean;
    provisioner: boolean;
  } | null>(null);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [repositoriesLoading, setRepositoriesLoading] = useState(
    !!session.execution_enabled,
  );
  const [repositoryLoadError, setRepositoryLoadError] = useState("");
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [environmentsLoading, setEnvironmentsLoading] = useState(false);
  const [environmentsError, setEnvironmentsError] = useState("");
  const [environmentErrorRepository, setEnvironmentErrorRepository] =
    useState("");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runPage, setRunPage] = useState(1);
  const [runSize, setRunSize] = useState(20);
  const [runTotal, setRunTotal] = useState(0);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState("");
  const [runRefresh, setRunRefresh] = useState(0);
  const [access, setAccess] = useState<EffectiveAccess | null>(null);
  const [accessFailure, setAccessFailure] = useState<{
    target: string;
    message: string;
  } | null>(null);
  const can = (capability: string) =>
    accessTarget === `${repository}:${environment}` &&
    (access?.capabilities.includes(capability) ?? false);
  const canPlan = can("plan_infrastructure") || can("plan_operations");
  const [includeInfrastructure, setIncludeInfrastructure] = useState(true);
  const [runTags, setRunTags] = useState("");
  const [includeOperations, setIncludeOperations] = useState(false);
  const [applyAfterPlan, setApplyAfterPlan] = useState(false);
  const [destroyPlan, setDestroyPlan] = useState(false);
  const [runRevision, setRunRevision] = useState("");
  const [runLocalBackend, setRunLocalBackend] = useState(false);
  const runAction = destroyPlan
    ? applyAfterPlan
      ? "destroy"
      : "destroy-plan"
    : applyAfterPlan
      ? includeInfrastructure
        ? "apply"
        : "apply-operation"
      : includeInfrastructure
        ? "plan"
        : "plan-operation";
  const [selectedOperations, setSelectedOperations] = useState<string[] | null>(
    null,
  );
  const [settingsNotice, setSettingsNotice] = useState("");
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const tab =
    location.pathname === "/settings"
      ? location.searchParams.get("section") === "runs"
        ? "workspace"
        : (location.searchParams.get("section") ?? "workspace")
      : "runs";
  useEffect(() => setSettingsNotice(""), [tab, repository, environment]);
  const sections = [
    {
      id: "workspace",
      group: "Workspace",
      label: "Workspace",
      icon: FolderGit2,
      description:
        "Choose your active repository, Composer stack, and inspection environment.",
      available: true,
    },
    {
      id: "github",
      group: "Account",
      label: "GitHub connection",
      icon: FolderGit2,
      description: "Manage GitHub access and repository discovery.",
      available: session.mode === "github",
    },
    {
      id: "tokens",
      group: "Account",
      label: "API tokens",
      icon: KeyRound,
      description: "Create and revoke scoped API tokens for your account.",
      available: session.execution_enabled,
    },
    {
      id: "environments",
      group: "Repository",
      label: "Deployment settings",
      icon: SlidersHorizontal,
      description:
        "Configure deployment activation, discovery, and behavior for the active repository.",
      available: can("configure_environment"),
    },
    {
      id: "repository-config",
      group: "Repository",
      label: "Repository configuration",
      icon: Layers,
      description: "Configure the layers used by the active repository.",
      available: can("configure_repository"),
    },
    {
      id: "effective-config",
      group: "Repository",
      label: "Effective configuration",
      icon: Layers,
      description: "Inspect the configuration available to this workspace.",
      available: true,
    },
    {
      id: "credentials",
      group: "Repository",
      label: "Credentials",
      icon: KeyRound,
      description:
        "Manage phase-scoped credentials for the active environment.",
      available: can("manage_credentials"),
    },
    {
      id: "permissions",
      group: "Repository",
      label: "Stack roles",
      icon: ShieldCheck,
      description: "Manage repository and environment permissions.",
      available: !!identity?.administrator,
    },
    {
      id: "platform-config",
      group: "Administration",
      label: "Platform defaults",
      icon: Globe2,
      description: "Manage default configuration layers for repositories.",
      available: !!identity?.administrator,
    },
    {
      id: "audit",
      group: "Administration",
      label: "Audit log",
      icon: ShieldCheck,
      description: "Review platform activity across repositories and runs.",
      available: !!identity?.administrator,
    },
    {
      id: "health",
      group: "Administration",
      label: "Execution health",
      icon: Activity,
      description: "Inspect execution supervision and the deployment queue.",
      available: !!identity?.administrator,
    },
  ];
  const currentSection = sections.find(
    (item) => item.id === tab && item.available,
  );
  const historyReady =
    repositories.some((item) => item.id === repository) && !repositoryLoadError;
  const workspaceReady =
    historyReady &&
    environmentRepository === repository &&
    environments.some((item) => item.name === environment) &&
    !repositoryLoadError;
  const currentEnvironmentError =
    environmentErrorRepository === repository ? environmentsError : "";
  const environmentPending =
    historyReady &&
    !currentEnvironmentError &&
    (environmentsLoading || environmentRepository !== repository);
  const currentAccessFailure =
    accessFailure?.target === `${repository}:${environment}`
      ? accessFailure.message
      : "";
  const accessPending =
    workspaceReady &&
    !currentAccessFailure &&
    accessTarget !== `${repository}:${environment}`;
  useEffect(() => {
    setRunPage(1);
    setSelected("");
    setSelectedOperations(null);
    setApplyAfterPlan(false);
    setDestroyPlan(false);
    setRunRevision("");
    setRunLocalBackend(false);
  }, [repository, environment]);
  useEffect(() => {
    if (location.pathname === "/runs")
      setSelected(location.searchParams.get("run") ?? "");
  }, [location.href]);
  const settingsSection = tab !== "runs";
  useEffect(() => {
    if (!session.execution_enabled) return;
    let active = true;
    request<{ login: string; administrator: boolean; provisioner: boolean }>(
      "/me",
    )
      .then((user) => {
        if (active) setIdentity(user);
      })
      .catch((failure) => {
        if (active) setError(message(failure));
      });
    return () => {
      active = false;
    };
  }, [refresh, session.execution_enabled]);
  useEffect(() => {
    if (!session.execution_enabled) return;
    let active = true;
    setRepositoriesLoading(true);
    setRepositoryLoadError("");
    request<Repository[]>("/repositories")
      .then((repos) => {
        if (active) {
          setRepositories(repos);
        }
      })
      .catch((failure) => {
        if (active) {
          setRepositoryLoadError(message(failure));
          setError(message(failure));
        }
      })
      .finally(() => {
        if (active) setRepositoriesLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refresh, session.execution_enabled]);
  useEffect(() => {
    if (
      !session.execution_enabled ||
      !repository ||
      !["runs", "environments", "credentials"].includes(tab)
    )
      return;
    let active = true;
    setEnvironments([]);
    setEnvironmentRepository("");
    setEnvironmentsLoading(true);
    setEnvironmentsError("");
    setEnvironmentErrorRepository("");
    request<Environment[]>(`${endpoint(repository)}/environments`)
      .then((items) => {
        if (active) {
          setEnvironments(items);
          setEnvironmentRepository(repository);
          if (tab === "runs" && autoEnvironment.current === repository) {
            autoEnvironment.current = null;
            if (items.length === 1)
              void navigate(
                runTargetUrl({ repository, environment: items[0].name }),
                { replace: true },
              );
          }
        }
      })
      .catch((error) => {
        if (active) {
          setEnvironmentsError(message(error));
          setEnvironmentErrorRepository(repository);
        }
      })
      .finally(() => {
        if (active) setEnvironmentsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [repository, refresh, tab, session.execution_enabled]);
  useEffect(() => {
    if (tab !== "runs" || !historyReady) return;
    let active = true;
    const load = () =>
      request<Page<RunSummary>>(
        `/runs?repository=${encodeURIComponent(repository)}${environment ? `&environment=${encodeURIComponent(environment)}` : ""}&page=${runPage}&page_size=${runSize}`,
      )
        .then((result) => {
          if (active) {
            setRunsError("");
            setRuns(result.items);
            setRunTotal(result.total);
            setRunPage((current) =>
              Math.min(current, pageCount(result.total, runSize)),
            );
          }
        })
        .catch((error) => {
          if (active) setRunsError(message(error));
        })
        .finally(() => {
          if (active) setRunsLoading(false);
        });
    setRunsError("");
    setRuns([]);
    setRunsLoading(true);
    void load();
    const timer = setInterval(load, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [
    repository,
    environment,
    historyReady,
    tab,
    runPage,
    runSize,
    runRefresh,
  ]);
  useEffect(() => {
    setAccess(null);
    setAccessTarget("");
    setAccessFailure(null);
    if (!session.execution_enabled || !repository) return;
    let active = true;
    let initialized = false;
    const load = () =>
      request<EffectiveAccess>(
        `${endpoint(repository)}/access?environment=${encodeURIComponent(environment || "*")}`,
      )
        .then((value) => {
          if (!active) return;
          setAccess(value);
          setAccessTarget(`${repository}:${environment}`);
          setAccessFailure(null);
          if (!initialized) {
            const infrastructure = value.capabilities.includes(
              "plan_infrastructure",
            );
            setIncludeInfrastructure(infrastructure);
            setIncludeOperations(
              !infrastructure && value.capabilities.includes("plan_operations"),
            );
            setApplyAfterPlan(false);
            setDestroyPlan(false);
            initialized = true;
          }
        })
        .catch((failure) => {
          if (active) {
            setAccess(null);
            setAccessFailure({
              target: `${repository}:${environment}`,
              message: message(failure),
            });
            setError(message(failure));
          }
        });
    void load();
    const timer = setInterval(load, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [repository, environment, refresh, session.execution_enabled]);
  async function start(event: React.FormEvent<HTMLFormElement>) {
    const data = fields(event);
    if (!workspaceReady || !canPlan) return;
    const target = `${repository}:${environment}`;
    const revision = text(data, "revision").trim();
    const selectedOperationPhase =
      data.has("include_operations") && !!selectedOperations?.length;
    const selectedAction = destroyPlan
      ? applyAfterPlan
        ? "destroy"
        : "destroy-plan"
      : applyAfterPlan
        ? includeInfrastructure
          ? "apply"
          : "apply-operation"
        : includeInfrastructure
          ? "plan"
          : "plan-operation";
    try {
      const run = await request<Run>("/runs", {
        repository,
        environment,
        ...(identity?.administrator && revision ? { revision } : {}),
        action: selectedAction,
        include_infrastructure: data.has("include_infrastructure"),
        include_operations: selectedOperationPhase,
        apply_after_plan: data.has("apply_after_plan"),
        destroy_plan: data.has("destroy_plan"),
        allow_local_backend:
          identity?.administrator === true && data.has("allow_local_backend"),
        operations:
          selectedOperationPhase && !data.has("all_operations")
            ? (selectedOperations ?? [])
            : [],
        tags: includeInfrastructure
          ? runTags
              .split(",")
              .map((tag) => tag.trim())
              .filter(Boolean)
          : [],
      });
      if (currentRunTarget.current !== target) return;
      setSelected(run.id);
      void navigate(runTargetUrl({ repository, environment }, run.id), {
        replace: true,
      });
      setRunPage(1);
      setRunRefresh((value) => value + 1);
      setError("");
    } catch (error) {
      if (currentRunTarget.current === target) setError(message(error));
    }
  }
  return (
    <div className="platform-workspace">
      {error && (
        <ErrorBanner message={error} signIn onDismiss={() => setError("")} />
      )}
      <main className="platform-main">
        <header>
          <div>
            <h1>{settingsSection ? "Settings" : "Runs"}</h1>
            <p>
              {settingsSection
                ? "Manage your workspace, account, and platform."
                : "Review changes and control deployments."}
            </p>
          </div>
        </header>
        <div
          className={
            settingsSection
              ? "platform-admin-layout settings-layout"
              : undefined
          }
        >
          {settingsSection && (
            <nav className="platform-admin-nav" aria-label="Settings sections">
              {["Workspace", "Account", "Repository", "Administration"].map(
                (group) => {
                  const items = sections.filter(
                    (section) => section.group === group && section.available,
                  );
                  return items.length ? (
                    <div className="platform-admin-group" key={group}>
                      <p className="settings-nav-group-title">{group}</p>
                      {items.map((section) => (
                        <a
                          className="settings-section-link"
                          key={section.id}
                          aria-current={tab === section.id ? "page" : undefined}
                          href={`/settings?section=${section.id}`}
                        >
                          <section.icon size={17} />
                          {section.label}
                        </a>
                      ))}
                    </div>
                  ) : null;
                },
              )}
            </nav>
          )}
          <div className="platform-workspace-content">
            {settingsSection && (
              <SettingsPageHeader
                title={currentSection?.label ?? "Settings unavailable"}
                description={
                  currentSection?.description ??
                  "This section is unavailable for your account or workspace. Choose an available section."
                }
                context={
                  currentSection?.group === "Repository" ? (
                    <>
                      {repository || "No repository selected"}
                      {environment &&
                        tab !== "environments" &&
                        ` / ${environment}`}{" "}
                      ·{" "}
                      <a href="/settings?section=workspace">Change workspace</a>
                    </>
                  ) : undefined
                }
              />
            )}
            {settingsSection && !currentSection && (
              <SettingsPanel title="Access unavailable">
                <SettingsFeedback>
                  Choose an available section from the Settings navigation.
                </SettingsFeedback>
              </SettingsPanel>
            )}
            {tab === "workspace" && <WorkspaceSettings />}
            {tab === "github" && session.mode === "github" && (
              <GitHubSettings
                administrator={!!identity?.administrator}
                onSaved={() => setRefresh((value) => value + 1)}
                onError={setError}
              />
            )}
            {tab === "effective-config" && <EffectiveConfiguration />}
            {tab === "audit" && identity?.administrator && <AuditLog />}
            {tab === "environments" && identity?.administrator && (
              <DeploymentSetup
                repository={repositories.find((item) => item.id === repository)}
                notice={settingsNotice}
                onSaved={(notice) => {
                  setSettingsNotice(notice ?? "");
                  setRefresh((value) => value + 1);
                }}
                onError={setError}
              />
            )}
            {tab === "platform-config" && identity?.administrator && (
              <ConfigurationEditor
                onError={setError}
                onSaved={() => setRefresh((value) => value + 1)}
              />
            )}
            {tab === "repository-config" &&
              can("configure_repository") &&
              (repository ? (
                <ConfigurationEditor
                  key={repository}
                  repository={repository}
                  onError={setError}
                  onSaved={() => setRefresh((value) => value + 1)}
                />
              ) : (
                <SettingsPanel title="Configuration policy">
                  <SettingsFeedback>
                    Select a repository in{" "}
                    <a href="/settings?section=workspace">Workspace settings</a>{" "}
                    to configure it.
                  </SettingsFeedback>
                </SettingsPanel>
              ))}
            {tab === "runs" && (
              <>
                <section
                  className="platform-card run-launch-panel"
                  aria-label="Run setup"
                >
                  <div className="run-target-toolbar" aria-label="Run target">
                    <RepositorySelector
                      repositories={repositories}
                      selected={repository}
                      loading={repositoriesLoading}
                      error={repositoryLoadError}
                      onSelect={(value) =>
                        chooseRunTarget(
                          { repository: value, environment: "" },
                          true,
                        )
                      }
                    />
                    <label>
                      Environment
                      <select
                        value={environment}
                        disabled={
                          !historyReady ||
                          environmentsLoading ||
                          environmentRepository !== repository
                        }
                        onChange={(event) =>
                          chooseRunTarget({
                            repository,
                            environment: event.target.value,
                          })
                        }
                      >
                        <option value="">All environments</option>
                        {environment &&
                          (environmentRepository !== repository ||
                            !environments.some(
                              (item) => item.name === environment,
                            )) && (
                            <option value={environment}>
                              {environment}
                              {repositoriesLoading || environmentPending
                                ? " (loading…)"
                                : currentEnvironmentError
                                  ? " (unverified)"
                                  : " (unavailable)"}
                            </option>
                          )}
                        {environmentRepository === repository &&
                          environments.map((item) => (
                            <option key={item.name} value={item.name}>
                              {item.name}
                            </option>
                          ))}
                      </select>
                    </label>
                  </div>
                  <div className="run-setup-layout">
                    <div className="run-setup-controls">
                      <h2>Start a run</h2>
                      <div className="run-launch-content">
                        {repositoriesLoading ? (
                          <p role="status">Loading repositories…</p>
                        ) : !historyReady ? (
                          <div className="run-target-guidance">
                            <h3>
                              {repositoryLoadError
                                ? "Repositories unavailable"
                                : repository
                                  ? "Repository unavailable"
                                  : "Choose a repository"}
                            </h3>
                            <p>
                              {repositoryLoadError ||
                                (repository
                                  ? "The selected repository is unavailable. Choose another repository or review your access."
                                  : "Choose a repository to view history and start runs.")}
                            </p>
                            {repositoryLoadError && (
                              <button
                                type="button"
                                onClick={() => setRefresh((value) => value + 1)}
                              >
                                Retry repositories
                              </button>
                            )}
                          </div>
                        ) : environmentPending ? (
                          <p role="status">Loading deployment environments…</p>
                        ) : currentEnvironmentError ? (
                          <div role="alert">
                            <p>{currentEnvironmentError}</p>
                            <button
                              type="button"
                              onClick={() => setRefresh((value) => value + 1)}
                            >
                              Retry environments
                            </button>
                          </div>
                        ) : !workspaceReady ? (
                          <div className="run-target-guidance">
                            <h3>
                              {environment
                                ? "Environment unavailable"
                                : "Choose a deployment environment to start a run"}
                            </h3>
                            <p>
                              Run history is available below. Choose an active
                              environment to start a run.
                              {!environments.length &&
                                !can("configure_environment") &&
                                " Ask an administrator to configure a deployment or grant access."}
                            </p>
                            {identity?.administrator &&
                              !environments.length && (
                                <a
                                  className="button"
                                  href={`/settings?section=environments&repo=${encodeURIComponent(repository)}`}
                                >
                                  Configure deployments
                                </a>
                              )}
                          </div>
                        ) : accessPending ? (
                          <p role="status">Loading permissions…</p>
                        ) : currentAccessFailure ? (
                          <div role="alert">
                            <p>{currentAccessFailure}</p>
                            <button
                              type="button"
                              onClick={() => setRefresh((value) => value + 1)}
                            >
                              Retry permissions
                            </button>
                          </div>
                        ) : !canPlan ? (
                          <p className="platform-notice">
                            Your stack role allows viewing history but does not
                            allow starting runs.
                          </p>
                        ) : null}
                        {!repositoriesLoading &&
                          !environmentPending &&
                          !currentEnvironmentError &&
                          !accessPending &&
                          !currentAccessFailure &&
                          canPlan &&
                          workspaceReady && (
                            <form
                              key={`${repository}:${environment}`}
                              className="platform-run-form"
                              onSubmit={start}
                            >
                              <p className="platform-run-source">
                                Uses the latest default-branch commit.
                              </p>

                              <fieldset className="run-include-options">
                                <legend>Include</legend>
                                <label className="phase-choice">
                                  <input
                                    type="checkbox"
                                    name="include_infrastructure"
                                    disabled={!can("plan_infrastructure")}
                                    checked={includeInfrastructure}
                                    onChange={(event) => {
                                      const checked = event.target.checked;
                                      setIncludeInfrastructure(checked);
                                      if (!checked) {
                                        setDestroyPlan(false);
                                        if (!includeOperations)
                                          setApplyAfterPlan(false);
                                      }
                                    }}
                                  />
                                  <span>Infrastructure</span>
                                </label>
                                <label className="phase-choice">
                                  <input
                                    type="checkbox"
                                    name="include_operations"
                                    disabled={!can("plan_operations")}
                                    checked={includeOperations}
                                    onChange={(event) => {
                                      const checked = event.target.checked;
                                      setIncludeOperations(checked);
                                      if (!checked && !includeInfrastructure) {
                                        setApplyAfterPlan(false);
                                      }
                                    }}
                                  />
                                  <span>Operations</span>
                                </label>
                              </fieldset>
                              {includeInfrastructure && (
                                <label>
                                  Component tags
                                  <input
                                    name="tags"
                                    value={runTags}
                                    onChange={(event) =>
                                      setRunTags(event.target.value)
                                    }
                                    placeholder="application, production"
                                  />
                                  <small className="platform-muted">
                                    Separate tags with commas. Components must
                                    match all tags; leave empty to target all
                                    components.
                                    {includeOperations &&
                                      " Tags do not filter operations."}
                                  </small>
                                </label>
                              )}
                              {can("plan_operations") && (
                                <OperationSelector
                                  repository={repository}
                                  environment={environment}
                                  visible={includeOperations}
                                  selected={selectedOperations}
                                  onChange={setSelectedOperations}
                                  onError={setError}
                                />
                              )}
                              {includeOperations &&
                                selectedOperations?.length === 0 && (
                                  <p className="platform-muted">
                                    No operations selected.
                                  </p>
                                )}
                              <fieldset className="run-execution-options">
                                <legend>Execution</legend>
                                <label className="phase-choice">
                                  <input
                                    type="checkbox"
                                    name="apply_after_plan"
                                    checked={applyAfterPlan}
                                    disabled={
                                      !includeInfrastructure &&
                                      !includeOperations
                                    }
                                    onChange={(event) =>
                                      setApplyAfterPlan(event.target.checked)
                                    }
                                  />
                                  <span>Apply after approval</span>
                                </label>
                                <small className="platform-muted">
                                  Pause for approval before making changes.
                                </small>
                                {can("plan_infrastructure") && (
                                  <>
                                    <label className="phase-choice">
                                      <input
                                        type="checkbox"
                                        name="destroy_plan"
                                        checked={destroyPlan}
                                        disabled={!includeInfrastructure}
                                        onChange={(event) =>
                                          setDestroyPlan(event.target.checked)
                                        }
                                      />
                                      <span>Plan to destroy</span>
                                    </label>
                                    <small className="platform-muted">
                                      Preview removal of infrastructure. With
                                      apply enabled, destruction requires
                                      approval.
                                    </small>
                                  </>
                                )}
                              </fieldset>
                              {destroyPlan && (
                                <p className="platform-notice">
                                  {applyAfterPlan
                                    ? "Destruction removes infrastructure after approval."
                                    : "This run previews destruction only. Removing infrastructure requires a later approved apply run."}
                                </p>
                              )}
                              <AdvancedRunOptions
                                administrator={!!identity?.administrator}
                                action={runAction}
                                revision={runRevision}
                                onRevisionChange={setRunRevision}
                                localBackend={runLocalBackend}
                                onLocalBackendChange={setRunLocalBackend}
                              />
                              <button
                                className="run-start-button"
                                disabled={
                                  !repository ||
                                  !environment ||
                                  (includeInfrastructure &&
                                    !can(
                                      applyAfterPlan
                                        ? "execute_infrastructure"
                                        : "plan_infrastructure",
                                    )) ||
                                  (includeOperations &&
                                    !can(
                                      applyAfterPlan
                                        ? "execute_operations"
                                        : "plan_operations",
                                    )) ||
                                  (!includeInfrastructure &&
                                    !includeOperations) ||
                                  (includeOperations &&
                                    selectedOperations === null) ||
                                  (!includeInfrastructure &&
                                    !selectedOperations?.length)
                                }
                              >
                                {destroyPlan
                                  ? applyAfterPlan
                                    ? "Plan destruction and request approval"
                                    : "Plan destruction"
                                  : "Start run"}
                              </button>
                            </form>
                          )}
                      </div>
                    </div>
                    <LatestRun
                      key={JSON.stringify([
                        repository,
                        environment,
                        historyReady,
                      ])}
                      repository={repository}
                      environment={environment}
                      ready={historyReady}
                      refresh={runRefresh}
                      actionLabel={actionLabel}
                      runUrl={(id) =>
                        runTargetUrl({ repository, environment }, id)
                      }
                    />{" "}
                  </div>
                </section>
                {historyReady && (
                  <div className="platform-columns">
                    <section className="platform-card">
                      <h2>
                        {environment
                          ? `Run history: ${environment}`
                          : "Repository run history"}
                      </h2>
                      {runsError ? (
                        <div role="alert">
                          <p>{runsError}</p>
                          <button
                            onClick={() => setRunRefresh((value) => value + 1)}
                          >
                            Retry run history
                          </button>
                        </div>
                      ) : runsLoading ? (
                        <p role="status">Loading run history…</p>
                      ) : runs.length === 0 ? (
                        <p>
                          {environment
                            ? "No runs in this environment yet."
                            : "No runs in this repository yet."}
                        </p>
                      ) : (
                        <ul className="platform-history">
                          {runs.map((run) => (
                            <li key={run.id}>
                              <button
                                onClick={() =>
                                  void navigate(
                                    runTargetUrl(
                                      { repository, environment },
                                      run.id,
                                    ),
                                  )
                                }
                                aria-pressed={selected === run.id}
                              >
                                <RunStatusIcon status={run.status} />
                                <span className="run-history-content">
                                  <strong>{actionLabel(run.action)}</strong>
                                  {!environment && (
                                    <span>{run.environment}</span>
                                  )}
                                  <span
                                    className={`platform-status ${run.status}`}
                                  >
                                    {run.status.replaceAll("_", " ")}
                                  </span>
                                  <code>{run.revision.slice(0, 10)}</code>
                                  <small>
                                    {new Date(
                                      run.created * 1000,
                                    ).toLocaleString()}{" "}
                                    · {run.actor}
                                  </small>
                                </span>
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                      <Pagination
                        label="Run history"
                        page={runPage}
                        size={runSize}
                        total={runTotal}
                        loading={runsLoading}
                        onPage={setRunPage}
                        onSize={(value) => {
                          setRunSize(value);
                          setRunPage(1);
                        }}
                      />
                    </section>
                    {selected && (
                      <RunDetail
                        key={selected}
                        id={selected}
                        administrator={!!identity?.administrator}
                        onDelete={() => {
                          setSelected("");
                          setRunRefresh((value) => value + 1);
                        }}
                        onError={setError}
                      />
                    )}
                  </div>
                )}
              </>
            )}
            {((tab === "credentials" && can("manage_credentials")) ||
              (tab === "permissions" && identity?.administrator)) && (
              <Settings
                key={`${repository}:${environment}:${refresh}`}
                repository={repository}
                environment={environment}
                section={tab}
                notice={settingsNotice}
                onSaved={(notice) => {
                  setSettingsNotice(notice ?? "");
                  setRefresh((value) => value + 1);
                }}
                onError={setError}
              />
            )}
            {tab === "health" && identity?.administrator && (
              <ExecutionHealth onError={setError} />
            )}
            {tab === "tokens" && session.execution_enabled && (
              <Tokens
                repositories={repositories}
                repositoriesLoading={repositoriesLoading}
                repositoryLoadError={repositoryLoadError}
                onError={setError}
              />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function RunDetail({
  id,
  administrator,
  onDelete,
  onError,
}: {
  id: string;
  administrator: boolean;
  onDelete: () => void;
  onError: (value: string) => void;
}) {
  const [run, setRun] = useState<Run | null>(null);
  const [logs, setLogs] = useState("");
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [busy, setBusy] = useState(false);
  const [capabilities, setCapabilities] = useState<string[]>([]);

  useEffect(() => {
    let active = true;
    let updated = 0;
    const load = async () => {
      try {
        const current = await request<Run>(`/runs/${id}`);
        const currentAccess = await request<EffectiveAccess>(
          `${endpoint(current.repository)}/access?environment=${encodeURIComponent(current.environment)}`,
        );
        if (!active) return;
        setCapabilities(currentAccess.capabilities);
        setRun(current);
        setLogs(current.result.logs ?? "");
        if (current.updated !== updated) {
          const files = await request<Artifact[]>(`/runs/${id}/artifacts`);
          if (!active) return;
          setArtifacts(files);
          updated = current.updated;
        }
        if (
          ["succeeded", "failed", "cancelled", "expired"].includes(
            current.status,
          )
        )
          clearInterval(timer);
      } catch (error) {
        if (active) onError(message(error));
      }
    };
    void load();
    const timer = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [id, onError]);
  async function act(action: string, body: unknown = {}) {
    setBusy(true);
    try {
      await request(`/runs/${id}/${action}`, body);
      setRun(await request<Run>(`/runs/${id}`));
    } catch (error) {
      onError(message(error));
    } finally {
      setBusy(false);
    }
  }
  const allowed = (action: string) => {
    if (!run) return false;
    const workloads = [
      ...(run.spec.phases.some((phase) =>
        ["plan", "apply", "destroy"].includes(phase),
      )
        ? ["infrastructure"]
        : []),
      ...(run.spec.phases.some((phase) =>
        ["plan-operation", "operation"].includes(phase),
      )
        ? ["operations"]
        : []),
    ];
    return (
      workloads.length > 0 &&
      workloads.every((workload) =>
        capabilities.includes(`${action}_${workload}`),
      )
    );
  };
  if (!run) return <section className="platform-card">Loading run…</section>;
  return (
    <section className="platform-card platform-detail">
      <h2>
        {actionLabel(run.action)} ·{" "}
        {run.spec.settings?.discovery_mode === "single"
          ? "Repository default"
          : run.environment}
      </h2>
      <p className={`platform-status ${run.status}`}>
        {run.status.replaceAll("_", " ")}
      </p>
      <RunChangeSummary reports={run.result.reports ?? {}} />
      {run.spec.revision_override && (
        <p className="platform-notice">
          Administrator commit override. Deployment approval also requires a
          platform administrator.
        </p>
      )}
      <dl>
        <dt>Revision</dt>
        <dd>
          <code>{run.revision}</code>
        </dd>
        {run.spec.phases.includes("plan") && (
          <>
            <dt>Component tags</dt>
            <dd>
              {run.spec.tags?.length
                ? run.spec.tags.join(", ")
                : "All components"}
            </dd>
          </>
        )}
        {["succeeded", "failed", "cancelled", "expired", "uncertain"].includes(
          run.status,
        ) ? (
          <>
            <dt>Finished</dt>
            <dd>{new Date(run.updated * 1000).toLocaleString()}</dd>
          </>
        ) : (
          <>
            <dt>Phase</dt>
            <dd>{run.spec.phases[run.phase]?.replaceAll("-", " ")}</dd>
          </>
        )}
        {run.expires && (
          <>
            <dt>Approval expires</dt>
            <dd>{new Date(run.expires * 1000).toLocaleString()}</dd>
          </>
        )}
      </dl>
      {run.result.message && <p role="status">{run.result.message}</p>}
      {run.spec.allow_local_backend && (
        <p className="platform-notice">
          Local-backend test plan. This run’s local state is not reused by later
          runs.
        </p>
      )}
      {run.status === "awaiting_authorization" && allowed("approve") && (
        <div className="platform-approval">
          <p>
            This PR revision can execute code. Authorizing it allows access to
            the configured plan credentials.
          </p>
          <button disabled={busy} onClick={() => void act("authorize")}>
            Authorize this revision
          </button>
        </div>
      )}
      {run.status === "awaiting_approval" && allowed("approve") && (
        <div className="platform-approval">
          <p>
            Review the reports before approving {run.action}. Infrastructure
            execution uses the saved plan. Operations may replan against current
            outputs.
            {!!run.spec.tags?.length &&
              run.spec.phases.includes("operation") &&
              " Component tags do not filter operations."}
          </p>
          {run.result.plan_digest && <code>{run.result.plan_digest}</code>}
          <button disabled={busy} onClick={() => void act("approve")}>
            Approve {run.action}
          </button>
        </div>
      )}
      {run.status === "uncertain" && allowed("reconcile") && (
        <form
          onSubmit={(event) => {
            const data = fields(event);
            void act("resolve", { note: text(data, "note") });
          }}
        >
          <p>
            Inspect the remote infrastructure and state before releasing this
            deployment lock.
          </p>
          <SettingsField label="Reconciliation record">
            <textarea name="note" minLength={10} required />
          </SettingsField>
          <button disabled={busy}>
            Record reconciliation and release lock
          </button>
        </form>
      )}
      {allowed("execute") &&
        !["succeeded", "failed", "cancelled", "expired", "uncertain"].includes(
          run.status,
        ) && (
          <button disabled={busy} onClick={() => void act("cancel")}>
            Cancel run
          </button>
        )}
      {administrator &&
        ["succeeded", "failed", "cancelled", "expired", "uncertain"].includes(
          run.status,
        ) && (
          <button
            disabled={busy}
            onClick={() => {
              setBusy(true);
              request(`/runs/${id}`, undefined, "DELETE")
                .then(onDelete)
                .catch((error) => onError(message(error)))
                .finally(() => setBusy(false));
            }}
          >
            Delete run
          </button>
        )}
      <RunReportDetails reports={run.result.reports ?? {}} />
      {artifacts.length > 0 && (
        <details className="report-artifacts">
          <summary>Reports ({artifacts.length})</summary>
          <ul>
            {artifacts.map((item) => (
              <li key={item.id}>
                <button
                  onClick={() => {
                    void request<{ url: string }>(
                      `/runs/${id}/artifacts/${item.id}`,
                    )
                      .then((data) =>
                        window.open(data.url, "_blank", "noopener,noreferrer"),
                      )
                      .catch((error) => onError(message(error)));
                  }}
                >
                  {reportLabel(item.name)}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      <h3>Phase logs</h3>
      <LogViewer key={id} logs={logs || "Waiting for execution output…"} />
      {administrator && (
        <a href={`/settings?section=audit&run_id=${encodeURIComponent(id)}`}>
          View audit log
        </a>
      )}
    </section>
  );
}

const changeLabels = ["create", "update", "replace", "destroy"] as const;

function RunStatusIcon({ status }: { status: string }) {
  const Icon =
    status === "succeeded"
      ? CircleCheck
      : status === "failed"
        ? CircleX
        : status === "uncertain"
          ? CircleAlert
          : status.startsWith("awaiting_")
            ? CirclePause
            : status === "running" || status === "cancelling"
              ? LoaderCircle
              : CircleDashed;
  return (
    <Icon
      aria-hidden="true"
      className={`run-status-icon ${status}`}
      size={20}
    />
  );
}

function reportTotals(value: unknown): Record<string, number> {
  if (
    !value ||
    typeof value !== "object" ||
    !("totals" in value) ||
    !value.totals ||
    typeof value.totals !== "object"
  )
    return {};
  return Object.fromEntries(
    Object.entries(value.totals).filter(
      (entry): entry is [string, number] =>
        typeof entry[1] === "number" && Number.isFinite(entry[1]),
    ),
  );
}

function primaryReportName(
  reports: Record<string, unknown>,
): string | undefined {
  return ["apply-summary.json", "plan-summary.json"].find((name) =>
    changeLabels.some((label) => label in reportTotals(reports[name])),
  );
}

function RunChangeSummary({ reports }: { reports: Record<string, unknown> }) {
  const name = primaryReportName(reports);
  const report = name ? reports[name] : undefined;
  const totals = reportTotals(report);
  return (
    <section className="run-change-summary" aria-label="Resource changes">
      <div className="run-change-heading">
        <h3>Resource changes</h3>
        {name && <span>{reportLabel(name)}</span>}
      </div>
      {name ? (
        <>
          {report &&
            typeof report === "object" &&
            "complete" in report &&
            report.complete === false && (
              <p className="run-coverage-warning">
                Incomplete report: counts may not include all changes.
              </p>
            )}
          <dl>
            {changeLabels.map((label) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{totals[label] ?? "—"}</dd>
              </div>
            ))}
          </dl>
        </>
      ) : (
        <p>Change counts unavailable.</p>
      )}
    </section>
  );
}

function RunReportDetails({ reports }: { reports: Record<string, unknown> }) {
  const primary = primaryReportName(reports);
  const summaries = Object.entries(reports)
    .map(([name, value]) => ({
      name,
      complete:
        value &&
        typeof value === "object" &&
        "complete" in value &&
        typeof value.complete === "boolean"
          ? value.complete
          : undefined,
      totals: Object.entries(reportTotals(value)).filter(
        ([label]) =>
          name !== primary ||
          !changeLabels.includes(label as (typeof changeLabels)[number]),
      ),
    }))
    .filter(({ totals }) => totals.length > 0);
  if (!summaries.length) return null;
  return (
    <details className="run-report-details">
      <summary>Additional report totals</summary>
      {summaries.map(({ name, totals, complete }) => (
        <ReportSummary
          key={name}
          name={name}
          totals={totals}
          complete={complete}
        />
      ))}
    </details>
  );
}

function ReportSummary({
  name,
  totals,
  complete,
}: {
  name: string;
  totals: [string, number][];
  complete?: boolean;
}) {
  if (!totals.length) return null;
  return (
    <article className="platform-report">
      <h4>{reportLabel(name)}</h4>
      {complete !== undefined && (
        <p>Reported coverage: {complete ? "complete" : "incomplete"}</p>
      )}
      <dl>
        {totals.map(([label, count]) => (
          <div key={label}>
            <dt>{label.replaceAll("_", " ")}</dt>
            <dd>{count}</dd>
          </div>
        ))}
      </dl>
    </article>
  );
}

function GitHubSettings({
  administrator,
  onSaved,
  onError,
}: {
  administrator: boolean;
  onSaved: () => void;
  onError: (value: string) => void;
}) {
  return (
    <section className="platform-settings github-settings">
      <GitHubConnection />
      {administrator && (
        <RepositoryFilterEditor onSaved={onSaved} onError={onError} />
      )}
    </section>
  );
}

function deploymentModeLabel(mode: string) {
  return (
    {
      single: "Single deployment",
      folders: "Environment folders",
      "flat-files": "Environment files in one directory",
      "env-files": "Environment files",
    }[mode] ?? mode
  );
}

function DeploymentSetup({
  repository,
  onSaved,
  notice,
  onError,
}: {
  repository: Repository | undefined;
  onSaved: (notice?: string) => void;
  notice: string;
  onError: (value: string) => void;
}) {
  const {
    error: requestError,
    reportError,
    clearError,
  } = useSettingsError(onError);
  const { confirm } = useConfirmation();
  const [configuration, setConfiguration] = useState<DeploymentConfiguration>();
  const [discovery, setDiscovery] = useState<DeploymentDiscovery>();
  const [selectedRoot, setSelectedRoot] = useState("");
  const [customRoot, setCustomRoot] = useState("");
  const [discoveryMode, setDiscoveryMode] = useState("auto");
  const [automatic, setAutomatic] = useState<boolean>();
  const [locked, setLocked] = useState(false);
  const [lockKey, setLockKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    if (!repository) return;
    setLoading(true);
    clearError();
    try {
      const current = await request<DeploymentConfiguration>(
        `${endpoint(repository.id)}/deployment-configuration`,
      );
      const detected = await request<DeploymentDiscovery>(
        `${endpoint(repository.id)}/deployment-discovery`,
        {},
      );
      setConfiguration(current);
      setDiscovery(detected);
      const settings = current.settings;
      const candidate =
        detected.deployments.find(
          (item) => item.gitops_root === (settings?.gitops_root ?? ""),
        ) ??
        (detected.deployments.length === 1
          ? detected.deployments[0]
          : undefined);
      setSelectedRoot(candidate?.gitops_root ?? "");
      setCustomRoot(candidate?.gitops_root ?? settings?.gitops_root ?? "");
      setDiscoveryMode(settings?.discovery_mode ?? "auto");
      setAutomatic(settings?.automatic);
      setLocked(
        settings?.locked ?? candidate?.source_lockfiles.status === "complete",
      );
      setLockKey(settings?.lock_key ?? "");
    } catch (failure) {
      reportError(failure);
    } finally {
      setLoading(false);
    }
  }, [repository, clearError, reportError]);
  useEffect(() => void load(), [load]);
  if (!repository)
    return (
      <SettingsPanel title="Set up deployments">
        <SettingsFeedback>
          Select a repository in{" "}
          <a href="/settings?section=workspace">Workspace settings</a> to
          continue.
        </SettingsFeedback>
      </SettingsPanel>
    );
  const candidate = discovery?.deployments.find(
    (item) => item.gitops_root === selectedRoot,
  );
  const active =
    configuration?.environments
      .filter((item) => item.active)
      .map((item) => item.name) ?? [];
  const inactive =
    configuration?.environments
      .filter((item) => !item.active)
      .map((item) => item.name) ?? [];
  const additions =
    candidate?.all_environments.filter(
      (name) => !configuration?.environments.some((item) => item.name === name),
    ) ?? [];
  const reactivations =
    candidate?.all_environments.filter((name) => inactive.includes(name)) ?? [];
  const removals = active.filter(
    (name) => !candidate?.all_environments.includes(name),
  );
  const needsDetection =
    !!candidate &&
    (customRoot !== candidate.gitops_root ||
      ![candidate.requested_discovery_mode, candidate.discovery_mode].includes(
        discoveryMode,
      ));
  async function activate(enabled: boolean) {
    setBusy(true);
    clearError();
    try {
      await request(`${endpoint(repository!.id)}/deployment`, { enabled });
      onSaved(enabled ? "Deployments enabled." : "Deployments disabled.");
      await load();
    } catch (failure) {
      reportError(failure);
    } finally {
      setBusy(false);
    }
  }
  async function detectLocation() {
    setBusy(true);
    clearError();
    try {
      const detected = await request<DeploymentDiscovery>(
        `${endpoint(repository!.id)}/deployment-discovery`,
        { gitops_root: customRoot, discovery_mode: discoveryMode },
      );
      setDiscovery(detected);
      setSelectedRoot(detected.deployments[0]?.gitops_root ?? "");
      if (!configuration?.settings)
        setLocked(
          detected.deployments[0]?.source_lockfiles.status === "complete",
        );
    } catch (failure) {
      reportError(failure);
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (!candidate || !discovery || automatic === undefined || needsDetection)
      return;
    if (
      removals.length &&
      !(await confirm({
        title: "Deactivate missing environments?",
        description: (
          <p>
            {removals.join(", ")} will be hidden from new runs. Credentials,
            roles, tokens, and run history will be retained.
          </p>
        ),
        actionLabel: "Deactivate and apply",
      }))
    )
      return;
    setBusy(true);
    clearError();
    try {
      await request(
        `${endpoint(repository!.id)}/deployment-configuration`,
        {
          revision: discovery.revision,
          gitops_root: candidate.gitops_root,
          discovery_mode: discoveryMode,
          lock_key: lockKey,
          locked,
          automatic,
          deactivate_missing: removals.length > 0,
        },
        "PUT",
      );
      onSaved("Deployment setup applied.");
      await load();
    } catch (failure) {
      reportError(failure);
    } finally {
      setBusy(false);
    }
  }
  return (
    <SettingsPanel
      title={
        configuration?.environments.length
          ? "Deployment setup"
          : "Set up deployments"
      }
      description="Detect Stacksmith environments from the repository and review how they will run."
      footer={
        <SettingsActions
          feedback={
            (requestError || notice) && (
              <SettingsFeedback tone={requestError ? "error" : "success"}>
                {requestError || notice}
              </SettingsFeedback>
            )
          }
        >
          {requestError && (
            <SettingsButton
              disabled={busy || loading}
              onClick={() => void load()}
            >
              Retry detection
            </SettingsButton>
          )}
          <SettingsButton
            variant="primary"
            disabled={
              busy ||
              loading ||
              !candidate ||
              automatic === undefined ||
              needsDetection
            }
            onClick={() => void apply()}
          >
            {configuration?.environments.length
              ? "Apply deployment changes"
              : "Configure and enable deployments"}
          </SettingsButton>
          {!!configuration?.environments.some((item) => item.active) && (
            <SettingsButton
              disabled={busy}
              onClick={() => void activate(!configuration.deployment_enabled)}
            >
              {configuration.deployment_enabled
                ? "Disable deployments"
                : "Enable deployments"}
            </SettingsButton>
          )}
        </SettingsActions>
      }
    >
      <SettingsFields>
        {loading && (
          <SettingsFeedback>Inspecting the default branch…</SettingsFeedback>
        )}
        {!loading && configuration && (
          <p>
            {configuration.deployment_enabled
              ? "Deployments are enabled."
              : "Deployments are not enabled."}
          </p>
        )}
        {configuration?.inconsistent && (
          <SettingsFeedback tone="error">
            Existing environments have different settings. Review and apply one
            repository-level setup to make them consistent.
          </SettingsFeedback>
        )}
        {!!active.length && (
          <SettingsFields compact>
            <h4>Active environments</h4>
            <p>{active.join(", ")}</p>
          </SettingsFields>
        )}
        {!loading && discovery && !discovery.deployments.length && (
          <SettingsFeedback>
            No Stacksmith deployment was detected. Enter its location under
            Advanced settings and try again.
          </SettingsFeedback>
        )}
        {!loading && discovery && !candidate && (
          <SettingsFields>
            <details className="settings-disclosure" open>
              <summary>Advanced settings</summary>
              <SettingsFields>
                <SettingsField
                  label="Stack location"
                  description="Leave blank for the repository root."
                >
                  <input
                    value={customRoot}
                    placeholder="Repository root"
                    onChange={(event) => setCustomRoot(event.target.value)}
                  />
                </SettingsField>
                <SettingsField label="Environment structure">
                  <select
                    value={discoveryMode}
                    onChange={(event) => setDiscoveryMode(event.target.value)}
                  >
                    <option value="auto">Detect automatically</option>
                    <option value="single">Single deployment</option>
                    <option value="folders">Environment folders</option>
                    <option value="flat-files">
                      Environment files in one directory
                    </option>
                    <option value="env-files">Environment files</option>
                  </select>
                </SettingsField>
                <SettingsActions>
                  <SettingsButton
                    disabled={busy}
                    onClick={() => void detectLocation()}
                  >
                    Detect this location
                  </SettingsButton>
                </SettingsActions>
              </SettingsFields>
            </details>
          </SettingsFields>
        )}
        {!!discovery?.deployments.length && (
          <fieldset className="deployment-choices">
            <legend>Detected deployment</legend>
            {discovery.deployments.map((item) => (
              <label key={item.gitops_root || "."}>
                <input
                  type="radio"
                  name="deployment-candidate"
                  checked={selectedRoot === item.gitops_root}
                  onChange={() => {
                    setSelectedRoot(item.gitops_root);
                    setCustomRoot(item.gitops_root);
                    setDiscoveryMode(item.requested_discovery_mode);
                    if (!configuration?.settings)
                      setLocked(item.source_lockfiles.status === "complete");
                  }}
                />
                <span>
                  <strong>{item.gitops_root || "Repository root"}</strong>
                  <small>
                    {deploymentModeLabel(item.discovery_mode)} ·{" "}
                    {item.all_environments.join(", ")}
                  </small>
                </span>
              </label>
            ))}
          </fieldset>
        )}
        {candidate && (
          <SettingsFields>
            <fieldset className="deployment-choices">
              <legend>GitHub event runs</legend>
              <label>
                <input
                  type="radio"
                  name="event-runs"
                  checked={automatic === true}
                  onChange={() => setAutomatic(true)}
                />
                <span>
                  <strong>Automatic GitOps</strong>
                  <small>
                    Plan same-repository pull requests and apply pushes to the
                    default branch.
                  </small>
                </span>
              </label>
              <label>
                <input
                  type="radio"
                  name="event-runs"
                  checked={automatic === false}
                  onChange={() => setAutomatic(false)}
                />
                <span>
                  <strong>Manual runs only</strong>
                  <small>
                    Run deployments only when someone starts one in Platform.
                  </small>
                </span>
              </label>
            </fieldset>
            <SettingsFields compact>
              <h4>Environment changes</h4>
              <p>
                {additions.length ? `Add: ${additions.join(", ")}. ` : ""}
                {reactivations.length
                  ? `Reactivate: ${reactivations.join(", ")}. `
                  : ""}
                {removals.length ? `Deactivate: ${removals.join(", ")}.` : ""}
                {!additions.length && !reactivations.length && !removals.length
                  ? "The environment catalog is up to date."
                  : ""}
              </p>
            </SettingsFields>
            <details className="settings-disclosure">
              <summary>Advanced settings</summary>
              <SettingsFields>
                <SettingsField
                  label="Stack location"
                  description="Leave blank for the repository root."
                >
                  <input
                    value={customRoot}
                    placeholder="Repository root"
                    onChange={(event) => setCustomRoot(event.target.value)}
                  />
                </SettingsField>
                <SettingsField label="Environment structure">
                  <select
                    value={discoveryMode}
                    onChange={(event) => setDiscoveryMode(event.target.value)}
                  >
                    <option value="auto">Detect automatically</option>
                    <option value="single">Single deployment</option>
                    <option value="folders">Environment folders</option>
                    <option value="flat-files">
                      Environment files in one directory
                    </option>
                    <option value="env-files">Environment files</option>
                  </select>
                </SettingsField>
                <SettingsActions>
                  <SettingsButton
                    disabled={busy}
                    onClick={() => void detectLocation()}
                  >
                    Detect this location
                  </SettingsButton>
                </SettingsActions>
                {needsDetection && (
                  <SettingsFeedback>
                    Detect this location to review its environments before
                    applying.
                  </SettingsFeedback>
                )}
                <SettingsFields compact>
                  <label>
                    <input
                      type="checkbox"
                      checked={locked}
                      onChange={(event) => setLocked(event.target.checked)}
                    />{" "}
                    Verify Stacksmith source lockfiles
                  </label>
                  {candidate.source_lockfiles.status !== "complete" && (
                    <SettingsFeedback>
                      Source lockfile coverage is{" "}
                      {candidate.source_lockfiles.status}. Runs can proceed
                      unlocked, but remote inputs are less reproducible.
                    </SettingsFeedback>
                  )}
                </SettingsFields>
                <SettingsField
                  label="Cross-repository concurrency group"
                  description="Leave blank to serialize deployments within this repository."
                >
                  <input
                    value={lockKey}
                    placeholder="This repository"
                    onChange={(event) => setLockKey(event.target.value)}
                  />
                </SettingsField>
              </SettingsFields>
            </details>
            {!!inactive.length && (
              <details className="settings-disclosure">
                <summary>Retained environments ({inactive.length})</summary>
                <SettingsFields>
                  <p>{inactive.join(", ")}</p>
                </SettingsFields>
              </details>
            )}
          </SettingsFields>
        )}
      </SettingsFields>
    </SettingsPanel>
  );
}

function Settings({
  repository,
  environment,
  section,
  onSaved,
  notice,
  onError,
}: {
  repository: string;
  environment: string;
  section: string;
  onSaved: (notice?: string) => void;
  notice: string;
  onError: (value: string) => void;
}) {
  const {
    error: requestError,
    reportError,
    clearError,
  } = useSettingsError(onError);
  const [credentialSource, setCredentialSource] = useState("value");
  const [secretReference, setSecretReference] = useState("");
  useEffect(() => setSecretReference(""), [repository, environment]);
  const [bindingsLoading, setBindingsLoading] = useState(false);
  const {
    error: bindingsError,
    reportError: reportBindingsError,
    clearError: clearBindingsError,
  } = useSettingsError(onError);
  const [bindingsRetry, setBindingsRetry] = useState(0);
  const [secretMetadata, setSecretMetadata] = useState<
    {
      id: string;
      name: string;
      phases: string[];
      reference: string | null;
      json_key: string | null;
    }[]
  >([]);
  async function save(path: string, body: unknown, form: HTMLFormElement) {
    clearError();
    try {
      await request(path, body);
      form.reset();
      setSecretReference("");
      onSaved(
        section === "environments"
          ? "Deployment settings saved."
          : "Credential binding saved.",
      );
    } catch (error) {
      reportError(error);
    }
  }
  useEffect(() => {
    let active = true;
    setSecretMetadata([]);
    clearBindingsError();
    if (repository && section === "credentials" && environment) {
      setBindingsLoading(true);
      request<typeof secretMetadata>(
        `${endpoint(repository)}/environments/${environment}/secrets`,
      )
        .then((items) => {
          if (active) setSecretMetadata(items);
        })
        .catch((failure) => {
          if (active) reportBindingsError(failure);
        })
        .finally(() => {
          if (active) setBindingsLoading(false);
        });
    }
    return () => {
      active = false;
    };
  }, [
    repository,
    environment,
    section,
    bindingsRetry,
    reportBindingsError,
    clearBindingsError,
  ]);
  return (
    <div className="platform-settings">
      {!repository && (
        <SettingsPanel
          title={
            section === "credentials"
              ? "Add credential binding"
              : "Role assignments"
          }
        >
          <SettingsFeedback>
            Select a repository in{" "}
            <a href="/settings?section=workspace">Workspace settings</a> to
            continue.
          </SettingsFeedback>
        </SettingsPanel>
      )}
      {repository && section === "credentials" && !environment && (
        <SettingsPanel title="Add credential binding">
          <SettingsFeedback>
            Select an environment in{" "}
            <a href="/settings?section=workspace">Workspace settings</a> to
            manage its credentials.
          </SettingsFeedback>
        </SettingsPanel>
      )}
      {repository && section === "permissions" && (
        <Permissions
          key={repository}
          repository={repository}
          onError={reportError}
        />
      )}
      {repository && environment && section === "credentials" && (
        <>
          <ExecutionCredentials
            key={`${repository}:${environment}`}
            repository={repository}
            environment={environment}
            onSaved={onSaved}
            onError={onError}
          />
          <SettingsPanel
            title="Credential bindings"
            description="Provide secret values as environment variables to selected execution phases."
            toolbar={
              bindingsError && (
                <SettingsButton
                  onClick={() => setBindingsRetry((value) => value + 1)}
                >
                  Retry bindings
                </SettingsButton>
              )
            }
          >
            {bindingsLoading && (
              <SettingsFeedback>Loading credential bindings…</SettingsFeedback>
            )}
            {bindingsError && (
              <SettingsFeedback tone="error">{bindingsError}</SettingsFeedback>
            )}
            {!bindingsLoading && !bindingsError && !secretMetadata.length && (
              <SettingsFeedback>No credential bindings yet.</SettingsFeedback>
            )}
            <ul className="settings-binding-list credential-binding-list">
              {secretMetadata.map((item) => (
                <li key={item.id}>
                  <div>
                    <strong>{item.name}</strong>
                    <p>
                      {item.phases.join(", ")} ·{" "}
                      {item.reference
                        ? `Deployment secret: ${item.reference}${item.json_key ? ` (field: ${item.json_key})` : ""}`
                        : "Stored secret value"}
                    </p>
                  </div>
                  <SettingsButton
                    variant="danger"
                    compact
                    type="button"
                    onClick={() => {
                      clearBindingsError();
                      void request(
                        `${endpoint(repository)}/environments/${environment}/secrets/${item.name}/remove`,
                        {},
                      )
                        .then(() => onSaved())
                        .catch(reportBindingsError);
                    }}
                  >
                    Revoke binding
                  </SettingsButton>
                </li>
              ))}
            </ul>
            <form
              className="credential-inline-form"
              onSubmit={(event) => {
                const form = event.currentTarget;
                const data = fields(event);
                if (credentialSource === "reference" && !secretReference)
                  return;
                void save(
                  `${endpoint(repository)}/environments/${environment}/secrets`,
                  {
                    name: text(data, "name"),
                    phases: data.getAll("phase"),
                    ...(credentialSource === "reference"
                      ? {
                          reference: text(data, "reference"),
                          json_key: text(data, "json_key") || null,
                        }
                      : { value: text(data, "value") }),
                  },
                  form,
                );
              }}
            >
              <h4>Add credential binding</h4>

              <SettingsFields wide>
                <SettingsField label="Environment variable">
                  <input name="name" required placeholder="AWS_ACCESS_KEY_ID" />
                </SettingsField>
                <fieldset className="credential-source">
                  <legend>Credential source</legend>
                  <div className="credential-source-options">
                    <label
                      className={credentialSource === "value" ? "selected" : ""}
                    >
                      <input
                        type="radio"
                        name="credential-source"
                        value="value"
                        checked={credentialSource === "value"}
                        onChange={() => setCredentialSource("value")}
                      />
                      <span>
                        <strong>Enter a secret value</strong>
                        <small>Encrypted and stored by this deployment</small>
                      </span>
                    </label>
                    <label
                      className={
                        credentialSource === "reference" ? "selected" : ""
                      }
                    >
                      <input
                        type="radio"
                        name="credential-source"
                        value="reference"
                        checked={credentialSource === "reference"}
                        onChange={() => setCredentialSource("reference")}
                      />
                      <span>
                        <strong>Use a deployment secret</strong>
                        <small>
                          Resolve a secret configured by the platform
                          administrator
                        </small>
                      </span>
                    </label>
                  </div>
                  {credentialSource === "value" ? (
                    <SettingsField label="Secret value">
                      <input
                        name="value"
                        type="password"
                        autoComplete="new-password"
                        required
                        placeholder="Enter secret value"
                      />
                    </SettingsField>
                  ) : (
                    <SecretReferenceSelector
                      key={`${repository}:${environment}`}
                      repository={repository}
                      environment={environment}
                      value={secretReference}
                      onChange={setSecretReference}
                      onError={reportError}
                    />
                  )}
                </fieldset>
                <CredentialPhases />
              </SettingsFields>
              <div className="settings-panel-footer">
                <SettingsActions
                  feedback={
                    (requestError || notice) && (
                      <SettingsFeedback
                        tone={requestError ? "error" : "success"}
                      >
                        {requestError || notice}
                      </SettingsFeedback>
                    )
                  }
                >
                  <SettingsButton
                    type="submit"
                    variant="primary"
                    disabled={
                      credentialSource === "reference" && !secretReference
                    }
                  >
                    Save binding
                  </SettingsButton>
                </SettingsActions>
              </div>
            </form>
          </SettingsPanel>
        </>
      )}
    </div>
  );
}

function Tokens({
  repositories,
  repositoriesLoading,
  repositoryLoadError,
  onError,
}: {
  repositories: Repository[];
  repositoriesLoading: boolean;
  repositoryLoadError: string;
  onError: (value: string) => void;
}) {
  const {
    error: requestError,
    reportError,
    clearError,
  } = useSettingsError(onError);
  const [tokenData, setTokenData] = useState<
    (Page<TokenMetadata> & { counts: TokenCounts }) | null
  >(null);
  const [tokenLoadError, setTokenLoadError] = useState("");
  const [tokenPage, setTokenPage] = useState(1);
  const [tokenSize, setTokenSize] = useState(20);
  const [tokenLoading, setTokenLoading] = useState(true);
  const [tokenFilter, setTokenFilter] = useState<TokenStatus | "All">("Active");
  const [created, setCreated] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [repository, setRepository] = useState("");
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [environment, setEnvironment] = useState("*");
  const [loadingScope, setLoadingScope] = useState(false);
  const [busy, setBusy] = useState(false);
  const [tokenAccess, setTokenAccess] = useState<EffectiveAccess | null>(null);
  useEffect(() => {
    let active = true;
    setTokenAccess(null);
    if (!repository) return;
    request<EffectiveAccess>(
      `${endpoint(repository)}/access?environment=${encodeURIComponent(environment)}`,
    )
      .then((value) => {
        if (active) setTokenAccess(value);
      })
      .catch((failure) => {
        if (active) reportError(failure);
      });
    return () => {
      active = false;
    };
  }, [repository, environment, reportError]);

  useEffect(() => {
    setEnvironments([]);
    setEnvironment("*");
    if (!repository) return;
    let active = true;
    setLoadingScope(true);
    request<Environment[]>(`${endpoint(repository)}/environments`)
      .then((items) => {
        if (active) setEnvironments(items);
      })
      .catch((error) => {
        if (active) reportError(error);
      })
      .finally(() => {
        if (active) setLoadingScope(false);
      });
    return () => {
      active = false;
    };
  }, [repository, reportError]);
  const tokenPath = `/tokens?page=${tokenPage}&page_size=${tokenSize}&status=${tokenFilter.toLowerCase()}`;
  const tokenPathRef = useRef(tokenPath);
  tokenPathRef.current = tokenPath;
  function acceptTokenPage(
    result: Page<TokenMetadata> & { counts: TokenCounts },
  ) {
    setTokenLoadError("");
    setTokenData(result);
    setTokenPage((current) =>
      Math.min(current, pageCount(result.total, result.page_size)),
    );
  }
  async function refreshTokenPage() {
    const path = tokenPathRef.current;
    const result = await request<Page<TokenMetadata> & { counts: TokenCounts }>(
      path,
    );
    if (path === tokenPathRef.current) acceptTokenPage(result);
  }
  useEffect(() => {
    let active = true;
    setTokenLoading(true);
    const load = () =>
      request<Page<TokenMetadata> & { counts: TokenCounts }>(tokenPath)
        .then((result) => {
          if (active) acceptTokenPage(result);
        })
        .catch((error) => {
          if (!active) return;
          if (error instanceof ApiError && [401, 403].includes(error.status))
            reportError(error);
          else setTokenLoadError(message(error));
        })
        .finally(() => {
          if (active) setTokenLoading(false);
        });
    void load();
    const timer = setInterval(load, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [tokenPath, refresh, reportError]);
  return (
    <div className="platform-settings platform-token-settings">
      <SettingsPanel
        title="Create a token"
        description="Tokens belong to your account and expire within your current sign-in session. Select the repository, environments, and capabilities each token can access."
        form={{
          onSubmit: (event) => {
            const form = event.currentTarget;
            const data = fields(event);
            setBusy(true);
            clearError();
            void request<{ token: string }>("/tokens", {
              name: text(data, "name"),
              repository,
              environment,
              permissions: data.getAll("permission"),
              workloads: data.getAll("workload"),
              expires_in: Number(text(data, "lifetime")),
            })
              .then((value) => {
                setCreated(value.token);
                setTokenFilter("Active");
                setTokenPage(1);
                form.reset();
                setRepository("");
                setEnvironment("*");
                setRefresh((value) => value + 1);
              })
              .catch((error) => reportError(error))
              .finally(() => setBusy(false));
          },
        }}
        footer={
          <SettingsActions
            feedback={
              <>
                {requestError && (
                  <SettingsFeedback tone="error">
                    {requestError}
                  </SettingsFeedback>
                )}
                {created && (
                  <SettingsFeedback tone="success">
                    <p>Copy this token now. Its value is shown once.</p>
                    <code className="platform-token">{created}</code>
                    <SettingsButton compact onClick={() => setCreated("")}>
                      Hide token
                    </SettingsButton>
                  </SettingsFeedback>
                )}
              </>
            }
          >
            <SettingsButton
              type="submit"
              variant="primary"
              disabled={!repository || loadingScope || busy || !tokenAccess}
            >
              {busy ? "Creating…" : "Create scoped token"}
            </SettingsButton>
          </SettingsActions>
        }
      >
        <SettingsFields>
          <SettingsField label="Name">
            <input name="name" required />
          </SettingsField>

          <div className="settings-fields">
            <RepositorySelector
              label="Repository access"
              repositories={repositories}
              selected={repository}
              onSelect={setRepository}
              loading={repositoriesLoading}
              error={repositoryLoadError}
              disabled={busy}
              required
            />
            <SettingsField label="Environment access">
              <select
                value={environment}
                onChange={(event) => setEnvironment(event.target.value)}
                disabled={!repository || loadingScope}
              >
                <option value="*">All environments</option>
                {environments.map((item) => (
                  <option key={item.id} value={item.name}>
                    {item.settings?.discovery_mode === "single"
                      ? "Repository default"
                      : item.name}
                  </option>
                ))}
              </select>
            </SettingsField>
          </div>
          {!repositories.length &&
            !repositoriesLoading &&
            !repositoryLoadError && (
              <p className="platform-notice">
                You need platform access to a repository to create a scoped
                token. You can still manage your existing tokens below.
              </p>
            )}
          <SettingsField label="Lifetime">
            <select name="lifetime">
              <option value="3600">One hour</option>
              <option value="28800">Up to eight hours</option>
            </select>
          </SettingsField>
          <p className="field-help">
            Tokens are limited to the selected repository, environments,
            permissions, and workloads. They cannot exceed your current stack
            role. Read access is always included.
          </p>
          <input type="hidden" name="permission" value="view" />
          {["plan", "deploy"].map((value) => (
            <label key={value}>
              <input
                type="checkbox"
                name="permission"
                value={value}
                disabled={
                  value !== "view" &&
                  !tokenAccess?.capabilities.some((capability) =>
                    capability.startsWith(
                      value === "deploy" ? "execute_" : "plan_",
                    ),
                  )
                }
                defaultChecked={value === "view"}
              />{" "}
              {value === "plan" ? "Plan runs" : "Execute deployments"}
            </label>
          ))}
          <fieldset>
            <legend>Workload access</legend>
            {["infrastructure", "operations"].map((workload) => (
              <label key={workload}>
                <input
                  type="checkbox"
                  name="workload"
                  value={workload}
                  disabled={
                    !tokenAccess?.capabilities.includes(`plan_${workload}`)
                  }
                />
                {workload === "infrastructure"
                  ? "Infrastructure"
                  : "Operations"}
              </label>
            ))}
          </fieldset>
        </SettingsFields>
      </SettingsPanel>
      <TokenTable
        error={tokenLoadError}
        tokens={tokenData?.items ?? null}
        filter={tokenFilter}
        onFilter={(value) => {
          setTokenLoading(true);
          setTokenFilter(value);
          setTokenPage(1);
          setRefresh((current) => current + 1);
        }}
        onRefresh={refreshTokenPage}
        counts={
          tokenData?.counts ?? { active: 0, all: 0, expired: 0, revoked: 0 }
        }
        page={tokenPage}
        size={tokenSize}
        total={tokenData?.total ?? 0}
        loading={tokenLoading}
        onPage={(value) => {
          setTokenLoading(true);
          setTokenPage(value);
        }}
        onSize={(value) => {
          setTokenLoading(true);
          setTokenSize(value);
          setTokenPage(1);
        }}
        onError={onError}
      />
    </div>
  );
}
