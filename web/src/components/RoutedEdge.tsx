import {
  BaseEdge,
  EdgeLabelRenderer,
  useNodes,
  useReactFlow,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import { useEffect, useState } from "react";
import type { DiagramPoint, Relationship } from "../types";
import {
  automaticRoute,
  routeLabelPosition,
  insertRoutePoint,
  orthogonalRoute,
  projectToRoute,
  roundedRoutePath,
} from "../routing";
import { snapPoint } from "../layout";

export interface RoutedEdgeData extends Record<string, unknown> {
  readOnly: boolean;
  relationship: Relationship;
  points: DiagramPoint[];
  onChange: (points: DiagramPoint[], record: boolean) => void;
  labelPosition?: DiagramPoint;
  labelHidden: boolean;
  onLabelChange: (point: DiagramPoint, record: boolean) => void;
}

export type RoutedEdgeDefinition = Edge<RoutedEdgeData, "route">;

export function RoutedEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
  selected,
  style,
  label,
  markerStart,
  markerEnd,
  interactionWidth,
}: EdgeProps<RoutedEdgeDefinition>) {
  const { screenToFlowPosition } = useReactFlow();
  const nodes = useNodes();
  const [selectedPoint, setSelectedPoint] = useState<number | null>(null);
  const points = data?.points ?? [];
  const source = { x: sourceX, y: sourceY };
  const target = { x: targetX, y: targetY };
  const completePath = points.length
    ? orthogonalRoute(source, points, target)
    : automaticRoute(
        source,
        target,
        nodes.map((node) => ({
          ...node.position,
          width: node.measured?.width ?? 232,
          height: node.measured?.height ?? 120,
        })),
      );
  const edgePath = roundedRoutePath(completePath);
  const projectedLabel = projectToRoute(
    data?.labelPosition ?? routeLabelPosition(completePath),
    completePath,
  );
  const labelPosition = projectedLabel.point;

  function beginLabelDrag(event: React.PointerEvent<HTMLDivElement>) {
    if (data?.readOnly) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.focus();
    let firstMove = true;
    const move = (pointerEvent: PointerEvent) => {
      const current = screenToFlowPosition({
        x: pointerEvent.clientX,
        y: pointerEvent.clientY,
      });
      data?.onLabelChange(
        projectToRoute(current, completePath).point,
        firstMove,
      );
      firstMove = false;
    };
    const finish = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish, { once: true });
    window.addEventListener("pointercancel", finish, { once: true });
  }
  useEffect(() => {
    if (!selected || (selectedPoint !== null && selectedPoint >= points.length))
      setSelectedPoint(null);
  }, [points.length, selected, selectedPoint]);

  function beginDrag(
    event: React.PointerEvent<SVGCircleElement>,
    index: number,
  ) {
    if (data?.readOnly) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.focus();
    setSelectedPoint(index);
    let firstMove = true;
    const move = (pointerEvent: PointerEvent) => {
      data?.onChange(
        points.map((point, pointIndex) =>
          pointIndex === index
            ? snapPoint(
                screenToFlowPosition({
                  x: pointerEvent.clientX,
                  y: pointerEvent.clientY,
                }),
              )
            : point,
        ),
        firstMove,
      );
      firstMove = false;
    };
    const finish = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish, { once: true });
    window.addEventListener("pointercancel", finish, { once: true });
  }

  return (
    <g
      onDoubleClick={(event) => {
        if (data?.readOnly) return;
        event.preventDefault();
        event.stopPropagation();
        data?.onChange(
          insertRoutePoint(
            source,
            points.length ? points : completePath.slice(1, -1),
            target,
            snapPoint(
              screenToFlowPosition({ x: event.clientX, y: event.clientY }),
            ),
          ),
          true,
        );
      }}
    >
      <BaseEdge
        id={id}
        path={edgePath}
        style={style}
        markerStart={markerStart}
        markerEnd={markerEnd}
        interactionWidth={interactionWidth}
      />
      {!data?.labelHidden && label && (
        <EdgeLabelRenderer>
          <div
            className={
              data?.readOnly ? "edge-label" : "edge-label nodrag nopan"
            }
            style={{
              transform: `translate(-50%, -50%) translate(${labelPosition.x}px, ${labelPosition.y}px) rotate(${projectedLabel.angle}deg)`,
            }}
            role={data?.readOnly ? undefined : "button"}
            tabIndex={data?.readOnly ? undefined : 0}
            aria-label={data?.readOnly ? undefined : `Move label: ${label}`}
            data-keyboard-owner={!data?.readOnly || undefined}
            onPointerDown={beginLabelDrag}
            onDoubleClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
              if (data?.readOnly) return;
              if (
                !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(
                  event.key,
                )
              )
                return;
              event.preventDefault();
              event.stopPropagation();
              const candidate = {
                x:
                  labelPosition.x +
                  (event.key === "ArrowLeft"
                    ? -22
                    : event.key === "ArrowRight"
                      ? 22
                      : 0),
                y:
                  labelPosition.y +
                  (event.key === "ArrowUp"
                    ? -22
                    : event.key === "ArrowDown"
                      ? 22
                      : 0),
              };
              data?.onLabelChange(
                projectToRoute(candidate, completePath).point,
                true,
              );
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
      {!data?.readOnly &&
        selected &&
        points.map((point, index) => (
          <circle
            className={`route-point nodrag nopan ${
              selectedPoint === index ? "is-selected" : ""
            }`}
            cx={point.x}
            cy={point.y}
            r={7}
            key={index}
            role="button"
            aria-label={`Route point ${index + 1}`}
            aria-pressed={selectedPoint === index}
            data-keyboard-owner="true"
            tabIndex={0}
            onPointerDown={(event) => beginDrag(event, index)}
            onClick={(event) => {
              event.stopPropagation();
              event.currentTarget.focus();
              setSelectedPoint(index);
            }}
            onFocus={() => setSelectedPoint(index)}
            onDoubleClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              setSelectedPoint(null);
              data?.onChange(
                points.filter((_, pointIndex) => pointIndex !== index),
                true,
              );
            }}
            onKeyDown={(event) => {
              if (event.key !== "Backspace" && event.key !== "Delete") return;
              event.preventDefault();
              event.stopPropagation();
              setSelectedPoint(null);
              data?.onChange(
                points.filter((_, pointIndex) => pointIndex !== index),
                true,
              );
            }}
          />
        ))}
    </g>
  );
}
