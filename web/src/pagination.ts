export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};
export function pageCount(total: number, size: number): number {
  return Math.max(1, Math.ceil(total / size));
}
