import { useEffect, useMemo, useRef, useState } from "react";
import { ansiSegments, findLogMatches, hideLogPrefixes } from "../logs";
import { importantLogLines } from "../logFilters";

export function LogViewer({ logs }: { logs: string }) {
  const viewport = useRef<HTMLPreElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [following, setFollowing] = useState(true);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [showPrefixes, setShowPrefixes] = useState(false);
  const filteredLogs = useMemo(
    () => (issuesOnly ? importantLogLines(logs) : logs),
    [issuesOnly, logs],
  );
  const segments = useMemo(() => {
    const parsed = ansiSegments(filteredLogs);
    return showPrefixes ? parsed : hideLogPrefixes(parsed);
  }, [filteredLogs, showPrefixes]);
  const totalLines = logs ? logs.split(/\r?\n/).length : 0;
  const visibleLines = filteredLogs ? filteredLogs.split(/\r?\n/).length : 0;
  const matches = useMemo(
    () => findLogMatches(segments, query),
    [segments, query],
  );
  const current = matches.length ? active % matches.length : 0;

  useEffect(() => {
    if (following && viewport.current)
      viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [logs, following, issuesOnly, showPrefixes]);

  useEffect(() => {
    if (query && !following)
      viewport.current
        ?.querySelector(`[data-match="${current}"]`)
        ?.scrollIntoView({ block: "nearest" });
  }, [current, query, following]);

  function navigate(direction: number) {
    setFollowing(false);
    setActive((current + direction + matches.length) % (matches.length || 1));
  }

  let offset = 0;
  let firstMatch = 0;
  return (
    <section
      className="log-viewer"
      aria-label="Phase logs"
      onKeyDown={(event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "f") {
          event.preventDefault();
          search.current?.focus();
          search.current?.select();
        }
      }}
    >
      <div className="log-toolbar">
        <input
          ref={search}
          type="search"
          aria-label="Find in phase logs"
          placeholder="Find in logs"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
            if (event.target.value) setFollowing(false);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              navigate(event.shiftKey ? -1 : 1);
            }
            if (event.key === "Escape") setQuery("");
          }}
        />
        <span role="status" className="log-match-count">
          {query
            ? `${matches.length ? current + 1 : 0} of ${matches.length}`
            : ""}
        </span>
        <label className="log-filter">
          <input
            type="checkbox"
            checked={showPrefixes}
            onChange={(event) => {
              setShowPrefixes(event.target.checked);
              setActive(0);
            }}
          />
          Show log prefixes
        </label>
        <label className="log-filter">
          <input
            type="checkbox"
            checked={issuesOnly}
            onChange={(event) => {
              setIssuesOnly(event.target.checked);
              setActive(0);
            }}
          />
          Warnings and errors only
        </label>
        <button
          type="button"
          aria-label="Previous match"
          disabled={!matches.length}
          onClick={() => navigate(-1)}
        >
          ↑
        </button>
        <button
          type="button"
          aria-label="Next match"
          disabled={!matches.length}
          onClick={() => navigate(1)}
        >
          ↓
        </button>
        <label className="log-follow">
          <input
            type="checkbox"
            checked={following}
            onChange={(event) => setFollowing(event.target.checked)}
          />
          Follow output
        </label>
      </div>
      {issuesOnly && (
        <p className="log-filter-note" role="status">
          Showing {visibleLines} of {totalLines} lines. Full execution output is
          unchanged.
        </p>
      )}
      <pre
        ref={viewport}
        className="platform-logs"
        tabIndex={0}
        aria-label="Phase log output"
        onScroll={() => {
          const element = viewport.current;
          if (
            element &&
            element.scrollHeight - element.scrollTop - element.clientHeight > 32
          )
            setFollowing(false);
        }}
      >
        {issuesOnly && !visibleLines ? (
          <span>No warning or error lines found.</span>
        ) : (
          segments.map((segment, index) => {
            const start = offset;
            offset += segment.text.length;
            let position = 0;
            const pieces = [];
            while (
              firstMatch < matches.length &&
              matches[firstMatch] + query.length <= start
            )
              firstMatch++;
            for (
              let match = firstMatch;
              match < matches.length && matches[match] < offset;
              match++
            ) {
              const from = Math.max(0, matches[match] - start);
              const to = Math.min(
                segment.text.length,
                matches[match] + query.length - start,
              );
              if (to <= from) continue;
              pieces.push(segment.text.slice(position, from));
              pieces.push(
                <mark
                  key={match}
                  data-match={match}
                  className={match === current ? "active" : ""}
                >
                  {segment.text.slice(from, to)}
                </mark>,
              );
              position = to;
            }
            pieces.push(segment.text.slice(position));
            return (
              <span key={index} style={segment.style}>
                {pieces}
              </span>
            );
          })
        )}
      </pre>
    </section>
  );
}
