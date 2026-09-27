import { describe, expect, it } from "vitest";
import {
  createDraftHistory,
  draftHistoryReducer,
  keyboardCommand,
} from "./history";

describe("draft history", () => {
  it("undoes and redoes authored changes", () => {
    const initial = createDraftHistory();
    const changed = draftHistoryReducer(initial, {
      type: "change",
      update: (draft) => ({
        ...draft,
        components: { api: { type: "service" } },
      }),
    });

    expect(draftHistoryReducer(changed, { type: "undo" }).present).toEqual(
      initial.present,
    );
    expect(
      draftHistoryReducer(draftHistoryReducer(changed, { type: "undo" }), {
        type: "redo",
      }).present,
    ).toEqual(changed.present);
  });

  it("groups unrecorded drag updates with the preceding checkpoint", () => {
    const initial = createDraftHistory();
    const started = draftHistoryReducer(initial, {
      type: "change",
      update: (draft) => ({
        ...draft,
        positions: { api: { x: 10, y: 10 } },
      }),
    });
    const moved = draftHistoryReducer(started, {
      type: "change",
      record: false,
      update: (draft) => ({
        ...draft,
        positions: { api: { x: 80, y: 40 } },
      }),
    });

    expect(draftHistoryReducer(moved, { type: "undo" }).present).toEqual(
      initial.present,
    );
  });
});

describe("keyboard commands", () => {
  const event = {
    key: "",
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
  };

  it("recognizes platform-standard undo and redo shortcuts", () => {
    expect(keyboardCommand({ ...event, key: "z", metaKey: true })).toBe("undo");
    expect(
      keyboardCommand({ ...event, key: "z", ctrlKey: true, shiftKey: true }),
    ).toBe("redo");
    expect(keyboardCommand({ ...event, key: "y", ctrlKey: true })).toBe("redo");
  });

  it("recognizes both component deletion keys", () => {
    expect(keyboardCommand({ ...event, key: "Backspace" })).toBe("delete");
    expect(keyboardCommand({ ...event, key: "Delete" })).toBe("delete");
  });
});
