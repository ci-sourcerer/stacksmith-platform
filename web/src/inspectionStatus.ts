export function inspectionStatus({
  environment,
  inspecting,
  current,
  error,
  hasPrevious,
}: {
  environment: string | null;
  inspecting: boolean;
  current: boolean;
  error: string;
  hasPrevious: boolean;
}): string {
  const context = environment ? `Deployment ${environment}` : "Stack preview";
  if (inspecting)
    return `${context}: inspecting managed connections. The diagram is incomplete until inspection finishes.`;
  if (error)
    return `${context} inspection failed: ${error} ${hasPrevious ? "Managed connections show the last inspection and may be out of date." : "Managed connections are unavailable; only authored connections are shown."}`;
  if (!current)
    return `${context}: managed connections have not been inspected for this draft.`;
  return environment
    ? `${context}: references and managed connections inspected. Deployment planning remains separate.`
    : "Stack preview: references and managed connections inspected using repository and platform configuration. Deployment variables and credentials are excluded.";
}
