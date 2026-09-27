import type { Components, DiagramRoute } from "./types";

export interface Draft {
  components: Components;
  disabled: string[];
  positions: Record<string, { x: number; y: number }>;
  routes: DiagramRoute[];
}

export interface DraftHistory {
  past: Draft[];
  present: Draft;
  future: Draft[];
}

export function inspectionBody(
  snapshot: string,
  draft: Pick<Draft, "components" | "disabled">,
): string {
  return JSON.stringify({
    snapshot,
    components: draft.components,
    disabled_associations: draft.disabled,
  });
}

export type DraftAction =
  | { type: "reset"; draft: Draft }
  | {
      type: "change";
      update: (draft: Draft) => Draft;
      record?: boolean;
    }
  | { type: "undo" }
  | { type: "redo" };

const HISTORY_LIMIT = 100;

export function createDraftHistory(draft?: Partial<Draft>): DraftHistory {
  return {
    past: [],
    present: {
      components: draft?.components ?? {},
      disabled: draft?.disabled ?? [],
      positions: draft?.positions ?? {},
      routes: draft?.routes ?? [],
    },
    future: [],
  };
}

export function draftHistoryReducer(
  history: DraftHistory,
  action: DraftAction,
): DraftHistory {
  switch (action.type) {
    case "reset":
      return createDraftHistory(action.draft);
    case "change": {
      const next = action.update(history.present);
      if (next === history.present) return history;
      if (action.record === false) return { ...history, present: next };
      return {
        past: [...history.past, history.present].slice(-HISTORY_LIMIT),
        present: next,
        future: [],
      };
    }
    case "undo": {
      const present = history.past.at(-1);
      if (!present) return history;
      return {
        past: history.past.slice(0, -1),
        present,
        future: [history.present, ...history.future],
      };
    }
    case "redo": {
      const [present, ...future] = history.future;
      if (!present) return history;
      return {
        past: [...history.past, history.present].slice(-HISTORY_LIMIT),
        present,
        future,
      };
    }
  }
}

export type KeyboardCommand = "delete" | "undo" | "redo";

export function keyboardCommand(event: {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}): KeyboardCommand | null {
  if (event.altKey) return null;
  const modifier = event.metaKey || event.ctrlKey;
  const key = event.key.toLowerCase();
  if (modifier && key === "z") return event.shiftKey ? "redo" : "undo";
  if (modifier && key === "y" && !event.shiftKey) return "redo";
  if (!modifier && (event.key === "Backspace" || event.key === "Delete"))
    return "delete";
  return null;
}

export function isTextEditingTarget(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    Boolean(
      target.closest(
        "input, textarea, select, [contenteditable='true'], [data-keyboard-owner='true']",
      ),
    )
  );
}
