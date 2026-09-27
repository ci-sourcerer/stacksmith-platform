export type View = "compose" | "catalog" | "review" | "resolution";
export type StackTarget =
  { repository: string; path: string } | "example" | null;
export interface EditorRoute {
  stack: StackTarget;
  view: View;
  viewOnly: boolean;
}

const VIEWS: View[] = ["compose", "catalog", "review", "resolution"];
const RETURN_ROUTE_KEY = "stacksmith-platform:return-route";

export function parseEditorRoute(hash: string): EditorRoute {
  const [path, query] = hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query);
  const view = params.get("view") as View;
  const route: EditorRoute = {
    stack: null,
    view: VIEWS.includes(view) ? view : "compose",
    viewOnly: params.get("mode") === "view",
  };
  if (path === "/example") route.stack = "example";
  else if (VIEWS.includes(path?.slice(1) as View))
    route.view = path.slice(1) as View;
  else if (path?.startsWith("/stacks/")) {
    try {
      const segments = path.slice(8).split("/").map(decodeURIComponent);
      if (segments.length >= 3 && segments.every(Boolean))
        route.stack = {
          repository: segments.slice(0, 2).join("/"),
          path: segments.slice(2).join("/"),
        };
    } catch (error) {
      if (!(error instanceof URIError)) throw error;
    }
  }
  return route;
}

export function editorHash(route: EditorRoute): string {
  const params = new URLSearchParams();
  let path = route.view === "compose" ? "/" : `/${route.view}`;
  if (route.stack) {
    path =
      route.stack === "example"
        ? "/example"
        : `/stacks/${route.stack.repository.split("/").map(encodeURIComponent).join("/")}/${route.stack.path.split("/").map(encodeURIComponent).join("/")}`;
    if (route.view !== "compose") params.set("view", route.view);
  }
  if (route.viewOnly) params.set("mode", "view");
  return `#${path}${params.size ? `?${params}` : ""}`;
}

export function sameStack(left: StackTarget, right: StackTarget): boolean {
  return (
    left === right ||
    (typeof left === "object" &&
      left !== null &&
      typeof right === "object" &&
      right !== null &&
      left.repository === right.repository &&
      left.path === right.path)
  );
}

export function stackRequest(stack: Exclude<StackTarget, null>): string {
  return stack === "example"
    ? "/sample"
    : `/repositories/${stack.repository.split("/").map(encodeURIComponent).join("/")}/stack?path=${encodeURIComponent(stack.path)}`;
}

export function rememberReturnRoute() {
  try {
    sessionStorage.setItem(
      RETURN_ROUTE_KEY,
      window.location.pathname + window.location.search + window.location.hash,
    );
  } catch (error) {
    if (!(error instanceof DOMException)) throw error;
  }
}

export function restoreReturnRoute() {
  try {
    const saved = sessionStorage.getItem(RETURN_ROUTE_KEY);
    if (!saved) return;
    sessionStorage.removeItem(RETURN_ROUTE_KEY);
    const current = new URL(window.location.href);
    if (
      saved.startsWith("/") &&
      !saved.startsWith("//") &&
      (current.searchParams.has("auth_return") ||
        (current.pathname === "/" && !current.hash && !current.search))
    ) {
      const target = new URL(saved, current.origin);
      if (
        target.origin === current.origin &&
        ["/", "/runs", "/settings"].includes(target.pathname)
      )
        window.history.replaceState(
          window.history.state,
          "",
          target.pathname + target.search + target.hash,
        );
    }
  } catch (error) {
    if (!(error instanceof DOMException)) throw error;
  }
}

export function initialEditorRoute(): EditorRoute {
  return parseEditorRoute(window.location.hash);
}
