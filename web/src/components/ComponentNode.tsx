import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { Box, Database, Globe, Network, ShieldCheck, Zap } from "lucide-react";
import type { ComponentType } from "../types";

export type ComposerNode = Node<
  { name: string; type: string; definition: ComponentType },
  "component"
>;

export function TypeIcon({ type, size = 18 }: { type: string; size?: number }) {
  if (/lambda/.test(type)) return <Zap size={size} />;
  if (/iam|policy|role/.test(type)) return <ShieldCheck size={size} />;
  if (/database|postgres|rds/.test(type)) return <Database size={size} />;
  if (/network|vpc/.test(type)) return <Network size={size} />;
  if (/service|app/.test(type)) return <Globe size={size} />;
  return <Box size={size} />;
}

export function ComponentNode({ data, selected }: NodeProps<ComposerNode>) {
  return (
    <div className={`component-node ${selected ? "is-selected" : ""}`}>
      <div className="node-heading">
        <span
          className={`type-icon tone-${data.definition.tags?.[0] ?? "default"}`}
        >
          <TypeIcon type={data.type} />
        </span>
        <div>
          <strong>{data.name}</strong>
          <small>{data.definition.display_name}</small>
        </div>
      </div>
      <div className="node-ports">
        {data.definition.inputs.map((input) => (
          <div
            className="port input-port"
            key={`in:${input.name}`}
            title={input.description}
          >
            <Handle
              type="target"
              position={Position.Left}
              id={`in:${input.name}`}
            />
            <span>{input.name}</span>
            <small>in</small>
          </div>
        ))}
        {(data.definition.outputs ?? []).map((output) => (
          <div
            className="port output-port"
            key={`out:${output.name}`}
            title={output.description}
          >
            <small>out</small>
            <span>{output.name}</span>
            <Handle
              type="source"
              position={Position.Right}
              id={`out:${output.name}`}
            />
          </div>
        ))}
      </div>
      <div className="node-footer">
        <span className="status-dot" />
        {data.definition.module_version === "local"
          ? "Local module"
          : data.definition.module_version}
      </div>
    </div>
  );
}
