import { afterEach, describe, expect, it, vi } from "vitest";
import {
  editorHash,
  initialEditorRoute,
  parseEditorRoute,
  rememberReturnRoute,
  restoreReturnRoute,
  sameStack,
  stackRequest,
} from "./navigation";

afterEach(() => vi.unstubAllGlobals());

describe("editor links", () => {
  it("round-trips repository files, special characters, view and mode", () => {
    const route = {
      stack: {
        repository: "team/infrastructure",
        path: "stacks/a #?%.stack.yaml",
      },
      view: "resolution" as const,
      viewOnly: true,
    };
    expect(editorHash(route)).toBe(
      "#/stacks/team/infrastructure/stacks/a%20%23%3F%25.stack.yaml?view=resolution&mode=view",
    );
    expect(parseEditorRoute(editorHash(route))).toEqual(route);
    expect(stackRequest(route.stack)).toBe(
      "/repositories/team/infrastructure/stack?path=stacks%2Fa%20%23%3F%25.stack.yaml",
    );
  });

  it("keeps the example and stack-free workspace views linkable", () => {
    for (const stack of ["example", null] as const) {
      for (const view of [
        "compose",
        "catalog",
        "review",
        "resolution",
      ] as const) {
        expect(
          parseEditorRoute(editorHash({ stack, view, viewOnly: false })),
        ).toEqual({ stack, view, viewOnly: false });
      }
    }
    expect(stackRequest("example")).toBe("/sample");
  });

  it("uses editing and composer defaults for unknown options", () => {
    expect(parseEditorRoute("#/example?view=unknown&mode=unknown")).toEqual({
      stack: "example",
      view: "compose",
      viewOnly: false,
    });
  });

  it("does not treat incomplete or malformed links as stack targets", () => {
    for (const hash of [
      "",
      "#/stacks/team/repo",
      "#/stacks/team/repo/",
      "#/stacks/team/repo/%zz",
    ]) {
      expect(parseEditorRoute(hash).stack).toBeNull();
    }
  });

  it("compares stack identity independently of parsed object references", () => {
    expect(
      sameStack(
        { repository: "team/repo", path: "a.yaml" },
        { repository: "team/repo", path: "a.yaml" },
      ),
    ).toBe(true);
    expect(
      sameStack(
        { repository: "team/repo", path: "a.yaml" },
        { repository: "team/repo", path: "b.yaml" },
      ),
    ).toBe(false);
    expect(sameStack("example", null)).toBe(false);
  });
});

describe("sign-in return links", () => {
  function browser(hash: string) {
    const values = new Map<string, string>();
    const location = new URL(`https://platform.example/${hash}`);
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    });
    vi.stubGlobal("window", {
      location,
      history: {
        state: null,
        replaceState: (_state: unknown, _title: string, href: string) => {
          location.href = new URL(href, location).href;
        },
      },
    });
    return location;
  }

  it("restores a requested stack after the sign-in callback returns home", () => {
    const location = browser("#/stacks/team/repo/stack.yaml?mode=view");
    rememberReturnRoute();
    location.hash = "";
    restoreReturnRoute();
    expect(initialEditorRoute()).toEqual({
      stack: { repository: "team/repo", path: "stack.yaml" },
      view: "compose",
      viewOnly: true,
    });
    expect(location.hash).toBe("#/stacks/team/repo/stack.yaml?mode=view");
  });

  it("prefers a new explicit link and consumes the saved return route", () => {
    const location = browser("#/example");
    rememberReturnRoute();
    location.hash = "#/catalog";
    restoreReturnRoute();
    expect(initialEditorRoute().view).toBe("catalog");
    location.hash = "";
    expect(initialEditorRoute().stack).toBeNull();
  });

  it("restores the full Settings destination after authentication", () => {
    const location = browser("");
    location.href = "https://platform.example/settings?section=tokens";
    rememberReturnRoute();
    location.href =
      "https://platform.example/settings?section=github&auth_return=1";
    restoreReturnRoute();
    expect(location.pathname + location.search).toBe(
      "/settings?section=tokens",
    );
  });

  it("rejects external sign-in return destinations", () => {
    const location = browser("");
    vi.stubGlobal("sessionStorage", {
      getItem: () => "//outside.example/settings",
      removeItem: () => {},
    });
    restoreReturnRoute();
    expect(location.origin).toBe("https://platform.example");
  });

  it("allows sign-in and navigation when browser storage is unavailable", () => {
    browser("#/example");
    vi.stubGlobal("sessionStorage", {
      getItem: () => {
        throw new DOMException("Blocked", "SecurityError");
      },
      setItem: () => {
        throw new DOMException("Blocked", "SecurityError");
      },
    });
    expect(() => rememberReturnRoute()).not.toThrow();
    expect(() => restoreReturnRoute()).not.toThrow();
    expect(initialEditorRoute().stack).toBe("example");
  });
});
