import { useState } from "react";
import {
  defaultValueForConstraint,
  parseTerraformType,
  valueAtPath,
  type ValueConstraint,
} from "../model";
import { Modal } from "./Dialogs";

type Path = (string | number)[];
type Operation = "append" | "field" | "input";
interface Destination {
  path: Path;
  value: unknown;
}

export function destinationLabel(path: Path): string {
  return path
    .map((part) => (typeof part === "number" ? `Item ${part + 1}` : part))
    .join(" › ");
}

function destinations(
  value: unknown,
  constraint: ValueConstraint,
  path: Path,
): Destination[] {
  const shape =
    value === undefined &&
    (constraint.type === "array" || constraint.type === "object")
      ? defaultValueForConstraint(constraint)
      : value;
  return [
    { path, value: shape },
    ...(Array.isArray(shape)
      ? shape.flatMap((child, index) =>
          destinations(
            child,
            constraint.tupleItems?.[index] ??
              constraint.items ?? { type: "any" },
            [...path, index],
          ),
        )
      : shape !== null && typeof shape === "object"
        ? Object.entries(shape).flatMap(([key, child]) =>
            destinations(
              child,
              constraint.properties?.[key] ??
                constraint.additionalProperties ?? { type: "any" },
              [...path, key],
            ),
          )
        : []),
  ];
}

export function ConnectionDestination({
  source,
  target,
  property,
  value,
  type,
  onChoose,
  onCancel,
}: {
  source: string;
  target: string;
  property: string;
  value: unknown;
  type?: string;
  onChoose: (path: Path) => void;
  onCancel: () => void;
}) {
  const choices = destinations(value, parseTerraformType(type), [property]);
  const collection =
    choices[0].value !== null && typeof choices[0].value === "object";
  const array = Array.isArray(choices[0].value);
  const [operation, setOperation] = useState<Operation>(
    array ? "append" : collection ? "field" : "input",
  );
  const [selected, setSelected] = useState("");
  const [parent, setParent] = useState(
    JSON.stringify(
      choices.find(
        (choice) =>
          choice.value !== null &&
          typeof choice.value === "object" &&
          !Array.isArray(choice.value),
      )?.path ?? [],
    ),
  );
  const [newKey, setNewKey] = useState("");
  const lists = choices.filter((choice) => Array.isArray(choice.value));
  const objects = choices.filter(
    (choice) =>
      choice.value !== null &&
      typeof choice.value === "object" &&
      !Array.isArray(choice.value),
  );
  const destination =
    operation === "input"
      ? [property]
      : operation === "append"
        ? appendDestination(lists, selected)
        : selected === "new" && newKey
          ? [...JSON.parse(parent), newKey]
          : selected && selected !== "new"
            ? (JSON.parse(selected) as Path)
            : undefined;
  const currentValue = destination
    ? valueAtPath({ [property]: value }, destination)
    : undefined;
  const duplicateKey =
    selected === "new" &&
    Boolean(
      destination &&
      valueAtPath({ [property]: value }, destination) !== undefined,
    );
  const valid =
    destination && !duplicateKey && (selected !== "new" || newKey.length > 0);
  const action =
    operation === "append"
      ? "Add item"
      : operation === "input"
        ? collection
          ? "Replace input"
          : "Replace value"
        : currentValue !== undefined
          ? "Replace value"
          : "Connect field";

  function selectOperation(next: Operation) {
    setOperation(next);
    setSelected("");
    setNewKey("");
  }

  return (
    <Modal
      title={`Connect ${source} to ${target}.${property}`}
      description="Choose where this output should be connected, then review the change."
      onCancel={onCancel}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (valid) onChoose(destination);
        }}
      >
        {collection && (
          <fieldset className="connection-operations">
            <legend>How should this input be connected?</legend>
            {array && (
              <label className="connection-operation">
                <input
                  type="radio"
                  name="connection-operation"
                  checked={operation === "append"}
                  onChange={() => selectOperation("append")}
                />
                Add a list item
              </label>
            )}
            <label className="connection-operation">
              <input
                type="radio"
                name="connection-operation"
                checked={operation === "field"}
                onChange={() => selectOperation("field")}
              />
              {array ? "Choose an existing item or field" : "Connect a field"}
            </label>
            <label className="connection-operation">
              <input
                type="radio"
                name="connection-operation"
                checked={operation === "input"}
                onChange={() => selectOperation("input")}
              />
              Replace the entire input
            </label>
          </fieldset>
        )}
        <div className="connection-fields">
          {operation === "append" && (
            <label>
              List
              <select
                value={selected || JSON.stringify(lists[0]?.path)}
                data-dialog-initial-focus
                onChange={(event) => setSelected(event.target.value)}
              >
                {lists.map((choice) => (
                  <option
                    key={JSON.stringify(choice.path)}
                    value={JSON.stringify(choice.path)}
                  >
                    {destinationLabel(choice.path)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {operation === "field" && (
            <label>
              Destination
              <select
                data-dialog-initial-focus
                value={selected}
                onChange={(event) => setSelected(event.target.value)}
              >
                <option value="">Choose an item or field</option>
                {choices
                  .filter((choice) => choice.path.length > 1)
                  .map((choice) => (
                    <option
                      key={JSON.stringify(choice.path)}
                      value={JSON.stringify(choice.path)}
                    >
                      {destinationLabel(choice.path)}
                    </option>
                  ))}
                {objects.length > 0 && (
                  <option value="new">Create a new field…</option>
                )}
              </select>
            </label>
          )}
          {operation === "field" && selected === "new" && (
            <>
              <label>
                Object
                <select
                  value={parent}
                  onChange={(event) => setParent(event.target.value)}
                >
                  {objects.map((choice) => (
                    <option
                      key={JSON.stringify(choice.path)}
                      value={JSON.stringify(choice.path)}
                    >
                      {destinationLabel(choice.path)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                New field name
                <input
                  type="text"
                  value={newKey}
                  onChange={(event) => setNewKey(event.target.value)}
                />
              </label>
              {duplicateKey && (
                <p role="alert">
                  This field already exists. Select it from the destination list
                  to replace its value.
                </p>
              )}
            </>
          )}
        </div>
        {valid && (
          <section
            className="connection-preview"
            aria-label="Connection preview"
          >
            <h3>
              {target} › {destinationLabel(destination)}
            </h3>
            <strong>Current value</strong>
            {currentValue === undefined ? (
              <p>Not set</p>
            ) : (
              <pre>{JSON.stringify(currentValue, null, 2)}</pre>
            )}
            <strong>New value</strong>
            <p>
              Reference to <code>{source}</code>
            </p>
            {currentValue !== undefined && (
              <p className="connection-warning">
                {operation === "input" && collection
                  ? "The entire input will be replaced, including all its current fields or items."
                  : "The current value at this destination will be replaced. Other fields and items are preserved."}
              </p>
            )}
          </section>
        )}
        <div className="dialog-footer">
          <button
            type="button"
            className="button"
            data-dialog-initial-focus={operation === "input" ? "" : undefined}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="submit"
            className={`button ${currentValue !== undefined ? "danger" : "primary"}`}
            disabled={!valid}
          >
            {action}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function appendDestination(
  lists: Destination[],
  selected: string,
): Path | undefined {
  const list =
    lists.find((choice) => JSON.stringify(choice.path) === selected) ??
    lists[0];
  return list ? [...list.path, (list.value as unknown[]).length] : undefined;
}
