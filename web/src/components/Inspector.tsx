import { useEffect, useState } from "react";
import { Info, Trash2, X } from "lucide-react";
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
  component: ComponentDefinition;
  definition?: ComponentType;
  relationships: Relationship[];
  onChange: (component: ComponentDefinition) => void;
  onRemove: () => void;
  onClose: () => void;
}

export function Inspector(props: Props) {
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
        <h2>{props.name}</h2>
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
