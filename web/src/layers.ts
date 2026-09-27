export type ConfigurationPolicy = {
  mode: "inherit" | "append" | "replace";
  refs: string[];
  platform_refs: string[];
  effective_refs: string[];
};
export type Layer = {
  id: string;
  repository: string;
  file: string;
  commit: string;
  raw?: string;
};
export type EffectiveLayer = {
  reference: string;
  origin: "Platform" | "Repository";
};

export function parseLayer(reference: string, id: string): Layer {
  const match =
    /^git\+https:\/\/github.com\/([^/]+\/[^/]+)\/\/(.+)@([^@]+)$/.exec(
      reference,
    );
  return match
    ? { id, repository: match[1], file: match[2], commit: match[3] }
    : { id, repository: "", file: "", commit: "", raw: reference };
}

export function layerReference(layer: Layer): string {
  return (
    layer.raw ??
    `git+https://github.com/${layer.repository.trim()}//${layer.file.trim()}@${layer.commit.trim()}`
  );
}

export function validLayer(layer: Layer): boolean {
  return (
    layer.raw === undefined &&
    /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(layer.repository.trim()) &&
    !!layer.file.trim() &&
    !/[\s,@?#]/.test(layer.file.trim()) &&
    !layer.file.trim().startsWith("/") &&
    /^[0-9a-f]{40}$/.test(layer.commit.trim())
  );
}

export function effectiveLayers(
  platform: string[],
  own: string[],
  mode: ConfigurationPolicy["mode"],
  repository: boolean,
): EffectiveLayer[] {
  const candidates: EffectiveLayer[] = repository
    ? [
        ...(mode === "replace"
          ? []
          : platform.map((reference) => ({
              reference,
              origin: "Platform" as const,
            }))),
        ...(mode === "inherit"
          ? []
          : own.map((reference) => ({
              reference,
              origin: "Repository" as const,
            }))),
      ]
    : own.map((reference) => ({ reference, origin: "Platform" as const }));
  return candidates.filter(
    (item, index) =>
      candidates.findIndex(
        (candidate) => candidate.reference === item.reference,
      ) === index,
  );
}

export function layerSource(reference: string): string | undefined {
  const layer = parseLayer(reference, "source");
  if (!validLayer(layer)) return;
  return `https://github.com/${layer.repository.replace(/\.git$/, "")}/blob/${layer.commit}/${layer.file.split("/").map(encodeURIComponent).join("/")}`;
}

export function moveLayer(
  layers: Layer[],
  index: number,
  direction: -1 | 1,
): Layer[] {
  const target = index + direction;
  if (target < 0 || target >= layers.length) return layers;
  const next = [...layers];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}
