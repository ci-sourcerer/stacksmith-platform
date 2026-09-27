import { useRef } from "react";

export function AdvancedRunOptions({
  administrator,
  action = "plan",
  revision = "",
  onRevisionChange,
  localBackend = false,
  onLocalBackendChange,
}: {
  administrator: boolean;
  action?: string;
  revision?: string;
  onRevisionChange?: (value: string) => void;
  localBackend?: boolean;
  onLocalBackendChange?: (value: boolean) => void;
}) {
  const supportsLocalBackend = ["plan", "plan-operation"].includes(action);
  const details = useRef<HTMLDetailsElement>(null);
  if (!administrator) return null;
  return (
    <details ref={details} className="advanced-run-options">
      <summary>
        Advanced run options{" "}
        {revision.trim() && (
          <span className="layer-badge draft">
            Override set · {revision.trim().slice(0, 10)}
          </span>
        )}
        {supportsLocalBackend && localBackend && (
          <span className="layer-badge draft">Local-backend test</span>
        )}
      </summary>
      <div className="advanced-run-content">
        {supportsLocalBackend && (
          <div className="local-backend-option">
            <label>
              <input
                type="checkbox"
                name="allow_local_backend"
                checked={localBackend}
                onChange={(event) =>
                  onLocalBackendChange?.(event.target.checked)
                }
              />{" "}
              Allow local backend for this test plan
            </label>
            <p>
              Admin override for disposable testing. Workspace files are carried
              between phases, but local state is not reused by later runs.
            </p>
          </div>
        )}
        <label>
          Override commit (optional)
          <input
            name="revision"
            value={revision}
            pattern="[0-9a-f]{40}"
            placeholder="Full commit SHA"
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => onRevisionChange?.(event.target.value)}
            onInvalid={() => {
              if (details.current) details.current.open = true;
            }}
          />
          <small>
            Leave blank to use the latest default-branch commit. Deployment from
            an override also requires administrator approval.
          </small>
        </label>
      </div>
    </details>
  );
}
