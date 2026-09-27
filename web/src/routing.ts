import type { DiagramPoint } from "./types";

export interface RouteObstacle extends DiagramPoint {
  width: number;
  height: number;
}

function crossesObstacle(
  start: DiagramPoint,
  end: DiagramPoint,
  obstacles: RouteObstacle[],
): boolean {
  return obstacles.some((box) =>
    start.y === end.y
      ? start.y > box.y &&
        start.y < box.y + box.height &&
        Math.max(start.x, end.x) > box.x &&
        Math.min(start.x, end.x) < box.x + box.width
      : start.x > box.x &&
        start.x < box.x + box.width &&
        Math.max(start.y, end.y) > box.y &&
        Math.min(start.y, end.y) < box.y + box.height,
  );
}

export function automaticRoute(
  source: DiagramPoint,
  target: DiagramPoint,
  obstacles: RouteObstacle[],
): DiagramPoint[] {
  const start = { x: source.x + 22, y: source.y };
  const end = { x: target.x - 22, y: target.y };
  const boxes = obstacles.map((box) => ({
    x: box.x - 16,
    y: box.y - 16,
    width: box.width + 32,
    height: box.height + 32,
  }));
  const xs = [
    ...new Set([
      start.x,
      end.x,
      ...boxes.flatMap((box) => [box.x, box.x + box.width]),
    ]),
  ].sort((a, b) => a - b);
  const ys = [
    ...new Set([
      start.y,
      end.y,
      ...boxes.flatMap((box) => [box.y, box.y + box.height]),
    ]),
  ].sort((a, b) => a - b);
  const vertices = ys.flatMap((y) => xs.map((x) => ({ x, y })));
  const startIndex = vertices.findIndex(
    (point) => point.x === start.x && point.y === start.y,
  );
  const endIndex = vertices.findIndex(
    (point) => point.x === end.x && point.y === end.y,
  );
  const distances = vertices.map(() => Infinity);
  const previous = vertices.map(() => -1);
  const pending = new Set(vertices.map((_, index) => index));
  distances[startIndex] = 0;
  while (pending.size) {
    let current = -1;
    for (const index of pending) {
      if (current === -1 || distances[index] < distances[current])
        current = index;
    }
    if (!Number.isFinite(distances[current])) break;
    if (current === endIndex) {
      const route: DiagramPoint[] = [target];
      for (let index = current; index !== -1; index = previous[index])
        route.unshift(vertices[index]);
      route.unshift(source);
      const simplified: DiagramPoint[] = [];
      for (const point of route) appendPoint(simplified, point);
      return simplified;
    }
    pending.delete(current);
    for (const neighbor of [
      ...(current % xs.length ? [current - 1] : []),
      ...(current % xs.length < xs.length - 1 ? [current + 1] : []),
      current - xs.length,
      current + xs.length,
    ]) {
      if (
        !pending.has(neighbor) ||
        crossesObstacle(vertices[current], vertices[neighbor], boxes)
      )
        continue;
      const distance =
        distances[current] +
        Math.abs(vertices[current].x - vertices[neighbor].x) +
        Math.abs(vertices[current].y - vertices[neighbor].y);
      if (distance < distances[neighbor]) {
        distances[neighbor] = distance;
        previous[neighbor] = current;
      }
    }
  }
  return orthogonalRoute(source, [], target);
}

export function routeLabelPosition(route: DiagramPoint[]): DiagramPoint {
  if (route.length < 2) return route[0] ?? { x: 0, y: 0 };
  let longestIndex = 0;
  for (let index = 1; index < route.length - 1; index++) {
    if (
      Math.hypot(
        route[index + 1].x - route[index].x,
        route[index + 1].y - route[index].y,
      ) >
      Math.hypot(
        route[longestIndex + 1].x - route[longestIndex].x,
        route[longestIndex + 1].y - route[longestIndex].y,
      )
    )
      longestIndex = index;
  }
  return {
    x: (route[longestIndex].x + route[longestIndex + 1].x) / 2,
    y: (route[longestIndex].y + route[longestIndex + 1].y) / 2,
  };
}

