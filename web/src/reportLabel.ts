export function reportLabel(name: string): string {
  return name
    .split("/")
    .pop()!
    .replace(/\.[^.]+$/, "")
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
