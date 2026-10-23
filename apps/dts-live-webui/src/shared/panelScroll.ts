const scrollableOverflow = /^(auto|scroll|overlay)$/;

function scrollAxis(
  target: Element,
  boundary: HTMLElement,
  axis: "x" | "y",
  delta: number,
  mode: number,
) {
  if (!Number.isFinite(delta) || !delta) return;
  for (
    let element: Element | null = target;
    element;
    element = element.parentElement
  ) {
    if (element instanceof HTMLElement) {
      const style = window.getComputedStyle(element);
      const horizontal = axis === "x";
      const viewport = horizontal ? element.clientWidth : element.clientHeight;
      const extent =
        (horizontal ? element.scrollWidth : element.scrollHeight) - viewport;
      if (
        extent > 0 &&
        scrollableOverflow.test(horizontal ? style.overflowX : style.overflowY)
      ) {
        const unit =
          mode === 1
            ? Number.parseFloat(style.lineHeight) || 16
            : mode === 2
              ? viewport
              : 1;
        const current = horizontal ? element.scrollLeft : element.scrollTop;
        const next = Math.max(0, Math.min(extent, current + delta * unit));
        if (horizontal) element.scrollLeft = next;
        else element.scrollTop = next;
        return;
      }
    }
    if (element === boundary) return;
  }
}

// Explorer may consume the native wheel default; keep scrolling local to the panel.
export function scrollPanelWheel(event: WheelEvent) {
  event.preventDefault();
  event.stopPropagation();
  if (
    !(event.currentTarget instanceof HTMLElement) ||
    !(event.target instanceof Element)
  )
    return;
  const horizontal = event.deltaX || (event.shiftKey ? event.deltaY : 0);
  const vertical = event.shiftKey && !event.deltaX ? 0 : event.deltaY;
  scrollAxis(
    event.target,
    event.currentTarget,
    "x",
    horizontal,
    event.deltaMode,
  );
  scrollAxis(event.target, event.currentTarget, "y", vertical, event.deltaMode);
}
