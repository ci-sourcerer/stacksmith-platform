export interface ComponentDefinition {
  type: string;
  description?: string;
  tags?: string[];
  disabled_associations?: string[];
  properties?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface Port {
  name: string;
  description?: string;
  type?: string;
}
export interface ComponentType {
  display_name: string;
  module_source: string;
  module_source_url?: string;
  module_path?: string;
  module_version: string;
  tags?: string[];
  inputs: Port[];
  outputs?: Port[];
}
export type Catalog = Record<string, ComponentType>;
export type Components = Record<string, ComponentDefinition>;
export interface StackDocument {
  name: string;
  description?: string;
  components?: Components;
  disabled_associations?: string[];
  depends_on?: string[];
  metadata?: {
    stacksmith_composer?: {
      positions?: Record<string, { x: number; y: number }>;
      routes?: DiagramRoute[];
    };
    [key: string]: unknown;
  };
}
export interface Snapshot {
  snapshot: string;
  source: string;
  path: string;
  repository: string | null;
  branch: string;
  head: string | null;
  editable: boolean;
  can_author?: boolean;
  reason: string | null;
  document: StackDocument | null;
}
export interface Relationship {
  source: string;
  target: string;
  output: string;
  property: string;
  path: (string | number)[];
  kind: "reference" | "association";
  rule: string | null;
}
export interface DiagramPoint {
  x: number;
  y: number;
}
export interface DiagramRoute extends Relationship {
  points: DiagramPoint[];
  label_position?: DiagramPoint;
}
export interface AssociationRule {
  name: string;
  description?: string;
  status: string;
  reason: string | null;
}
export interface Review {
  catalog: Catalog;
  source: string;
  diff: string;
  edges: Relationship[];
  validation: string;
  associations: { rules: AssociationRule[] };
  context?: InspectionContext | null;
}
interface InspectionIdentity {
  repository: string;
  revision: string;
  path: string;
  config_revision: string;
}
export type InspectionContext = InspectionIdentity &
  (
    | { mode: "stack_preview"; environment: null }
    | { mode: "deployment"; environment: string; environment_revision: number }
  );
export interface ResolutionEvent {
  path: string;
  action: string;
  source: string;
  value: unknown;
  [key: string]: unknown;
}
export interface Resolution {
  context?: InspectionContext | null;
  effective?: Record<string, unknown>;
  query?: string;
  value?: unknown;
  events: ResolutionEvent[];
}
export interface SourceDetails {
  kind: string;
  label: string;
  detail: string | null;
  url: string | null;
  configuration?: unknown;
  content?: string | null;
}
export interface PlatformPort {
  name: string;
  description: string;
}
export interface Platform {
  layers?: { reference: string; summary: Platform }[];
  description: string;
  source: string;
  scope: string;
  backend: {
    kind: string;
    type: string | null;
    path?: string;
    source: SourceDetails;
  };
  tools: Record<string, { version?: string; source: SourceDetails }>;
  providers: {
    name: string;
    address: string;
    version: string | null;
    source: SourceDetails;
    instances: {
      name: string;
      description: string;
      source: SourceDetails;
    }[];
  }[];
  components: {
    name: string;
    description: string;
    tags: string[];
    source: SourceDetails;
    inputs: PlatformPort[];
    outputs: PlatformPort[];
  }[];
  policies: {
    name: string;
    description: string;
    kind: string;
    source: SourceDetails;
  }[];
  associations: {
    name: string;
    description: string;
    source: SourceDetails;
  }[];
  required_input_sets: string[];
  has_default_mapping: boolean;
}
export interface Repository {
  name: string;
  account?: string;
  github_id?: string;
  installation_id?: number;
  management_url?: string | null;
  description?: string;
  writable: boolean;
}
export interface Session {
  mode: "demo" | "github";
  login: string | null;
  execution_enabled?: boolean;
  inspection_controller_enabled?: boolean;
}

export interface RunSummary {
  id: string;
  repository: string;
  environment: string;
  revision: string;
  action: string;
  actor: string;
  status: string;
  phase: number;
  created: number;
  updated: number;
  expires: number | null;
}
