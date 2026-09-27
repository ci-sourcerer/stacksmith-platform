import { useLayoutEffect, useRef, type TextareaHTMLAttributes } from "react";

function fitContent(textarea: HTMLTextAreaElement) {
  textarea.style.height = "auto";
  textarea.style.height = `${textarea.scrollHeight + textarea.offsetHeight - textarea.clientHeight}px`;
}

export function AutoSizeTextarea({
  className = "",
  rows = 1,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    if (ref.current) fitContent(ref.current);
  }, [props.value]);

  useLayoutEffect(() => {
    const textarea = ref.current;
    if (!textarea) return;
    let width = 0;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width === width) return;
      width = entry.contentRect.width;
      fitContent(textarea);
    });
    observer.observe(textarea);
    return () => observer.disconnect();
  }, []);

  return (
    <textarea
      {...props}
      ref={ref}
      rows={rows}
      className={`auto-size-textarea ${className}`.trim()}
    />
  );
}
