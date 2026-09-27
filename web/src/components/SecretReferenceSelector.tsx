import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api";
import "./SecretReferenceSelector.css";

export type SecretReference = {
  reference: string;
  name: string;
  source: string;
  supports_json_key: boolean;
};

export function filterSecretReferences(
  references: SecretReference[],
  query: string,
) {
  return references.filter((item) =>
    `${item.name} ${item.source}`.toLowerCase().includes(query.toLowerCase()),
  );
}

export function SecretReferenceSelector({
  repository,
  environment,
  value,
  onChange,
  onError,
}: {
  repository: string;
  environment: string;
  value: string;
  onChange: (value: string) => void;
  onError: (message: string) => void;
}) {
  const id = useId();
  const container = useRef<HTMLDivElement>(null);
  const [references, setReferences] = useState<SecretReference[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const filtered = filterSecretReferences(references, query);
  const selected = references.find((item) => item.reference === value);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setFailed(false);
    onChange("");
    api<{ references: SecretReference[] }>(
      `/v1/repositories/${repository}/environments/${environment}/secret-references`,
    )
      .then((result) => {
        if (!cancelled) setReferences(result.references);
      })
      .catch((error) => {
        if (!cancelled) {
          setFailed(true);
          setReferences([]);
          onError(
            error instanceof Error
              ? error.message
              : "Unable to load deployment secrets.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [repository, environment, refresh, onChange, onError]);

  useEffect(() => {
    function dismiss(event: PointerEvent) {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);

  useEffect(() => {
    if (open)
      document
        .getElementById(`${id}-${active}`)
        ?.scrollIntoView?.({ block: "nearest" });
  }, [active, open, id]);

  function select(name: string) {
    onChange(name);
    setQuery("");
    setOpen(false);
  }

  return (
    <div className="secret-reference-selector" ref={container}>
      <label htmlFor={id}>Deployment secret name</label>
      <input type="hidden" name="reference" value={value} />
      <input
        id={id}
        role="combobox"
        autoComplete="off"
        aria-expanded={open}
        aria-controls={`${id}-options`}
        aria-haspopup="listbox"
        aria-autocomplete="list"
        aria-activedescendant={
          open && filtered[active] ? `${id}-${active}` : undefined
        }
        disabled={loading || failed || !references.length}
        placeholder={
          loading ? "Loading deployment secrets…" : "Search deployment secrets"
        }
        value={
          open ? query : selected ? `${selected.name} (${selected.source})` : ""
        }
        onFocus={() => {
          setOpen(true);
          setQuery("");
          setActive(0);
        }}
        onClick={() => setOpen(true)}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
          setActive(0);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape" || event.key === "Tab") setOpen(false);
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
            setActive((index) =>
              Math.max(
                0,
                Math.min(
                  filtered.length - 1,
                  index + (event.key === "ArrowDown" ? 1 : -1),
                ),
              ),
            );
          }
          if (event.key === "Enter" && open) {
            event.preventDefault();
            if (filtered[active]) select(filtered[active].reference);
          }
        }}
      />
      {selected && !open && (
        <p className="secret-reference-selected">
          {selected.name} ({selected.source})
        </p>
      )}
      {open && (
        <ul id={`${id}-options`} role="listbox" aria-label="Deployment secrets">
          {filtered.map((item, index) => (
            <li
              id={`${id}-${index}`}
              key={item.reference}
              role="option"
              aria-selected={item.reference === value}
              className={index === active ? "active" : ""}
              onPointerDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActive(index)}
              onClick={() => select(item.reference)}
            >
              {item.name} <small>({item.source})</small>
            </li>
          ))}
          {!filtered.length && (
            <li role="presentation">No matching deployment secrets.</li>
          )}
        </ul>
      )}
      {selected?.supports_json_key && (
        <label key={value}>
          JSON field (optional)
          <input
            name="json_key"
            placeholder="Use the whole secret, or enter a top-level field"
            autoComplete="off"
            maxLength={512}
          />
        </label>
      )}
      {!loading && !failed && !references.length && (
        <p>
          No deployment secrets are configured. Ask a platform administrator to
          configure references, or enter a secret value.
        </p>
      )}
      {failed && <p>Deployment secret names could not be loaded.</p>}
      <button
        type="button"
        disabled={loading}
        onClick={() => {
          setOpen(false);
          setRefresh((count) => count + 1);
        }}
      >
        {failed ? "Retry" : "Refresh secrets"}
      </button>
    </div>
  );
}
