import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Blocks,
  BookOpen,
  Check,
  CircleAlert,
  ExternalLink,
  Eye,
  FileCode2,
  FolderGit2,
  GitBranch,
  Github,
  LoaderCircle,
  Plus,
  Pencil,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import type { Connection } from "@xyflow/react";
import { api } from "./api";
import { inspectionStatus } from "./inspectionStatus";
import { InspectionSummary } from "./components/InspectionSummary";
import {
  affectedConnections,
  disabledAssociationScopes,
  setRuleEnabled,
  visibleAssociation,
  type AssociationScope,
} from "./associations";
import {
  canRemove,
  connectComponents,
  nextComponentName,
  relationshipKey,
  removeReference,
  parseTerraformType,
  pathStartsWith,
  routesAfterRemoval,
  valueAtPath,
} from "./model";
import {
  createDraftHistory,
  draftHistoryReducer,
  isTextEditingTarget,
  keyboardCommand,
  inspectionBody,
  type DraftAction,
} from "./history";
import { Canvas } from "./components/Canvas";
import { Inspector } from "./components/Inspector";
import { ResolutionView } from "./components/ResolutionView";
import { AssociationImpact } from "./components/AssociationImpact";
import { ConnectionInspector } from "./components/ConnectionInspector";
import {
  ManagedConnections,
  ManagedConnectionSummary,
} from "./components/ManagedConnections";
import { ActionMenu } from "./components/ActionMenu";
import { ErrorBanner } from "./components/ErrorBanner";
import { InspectionDetails } from "./components/InspectionDetails";
import { ConnectionDestination } from "./components/ConnectionDestination";
import { useConfirmation } from "./components/Dialogs";
import { useAuthoredGraph } from "./useAuthoredGraph";
import { useDraftReview } from "./useDraftReview";
import {
  canRestore,
  parseRecovery,
  recoveryKey,
  recoveryRecord,
  type Recovery,
} from "./recovery";
import { TypeIcon } from "./components/ComponentNode";
import { useEditorRoute } from "./useEditorRoute";
import { useApplication } from "./ApplicationContext";
import { stackRequest, type View } from "./navigation";
import { newComponentPosition, snapPoint } from "./layout";
import type {
  Catalog,
  Platform,
  Relationship,
  Review,
  Snapshot,
} from "./types";

const CATALOG_PAGE_SIZE = 12;

interface ConnectionSelection {
  context: string;
  relationship: Relationship;
  knownConnections: Relationship[];
}

