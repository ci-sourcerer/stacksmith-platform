import { useSettingsError } from "../useSettingsError";
import {
  SettingsPanel,
  SettingsFields,
  SettingsField,
  SettingsActions,
  SettingsFeedback,
  SettingsButton,
} from "./SettingsLayout";
import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, ExternalLink } from "lucide-react";
import { ActionTooltip } from "./HelpTip";
import { api } from "../api";
import { ListAddButton, ListRemoveButton } from "./ListControls";
import {
  effectiveLayers,
  layerReference,
  layerSource,
  moveLayer,
  parseLayer,
  validLayer,
} from "../layers";
import type { ConfigurationPolicy, Layer } from "../layers";

function readLayers(refs: string[]): Layer[] {
  return refs.map((reference) => parseLayer(reference, crypto.randomUUID()));
}

function LayerSummary({
  reference,
  draft,
}: {
  reference: string;
  draft?: Layer;
}) {
  const layer = draft ?? parseLayer(reference, "summary");
  const source = layerSource(reference);
  return layer.raw !== undefined ? (
    <code className="layer-raw">{reference}</code>
  ) : (
    <>
      <strong>{layer.repository || "Incomplete layer"}</strong>
      <span className="layer-file">{layer.file || "File path not set"}</span>
      <div className="layer-source">
        <code title={layer.commit}>
          {layer.commit.slice(0, 10) || "Commit not set"}
        </code>
        {source && (
          <a href={source} target="_blank" rel="noreferrer">
            View file <ExternalLink size={13} />
          </a>
        )}
      </div>
    </>
  );
}

function LayerFields({
  layer,
  index,
  count,
  busy,
  onChange,
  onMove,
  onRemove,
}: {
  layer: Layer;
  index: number;
  count: number;
  busy: boolean;
  onChange: (layer: Layer) => void;
  onMove: (direction: -1 | 1) => void;
  onRemove: () => void;
}) {
  return (
    <fieldset className="layer-editor-row" disabled={busy}>
      <legend>Layer {index + 1}</legend>
      <div className="layer-row-actions">
        <ActionTooltip description="Move this layer earlier so later layers can override its values.">
          <SettingsButton
            compact
            type="button"
            disabled={index === 0}
            aria-label={`Move layer ${index + 1} up`}
            onClick={() => onMove(-1)}
          >
            <ArrowUp size={15} />
          </SettingsButton>
        </ActionTooltip>
        <ActionTooltip description="Move this layer later to give its values higher precedence.">
          <SettingsButton
            compact
            type="button"
            disabled={index === count - 1}
            aria-label={`Move layer ${index + 1} down`}
            onClick={() => onMove(1)}
          >
            <ArrowDown size={15} />
          </SettingsButton>
        </ActionTooltip>
        <ListRemoveButton
          label={`Remove layer ${index + 1}`}
          onClick={onRemove}
        />
      </div>
      {layer.raw !== undefined ? (
        <div className="layer-unsupported">
          <p>
            This existing reference needs replacement before saving structured
            layers.
          </p>
          <code>{layer.raw}</code>
          <SettingsButton
            compact
            type="button"
            onClick={() =>
              onChange({ id: layer.id, repository: "", file: "", commit: "" })
            }
          >
            Replace reference
          </SettingsButton>
        </div>
      ) : (
        <div className="layer-fields">
          <SettingsField
            label="GitHub repository"
            description={<> Repository containing this configuration file. </>}
          >
            <input
              value={layer.repository}
              required
              pattern="[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
              placeholder="owner/repository"
              onChange={(event) =>
                onChange({ ...layer, repository: event.target.value })
              }
            />
          </SettingsField>
          <SettingsField
            label="File path"
            description={<> Path relative to the repository root. </>}
          >
            <input
              value={layer.file}
              required
              pattern="[^\s,@?#]+"
              placeholder="config/platform.yaml"
              onChange={(event) =>
                onChange({ ...layer, file: event.target.value })
              }
            />
          </SettingsField>
          <label className="layer-commit">
            Full commit SHA
            <input
              value={layer.commit}
              required
              pattern="[0-9a-f]{40}"
              placeholder="40-character commit SHA"
              spellCheck={false}
              autoComplete="off"
              onChange={(event) =>
                onChange({ ...layer, commit: event.target.value })
              }
            />
            <small>
              Pin to a full lowercase commit SHA, rather than a branch.
            </small>
          </label>
          {!validLayer(layer) && (
            <p className="layer-validation">
              Complete a valid repository, file path, and full commit SHA before
              saving.
            </p>
          )}
        </div>
      )}
    </fieldset>
  );
}

