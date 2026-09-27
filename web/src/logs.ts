import type { CSSProperties } from "react";

export type LogSegment = { text: string; style: CSSProperties };

export function hideLogPrefixes(segments: LogSegment[]): LogSegment[] {
  const text = segments.map((segment) => segment.text).join("");
  const prefixes = Array.from(
    text.matchAll(
      /^\d{2}:\d{2}:\d{2}\.\d{3} +(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL|STDOUT|STDERR) +(?:tofu:(?: |(?=\r?$)))?/gm,
    ),
    (match) => [match.index, match.index + match[0].length] as const,
  );
  let offset = 0;
  let prefixIndex = 0;
  return segments.map((segment) => {
    const start = offset;
    offset += segment.text.length;
    let position = start;
    let result = "";
    while (prefixIndex < prefixes.length && prefixes[prefixIndex][1] <= start)
      prefixIndex++;
    for (
      let index = prefixIndex;
      index < prefixes.length && prefixes[index][0] < offset;
      index++
    ) {
      const [from, to] = prefixes[index];
      result += segment.text.slice(
        position - start,
        Math.max(position, from) - start,
      );
      position = Math.min(offset, to);
    }
    result += segment.text.slice(position - start);
    return { ...segment, text: result };
  });
}

export function findLogMatches(
  segments: LogSegment[],
  query: string,
): number[] {
  if (!query) return [];
  return Array.from(
    segments
      .map((segment) => segment.text)
      .join("")
      .matchAll(new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi")),
    (match) => match.index,
  );
}

const colors = [
  "#282828",
  "#cc241d",
  "#98971a",
  "#d79921",
  "#458588",
  "#b16286",
  "#689d6a",
  "#a89984",
  "#928374",
  "#fb4934",
  "#b8bb26",
  "#fabd2f",
  "#83a598",
  "#d3869b",
  "#8ec07c",
  "#ebdbb2",
];

function color256(index: number): string {
  if (index < 16) return colors[index];
  if (index >= 232) {
    const gray = 8 + (index - 232) * 10;
    return `rgb(${gray}, ${gray}, ${gray})`;
  }
  const value = index - 16;
  const channel = (position: number) => {
    const level = [0, 95, 135, 175, 215, 255][position];
    return level;
  };
  return `rgb(${channel(Math.floor(value / 36))}, ${channel(Math.floor((value % 36) / 6))}, ${channel(value % 6)})`;
}

function ansiColor(values: number[], offset: number): [string, number] {
  if (values[offset + 1] === 5 && values[offset + 2] !== undefined) {
    return [color256(values[offset + 2]), 2];
  }
  if (
    values[offset + 1] === 2 &&
    values[offset + 2] !== undefined &&
    values[offset + 3] !== undefined &&
    values[offset + 4] !== undefined
  ) {
    return [`rgb(${values.slice(offset + 2, offset + 5).join(", ")})`, 4];
  }
  return ["", 0];
}

function applySgr(style: CSSProperties, values: number[]): void {
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index] || 0;
    if (value === 0) {
      for (const key of Object.keys(style))
        delete (style as Record<string, unknown>)[key];
    } else if (value === 1) style.fontWeight = "bold";
    else if (value === 2) style.opacity = 0.75;
    else if (value === 3) style.fontStyle = "italic";
    else if (value === 4) style.textDecoration = "underline";
    else if (value === 22) {
      delete style.fontWeight;
    } else if (value === 23) delete style.fontStyle;
    else if (value === 24) delete style.textDecoration;
    else if (value === 39) delete style.color;
    else if (value === 49) delete style.backgroundColor;
    else if (value >= 30 && value <= 37) style.color = colors[value - 30];
    else if (value >= 90 && value <= 97) style.color = colors[value - 90 + 8];
    else if (value >= 40 && value <= 47)
      style.backgroundColor = colors[value - 40];
    else if (value >= 100 && value <= 107)
      style.backgroundColor = colors[value - 100 + 8];
    else if (value === 38 || value === 48) {
      const [color, consumed] = ansiColor(values, index);
      if (color) style[value === 38 ? "color" : "backgroundColor"] = color;
      index += consumed;
    }
  }
}

export function ansiSegments(value: string): LogSegment[] {
  const segments: LogSegment[] = [];
  const style: React.CSSProperties = {};
  const pattern =
    /\x1b\[([0-9;]*)m|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|(?:\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_])/g;
  let offset = 0;
  for (const match of value.matchAll(pattern)) {
    if (match.index > offset)
      segments.push({
        text: value.slice(offset, match.index),
        style: { ...style },
      });
    if (match[0].endsWith("m") && match[1] !== undefined) {
      applySgr(style, match[1] ? match[1].split(";").map(Number) : [0]);
    }
    offset = match.index + match[0].length;
  }
  if (offset < value.length)
    segments.push({ text: value.slice(offset), style: { ...style } });
  return segments;
}
