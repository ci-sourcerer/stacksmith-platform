import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, FolderGit2, Search, X } from "lucide-react";
import {
  filterRepositories,
  repositoryPopup,
  repositoryWindow,
  repositoryNextIndex,
  REPOSITORY_ROW_HEIGHT,
} from "../repositorySelection";
import type { RepositoryOption } from "../repositorySelection";
import "./RepositorySelector.css";

export function RepositorySelector({
  repositories,
  selected,
  onSelect,
  onOpen,
  label = "Repository",
  loading = false,
  disabled = false,
  error = "",
  required = false,
  description,
}: {
  repositories: RepositoryOption[];
  selected: string;
  onSelect: (repository: string) => void;
  onOpen?: () => void;
  label?: string;
  loading?: boolean;
  disabled?: boolean;
  error?: string;
  required?: boolean;
  description?: string;
}) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(-1);
  const [scroll, setScroll] = useState(0);
  const [position, setPosition] = useState({
    left: 0,
    top: 0,
    width: 360,
    height: 320,
  });
  const options = useMemo(
    () => filterRepositories(repositories, query),
    [repositories, query],
  );
  const range = repositoryWindow(options.length, scroll, position.height);
  const indices = Array.from(
    { length: Math.max(0, range.end - range.start) },
    (_, offset) => range.start + offset,
  );
  if (
    active >= 0 &&
    active < options.length &&
    (active < range.start || active >= range.end)
  )
    indices.push(active);
  indices.sort((left, right) => left - right);
  const close = useCallback((restore = false) => {
    setOpen(false);
    setQuery("");
    if (restore) trigger.current?.focus();
  }, []);
  const place = useCallback(() => {
    if (!trigger.current) return;
    setPosition(
      repositoryPopup(
        trigger.current.getBoundingClientRect(),
        {
          width: globalThis.innerWidth,
          height: globalThis.visualViewport?.height ?? globalThis.innerHeight,
        },
        options.length,
      ),
    );
  }, [options.length]);
  useLayoutEffect(() => {
    if (!open) return;
    place();
    search.current?.focus();
  }, [open, place]);
  useLayoutEffect(() => {
    if (!open || !list.current) return;
    const target = Math.min(Math.max(0, active), options.length - 1);
    if (target !== active) setActive(target);
    const top = Math.max(0, target) * REPOSITORY_ROW_HEIGHT;
    if (top < list.current.scrollTop) list.current.scrollTop = top;
    else if (
      top + REPOSITORY_ROW_HEIGHT >
      list.current.scrollTop + position.height
    )
      list.current.scrollTop = top + REPOSITORY_ROW_HEIGHT - position.height;
    setScroll(list.current.scrollTop);
  }, [active, open, options.length, position.height]);
  useEffect(() => {
    if (!open) return;
    function dismiss(event: Event) {
      if (
        event.target instanceof Node &&
        !root.current?.contains(event.target) &&
        !popup.current?.contains(event.target)
      )
        close();
    }
    function reposition(event: Event) {
      if (
        !(event.target instanceof Node) ||
        !popup.current?.contains(event.target)
      )
        place();
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("focusin", dismiss);
    globalThis.addEventListener("resize", place);
    globalThis.addEventListener("scroll", reposition, true);
    globalThis.visualViewport?.addEventListener("resize", place);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("focusin", dismiss);
      globalThis.removeEventListener("resize", place);
      globalThis.removeEventListener("scroll", reposition, true);
      globalThis.visualViewport?.removeEventListener("resize", place);
    };
  }, [open, close, place]);
  useEffect(() => {
    if (disabled) close();
  }, [disabled, close]);
  function show() {
    onOpen?.();
    setQuery("");
    setActive(
      Math.max(
        0,
        filterRepositories(repositories, "").findIndex(
          (option) => option.id === selected,
        ),
      ),
    );
    setOpen(true);
  }
  function choose(option: RepositoryOption) {
    onSelect(option.id);
    close(true);
  }
  return (
    <div ref={root} className="repository-selector">
      <label
        id={`${id}-label`}
        className="repository-selector-label"
        htmlFor={`${id}-trigger`}
      >
        {label}
      </label>
      <button
        ref={trigger}
        id={`${id}-trigger`}
        className="repository-selector-trigger"
        aria-labelledby={`${id}-label ${id}-value`}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-options` : undefined}
        aria-describedby={
          [description ? `${id}-description` : "", error ? `${id}-error` : ""]
            .filter(Boolean)
            .join(" ") || undefined
        }
        disabled={disabled || loading}
        onClick={() => (open ? close(true) : show())}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            show();
          }
        }}
      >
        <FolderGit2 size={17} />
        <span id={`${id}-value`} title={selected || undefined}>
          {selected ||
            (loading ? "Loading repositories…" : "Choose a repository")}
        </span>
        <ChevronDown size={16} />
      </button>
      {description && (
        <small id={`${id}-description`} className="field-help">
          {description}
        </small>
      )}
      {error && (
        <p
          id={`${id}-error`}
          className="repository-selector-error"
          role="alert"
        >
          {error}
        </p>
      )}
      {open &&
        createPortal(
          <div
            ref={popup}
            className="repository-selector-popup"
            style={{
              left: position.left,
              top: position.top,
              width: position.width,
            }}
          >
            <div className="repository-selector-search">
              <Search size={16} />
              <input
                ref={search}
                role="combobox"
                type="text"
                aria-label={`Search ${label.toLowerCase()}`}
                aria-autocomplete="list"
                aria-expanded="true"
                aria-controls={`${id}-options`}
                aria-required={required || undefined}
                aria-activedescendant={
                  active >= 0 && active < options.length && !error
                    ? `${id}-option-${active}`
                    : undefined
                }
                placeholder="Search repositories…"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setActive(0);
                  setScroll(0);
                  if (list.current) list.current.scrollTop = 0;
                }}
                onKeyDown={(event) => {
                  if (
                    [
                      "ArrowDown",
                      "ArrowUp",
                      "Home",
                      "End",
                      "PageDown",
                      "PageUp",
                    ].includes(event.key)
                  ) {
                    event.preventDefault();
                    setActive((index) =>
                      repositoryNextIndex(
                        index,
                        event.key,
                        options.length,
                        position.height,
                      ),
                    );
                  } else if (event.key === "Enter") {
                    event.preventDefault();
                    if (!error && options[active]) choose(options[active]);
                  } else if (event.key === "Escape") {
                    event.preventDefault();
                    event.stopPropagation();
                    close(true);
                  } else if (event.key === "Tab") {
                    event.preventDefault();
                    close(true);
                    const direction = event.shiftKey ? -1 : 1;
                    const focusable = [
                      ...document.querySelectorAll<HTMLElement>(
                        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], summary, [tabindex="0"]',
                      ),
                    ].filter(
                      (element) =>
                        element.tabIndex >= 0 &&
                        element.getClientRects().length &&
                        !popup.current?.contains(element),
                    );
                    const index = focusable.indexOf(trigger.current!);
                    focusable[index + direction]?.focus();
                  }
                }}
              />
              {query && (
                <button
                  type="button"
                  aria-label="Clear repository search"
                  onClick={() => {
                    setQuery("");
                    setActive(0);
                    setScroll(0);
                    if (list.current) list.current.scrollTop = 0;
                    search.current?.focus();
                  }}
                >
                  <X size={15} />
                </button>
              )}
            </div>
            {error ? (
              <div id={`${id}-options`} role="listbox" aria-label={label}>
                <p className="repository-selector-empty">
                  Repository discovery is unavailable. Reload to retry.
                </p>
              </div>
            ) : (
              <>
                <div
                  ref={list}
                  id={`${id}-options`}
                  role="listbox"
                  aria-label={label}
                  className="repository-selector-list"
                  style={{ height: options.length ? position.height : 64 }}
                  onScroll={(event) => setScroll(event.currentTarget.scrollTop)}
                >
                  {!options.length ? (
                    <p className="repository-selector-empty">
                      {repositories.length
                        ? "No repositories match your search."
                        : "No accessible repositories available."}
                    </p>
                  ) : (
                    <div
                      style={{
                        height: options.length * REPOSITORY_ROW_HEIGHT,
                        position: "relative",
                      }}
                    >
                      {indices.map((index) => {
                        const option = options[index];
                        return (
                          <div
                            key={option.id}
                            id={`${id}-option-${index}`}
                            role="option"
                            aria-selected={selected === option.id}
                            aria-posinset={index + 1}
                            aria-setsize={options.length}
                            className={`repository-selector-option ${active === index ? "is-active" : ""}`}
                            style={{
                              top: index * REPOSITORY_ROW_HEIGHT,
                              height: REPOSITORY_ROW_HEIGHT,
                            }}
                            onMouseDown={(event) => event.preventDefault()}
                            onClick={() => choose(option)}
                          >
                            <span className="repository-selector-check">
                              {selected === option.id && <Check size={16} />}
                            </span>
                            <span className="repository-selector-copy">
                              {(option.account || option.id.includes("/")) &&
                                (index === 0 ||
                                  (options[index - 1].account ??
                                    options[index - 1].id.split("/")[0]) !==
                                    (option.account ??
                                      option.id.split("/")[0])) && (
                                  <small className="repository-selector-account">
                                    {option.account ?? option.id.split("/")[0]}
                                  </small>
                                )}
                              <strong title={option.id}>{option.id}</strong>
                              {option.description && (
                                <small title={option.description}>
                                  {option.description}
                                </small>
                              )}
                            </span>
                            {option.writable !== undefined && (
                              <span
                                className={`repository-selector-access ${option.writable ? "writable" : ""}`}
                              >
                                {option.writable ? "Can edit" : "Read only"}
                              </span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
                <p className="repository-selector-count" role="status">
                  {options.length}{" "}
                  {options.length === 1 ? "repository" : "repositories"}
                  {query && ` matching “${query}”`}
                </p>
              </>
            )}
          </div>,
          root.current?.closest('[role="dialog"]') ?? document.body,
        )}
    </div>
  );
}