function appendPoint(path: DiagramPoint[], point: DiagramPoint) {
  if (path.at(-1)?.x === point.x && path.at(-1)?.y === point.y) return;
  while (path.length >= 2) {
    const previous = path[path.length - 2];
    const last = path[path.length - 1];
    if (!(
      (previous.x === last.x && last.x === point.x) ||
      (previous.y === last.y && last.y === point.y)
    ))
      break;
    path.pop();
  }
  if (path.at(-1)?.x !== point.x || path.at(-1)?.y !== point.y)
    path.push(point);
}

export function orthogonalRoute(
  source: DiagramPoint,
  points: DiagramPoint[],
  target: DiagramPoint,
): DiagramPoint[] {
  const path = [source, { x: source.x + 22, y: source.y }];
  for (const point of points) {
    appendPoint(path, { x: path[path.length - 1].x, y: point.y });
    appendPoint(path, point);
  }
  appendPoint(path, { x: target.x - 22, y: path[path.length - 1].y });
  appendPoint(path, { x: target.x - 22, y: target.y });
  appendPoint(path, target);
  return path;
}

export function roundedRoutePath(points: DiagramPoint[]): string {
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let index = 1; index < points.length - 1; index++) {
    const previous = points[index - 1];
    const point = points[index];
    const next = points[index + 1];
    const radius = Math.min(
      8,
      Math.hypot(point.x - previous.x, point.y - previous.y) / 2,
      Math.hypot(next.x - point.x, next.y - point.y) / 2,
    );
    path += ` L ${point.x - Math.sign(point.x - previous.x) * radius} ${point.y - Math.sign(point.y - previous.y) * radius}`;
    path += ` Q ${point.x} ${point.y} ${point.x + Math.sign(next.x - point.x) * radius} ${point.y + Math.sign(next.y - point.y) * radius}`;
  }
  return `${path} L ${points[points.length - 1].x} ${points[points.length - 1].y}`;
}

export function projectToRoute(
  point: DiagramPoint,
  route: DiagramPoint[],
): { point: DiagramPoint; angle: number } {
  let closest = route[0];
  let closestDistance = Infinity;
  let angle = 0;
  for (let index = 1; index < route.length; index++) {
    const start = route[index - 1];
    const end = route[index];
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const lengthSquared = dx * dx + dy * dy;
    const fraction = lengthSquared
      ? Math.max(
          0,
          Math.min(
            1,
            ((point.x - start.x) * dx + (point.y - start.y) * dy) /
              lengthSquared,
          ),
        )
      : 0;
    const candidate = {
      x: start.x + fraction * dx,
      y: start.y + fraction * dy,
    };
    const distance =
      (candidate.x - point.x) ** 2 + (candidate.y - point.y) ** 2;
    if (distance < closestDistance) {
      closest = candidate;
      closestDistance = distance;
      angle = Math.atan2(dy, dx) * (180 / Math.PI);
      if (angle > 90) angle -= 180;
      if (angle < -90) angle += 180;
    }
  }
  return { point: closest, angle };
}

function segmentDistanceSquared(
  point: DiagramPoint,
  start: DiagramPoint,
  end: DiagramPoint,
): number {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const lengthSquared = dx * dx + dy * dy;
  const fraction = lengthSquared
    ? Math.max(
        0,
        Math.min(
          1,
          ((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared,
        ),
      )
    : 0;
  return (
    (point.x - start.x - fraction * dx) ** 2 +
    (point.y - start.y - fraction * dy) ** 2
  );
}

export function insertRoutePoint(
  source: DiagramPoint,
  points: DiagramPoint[],
  target: DiagramPoint,
  point: DiagramPoint,
): DiagramPoint[] {
  const path = [source, ...points, target];
  let insertionIndex = 0;
  let nearestDistance = Infinity;
  for (let index = 0; index < path.length - 1; index++) {
    const distance = segmentDistanceSquared(
      point,
      path[index],
      path[index + 1],
    );
    if (distance < nearestDistance) {
      nearestDistance = distance;
      insertionIndex = index;
    }
  }
  return [
    ...points.slice(0, insertionIndex),
    point,
    ...points.slice(insertionIndex),
  ];
}
