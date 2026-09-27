export type RunTarget = { repository: string; environment: string };

export function runTargetKey(account: string): string {
  return `stacksmith-runs:${JSON.stringify(account)}`;
}

export function parseRunTarget(value: string | null): RunTarget | null {
  if (!value) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    if (
      parsed &&
      typeof parsed === "object" &&
      "repository" in parsed &&
      typeof parsed.repository === "string" &&
      "environment" in parsed &&
      typeof parsed.environment === "string"
    )
      return { repository: parsed.repository, environment: parsed.environment };
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
  }
  return null;
}

export function runTargetAtUrl(url: URL, fallback: RunTarget): RunTarget {
  return url.searchParams.has("repo") || url.searchParams.has("environment")
    ? {
        repository: url.searchParams.get("repo") ?? fallback.repository,
        environment: url.searchParams.get("environment") ?? "",
      }
    : fallback;
}

export function runTargetUrl(target: RunTarget, run?: string): string {
  const parameters = new URLSearchParams({ repo: target.repository });
  if (target.environment) parameters.set("environment", target.environment);
  if (run) parameters.set("run", run);
  return `/runs?${parameters}`;
}
