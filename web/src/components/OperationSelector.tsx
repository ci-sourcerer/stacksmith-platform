import { useEffect, useRef, useState } from "react";
import { api } from "../api";

type Operation = { name: string; description?: string; stacks: string[] };

export function OperationSelector({
  repository,
  environment,
  visible,
  selected,
  onChange,
  onError,
}: {
  repository: string;
  environment: string;
  visible: boolean;
  selected: string[] | null;
  onChange: (names: string[] | null) => void;
  onError: (message: string) => void;
}) {
  const [choices, setChoices] = useState<Operation[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  useEffect(() => {
    let active = true;
    onChange(null);
    setChoices([]);
    setLoading(true);
    setFailed(false);
    if (!repository || !environment) {
      setLoading(false);
      return;
    }
    api<{ operations: Operation[] }>(
      `/v1/repositories/${repository}/environments/${encodeURIComponent(environment)}/operations`,
    )
      .then((result) => {
        if (!active) return;
        setChoices(result.operations);
        onChange(result.operations.map((operation) => operation.name));
      })
      .catch((error) => {
        if (!active) return;
        setFailed(true);
        if (visibleRef.current)
          onError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [repository, environment, refresh, onChange, onError]);

  if (!visible) return null;

  return (
    <fieldset className="operation-selector">
      <legend>Operations</legend>
      {!!choices.length && selected?.length === choices.length && (
        <input type="hidden" name="all_operations" value="1" />
      )}
      <p>
        Choose stack operations from the latest default-branch commit.
        Dependencies are included by Stacksmith.
      </p>
      {loading ? (
        <p role="status">Discovering operations…</p>
      ) : failed ? (
        <p>Could not load operations. Use Refresh to retry.</p>
      ) : !choices.length ? (
        <p>No operations are declared in this environment’s stacks.</p>
      ) : (
        <div className="operation-choices">
          {choices.map((operation) => (
            <label key={operation.name}>
              <input
                type="checkbox"
                checked={selected?.includes(operation.name) ?? false}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...(selected ?? []), operation.name]
                      : (selected ?? []).filter(
                          (name) => name !== operation.name,
                        ),
                  )
                }
              />
              <span>
                <strong>{operation.name}</strong>
                {operation.description && <span>{operation.description}</span>}
                <small>{operation.stacks.join(", ")}</small>
              </span>
            </label>
          ))}
        </div>
      )}
      <div className="operation-actions">
        <button
          type="button"
          disabled={loading}
          onClick={() => setRefresh((value) => value + 1)}
        >
          Refresh
        </button>
        {!!choices.length && (
          <button
            type="button"
            onClick={() =>
              onChange(
                selected?.length === choices.length
                  ? []
                  : choices.map((operation) => operation.name),
              )
            }
          >
            {selected?.length === choices.length
              ? "Clear selection"
              : "Select all"}
          </button>
        )}
      </div>
    </fieldset>
  );
}
