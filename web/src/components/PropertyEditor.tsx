import { createContext, useContext, useEffect, useState } from "react";
import { ErrorBanner } from "./ErrorBanner";
import { Braces, ChevronDown, ChevronRight, Link2, Unlink } from "lucide-react";
import {
  defaultJsonValue,
  defaultValueForConstraint,
  isJsonValue,
  jsonValueType,
  matchesValueConstraint,
  parseTerraformType,
  removePropertyAtPath,
  type JsonValue,
  type JsonValueType,
  type ValueConstraint,
} from "../model";
import type { Port, Relationship } from "../types";
import { AutoSizeTextarea } from "./AutoSizeTextarea";
import { ListAddButton, ListRemoveButton } from "./ListControls";

const VALUE_TYPES: { value: JsonValueType; label: string }[] = [
  { value: "string", label: "Text" },
  { value: "number", label: "Number" },
  { value: "boolean", label: "True / false" },
  { value: "array", label: "List" },
  { value: "object", label: "Object" },
  { value: "null", label: "Empty / null" },
];

const Connections = createContext<{
  relationships: Relationship[];
  disconnect: (relationship: Relationship) => void;
}>({ relationships: [], disconnect: () => {} });

interface ValueEditorProps {
  value: JsonValue;
  onChange: (value: JsonValue) => void;
  depth?: number;
  constraint?: ValueConstraint;
  path?: (string | number)[];
}

