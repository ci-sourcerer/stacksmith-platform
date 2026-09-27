import { describe, expect, it } from "vitest";
import {
  composerUrl,
  EMPTY_WORKSPACE,
  parseWorkspace,
  sameWorkspaceStack,
  selectSoleTarget,
  workspaceAtUrl,
  workspaceKey,
} from "./workspaceSelection";

const remembered = {
  repository: "acme/service",
  path: "stacks/app.stack.yaml",
  environment: "production",
};

describe("workspace preferences", () => {
  it("isolates accounts and stores only workspace identifiers", () => {
    expect(workspaceKey("alice")).not.toBe(workspaceKey("bob"));
    expect(
      parseWorkspace(JSON.stringify({ ...remembered, secret: "discard" })),
    ).toEqual(remembered);
  });
  it.each([null, "bad json", "null", "{}", '{"repository": 3}'])(
    "handles unavailable or invalid preferences: %s",
    (value) => {
      expect(parseWorkspace(value)).toEqual(EMPTY_WORKSPACE);
    },
  );
  it("uses remembered choices when navigating to Settings or Runs", () => {
    for (const path of ["/settings?section=tokens", "/runs"])
      expect(
        workspaceAtUrl(new URL(path, "https://platform.example"), remembered),
      ).toEqual(remembered);
  });
  it("keeps Runs URL targets independent of the workspace", () => {
    expect(
      workspaceAtUrl(
        new URL(
          "https://platform.example/runs?repo=team/other&environment=dev",
        ),
        remembered,
      ),
    ).toEqual(remembered);
  });
  it("honors explicit stack links and preserves an environment only within its repository", () => {
    expect(
      workspaceAtUrl(
        new URL("https://platform.example/#/stacks/acme/service/other.yaml"),
        remembered,
      ),
    ).toEqual({ ...remembered, path: "other.yaml" });
    expect(
      workspaceAtUrl(
        new URL("https://platform.example/#/stacks/team/other/stack.yaml"),
        remembered,
      ),
    ).toEqual({
      repository: "team/other",
      path: "stack.yaml",
      environment: "",
    });
  });
  it("links Composer to the selected stack and allows repositories without stacks", () => {
    expect(composerUrl(remembered)).toBe(
      "/#/stacks/acme/service/stacks/app.stack.yaml",
    );
    expect(composerUrl({ ...remembered, path: "" })).toBe("/#/");
  });
  it("requires a draft guard only when the repository or stack changes", () => {
    expect(
      sameWorkspaceStack(remembered, { ...remembered, environment: "dev" }),
    ).toBe(true);
    expect(
      sameWorkspaceStack(remembered, { ...remembered, path: "other.yaml" }),
    ).toBe(false);
  });
  it("selects a sole inspectable target and preserves deliberate choices", () => {
    const targets = [
      { name: "dev", can_inspect: true },
      { name: "prod", can_inspect: false },
    ];
    expect(
      selectSoleTarget({ ...remembered, environment: "" }, targets).environment,
    ).toBe("dev");
    expect(selectSoleTarget(remembered, targets)).toBe(remembered);
    expect(
      selectSoleTarget(
        { ...remembered, environment: "", preview: true },
        targets,
      ).environment,
    ).toBe("");
    expect(
      selectSoleTarget({ ...remembered, environment: "" }, []).environment,
    ).toBe("");
    expect(
      selectSoleTarget(
        { ...remembered, environment: "" },
        targets.map((target) => ({ ...target, can_inspect: true })),
      ).environment,
    ).toBe("");
  });
  it("remembers explicit preview within its repository", () => {
    const preview = { ...remembered, environment: "", preview: true };
    expect(parseWorkspace(JSON.stringify(preview))).toEqual(preview);
    expect(
      workspaceAtUrl(
        new URL(composerUrl(preview), "https://platform.example"),
        preview,
      ),
    ).toEqual(preview);
    expect(
      workspaceAtUrl(
        new URL("/runs?repo=other/repo", "https://platform.example"),
        preview,
      ).preview,
    ).toBe(true);
  });
});
