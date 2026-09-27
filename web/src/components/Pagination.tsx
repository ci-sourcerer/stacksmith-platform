import { ChevronLeft, ChevronRight } from "lucide-react";
import { pageCount } from "../pagination";

export function Pagination({
  label,
  page,
  size,
  total,
  loading = false,
  onPage,
  onSize,
}: {
  label: string;
  page: number;
  size: number;
  total: number;
  loading?: boolean;
  onPage: (page: number) => void;
  onSize: (size: number) => void;
}) {
  return (
    <nav className="list-pagination" aria-label={`${label} pages`}>
      <span role="status">
        {total} {total === 1 ? "item" : "items"} · Page {page} of{" "}
        {pageCount(total, size)}
      </span>
      <div className="pagination-actions">
        <label>
          Per page
          <select
            aria-label={`${label} per page`}
            value={size}
            onChange={(event) => onSize(Number(event.target.value))}
            disabled={loading}
          >
            {[10, 20, 50].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={loading || page <= 1}
          onClick={() => onPage(page - 1)}
          aria-label={`Previous ${label.toLowerCase()} page`}
        >
          <ChevronLeft size={15} /> Previous
        </button>
        <button
          type="button"
          disabled={loading || page >= pageCount(total, size)}
          onClick={() => onPage(page + 1)}
          aria-label={`Next ${label.toLowerCase()} page`}
        >
          Next <ChevronRight size={15} />
        </button>
      </div>
    </nav>
  );
}