function ValueEditor({
  value,
  onChange,
  depth = 0,
  constraint = { type: "any" },
  path = [],
}: ValueEditorProps) {
  const connections = useContext(Connections);
  const references = connections.relationships.filter(
    (edge) =>
      edge.kind === "reference" &&
      JSON.stringify(edge.path) === JSON.stringify(path),
  );
  const type = jsonValueType(value);
  const typeIsInvalid = constraint.type !== "any" && type !== constraint.type;
  const valueIsInvalid = !matchesValueConstraint(value, constraint);
  const allowedTypes = VALUE_TYPES.filter(
    (option) =>
      constraint.type === "any" ||
      option.value === constraint.type ||
      option.value === type,
  );
  if (typeof value === "string" && references.length)
    return (
      <div className="connection-value">
        <Link2 size={14} />
        <span>
          Connected to{" "}
          {references.map((edge) => `${edge.source}.${edge.output}`).join(", ")}
        </span>
        <button
          className="icon-button"
          aria-label={`Disconnect ${JSON.stringify(path)}`}
          onClick={() => connections.disconnect(references[0])}
        >
          <Unlink size={13} />
        </button>
      </div>
    );
  return (
    <div className={`value-editor depth-${Math.min(depth, 3)}`}>
      <select
        className="value-type"
        aria-label="Value type"
        value={type}
        onChange={(event) => {
          const selected = event.target.value as JsonValueType;
          onChange(
            constraint.type === selected
              ? defaultValueForConstraint(constraint)
              : defaultJsonValue(selected),
          );
        }}
      >
        {allowedTypes.map((option) => (
          <option value={option.value} key={option.value}>
            {typeIsInvalid && option.value === type
              ? `${option.label} (invalid)`
              : option.label}
          </option>
        ))}
      </select>
      {type === "string" && (
        <AutoSizeTextarea
          aria-label="Text value"
          value={value as string}
          placeholder="Enter a value or Stacksmith expression"
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {type === "number" && (
        <input
          aria-label="Number value"
          type="number"
          value={value as number}
          onChange={(event) => {
            const next = event.target.valueAsNumber;
            if (Number.isFinite(next)) onChange(next);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur();
          }}
        />
      )}
      {type === "boolean" && (
        <label className="boolean-control">
          <input
            type="checkbox"
            checked={value as boolean}
            onChange={(event) => onChange(event.target.checked)}
          />
          <span>{value ? "True" : "False"}</span>
        </label>
      )}
      {type === "null" && <div className="null-value">No value</div>}
      {valueIsInvalid && (
        <p className="error-text">
          This value does not match the module input type.
        </p>
      )}
      {type === "array" && (
        <ArrayEditor
          path={path}
          value={value as JsonValue[]}
          onChange={onChange}
          depth={depth}
          constraint={constraint}
        />
      )}
      {type === "object" && (
        <ObjectEditor
          path={path}
          value={value as Record<string, JsonValue>}
          onChange={onChange}
          depth={depth}
          constraint={constraint}
        />
      )}
    </div>
  );
}

function ArrayEditor({
  path,
  value,
  onChange,
  depth,
  constraint,
}: {
  path: (string | number)[];
  value: JsonValue[];
  onChange: (value: JsonValue) => void;
  depth: number;
  constraint: ValueConstraint;
}) {
  return (
    <div className="collection-editor">
      {value.map((item, index) => (
        <div className="collection-row" key={index}>
          <span className="collection-index">{index + 1}</span>
          <ValueEditor
            path={[...path, index]}
            value={item}
            onChange={(next) =>
              onChange(
                value.map((current, itemIndex) =>
                  itemIndex === index ? next : current,
                ),
              )
            }
            depth={depth + 1}
            constraint={constraint.tupleItems?.[index] ?? constraint.items}
          />
          <ListRemoveButton
            label={`Remove list item ${index + 1}`}
            onClick={() =>
              onChange(value.filter((_, itemIndex) => itemIndex !== index))
            }
          />
        </div>
      ))}
      <ListAddButton
        className="add-value"
        onClick={() =>
          onChange([
            ...value,
            defaultValueForConstraint(
              constraint.tupleItems?.[value.length] ??
                constraint.items ?? { type: "any" },
            ),
          ])
        }
      >
        Add list item
      </ListAddButton>
    </div>
  );
}

function ObjectEditor({
  path,
  value,
  onChange,
  depth,
  constraint,
}: {
  path: (string | number)[];
  value: Record<string, JsonValue>;
  onChange: (value: JsonValue) => void;
  depth: number;
  constraint: ValueConstraint;
}) {
  const [newKey, setNewKey] = useState("");
  return (
    <div className="collection-editor object-editor">
      {Object.entries(value).map(([key, item]) => (
        <div className="object-row" key={key}>
          <ObjectKeyEditor
            name={key}
            existingNames={Object.keys(value)}
            onChange={(replacement) =>
              onChange(
                Object.fromEntries(
                  Object.entries(value).map(([currentKey, currentValue]) =>
                    currentKey === key
                      ? [replacement, currentValue]
                      : [currentKey, currentValue],
                  ),
                ),
              )
            }
          />
          <ValueEditor
            path={[...path, key]}
            value={item}
            onChange={(next) => onChange({ ...value, [key]: next })}
            depth={depth + 1}
            constraint={
              constraint.properties?.[key] ?? constraint.additionalProperties
            }
          />
          <ListRemoveButton
            label={`Remove ${key}`}
            onClick={() =>
              onChange(
                Object.fromEntries(
                  Object.entries(value).filter(([name]) => name !== key),
                ),
              )
            }
          />
        </div>
      ))}
      <div className="new-object-key">
        <input
          aria-label="New object key"
          placeholder="New key"
          value={newKey}
          onChange={(event) => setNewKey(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              newKey &&
              !Object.hasOwn(value, newKey)
            ) {
              onChange({
                ...value,
                [newKey]: defaultValueForConstraint(
                  constraint.properties?.[newKey] ??
                    constraint.additionalProperties ?? { type: "any" },
                ),
              });
              setNewKey("");
            }
          }}
        />
        <ListAddButton
          className="add-value"
          disabled={!newKey || Object.hasOwn(value, newKey)}
          onClick={() => {
            onChange({
              ...value,
              [newKey]: defaultValueForConstraint(
                constraint.properties?.[newKey] ??
                  constraint.additionalProperties ?? { type: "any" },
              ),
            });
            setNewKey("");
          }}
        >
          Add key
        </ListAddButton>
      </div>
    </div>
  );
}

function ObjectKeyEditor({
  name,
  existingNames,
  onChange,
}: {
  name: string;
  existingNames: string[];
  onChange: (name: string) => void;
}) {
  const [draft, setDraft] = useState(name);
  const invalid = !draft || (draft !== name && existingNames.includes(draft));
  return (
    <input
      className={`object-key ${invalid ? "is-invalid" : ""}`}
      aria-label={`Object key ${name}`}
      aria-invalid={invalid}
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        if (invalid) setDraft(name);
        else if (draft !== name) onChange(draft);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
      }}
    />
  );
}

interface PropertyEditorProps {
  inputs: Port[];
  properties: Record<string, unknown>;
  relationships: Relationship[];
  onChange: (properties: Record<string, unknown>) => void;
}

