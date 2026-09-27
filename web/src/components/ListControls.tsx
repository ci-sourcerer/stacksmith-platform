import type { ButtonHTMLAttributes } from "react";
import { Plus, Trash2 } from "lucide-react";
import "./ListControls.css";

export function ListAddButton({
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button {...props} type="button" className={"list-add-button " + className}>
      <Plus size={14} aria-hidden="true" />
      {children}
    </button>
  );
}

export function ListRemoveButton({
  label,
  className = "",
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  label: string;
}) {
  return (
    <button
      {...props}
      type="button"
      aria-label={label}
      title={label}
      className={"list-remove-button " + className}
    >
      <Trash2 size={14} aria-hidden="true" />
      Remove
    </button>
  );
}

export type StringListItem = { id: string; value: string };

export function stringListItems(values: string[]): StringListItem[] {
  return values.map((value) => ({ id: crypto.randomUUID(), value }));
}

export function StringListEditor({
  label,
  itemLabel,
  items,
  onChange,
  onCommit,
  disabled = false,
  maxItems,
  placeholder,
}: {
  label: string;
  itemLabel: string;
  items: StringListItem[];
  onChange: (items: StringListItem[]) => void;
  onCommit?: (values: string[]) => void;
  disabled?: boolean;
  maxItems?: number;
  placeholder?: string;
}) {
  return (
    <fieldset className="string-list-editor" disabled={disabled}>
      <legend>{label}</legend>
      <div className="string-list-rows">
        {items.map((item, index) => (
          <div className="string-list-row" key={item.id}>
            <input
              aria-label={itemLabel + " " + (index + 1)}
              value={item.value}
              placeholder={placeholder}
              onChange={(event) =>
                onChange(
                  items.map((current) =>
                    current.id === item.id
                      ? { ...current, value: event.target.value }
                      : current,
                  ),
                )
              }
              onBlur={() => onCommit?.(items.map((current) => current.value))}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  event.currentTarget.blur();
                }
              }}
            />
            <ListRemoveButton
              label={"Remove " + itemLabel.toLowerCase() + " " + (index + 1)}
              onClick={() => {
                const next = items.filter((current) => current.id !== item.id);
                onChange(next);
                onCommit?.(next.map((current) => current.value));
              }}
            />
          </div>
        ))}
      </div>
      <ListAddButton
        disabled={
          disabled || (maxItems !== undefined && items.length >= maxItems)
        }
        onClick={() =>
          onChange([...items, { id: crypto.randomUUID(), value: "" }])
        }
      >
        Add {itemLabel.toLowerCase()}
      </ListAddButton>
    </fieldset>
  );
}