function downloadSource(source: string, path: string) {
  const url = URL.createObjectURL(new Blob([source], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = path.split("/").at(-1) ?? "stack.yaml";
  link.click();
  URL.revokeObjectURL(url);
}

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "An unexpected error occurred.";
}

export default function App() {
  const {
    session,
    workspace,
    registerGuard,
    location,
    navigate: applicationNavigate,
    targetError,
  } = useApplication();
  const [baseCatalog, setBaseCatalog] = useState<Catalog>({});
  const [environmentCatalog, setEnvironmentCatalog] = useState<{
    key: string;
    catalog: Catalog;
  }>();
  const [catalogFailure, setCatalogFailure] = useState<unknown>();
  const [pendingConnection, setPendingConnection] = useState<Connection>();
  const connectionScope = useRef("");
  const [platform, setPlatform] = useState<Platform>();
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [draftHistory, reduceDraft] = useReducer(
    draftHistoryReducer,
    undefined,
    () => createDraftHistory(),
  );
  const { components, positions, routes, disabled } = draftHistory.present;
  const [selected, setSelected] = useState<string | null>(null);
  const [connectionSelection, setConnectionSelection] =
    useState<ConnectionSelection | null>(null);
  const [managedOpen, setManagedOpen] = useState(false);
  const { route, navigate } = useEditorRoute();
  const { confirm, cancel: cancelConfirmation } = useConfirmation();
  const pendingLeave = useRef<Promise<boolean> | null>(null);
  const { view } = route;
  const viewOnly = route.viewOnly || snapshot?.can_author === false;
  const [openingStack, setOpeningStack] = useState(false);
  const [search, setSearch] = useState("");
  const [catalogPage, setCatalogPage] = useState(0);
  const [review, setReview] = useState<Review>();
  const [reviewContext, setReviewContext] = useState("");
  const [inspectedKey, setInspectedKey] = useState("");
  const [rendered, setRendered] = useState<{
    key: string;
    source: string;
    diff: string;
  }>();
  const [sourceError, setSourceError] = useState("");
  const [sourceFailure, setSourceFailure] = useState<unknown>();
  const [inspectionAttempt, setInspectionAttempt] = useState(0);
  const [sourceAttempt, setSourceAttempt] = useState(0);
  const [recovery, setRecovery] = useState<Recovery>();
  const [storageError, setStorageError] = useState("");
  const [savedDraft, setSavedDraft] = useState(false);
  const structuralKey = useMemo(
    () => inspectionBody(snapshot?.snapshot ?? "", { components, disabled }),
    [snapshot?.snapshot, components, disabled],
  );
  const environment =
    session.execution_enabled &&
    snapshot?.repository &&
    snapshot.repository === workspace.repository
      ? workspace.environment || null
      : null;
  const hasDeploymentContext = Boolean(
    session.execution_enabled && snapshot?.repository,
  );
  const inspectionContextKey = JSON.stringify({
    snapshot: snapshot?.snapshot ?? "",
    environment,
  });
  const selectedConnection =
    connectionSelection?.context === inspectionContextKey
      ? connectionSelection
      : null;
  const selectedRelationship = selectedConnection
    ? relationshipKey(selectedConnection.relationship)
    : null;
  const disabledScopes = disabledAssociationScopes({ components, disabled });
  const knownConnections = useMemo(
    () => [
      ...new Map(
        [
          ...(selectedConnection?.knownConnections ?? []),
          ...(reviewContext === inspectionContextKey
            ? (review?.edges ?? [])
            : []),
        ]
          .filter(
            (edge) =>
              edge.kind === "association" &&
              Object.hasOwn(components, edge.source) &&
              Object.hasOwn(components, edge.target),
          )
          .map((edge) => [relationshipKey(edge), edge]),
      ).values(),
    ],
    [
      selectedConnection,
      reviewContext,
      inspectionContextKey,
      review,
      components,
    ],
  );
  const managedRules = useMemo(
    () => [
      ...new Map(
        [
          ...(platform?.associations ?? []),
          ...(reviewContext === inspectionContextKey
            ? (review?.associations.rules ?? [])
            : []),
        ].map((rule) => [rule.name, rule]),
      ).values(),
    ],
    [platform, reviewContext, inspectionContextKey, review],
  );
  const semanticKey = useMemo(
    () => JSON.stringify({ ...JSON.parse(structuralKey), environment }),
    [structuralKey, environment],
  );
  const sourceKey = useMemo(
    () => JSON.stringify({ ...JSON.parse(structuralKey), positions, routes }),
    [structuralKey, positions, routes],
  );
  const confirmationScope = useRef(sourceKey);
  confirmationScope.current = sourceKey;
  useEffect(
    () => () => cancelConfirmation(),
    [
      cancelConfirmation,
      sourceKey,
      environment,
      recovery,
      location.pathname,
      view,
    ],
  );
  useEffect(() => {
    if (pendingConnection && connectionScope.current !== sourceKey)
      setPendingConnection(undefined);
  }, [sourceKey, pendingConnection]);
  const authoredGraph = useAuthoredGraph(
    structuralKey,
    components,
    Boolean(snapshot?.editable),
  );
  const catalog =
    environmentCatalog?.key === inspectionContextKey
      ? environmentCatalog.catalog
      : baseCatalog;
  const relationships = useMemo(
    () => [
      ...authoredGraph.edges,
      ...(reviewContext === inspectionContextKey
        ? (review?.edges ?? []).filter((edge) =>
            visibleAssociation({ components, disabled }, edge),
          )
        : []),
    ],
    [
      authoredGraph.edges,
      review,
      reviewContext,
      inspectionContextKey,
      components,
      disabled,
    ],
  );
  const inspectionCurrent = inspectedKey === semanticKey;
  const sourceCurrent = rendered?.key === sourceKey;
  const [inspecting, setInspecting] = useState(false);
  const [validationError, setValidationError] = useState("");
  const [inspectionFailure, setInspectionFailure] = useState<unknown>();
  const managedConnectionStatus = inspectionStatus({
    environment,
    inspecting,
    current: inspectionCurrent,
    error: validationError,
    hasPrevious: reviewContext === inspectionContextKey && Boolean(review),
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [creatingPR, setCreatingPR] = useState(false);
  const [pr, setPr] = useState<{
    url: string;
    patch_url: string;
    number: number;
  }>();

  function loadSnapshot(
    opened: Snapshot,
    principal = session?.login ?? "local-demo",
  ) {
    setRecovery(undefined);
    setStorageError("");
    setSavedDraft(false);
    try {
      const stored = localStorage.getItem(recoveryKey(opened, principal));
      if (stored) setRecovery(parseRecovery(stored));
    } catch (failure) {
      setStorageError(
        `Draft recovery is unavailable: ${errorMessage(failure)} Download a recovery file to keep your work.`,
      );
    }
    dispatchDraft({
      type: "reset",
      draft: {
        positions:
          opened.document?.metadata?.stacksmith_composer?.positions ?? {},
        routes: opened.document?.metadata?.stacksmith_composer?.routes ?? [],
        components: opened.document?.components ?? {},
        disabled: opened.document?.disabled_associations ?? [],
      },
    });
    setSnapshot(opened);
    setSelected(null);
    setSelectedRelationship(null);
    setPendingConnection(undefined);
    setPr(undefined);
    setReview(undefined);
    setInspectedKey("");
    setRendered(undefined);
    setValidationError("");
  }

  const editable = snapshot?.editable ?? false;
  const canEdit = editable && !viewOnly && !openingStack;
  useEffect(() => {
    setPendingConnection(undefined);
  }, [canEdit, location.pathname, view]);
  const editingEnabled = useRef(canEdit);
  editingEnabled.current = canEdit;

  function dispatchDraft(action: DraftAction) {
    if (action.type === "reset" || editingEnabled.current) reduceDraft(action);
  }

  function clearStack() {
    setSnapshot(undefined);
    dispatchDraft({ type: "reset", draft: createDraftHistory().present });
    setReview(undefined);
    setRendered(undefined);
    setSelected(null);
    setSelectedRelationship(null);
    setPendingConnection(undefined);
    setRecovery(undefined);
    setStorageError("");
    setSavedDraft(false);
    setPr(undefined);
    setValidationError("");
    setSourceError("");
    setSourceFailure(undefined);
  }

  function setView(view: View) {
    navigate({ ...route, view });
  }

  function setSelectedRelationship(key: string | null) {
    setManagedOpen(false);
    if (!key) {
      setConnectionSelection(null);
      return;
    }
    const relationship = relationships.find(
      (edge) => relationshipKey(edge) === key,
    );
    if (!relationship) return;
    setSelected(null);
    setConnectionSelection({
      context: inspectionContextKey,
      relationship,
      knownConnections,
    });
  }

  function selectComponent(name: string | null) {
    setSelectedRelationship(null);
    setSelected(name);
  }

  function openManagedConnections() {
    setSelected(null);
    setConnectionSelection(null);
    setManagedOpen(true);
    if (view !== "compose") setView("compose");
  }

  function confirmStackChange(): Promise<boolean> {
    if (!dirty) return Promise.resolve(true);
    if (!pendingLeave.current)
      pendingLeave.current = confirm({
        title: "Leave this draft?",
        description: savedDraft
          ? "Your draft remains saved in this browser. You can restore it when you reopen this stack."
          : "Your current edits have not been saved. Download a recovery file before leaving if you want to keep them.",
        actionLabel: "Leave draft",
        destructive: !savedDraft,
      }).finally(() => {
        pendingLeave.current = null;
      });
    return pendingLeave.current;
  }

  useEffect(() => {
    const stack =
      session.mode === "demo"
        ? "example"
        : workspace.repository && workspace.path
          ? { repository: workspace.repository, path: workspace.path }
          : null;
    void navigate({ ...route, stack }, true, true);
  }, [workspace.repository, workspace.path, session.mode]);

  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    setError("");
    Promise.all([
      session.execution_enabled && workspace.repository
        ? Promise.resolve({} as Catalog)
        : api<Catalog>("/catalog", undefined, abort.signal),
      api<Platform>(
        `/platform${workspace.repository ? `?repository=${encodeURIComponent(workspace.repository)}` : ""}`,
        undefined,
        abort.signal,
      ),
    ])
      .then(([types, policy]) => {
        if (!abort.signal.aborted) {
          setBaseCatalog(types);
          setPlatform(policy);
        }
      })
      .catch((failure) => {
        if (!abort.signal.aborted) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [workspace.repository, session.execution_enabled]);

  useEffect(() => {
    setPendingConnection(undefined);
    setConnectionSelection(null);
    setSelected(null);
    setManagedOpen(false);
  }, [inspectionContextKey]);

  useEffect(() => {
    if (loading || !session || (session.mode === "github" && !session.login))
      return;
    if (!route.stack) {
      setOpeningStack(false);
      if (session.mode === "demo") {
        navigate({ ...route, stack: "example" }, true);
      } else {
        clearStack();
      }
      return;
    }
    const abort = new AbortController();
    setOpeningStack(true);
    clearStack();
    api<Snapshot>(stackRequest(route.stack), undefined, abort.signal)
      .then((opened) => {
        if (abort.signal.aborted) return;
        loadSnapshot(opened);
      })
      .catch((failure) => {
        if (abort.signal.aborted) return;
        setError(errorMessage(failure));
      })
      .finally(() => {
        if (!abort.signal.aborted) setOpeningStack(false);
      });
    return () => abort.abort();
  }, [loading, session, route.stack]);

  const draftReview = useDraftReview(
    semanticKey,
    Boolean(snapshot?.editable),
    inspectionAttempt,
  );

  useEffect(() => {
    if (
      !session.execution_enabled ||
      !snapshot?.editable ||
      !workspace.repository ||
      !draftReview.failure ||
      environmentCatalog?.key === inspectionContextKey
    )
      return;
    const abort = new AbortController();
    setCatalogFailure(undefined);
    api<{ catalog: Catalog } | Catalog>(
      environment
        ? "/catalog"
        : `/catalog?repository=${encodeURIComponent(workspace.repository)}`,
      environment ? JSON.parse(inspectionContextKey) : undefined,
      abort.signal,
    )
      .then((result) => {
        if (!abort.signal.aborted)
          setEnvironmentCatalog({
            key: inspectionContextKey,
            catalog: environment
              ? (result as { catalog: Catalog }).catalog
              : (result as Catalog),
          });
      })
      .catch((failure: unknown) => {
        if (!abort.signal.aborted) setCatalogFailure(failure);
      });
    return () => abort.abort();
  }, [
    session.execution_enabled,
    snapshot?.editable,
    workspace.repository,
    environment,
    inspectionContextKey,
    draftReview.failure,
    environmentCatalog?.key,
  ]);

  useEffect(() => {
    setInspecting(draftReview.inspecting);
    setInspectionFailure(draftReview.failure);
    setValidationError(
      draftReview.failure ? errorMessage(draftReview.failure) : "",
    );
    if (draftReview.failure) setInspectedKey("");
  }, [draftReview.inspecting, draftReview.failure]);

  useEffect(() => {
    if (draftReview.result?.key !== semanticKey) return;
    setCatalogFailure(undefined);
    setReview(draftReview.result.review);
    setReviewContext(inspectionContextKey);
    setInspectedKey(semanticKey);
    setEnvironmentCatalog({
      key: inspectionContextKey,
      catalog: draftReview.result.review.catalog,
    });
  }, [draftReview.result, semanticKey, inspectionContextKey]);

  const dirty = Boolean(
    snapshot?.document &&
    (JSON.stringify(components) !==
      JSON.stringify(snapshot.document.components ?? {}) ||
      JSON.stringify(disabled) !==
        JSON.stringify(snapshot.document.disabled_associations ?? []) ||
      JSON.stringify(positions) !==
        JSON.stringify(
          snapshot.document.metadata?.stacksmith_composer?.positions ?? {},
        ) ||
      JSON.stringify(routes) !==
        JSON.stringify(
          snapshot.document.metadata?.stacksmith_composer?.routes ?? [],
        )),
  );
  useEffect(() => {
    setPr(undefined);
    setSourceError("");
    setSourceFailure(undefined);
    if (!snapshot?.editable) return;
    if (!dirty) {
      setRendered({ key: sourceKey, source: snapshot.source, diff: "" });
      return;
    }
    const abort = new AbortController();
    const timer = setTimeout(() => {
      api<Pick<Review, "source" | "diff">>(
        "/source",
        JSON.parse(sourceKey),
        abort.signal,
      )
        .then((result) => {
          if (!abort.signal.aborted) setRendered({ ...result, key: sourceKey });
        })
        .catch((failure) => {
          if (!abort.signal.aborted) {
            setSourceError(errorMessage(failure));
            setSourceFailure(failure);
          }
        });
    }, 400);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [snapshot?.editable, sourceKey, sourceAttempt, dirty]);

  useEffect(
    () => registerGuard(confirmStackChange),
    [registerGuard, dirty, savedDraft],
  );

  useEffect(() => {
    if (!snapshot?.editable || recovery || storageError) return;
    setSavedDraft(false);
    function save() {
      try {
        if (dirty)
          localStorage.setItem(
            recoveryKey(snapshot!, session?.login ?? "local-demo"),
            JSON.stringify(recoveryRecord(snapshot!, draftHistory.present)),
          );
        else
          localStorage.removeItem(
            recoveryKey(snapshot!, session?.login ?? "local-demo"),
          );
        setSavedDraft(dirty);
      } catch (failure) {
        setSavedDraft(false);
        setStorageError(
          `Could not save this draft in your browser: ${errorMessage(failure)} Download a recovery file to keep your work.`,
        );
      }
    }
    const timer = setTimeout(save, 250);
    window.addEventListener("pagehide", save);
    window.addEventListener("beforeunload", save);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("pagehide", save);
      window.removeEventListener("beforeunload", save);
    };
  }, [
    snapshot,
    session?.login,
    draftHistory.present,
    dirty,
    recovery,
    storageError,
  ]);

  function downloadRecovery(
    record = snapshot
      ? recoveryRecord(snapshot, draftHistory.present)
      : undefined,
  ) {
    if (record)
      downloadSource(
        JSON.stringify(record, null, 2),
        `${record.base.path}.composer-draft.json`,
      );
  }

  async function importRecovery(file: File) {
    try {
      if (file.size > 10_000_000)
        throw new Error("Recovery files must be smaller than 10 MB.");
      setRecovery(parseRecovery(await file.text()));
    } catch (failure) {
      setError(`Could not import recovery file: ${errorMessage(failure)}`);
    }
  }

  async function toggleRule(
    rule: string,
    enabled: boolean,
    scope: AssociationScope,
  ) {
    if (!canEdit) return;
    const requestScope = confirmationScope.current;
    if (
      scope.kind === "stack" &&
      !(await confirm({
        title: `${enabled ? "Re-enable" : "Disable"} ${rule} across entire stack?`,
        description: (
          <>
            <AssociationImpact
              scope={scope}
              relationships={affectedConnections(
                enabled ? knownConnections : relationships,
                rule,
                scope,
              )}
              stale={!inspectionCurrent || inspecting || enabled}
            />
            {enabled && (
              <p>
                Connections will be recalculated. Component opt-outs remain in
                effect.
              </p>
            )}
          </>
        ),
        actionLabel: enabled
          ? "Re-enable across entire stack"
          : "Disable across entire stack",
        destructive: !enabled,
      }))
    )
      return;
    if (!editingEnabled.current || requestScope !== confirmationScope.current)
      return;
    dispatchDraft({
      type: "change",
      update: (draft) => setRuleEnabled(draft, rule, enabled, scope),
    });
  }
  useEffect(() => {
    function preventLoss(event: BeforeUnloadEvent) {
      if (dirty) event.preventDefault();
    }
    window.addEventListener("beforeunload", preventLoss);
    return () => window.removeEventListener("beforeunload", preventLoss);
  }, [dirty]);

  function addComponent(type: string): string {
    if (!canEdit) return "";
    const name = nextComponentName(type, components);
    dispatchDraft({
      type: "change",
      update: (draft) => ({
        ...draft,
        components: {
          ...draft.components,
          [name]: { type, properties: {} },
        },
        positions: {
          ...draft.positions,
          [name]: newComponentPosition(
            type,
            draft.components,
            draft.positions,
            catalog,
          ),
        },
      }),
    });
    setSelected(name);
    setSelectedRelationship(null);
    setView("compose");
    return name;
  }

  function connect(connection: Connection) {
    if (!canEdit) return;
    if (
      !connection.sourceHandle?.startsWith("out:") ||
      !connection.targetHandle?.startsWith("in:")
    )
      return;
    if (connection.source === connection.target) {
      setError("A component cannot consume its own output.");
      return;
    }
    if (
      !Object.hasOwn(components, connection.source) ||
      !Object.hasOwn(components, connection.target)
    ) {
      setError("Both components must exist.");
      return;
    }
    const property = connection.targetHandle.slice(3);
    const value = components[connection.target].properties?.[property];
    const type = parseTerraformType(
      catalog[components[connection.target].type]?.inputs.find(
        (input) => input.name === property,
      )?.type,
    ).type;
    if (
      (value !== null && typeof value === "object") ||
      type === "array" ||
      type === "object" ||
      value !== undefined
    ) {
      connectionScope.current = sourceKey;
      setPendingConnection(connection);
      return;
    }
    commitConnection(connection, [property]);
  }

  function commitConnection(connection: Connection, path: (string | number)[]) {
    if (
      !canEdit ||
      !Object.hasOwn(components, connection.target) ||
      !Object.hasOwn(components, connection.source)
    )
      return;
    try {
      connectComponents(
        components,
        connection.source,
        connection.sourceHandle!.slice(4),
        connection.target,
        path,
      );
    } catch (failure) {
      setError(errorMessage(failure));
      return;
    }
    dispatchDraft({
      type: "change",
      update: (draft) => ({
        ...draft,
        components: connectComponents(
          draft.components,
          connection.source,
          connection.sourceHandle!.slice(4),
          connection.target,
          path,
        ),
        routes: draft.routes.filter(
          (route) =>
            route.target !== connection.target ||
            !pathStartsWith(route.path, path),
        ),
      }),
    });
    setPendingConnection(undefined);
  }

  function removeComponent(name: string) {
    if (!canEdit) return;
    if (!authoredGraph.current) {
      setError(
        "Authored references are still being checked. Retry removing the component shortly.",
      );
      return;
    }
    if (!canRemove(relationships, name)) {
      setError(
        "Another component uses this output. Remove its authored reference first.",
      );
      return;
    }
    dispatchDraft({
      type: "change",
      update: (draft) => ({
        ...draft,
        components: Object.fromEntries(
          Object.entries(draft.components).filter(([key]) => key !== name),
        ),
        positions: Object.fromEntries(
          Object.entries(draft.positions).filter(([key]) => key !== name),
        ),
        routes: draft.routes.filter(
          (route) => route.source !== name && route.target !== name,
        ),
      }),
    });
    setSelected(null);
  }

  function removeRelationship(key: string) {
    if (!canEdit) return;
    const relationship = relationships.find(
      (edge) => relationshipKey(edge) === key,
    );
    if (!relationship) return;
    if (relationship.kind === "association" && relationship.rule) {
      void toggleRule(relationship.rule, false, {
        kind: "component",
        component: relationship.target,
      });
      return;
    }
    dispatchDraft({
      type: "change",
      update: (draft) => ({
        ...draft,
        components: removeReference(draft.components, relationship),
        routes: routesAfterRemoval(draft.routes, relationship),
      }),
    });
    setSelectedRelationship(null);
  }

  function changePosition(
    name: string,
    position: { x: number; y: number },
    record: boolean,
  ) {
    if (!canEdit) return;
    dispatchDraft({
      type: "change",
      record,
      update: (draft) => ({
        ...draft,
        positions: {
          ...draft.positions,
          [name]: snapPoint(position),
        },
      }),
    });
  }

  function changeRoute(
    relationship: Relationship,
    points: { x: number; y: number }[],
    record: boolean,
  ) {
    if (!canEdit) return;
    const key = relationshipKey(relationship);
    dispatchDraft({
      type: "change",
      record,
      update: (draft) => ({
        ...draft,
        routes: points.length
          ? [
              ...draft.routes.filter((route) => relationshipKey(route) !== key),
              {
                ...relationship,
                ...(draft.routes.find((route) => relationshipKey(route) === key)
                  ?.label_position
                  ? {
                      label_position: draft.routes.find(
                        (route) => relationshipKey(route) === key,
                      )!.label_position,
                    }
                  : {}),
                points: points.map(snapPoint),
              },
            ]
          : draft.routes.filter((route) => relationshipKey(route) !== key),
      }),
    });
  }

  function changeLabelPosition(
    relationship: Relationship,
    position: { x: number; y: number },
    record: boolean,
  ) {
    if (!canEdit) return;
    dispatchDraft({
      type: "change",
      record,
      update: (draft) => ({
        ...draft,
        routes: [
          ...draft.routes.filter(
            (route) => relationshipKey(route) !== relationshipKey(relationship),
          ),
          {
            ...relationship,
            points:
              draft.routes.find(
                (route) =>
                  relationshipKey(route) === relationshipKey(relationship),
              )?.points ?? [],
            label_position: position,
          },
        ],
      }),
    });
  }

  async function createPR() {
    if (
      !snapshot ||
      !rendered?.diff ||
      !sourceCurrent ||
      !inspectionCurrent ||
      inspecting
    )
      return;
    setCreatingPR(true);
    try {
      setPr(
        await api("/pull-requests", {
          snapshot: snapshot.snapshot,
          components,
          disabled_associations: disabled,
          positions,
          routes,
          environment,
        }),
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setCreatingPR(false);
    }
  }

  useEffect(() => {
    if (selected && !Object.hasOwn(components, selected)) setSelected(null);
  }, [components, selected]);

  useEffect(() => {
    if (
      selectedConnection &&
      (!Object.hasOwn(components, selectedConnection.relationship.source) ||
        !Object.hasOwn(components, selectedConnection.relationship.target))
    )
      setConnectionSelection(null);
  }, [components, selectedConnection]);

  useEffect(() => {
    function handleKeyboard(event: KeyboardEvent) {
      if (location.pathname !== "/" || isTextEditingTarget(event.target))
        return;
      const command = keyboardCommand(event);
      if (command && !canEdit) return;
      if (command === "undo" && draftHistory.past.length) {
        event.preventDefault();
        dispatchDraft({ type: "undo" });
      } else if (command === "redo" && draftHistory.future.length) {
        event.preventDefault();
        dispatchDraft({ type: "redo" });
      } else if (command === "delete" && view === "compose") {
        if (selected) {
          event.preventDefault();
          removeComponent(selected);
        } else if (selectedRelationship) {
          event.preventDefault();
          removeRelationship(selectedRelationship);
        }
      } else if (event.key === "Escape") {
        if (selected || selectedRelationship || managedOpen) {
          event.preventDefault();
          setSelected(null);
          setSelectedRelationship(null);
        }
      }
    }
    window.addEventListener("keydown", handleKeyboard);
    return () => window.removeEventListener("keydown", handleKeyboard);
  });

  const filteredCatalog = Object.entries(catalog).filter(([type, definition]) =>
    `${type} ${definition.display_name} ${definition.tags?.join(" ")}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const catalogPageCount = Math.max(
    1,
    Math.ceil(filteredCatalog.length / CATALOG_PAGE_SIZE),
  );
  const activeCatalogPage = Math.min(catalogPage, catalogPageCount - 1);
  const visibleCatalog = filteredCatalog.slice(
    activeCatalogPage * CATALOG_PAGE_SIZE,
    (activeCatalogPage + 1) * CATALOG_PAGE_SIZE,
  );
  return (
    <div className="application">
      <nav className="main-nav" aria-label="Workspace">
        <button
          className={
            view === "compose" || view === "review" || view === "resolution"
              ? "active"
              : ""
          }
          onClick={() => setView("compose")}
        >
          <Blocks size={17} />
          Diagram
        </button>
        <button
          className={view === "catalog" ? "active" : ""}
          onClick={() => setView("catalog")}
        >
          <BookOpen size={17} />
          Component catalog
          <span className="count">{Object.keys(catalog).length}</span>
        </button>
      </nav>
      {error && (
        <ErrorBanner
          message={error}
          signIn={session?.mode === "github"}
          onDismiss={() => setError("")}
        />
      )}
      {loading || openingStack ? (
        <main className="empty-state">
          <LoaderCircle className="spin" size={28} />
          <h2>{openingStack ? "Opening stack…" : "Loading platform…"}</h2>
        </main>
      ) : (
        <>
          {(view === "compose" ||
            view === "review" ||
            view === "resolution") && (
            <section className="stack-toolbar">
              <div>
                <div className="breadcrumb">
                  <FolderGit2 size={14} />
                  <span>
                    {snapshot?.repository ??
                      (session.mode === "demo"
                        ? "Example workspace"
                        : "No repository selected")}
                  </span>
                  {session.mode !== "demo" && snapshot && (
                    <a href="/settings?section=workspace">Change workspace</a>
                  )}
                  <span>/</span>
                  <span>{snapshot?.path ?? "No stack selected"}</span>
                  {hasDeploymentContext && (
                    <span> / {environment ?? "Stack preview"}</span>
                  )}
                </div>
                {targetError && (
                  <p role="alert">
                    {targetError} Stack preview remains available.
                  </p>
                )}
                <div className="stack-title">
                  <h1>{snapshot?.document?.name ?? "Stack composer"}</h1>
                  <span className="branch-badge">
                    <GitBranch size={12} />
                    {snapshot?.branch ?? "—"}
                  </span>
                  {dirty && (
                    <span className="unsaved">
                      {savedDraft
                        ? "Draft saved in this browser"
                        : "Unsaved draft"}
                    </span>
                  )}
                </div>
              </div>
              <div className="toolbar-actions">
                {editable && (
                  <button
                    className="button"
                    aria-pressed={viewOnly}
                    disabled={snapshot?.can_author === false}
                    title={
                      viewOnly
                        ? "Switch to editing"
                        : "Pan and inspect without changing the stack"
                    }
                    onClick={() => navigate({ ...route, viewOnly: !viewOnly })}
                  >
                    {viewOnly ? <Eye size={15} /> : <Pencil size={15} />}
                    {viewOnly ? "View only" : "Editing"}
                  </button>
                )}
                {snapshot?.editable && snapshot.can_author !== false && (
                  <ActionMenu label="Draft">
                    <label className="recovery-import">
                      Import recovery file
                      <input
                        type="file"
                        accept=".json"
                        aria-label="Import recovery file"
                        disabled={!canEdit || dirty || Boolean(recovery)}
                        onChange={(event) => {
                          if (event.target.files?.[0])
                            void importRecovery(event.target.files[0]);
                          event.target.value = "";
                        }}
                      />
                    </label>
                    {dirty && (
                      <button onClick={() => downloadRecovery()}>
                        Download current recovery file
                      </button>
                    )}
                    <p>Recovery files are backups, not deployable stacks.</p>
                  </ActionMenu>
                )}
                {view !== "resolution" && (
                  <button
                    className="button"
                    disabled={!snapshot || !editable}
                    onClick={() => setView("resolution")}
                  >
                    <Search size={15} />
                    Resolution
                  </button>
                )}
                {view === "review" || view === "resolution" ? (
                  <button className="button" onClick={() => setView("compose")}>
                    <ArrowLeft size={15} />
                    Back to canvas
                  </button>
                ) : (
                  <button
                    className="button primary"
                    disabled={!snapshot || !editable || !dirty}
                    onClick={() => setView("review")}
                  >
                    Review changes
                    <ArrowRight size={16} />
                  </button>
                )}
              </div>
            </section>
          )}
          {storageError && (
            <ErrorBanner
              message={storageError}
              signIn={false}
              onDismiss={() => setStorageError("")}
            />
          )}
          {recovery && snapshot && (
            <section
              className="notice recovery-notice"
              role="region"
              aria-label="Saved draft recovery"
            >
              <div className="recovery-message">
                <strong>Saved draft available</strong>
                <span className="recovery-timestamp">
                  {new Date(recovery.savedAt).toLocaleString()}
                </span>
                <p>
                  {canRestore(recovery, snapshot)
                    ? "Restore to resume your work. New edits won't be saved until you restore or discard this draft."
                    : "The base revision or source has changed. Download the saved recovery file before starting a new draft; automatic restoration is unavailable."}
                </p>
              </div>
              <div className="recovery-actions">
                <button
                  className="button small"
                  aria-label="Restore saved draft"
                  disabled={!canEdit || !canRestore(recovery, snapshot)}
                  onClick={async () => {
                    const scope = confirmationScope.current;
                    if (
                      dirty &&
                      !(await confirm({
                        title: "Restore the saved draft?",
                        description:
                          "This replaces your current edits with the saved draft. You can undo the restoration.",
                        actionLabel: "Restore saved draft",
                        destructive: true,
                      }))
                    )
                      return;
                    if (
                      !editingEnabled.current ||
                      scope !== confirmationScope.current
                    )
                      return;
                    dispatchDraft({
                      type: "change",
                      update: () => recovery.draft,
                    });
                    setRecovery(undefined);
                  }}
                >
                  Restore
                </button>
                <ActionMenu label="Saved draft options">
                  <button onClick={() => downloadRecovery(recovery)}>
                    Download saved recovery file
                  </button>
                  <button
                    className="danger"
                    onClick={async () => {
                      const scope = confirmationScope.current;
                      if (
                        !(await confirm({
                          title: "Discard the saved draft?",
                          description:
                            "This removes the saved browser draft. Download its recovery file first if you want to keep it. Your open stack remains available.",
                          actionLabel: "Discard saved draft",
                          destructive: true,
                        }))
                      )
                        return;
                      if (scope !== confirmationScope.current) return;
                      try {
                        localStorage.removeItem(
                          recoveryKey(snapshot, session?.login ?? "local-demo"),
                        );
                        setRecovery(undefined);
                      } catch (failure) {
                        setStorageError(errorMessage(failure));
                      }
                    }}
                  >
                    Discard saved draft
                  </button>
                  <p>
                    Recovery files include the base source and draft data, not a
                    deployable stack.
                  </p>
                </ActionMenu>
              </div>
            </section>
          )}
          {view === "compose" && (
            <>
              {session?.mode === "demo" && (
                <div className="demo-notice">
                  <span className="status-dot" />
                  <span>
                    Try the composer with private, versioned example modules.
                    Drafts are saved in this browser. Download a stack or
                    recovery file to keep a separate copy.
                  </span>
                </div>
              )}
              {!snapshot ? (
                <main className="empty-state">
                  <FolderGit2 size={36} />
                  <h2>Choose a stack to start composing.</h2>
                  <p>Select a repository and stack in Workspace settings.</p>
                  <button
                    className="button primary"
                    onClick={() =>
                      applicationNavigate("/settings?section=workspace")
                    }
                  >
                    Choose workspace in Settings
                  </button>
                </main>
              ) : !editable ? (
                <main className="source-view">
                  <div className="notice">{snapshot.reason}</div>
                  <pre>{snapshot.source}</pre>
                </main>
              ) : (
                <div
                  className={`composer-layout ${selected || selectedConnection || managedOpen ? "with-inspector" : ""}`}
                >
                  <aside className="palette">
                    <div className="panel-title">
                      <span>{viewOnly ? "Components" : "Add a component"}</span>
                      <span className="count">
                        {Object.keys(catalog).length}
                      </span>
                    </div>
                    <div className="search-field">
                      <Search size={15} />
                      <input
                        placeholder="Search components…"
                        aria-label="Search components"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                      />
                      {search && (
                        <button
                          className="icon-button clear-search"
                          aria-label="Clear search"
                          onClick={() => setSearch("")}
                        >
                          <X size={14} />
                        </button>
                      )}
                    </div>
                    {canEdit && (
                      <p className="palette-hint">
                        Drag onto the canvas or click +
                      </p>
                    )}
                    <div className="palette-list">
                      {filteredCatalog.map(([type, definition]) => (
                        <div
                          className="palette-item"
                          key={type}
                          draggable={canEdit}
                          onDragStart={(event) =>
                            event.dataTransfer.setData(
                              "application/stacksmith-component",
                              type,
                            )
                          }
                        >
                          <span
                            className={`type-icon tone-${definition.tags?.[0] ?? "default"}`}
                          >
                            <TypeIcon type={type} />
                          </span>
                          <div>
                            <strong>{definition.display_name}</strong>
                            <small>{type}</small>
                          </div>
                          <button
                            className="icon-button"
                            aria-label={`Add ${definition.display_name}`}
                            disabled={!canEdit}
                            onClick={() => addComponent(type)}
                          >
                            <Plus size={17} />
                          </button>
                        </div>
                      ))}
                      {!filteredCatalog.length &&
                        (inspecting && !Object.keys(catalog).length ? (
                          <p className="palette-loading" role="status">
                            <LoaderCircle
                              size={16}
                              className="spin"
                              aria-hidden="true"
                            />
                            Loading component types…
                          </p>
                        ) : (
                          <p className="muted">No matching components.</p>
                        ))}
                    </div>
                  </aside>
                  <div
                    className="diagram-column"
                    tabIndex={-1}
                    data-dialog-focus-fallback
                  >
                    {pendingConnection &&
                      canEdit &&
                      components[pendingConnection.source] &&
                      components[pendingConnection.target] && (
                        <ConnectionDestination
                          key={JSON.stringify([
                            sourceKey,
                            environment,
                            pendingConnection,
                          ])}
                          source={`${pendingConnection.source}.${pendingConnection.sourceHandle!.slice(4)}`}
                          target={pendingConnection.target}
                          property={pendingConnection.targetHandle!.slice(3)}
                          value={
                            components[pendingConnection.target].properties?.[
                              pendingConnection.targetHandle!.slice(3)
                            ]
                          }
                          type={
                            catalog[
                              components[pendingConnection.target].type
                            ]?.inputs.find(
                              (input) =>
                                input.name ===
                                pendingConnection.targetHandle!.slice(3),
                            )?.type
                          }
                          onChoose={(path) =>
                            commitConnection(pendingConnection, path)
                          }
                          onCancel={() => setPendingConnection(undefined)}
                        />
                      )}
                    <div className="managed-connections-toolbar">
                      <button
                        className="button small"
                        aria-expanded={managedOpen}
                        onClick={openManagedConnections}
                      >
                        <ShieldCheck size={15} />
                        Managed connections
                        <span
                          className="count"
                          aria-label={`${disabledScopes.length} disabled scopes`}
                        >
                          {disabledScopes.length} disabled
                        </span>
                      </button>
                    </div>
                    <Canvas
                      readOnly={!canEdit}
                      positions={positions}
                      routes={routes}
                      onLabelPositionChange={changeLabelPosition}
                      onPositionChange={changePosition}
                      onRouteChange={changeRoute}
                      key={snapshot.snapshot}
                      components={components}
                      catalog={catalog}
                      relationships={relationships}
                      selected={selected}
                      selectedRelationship={
                        relationships.some(
                          (edge) =>
                            relationshipKey(edge) === selectedRelationship,
                        )
                          ? selectedRelationship
                          : null
                      }
                      onSelect={selectComponent}
                      onSelectRelationship={setSelectedRelationship}
                      onConnect={connect}
                      onAdd={addComponent}
                      inspecting={inspecting}
                      hasInspectedAssociations={
                        reviewContext === inspectionContextKey &&
                        Boolean(review)
                      }
                      inspectionLabel={
                        inspecting
                          ? "Inspecting managed connections…"
                          : validationError
                            ? "Managed connections incomplete"
                            : inspectionCurrent
                              ? environment
                                ? `Deployment ${environment} · Inspected`
                                : "Stack preview · Inspected"
                              : "Managed connections pending"
                      }
                      associationsStale={!inspectionCurrent || inspecting}
                      canUndo={canEdit && Boolean(draftHistory.past.length)}
                      canRedo={canEdit && Boolean(draftHistory.future.length)}
                      onUndo={() => dispatchDraft({ type: "undo" })}
                      onRedo={() => dispatchDraft({ type: "redo" })}
                    />
                    <div
                      className={`validation-bar ${validationError ? "has-error" : ""}`}
                      role="status"
                    >
                      {inspecting ? (
                        <LoaderCircle size={15} className="spin" />
                      ) : validationError ? (
                        <CircleAlert size={15} />
                      ) : (
                        <Check size={15} />
                      )}
                      <span>{managedConnectionStatus}</span>
                      {validationError && (
                        <button
                          className="text-button"
                          onClick={() =>
                            setInspectionAttempt((attempt) => attempt + 1)
                          }
                        >
                          Retry inspection
                        </button>
                      )}
                    </div>
                    <InspectionDetails error={inspectionFailure} />
                    {catalogFailure !== undefined && (
                      <div role="alert">
                        {errorMessage(catalogFailure)}
                        <InspectionDetails error={catalogFailure} />
                      </div>
                    )}
                    {authoredGraph.error && (
                      <p role="alert">
                        {authoredGraph.error}{" "}
                        <button
                          className="text-button"
                          onClick={authoredGraph.retry}
                        >
                          Retry reference check
                        </button>
                      </p>
                    )}
                    {authoredGraph.diagnostics.length > 0 && (
                      <details className="inspection-details">
                        <summary>Authored reference problems</summary>
                        {authoredGraph.diagnostics.map((problem, index) => (
                          <p key={index}>
                            <code>{JSON.stringify(problem.path)}</code>:{" "}
                            {problem.message}
                          </p>
                        ))}
                      </details>
                    )}
                  </div>
                  {selectedConnection && (
                    <ConnectionInspector
                      relationship={selectedConnection.relationship}
                      description={
                        managedRules.find(
                          (rule) =>
                            rule.name === selectedConnection.relationship.rule,
                        )?.description
                      }
                      knownConnections={knownConnections}
                      componentDisabled={Boolean(
                        components[
                          selectedConnection.relationship.target
                        ]?.disabled_associations?.includes(
                          selectedConnection.relationship.rule ?? "",
                        ),
                      )}
                      stackDisabled={disabled.includes(
                        selectedConnection.relationship.rule ?? "",
                      )}
                      readOnly={!canEdit}
                      stale={!inspectionCurrent || inspecting}
                      error={validationError}
                      onToggle={toggleRule}
                      onRemoveReference={() =>
                        removeRelationship(
                          relationshipKey(selectedConnection.relationship),
                        )
                      }
                      onManage={openManagedConnections}
                      onRetry={() =>
                        setInspectionAttempt((attempt) => attempt + 1)
                      }
                      onClose={() => setSelectedRelationship(null)}
                    />
                  )}
                  {managedOpen && (
                    <ManagedConnections
                      rules={managedRules}
                      disabled={disabledScopes}
                      knownConnections={knownConnections}
                      stale={!inspectionCurrent || inspecting}
                      readOnly={!canEdit}
                      error={validationError}
                      onToggle={toggleRule}
                      onRetry={() =>
                        setInspectionAttempt((attempt) => attempt + 1)
                      }
                      onClose={() => setManagedOpen(false)}
                    />
                  )}
                  {selected && components[selected] && (
                    <Inspector
                      readOnly={!canEdit}
                      key={selected}
                      name={selected}
                      component={components[selected]}
                      definition={catalog[components[selected].type]}
                      relationships={relationships}
                      onChange={(component) =>
                        canEdit &&
                        dispatchDraft({
                          type: "change",
                          update: (draft) => ({
                            ...draft,
                            routes: draft.routes.filter(
                              (route) =>
                                route.target !== selected ||
                                route.kind !== "reference" ||
                                valueAtPath(
                                  draft.components[selected].properties,
                                  route.path,
                                ) ===
                                  valueAtPath(component.properties, route.path),
                            ),
                            components: {
                              ...draft.components,
                              [selected]: component,
                            },
                          }),
                        })
                      }
                      onRemove={() => removeComponent(selected)}
                      onClose={() => setSelected(null)}
                    />
                  )}
                </div>
              )}
            </>
          )}
          {view === "catalog" && (
            <main className="information-view">
              <h1>Component catalog</h1>
              <div className="search-field wide">
                <Search size={17} />
                <input
                  placeholder="Search by name, type, or tag…"
                  aria-label="Search catalog"
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setCatalogPage(0);
                  }}
                />
                {search && (
                  <button
                    className="icon-button clear-search"
                    aria-label="Clear search"
                    onClick={() => {
                      setSearch("");
                      setCatalogPage(0);
                    }}
                  >
                    <X size={15} />
                  </button>
                )}
              </div>
              <div className="catalog-grid">
                {visibleCatalog.map(([type, definition]) => (
                  <article className="catalog-card" key={type}>
                    <div className="catalog-card-heading">
                      <span
                        className={`type-icon large tone-${definition.tags?.[0] ?? "default"}`}
                      >
                        <TypeIcon type={type} size={23} />
                      </span>
                      <span className="badge">
                        {definition.tags?.[0] ?? "component"}
                      </span>
                    </div>
                    <h2>{definition.display_name}</h2>
                    <code>{type}</code>
                    <div className="catalog-ports">
                      <h3>Inputs</h3>
                      {definition.inputs.map((port) => (
                        <p key={port.name}>
                          <code>{port.name}</code>
                          <span>
                            {port.description ?? "No description provided."}
                          </span>
                        </p>
                      ))}
                      <h3>Outputs</h3>
                      {definition.outputs?.map((port) => (
                        <p key={port.name}>
                          <code>{port.name}</code>
                          <span>
                            {port.description ?? "No description provided."}
                          </span>
                        </p>
                      ))}
                    </div>
                    <div className="catalog-source">
                      <span>
                        Module ·{" "}
                        {definition.module_path ?? definition.module_version}
                      </span>
                      {!definition.module_path && (
                        <>
                          <code title={definition.module_source}>
                            {definition.module_source}
                          </code>
                          {definition.module_source_url && (
                            <a
                              className="catalog-source-link"
                              href={definition.module_source_url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              View source <ExternalLink size={12} />
                            </a>
                          )}
                        </>
                      )}
                    </div>
                    <button
                      className="button"
                      disabled={!canEdit}
                      onClick={() => addComponent(type)}
                    >
                      <Plus size={15} />
                      Add to stack
                    </button>
                  </article>
                ))}
              </div>
              {!filteredCatalog.length && (
                <p className="catalog-empty">No matching components.</p>
              )}
              {catalogPageCount > 1 && (
                <nav className="catalog-pagination" aria-label="Catalog pages">
                  <span>
                    Showing {activeCatalogPage * CATALOG_PAGE_SIZE + 1}–
                    {Math.min(
                      (activeCatalogPage + 1) * CATALOG_PAGE_SIZE,
                      filteredCatalog.length,
                    )}{" "}
                    of {filteredCatalog.length}
                  </span>
                  <div>
                    <button
                      className="button"
                      disabled={activeCatalogPage === 0}
                      onClick={() => setCatalogPage(activeCatalogPage - 1)}
                    >
                      Previous
                    </button>
                    <span aria-live="polite">
                      Page {activeCatalogPage + 1} of {catalogPageCount}
                    </span>
                    <button
                      className="button"
                      disabled={activeCatalogPage + 1 === catalogPageCount}
                      onClick={() => setCatalogPage(activeCatalogPage + 1)}
                    >
                      Next
                    </button>
                  </div>
                </nav>
              )}
            </main>
          )}
          {view === "resolution" && snapshot && (
            <ResolutionView
              key={snapshot.snapshot}
              snapshot={snapshot}
              draft={draftHistory.present}
              selected={selected}
              environment={environment}
            />
          )}
          {view === "review" && (
            <main className="review-view">
              <section className="review-main">
                <h2>Review changes</h2>
                <p className="muted">
                  Authored stack changes and diagram layout are included.
                  Resolved association values stay out of the file.
                </p>
                {(validationError || sourceError) && (
                  <div className="notice error-text">
                    {validationError && (
                      <p>
                        {validationError}{" "}
                        <button
                          className="text-button"
                          onClick={() =>
                            setInspectionAttempt((attempt) => attempt + 1)
                          }
                        >
                          Retry inspection
                        </button>
                      </p>
                    )}
                    {sourceError && (
                      <p>
                        {sourceError}{" "}
                        <button
                          className="text-button"
                          onClick={() =>
                            setSourceAttempt((attempt) => attempt + 1)
                          }
                        >
                          Retry source rendering
                        </button>
                      </p>
                    )}
                  </div>
                )}
                <div className="diff-heading">
                  <FileCode2 size={16} />
                  <span>{snapshot?.path}</span>
                  <span>
                    {!sourceCurrent
                      ? sourceError
                        ? "Source unavailable"
                        : "Preparing source…"
                      : rendered?.diff
                        ? "Modified"
                        : "No changes"}
                  </span>
                </div>
                <pre className="diff">
                  {sourceCurrent && rendered?.diff
                    ? rendered.diff.split("\n").map((line, index) => (
                        <span
                          className={
                            line.startsWith("+")
                              ? "diff-add"
                              : line.startsWith("-")
                                ? "diff-remove"
                                : line.startsWith("@@")
                                  ? "diff-meta"
                                  : ""
                          }
                          key={index}
                        >
                          {line || " "}
                          <br />
                        </span>
                      ))
                    : !sourceCurrent
                      ? sourceError || "Preparing the authored source…"
                      : "Edit a component or connect an output to create a change."}
                </pre>
              </section>
              <aside className="review-aside">
                <ManagedConnectionSummary
                  disabled={disabledScopes}
                  onManage={openManagedConnections}
                />
                <div className="review-checks">
                  {inspecting ? (
                    <LoaderCircle className="spin" size={17} />
                  ) : inspectionCurrent ? (
                    <Check size={17} />
                  ) : (
                    <CircleAlert size={17} />
                  )}
                  <p>{managedConnectionStatus}</p>
                </div>
                <InspectionDetails error={inspectionFailure} />
                <InspectionDetails error={sourceFailure} />
                {inspectionCurrent && (
                  <InspectionSummary context={review?.context} />
                )}
                {pr ? (
                  <a
                    className="button primary"
                    href={pr.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <Github size={16} />
                    Open pull request #{pr.number}
                  </a>
                ) : snapshot?.can_author === false ? (
                  <p className="notice">
                    Your stack role provides read-only Composer access.
                  </p>
                ) : snapshot?.repository ? (
                  <button
                    className="button primary"
                    disabled={
                      !rendered?.diff ||
                      !sourceCurrent ||
                      !inspectionCurrent ||
                      creatingPR ||
                      inspecting
                    }
                    onClick={() => void createPR()}
                  >
                    {creatingPR ? (
                      <>
                        <LoaderCircle size={16} className="spin" />
                        Proposing changes…
                      </>
                    ) : (
                      <>
                        <Github size={16} />
                        Create pull request
                      </>
                    )}
                  </button>
                ) : (
                  <div className="notice">
                    GitHub PR creation is available when a GitHub App is
                    configured and a repository stack is open.
                  </div>
                )}
                {pr && (
                  <a className="button" href={pr.patch_url} download>
                    <ArrowDownToLine size={16} />
                    Download PR patch
                  </a>
                )}
                <button
                  className="button"
                  disabled={!sourceCurrent}
                  onClick={() =>
                    rendered &&
                    sourceCurrent &&
                    snapshot &&
                    downloadSource(rendered.source, snapshot.path)
                  }
                >
                  <ArrowDownToLine size={16} />
                  {inspectionCurrent && !inspecting
                    ? "Download stack"
                    : "Download unvalidated stack"}
                </button>
                <p className="field-help">
                  A PR proposes this file change. CI owns full validation,
                  planning, and deployment.
                </p>
              </aside>
            </main>
          )}
        </>
      )}
    </div>
  );
}
