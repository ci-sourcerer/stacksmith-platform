import { describe, expect, it } from "vitest";
import {
  automaticRoute,
  routeLabelPosition,
  insertRoutePoint,
  orthogonalRoute,
  projectToRoute,
  roundedRoutePath,
} from "./routing";

describe("automatic diagram routes", () => {
  const boxes = [
    { x: 44, y: 44, width: 232, height: 158 },
    { x: 484, y: 44, width: 232, height: 230 },
    { x: 924, y: 44, width: 232, height: 254 },
    { x: 44, y: 484, width: 232, height: 350 },
  ];

  it.each([
    [
      { x: 276, y: 154 },
      { x: 924, y: 178 },
    ],
    [
      { x: 1156, y: 226 },
      { x: 44, y: 594 },
    ],
    [
      { x: 716, y: 226 },
      { x: 924, y: 178 },
    ],
  ])("routes between ports without crossing cards", (source, target) => {
    const route = automaticRoute(source, target, boxes);
    expect(route[0]).toEqual(source);
    expect(route.at(-1)).toEqual(target);
    for (let index = 1; index < route.length; index++) {
      const start = route[index - 1];
      const end = route[index];
      expect(start.x === end.x || start.y === end.y).toBe(true);
      for (const box of boxes) {
        expect(
          start.y === end.y
            ? start.y > box.y &&
                start.y < box.y + box.height &&
                Math.max(start.x, end.x) > box.x &&
                Math.min(start.x, end.x) < box.x + box.width
            : start.x > box.x &&
                start.x < box.x + box.width &&
                Math.max(start.y, end.y) > box.y &&
                Math.min(start.y, end.y) < box.y + box.height,
        ).toBe(false);
      }
    }
    expect(projectToRoute(routeLabelPosition(route), route).point).toEqual(
      routeLabelPosition(route),
    );
  });

  it("reattaches a saved label after the components move", () => {
    const saved = routeLabelPosition(
      automaticRoute({ x: 276, y: 154 }, { x: 924, y: 178 }, boxes),
    );
    expect(
      projectToRoute(
        saved,
        automaticRoute(
          { x: 276, y: 242 },
          { x: 924, y: 266 },
          boxes.map((box) => ({ ...box, y: box.y + 88 })),
        ),
      ).point,
    ).toEqual({ x: saved.x, y: saved.y + 88 });
  });

  it("centers a label on the longest straight segment instead of a corner", () => {
    expect(
      routeLabelPosition([
        { x: 0, y: 0 },
        { x: 22, y: 0 },
        { x: 22, y: 200 },
        { x: 44, y: 200 },
      ]),
    ).toEqual({ x: 22, y: 100 });
  });
});

describe("orthogonal routing", () => {
  it("removes a doubled-back horizontal spur after moving a component", () => {
    const route = orthogonalRoute(
      { x: 700, y: 100 },
      [
        { x: 250, y: 100 },
        { x: 340, y: 100 },
      ],
      { x: 430, y: 416 },
    );
    expect(route).toEqual([
      { x: 700, y: 100 },
      { x: 408, y: 100 },
      { x: 408, y: 416 },
      { x: 430, y: 416 },
    ]);
    expect(roundedRoutePath(route).match(/M /g)).toHaveLength(1);
  });

  it("keeps every segment axis aligned when endpoints move", () => {
    const route = orthogonalRoute(
      { x: 90, y: 75 },
      [
        { x: 220, y: 132 },
        { x: 330, y: 220 },
      ],
      { x: 160, y: 380 },
    );
    expect(route[0]).toEqual({ x: 90, y: 75 });
    expect(route.at(-1)).toEqual({ x: 160, y: 380 });
    expect(
      route
        .slice(1)
        .every(
          (point, index) =>
            point.x === route[index].x || point.y === route[index].y,
        ),
    ).toBe(true);
  });

  it("projects labels onto the nearest segment and returns its angle", () => {
    expect(
      projectToRoute({ x: 130, y: 94 }, [
        { x: 0, y: 100 },
        { x: 200, y: 100 },
      ]),
    ).toEqual({ point: { x: 130, y: 100 }, angle: 0 });
    expect(
      projectToRoute({ x: 204, y: 110 }, [
        { x: 200, y: 100 },
        { x: 200, y: 200 },
      ]),
    ).toEqual({ point: { x: 200, y: 110 }, angle: 90 });
  });
});

describe("route point insertion", () => {
  const source = { x: 0, y: 0 };
  const target = { x: 300, y: 0 };
  const points = [
    { x: 100, y: 0 },
    { x: 200, y: 0 },
  ];

  it.each([0, 1, 2])(
    "inserts into segment %i without reordering existing points",
    (index) => {
      const point = { x: 50 + index * 100, y: 3 };
      expect(insertRoutePoint(source, points, target, point)).toEqual([
        ...points.slice(0, index),
        point,
        ...points.slice(index),
      ]);
    },
  );

  it("follows route order when the line doubles back to the left", () => {
    const route = [
      { x: 200, y: 0 },
      { x: 200, y: 100 },
      { x: 50, y: 100 },
    ];
    const point = { x: 125, y: 101 };
    expect(insertRoutePoint(source, route, { x: 50, y: 200 }, point)).toEqual([
      route[0],
      route[1],
      point,
      route[2],
    ]);
  });

  it("handles a route without points and coincident endpoints", () => {
    const point = { x: 20, y: 20 };
    expect(insertRoutePoint(source, [], source, point)).toEqual([point]);
  });
});