export function ConfigurationEditor({
  repository,
  onError,
  onSaved,
}: {
  repository?: string;
  onError: (value: string) => void;
  onSaved: () => void;
}) {
  const {
    error: requestError,
    reportError,
    clearError,
  } = useSettingsError(onError);
  const [policy, setPolicy] = useState<ConfigurationPolicy | null>(null);
  const [mode, setMode] = useState<ConfigurationPolicy["mode"]>(
    repository ? "inherit" : "replace",
  );
  const [layers, setLayers] = useState<Layer[]>([]);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [retry, setRetry] = useState(0);
  const path = repository
    ? `/v1/repositories/${repository}/configuration`
    : "/v1/configuration";
  useEffect(() => {
    let active = true;
    setPolicy(null);
    clearError();
    api<ConfigurationPolicy>(path)
      .then((value) => {
        if (active) {
          setPolicy(value);
          setMode(value.mode);
          setLayers(readLayers(value.refs));
          setSaved(false);
        }
      })
      .catch((error) => {
        if (active) reportError(error);
      });
    return () => {
      active = false;
    };
  }, [path, reportError, clearError, retry]);
  const refs = mode === "inherit" ? [] : layers.map(layerReference);
  const dirty =
    !!policy &&
    (mode !== policy.mode ||
      JSON.stringify(refs) !== JSON.stringify(policy.refs));
  const valid =
    mode === "inherit" ||
    (layers.length > 0 && layers.length <= 32 && layers.every(validLayer));
  const preview = effectiveLayers(
    policy?.platform_refs ?? [],
    refs,
    mode,
    !!repository,
  );
  const candidates =
    (repository && mode !== "replace"
      ? (policy?.platform_refs.length ?? 0)
      : 0) + refs.length;
  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    clearError();
    setSaved(false);
    try {
      await api(path, { mode, refs });
      const value = await api<ConfigurationPolicy>(path);
      setPolicy(value);
      setMode(value.mode);
      setLayers(readLayers(value.refs));
      setSaved(true);
      onSaved();
    } catch (error) {
      reportError(error);
    } finally {
      setBusy(false);
    }
  }
  if (!policy)
    return (
      <SettingsPanel
        title={repository ? "Configuration policy" : "Default configuration"}
      >
        <SettingsFeedback tone={requestError ? "error" : "info"}>
          {requestError || "Loading configuration…"}
        </SettingsFeedback>
        {requestError && (
          <SettingsButton onClick={() => setRetry((value) => value + 1)}>
            Reload configuration
          </SettingsButton>
        )}
      </SettingsPanel>
    );
  return (
    <section className="platform-settings">
      <SettingsPanel
        title={repository ? "Configuration policy" : "Default configuration"}
        description="Layers are applied from top to bottom. Later layers override earlier values."
        toolbar={
          mode !== "inherit" && (
            <ListAddButton
              className="layer-add-button"
              disabled={busy || layers.length >= 32}
              onClick={() =>
                setLayers((items) => [
                  ...items,
                  {
                    id: crypto.randomUUID(),
                    repository: "",
                    file: "",
                    commit: "",
                  },
                ])
              }
            >
              Add layer
            </ListAddButton>
          )
        }
        form={{ onSubmit: save }}
        busy={busy}
        footer={
          <SettingsActions
            feedback={
              <SettingsFeedback
                tone={
                  requestError ? "error" : saved && !dirty ? "success" : "info"
                }
              >
                {requestError ||
                  (saved && !dirty
                    ? "Configuration saved. New runs use these layers; reviewed runs using different layers must be planned again."
                    : dirty
                      ? "Unsaved changes"
                      : "Configuration is saved")}
              </SettingsFeedback>
            }
          >
            <SettingsButton
              type="submit"
              variant="primary"
              disabled={busy || !dirty || !valid}
            >
              {busy ? "Saving…" : "Save configuration"}
            </SettingsButton>
          </SettingsActions>
        }
      >
        <SettingsFields wide>
          {repository && (
            <SettingsField label="Configuration mode">
              <select
                value={mode}
                disabled={busy}
                onChange={(event) =>
                  setMode(event.target.value as ConfigurationPolicy["mode"])
                }
              >
                <option value="inherit">Inherit platform defaults</option>
                <option value="append">
                  Append layers to platform defaults
                </option>
                <option value="replace">Replace platform defaults</option>
              </select>
            </SettingsField>
          )}
          {mode === "inherit" ? (
            <p>
              This repository follows the platform defaults. Changes to those
              defaults apply to future runs.
            </p>
          ) : (
            <>
              <p>
                Add configuration layers in precedence order. Later layers take
                precedence using Stacksmith’s merge rules.
              </p>
              {layers.map((layer, index) => (
                <LayerFields
                  key={layer.id}
                  layer={layer}
                  index={index}
                  count={layers.length}
                  busy={busy}
                  onChange={(value) =>
                    setLayers((items) =>
                      items.map((item) =>
                        item.id === layer.id ? value : item,
                      ),
                    )
                  }
                  onMove={(direction) =>
                    setLayers((items) => moveLayer(items, index, direction))
                  }
                  onRemove={() =>
                    setLayers((items) =>
                      items.filter((item) => item.id !== layer.id),
                    )
                  }
                />
              ))}

              {!layers.length && (
                <p className="layer-validation">
                  Add at least one layer before saving.
                </p>
              )}
            </>
          )}
        </SettingsFields>
      </SettingsPanel>
      <SettingsPanel
        title="Effective layers"
        description={
          <>
            {dirty
              ? "Proposed order after saving. Active runs still use the saved configuration."
              : "The ordered layers used by new runs."}{" "}
            Later layers take precedence.
          </>
        }
        toolbar={
          <span className={`layer-badge ${dirty ? "draft" : ""}`}>
            {dirty ? "Unsaved preview" : "Saved"}
          </span>
        }
      >
        {candidates > preview.length && (
          <p className="platform-notice">
            Repeated references appear once. Their first occurrence determines
            the source badge and position.
          </p>
        )}
        {!valid && (
          <p className="layer-validation">
            This preview contains incomplete or unsupported references. Complete
            or replace them before saving.
          </p>
        )}
        <ol className="effective-layer-list">
          {preview.map((item, index) => (
            <li key={item.reference}>
              <span className="layer-number">{index + 1}</span>
              <div className="layer-summary">
                <LayerSummary
                  reference={item.reference}
                  draft={
                    mode === "inherit"
                      ? undefined
                      : layers.find(
                          (layer) => layerReference(layer) === item.reference,
                        )
                  }
                />
              </div>
              <span className="layer-badge">{item.origin}</span>
            </li>
          ))}
        </ol>
        {!preview.length && (
          <p>No effective layers. Add a layer to continue.</p>
        )}
      </SettingsPanel>
    </section>
  );
}
