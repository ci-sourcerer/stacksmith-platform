export type RepositoryOption = {
  id: string;
  description?: string | null;
  writable?: boolean;
  account?: string;
};
export const REPOSITORY_ROW_HEIGHT = 64;

export function repositoryNextIndex(
  index: number,
  key: string,
  count: number,
  height: number,
): number {
  if (!count) return -1;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  const step = key.startsWith("Page")
    ? Math.max(1, Math.floor(height / REPOSITORY_ROW_HEIGHT))
    : 1;
  return Math.max(
    0,
    Math.min(
      count - 1,
      index + (key === "ArrowUp" || key === "PageUp" ? -step : step),
    ),
  );
}

export function filterRepositories(
  options: RepositoryOption[],
  query: string,
): RepositoryOption[] {
  const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  return options
    .filter((option) =>
      terms.every((term) =>
        `${option.id} ${option.description ?? ""}`.toLowerCase().includes(term),
      ),
    )
    .sort((left, right) => left.id.localeCompare(right.id));
}

export function repositoryWindow(
  count: number,
  scroll: number,
  height: number,
): { start: number; end: number } {
  const start = Math.max(
    0,
    Math.min(
      Math.max(0, count - 1),
      Math.floor(scroll / REPOSITORY_ROW_HEIGHT) - 3,
    ),
  );
  return {
    start,
    end: Math.min(count, start + Math.ceil(height / REPOSITORY_ROW_HEIGHT) + 7),
  };
}

export function repositoryPopup(
  rect: { left: number; top: number; bottom: number; width: number },
  viewport: { width: number; height: number },
  count: number,
) {
  const width = Math.min(Math.max(rect.width, 360), viewport.width - 24);
  const below = viewport.height - rect.bottom - 12;
  const above = rect.top - 12;
  const upwards = below < 250 && above > below;
  const available = Math.max(0, upwards ? above : below);
  const height = Math.max(
    32,
    Math.min(320, available - 100, Math.max(64, count * REPOSITORY_ROW_HEIGHT)),
  );
  return {
    left: Math.max(12, Math.min(rect.left, viewport.width - width - 12)),
    top: upwards ? Math.max(12, rect.top - height - 100) : rect.bottom + 6,
    width,
    height,
  };
}
