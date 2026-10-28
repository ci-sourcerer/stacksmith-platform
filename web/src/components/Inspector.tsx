import { useEffect, useState } from "react";
import { Info, Pencil, Trash2, X } from "lucide-react";
import type {
  ComponentDefinition,
  ComponentType,
  Relationship,
} from "../types";
import { TypeIcon } from "./ComponentNode";
import { PropertyEditor } from "./PropertyEditor";
import { StringListEditor, stringListItems } from "./ListControls";

interface Props {
  readOnly: boolean;
  name: string;
  existingNames: string[];
  onRename: (name: string) => Promise<void>;
  component: ComponentDefinition;
  definition?: ComponentType;
  relationships: Relationship[];
  onChange: (component: ComponentDefinition) => void;
  onRemove: () => void;
  onClose: () => void;
}

export function Inspector(props: Props) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(props.name);
  const [renameError, setRenameError] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [tags, setTags] = useState(() =>
    stringListItems(props.component.tags ?? []),
  );
  useEffect(() => {
    setTags((items) =>
      JSON.stringify(items.map((item) => item.value.trim()).filter(Boolean)) ===
      JSON.stringify(props.component.tags ?? [])
        ? items
        : stringListItems(props.component.tags ?? []),
    );
  }, [props.component.tags]);
  const incoming = props.relationships.filter(
    (edge) => edge.target === props.name && edge.kind === "association",
  );
  return (
    <aside className="inspector">
      <div className="panel-title">
        <span>Component details</span>
        <button
          className="icon-button"
          aria-label="Close component details"
          onClick={props.onClose}
        >
          <X size={16} />
        </button>
      </div>
      <div className="inspector-body">
        <span className="type-icon large">
          <TypeIcon type={props.component.type} size={24} />
        </span>
        <div className="component-name-heading">
          <h2>{props.name}</h2>
          {!props.readOnly && !renaming && (
            <button
              className="icon-button"
              aria-label={`Rename ${props.name}`}
              title="Rename component"
              onClick={() => setRenaming(true)}
            >
              <Pencil size={15} />
            </button>
          )}
        </div>
        {renaming && (
          <form
            className="component-rename"
            onSubmit={async (event) => {
              event.preventDefault();
              if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) {
                setRenameError(
                  "Use 1 to 64 letters, numbers, hyphens, or underscores.",
                );
                return;
              }
              if (props.existingNames.includes(name)) {
                setRenameError("A component with this name already exists.");
                return;
              }
              setRenameBusy(true);
              setRenameError("");
              try {
                await props.onRename(name);
                setRenaming(false);
              } catch (error) {
                setRenameError(
                  error instanceof Error
                    ? error.message
                    : "Could not rename the component.",
                );
              } finally {
                setRenameBusy(false);
              }
            }}
          >
            <label htmlFor="component-rename">Component name</label>
            <input
              id="component-rename"
              autoFocus
              value={name}
              disabled={renameBusy}
              maxLength={64}
              onChange={(event) => {
                setName(event.target.value);
                setRenameError("");
              }}
            />
            <p className="muted">
              Renaming changes the stack identifier and may replace
              infrastructure when applied.
            </p>
            {renameError && <p role="alert">{renameError}</p>}
            <div className="component-rename-actions">
              <button
                className="button small primary"
                disabled={renameBusy || name === props.name}
                type="submit"
              >
                {renameBusy ? "Renaming…" : "Rename"}
              </button>
              <button
                className="button small"
                type="button"
                disabled={renameBusy}
                onClick={() => {
                  setName(props.name);
                  setRenameError("");
                  setRenaming(false);
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        )}
        <p className="muted">
          {props.definition?.display_name ?? props.component.type}
        </p>
        <div className="detail-pair">
          <span>Type</span>
          <code>{props.component.type}</code>
        </div>
        <fieldset disabled={props.readOnly} className="inspector-fields">
          <PropertyEditor
            inputs={props.definition?.inputs ?? []}
            properties={props.component.properties ?? {}}
            relationships={props.relationships.filter(
              (edge) => edge.target === props.name,
            )}
            onChange={(properties) =>
              props.onChange({ ...props.component, properties })
            }
          />
          <StringListEditor
            label="Component tags"
            itemLabel="Tag"
            items={tags}
            onChange={setTags}
            onCommit={(values) =>
              props.onChange({
                ...props.component,
                tags: values.map((tag) => tag.trim()).filter(Boolean),
              })
            }
            disabled={props.readOnly}
            placeholder="application"
          />
        </fieldset>
        {incoming.length > 0 && (
          <div className="association-note">
            <Info size={16} />
            <div>
              <strong>Connected by your platform</strong>
              {incoming.map((edge, index) => (
                <p key={index}>
                  <code>{edge.property}</code> receives{" "}
                  <code>
                    {edge.source}.{edge.output}
                  </code>{" "}
                  through {edge.rule}.
                </p>
              ))}
            </div>
          </div>
        )}
        <h3>Public outputs</h3>
        {props.definition?.outputs?.map((output) => (
          <div className="port-doc" key={output.name}>
            <code>{output.name}</code>
            <p>{output.description ?? "No description provided."}</p>
          </div>
        ))}
        <button
          className="button danger small"
          disabled={props.readOnly}
          onClick={props.onRemove}
        >
          <Trash2 size={14} />
          Remove component
        </button>
      </div>
    </aside>
  );
}