export function PropertyEditor({
  inputs,
  properties,
  relationships,
  onChange,
}: PropertyEditorProps) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [source, setSource] = useState(JSON.stringify(properties, null, 2));
  const [error, setError] = useState("");
  const inputMap = new Map(inputs.map((input) => [input.name, input]));
  const names = [
    ...inputs.map((input) => input.name),
    ...Object.keys(properties).filter((name) => !inputMap.has(name)),
  ];
  useEffect(() => setSource(JSON.stringify(properties, null, 2)), [properties]);

  function update(next: Record<string, unknown>) {
    onChange(next);
    setSource(JSON.stringify(next, null, 2));
    setError("");
  }

  return (
    <div className="properties-editor">
      <div className="properties-heading">
        <span>Properties</span>
        <span>{Object.keys(properties).length} authored</span>
      </div>
      <Connections.Provider
        value={{
          relationships,
          disconnect: (relationship) =>
            update(removePropertyAtPath(properties, relationship.path)),
        }}
      >
        <div className="property-list">
          {names.map((name) => {
            const authored = Object.hasOwn(properties, name);
            const value = properties[name];
            const input = inputMap.get(name);
            const constraint = parseTerraformType(input?.type);
            const explicit = relationships.find(
              (edge) =>
                edge.kind === "reference" &&
                edge.property === name &&
                edge.path.length === 1,
            );
            const managed = relationships.find(
              (edge) => edge.kind === "association" && edge.property === name,
            );
            return (
              <section
                className={`property-field ${authored ? "is-authored" : ""}`}
                key={name}
              >
                <div className="property-label">
                  <div>
                    <code>{name}</code>
                    {input?.description && <p>{input.description}</p>}
                  </div>
                  {authored ? (
                    <ListRemoveButton
                      label={`Remove ${name}`}
                      title={`Remove ${name}`}
                      onClick={() =>
                        update(
                          Object.fromEntries(
                            Object.entries(properties).filter(
                              ([key]) => key !== name,
                            ),
                          ),
                        )
                      }
                    />
                  ) : null}
                </div>
                {explicit ? (
                  <div className="connection-value">
                    <Link2 size={14} />
                    <span>
                      Connected to{" "}
                      <code>
                        {explicit.source}.{explicit.output}
                      </code>
                    </span>
                    <button
                      className="icon-button"
                      aria-label={`Disconnect ${name}`}
                      onClick={() =>
                        update(
                          Object.fromEntries(
                            Object.entries(properties).filter(
                              ([key]) => key !== name,
                            ),
                          ),
                        )
                      }
                    >
                      <Unlink size={13} />
                    </button>
                  </div>
                ) : authored && isJsonValue(value) ? (
                  <ValueEditor
                    path={[name]}
                    value={value}
                    onChange={(next) => update({ ...properties, [name]: next })}
                    constraint={constraint}
                  />
                ) : managed ? (
                  <div className="managed-value">
                    <Link2 size={13} /> Supplied by {managed.rule}
                  </div>
                ) : (
                  <ListAddButton
                    className="add-property"
                    onClick={() =>
                      update({
                        ...properties,
                        [name]: defaultValueForConstraint(constraint),
                      })
                    }
                  >
                    Add a value
                  </ListAddButton>
                )}
              </section>
            );
          })}
        </div>
      </Connections.Provider>
      <button
        className="advanced-toggle"
        onClick={() => setShowAdvanced(!showAdvanced)}
      >
        {showAdvanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Braces size={14} /> Advanced JSON
      </button>
      {showAdvanced && (
        <div className="advanced-json">
          <AutoSizeTextarea
            aria-label="Advanced properties JSON"
            spellCheck={false}
            value={source}
            onChange={(event) => setSource(event.target.value)}
          />
          <p className="field-help">
            Use this fallback for copying or editing the complete property
            object.
          </p>
          {error && (
            <ErrorBanner
              message={error}
              signIn={false}
              onDismiss={() => setError("")}
            />
          )}
          <button
            className="button small"
            onClick={() => {
              try {
                const parsed: unknown = JSON.parse(source);
                if (
                  typeof parsed !== "object" ||
                  parsed === null ||
                  Array.isArray(parsed) ||
                  !isJsonValue(parsed)
                )
                  throw new Error(
                    "Properties must be a JSON object containing finite JSON values.",
                  );
                for (const [name, value] of Object.entries(parsed)) {
                  const input = inputMap.get(name);
                  if (input?.type && isJsonValue(value)) {
                    const constraint = parseTerraformType(input.type);
                    if (!matchesValueConstraint(value, constraint)) {
                      throw new Error(
                        `${name} must match the module input type ${input.type}.`,
                      );
                    }
                  }
                }
                update(parsed as Record<string, JsonValue>);
              } catch (caught) {
                setError(
                  caught instanceof Error ? caught.message : "Invalid JSON.",
                );
              }
            }}
          >
            Apply advanced JSON
          </button>
        </div>
      )}
    </div>
  );
}
