import { editorHash, parseEditorRoute } from "./navigation";

export interface WorkspaceSelection {
  repository: string;
  path: string;
  environment: string;
  preview?: boolean;
}

export interface DeploymentTarget {
  name: string;
  can_inspect: boolean;
}

export interface InspectionTargets {
  targets: DeploymentTarget[];
  availability: "unconfigured" | "forbidden" | "available";
  can_configure: boolean;
}

export function selectSoleTarget(
  workspace: WorkspaceSelection,
  targets: DeploymentTarget[],
): WorkspaceSelection {
  if (workspace.environment || workspace.preview) return workspace;
  const eligible = targets.filter((target) => target.can_inspect);
  return eligible.length === 1
    ? { ...workspace, environment: eligible[0].name }
    : workspace;
}

export const EMPTY_WORKSPACE: WorkspaceSelection = {
  repository: "",
  path: "",
  environment: "",
};

export function workspaceKey(account: string): string {
  return `stacksmith-workspace:${JSON.stringify(account)}`;
}

export function parseWorkspace(value: string | null): WorkspaceSelection {
  if (!value) return { ...EMPTY_WORKSPACE };
  try {
    const parsed: unknown = JSON.parse(value);
    if (
      parsed &&
      typeof parsed === "object" &&
      "repository" in parsed &&
      typeof parsed.repository === "string" &&
      "path" in parsed &&
      typeof parsed.path === "string" &&
      "environment" in parsed &&
      typeof parsed.environment === "string"
    )
      return {
        repository: parsed.repository,
        path: parsed.path,
        environment: parsed.environment,
        ...("preview" in parsed && parsed.preview === true
          ? { preview: true }
          : {}),
      };
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
  }
  return { ...EMPTY_WORKSPACE };
}

export function workspaceAtUrl(
  url: URL,
  current: WorkspaceSelection,
): WorkspaceSelection {
  const stack = url.pathname === "/" ? parseEditorRoute(url.hash).stack : null;
  if (stack && stack !== "example")
    return {
      repository: stack.repository,
      path: stack.path,
      environment:
        stack.repository === current.repository ? current.environment : "",
      ...(stack.repository === current.repository && current.preview
        ? { preview: true }
        : {}),
    };
  if (url.pathname !== "/runs" && url.searchParams.has("repo")) {
    const repository = url.searchParams.get("repo") ?? "";
    return {
      repository,
      path: repository === current.repository ? current.path : "",
      environment:
        url.searchParams.get("environment") ??
        (repository === current.repository ? current.environment : ""),
      ...(repository === current.repository &&
      current.preview &&
      !url.searchParams.has("environment")
        ? { preview: true }
        : {}),
    };
  }
  return current;
}

export function composerUrl(workspace: WorkspaceSelection): string {
  return (
    "/" +
    editorHash({
      stack:
        workspace.repository && workspace.path
          ? { repository: workspace.repository, path: workspace.path }
          : null,
      view: "compose",
      viewOnly: false,
    })
  );
}

export function sameWorkspaceStack(
  left: WorkspaceSelection,
  right: WorkspaceSelection,
): boolean {
  return left.repository === right.repository && left.path === right.path;
}
