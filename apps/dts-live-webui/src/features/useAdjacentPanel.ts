import { computed, onBeforeUnmount, onMounted, ref } from "vue";

export function useAdjacentPanel(
  anchor: () => HTMLElement | null,
  options: { width: number; height?: number },
) {
  const overlay = ref(false);
  const width = ref(options.width);
  const position = ref<Record<string, string>>({
    left: "16px",
    top: "16px",
    width: `${options.width}px`,
    maxHeight: "calc(100vh - 32px)",
  });
  const updatePosition = () => {
    const bounds = anchor()?.getBoundingClientRect();
    const available = (bounds?.left ?? 0) - 28;
    overlay.value = window.innerWidth < 760 || available < 320;
    width.value = overlay.value
      ? Math.min(options.width, window.innerWidth - 24)
      : Math.min(options.width, available);
    const maxHeight = Math.max(120, window.innerHeight - 32);
    const height = Math.min(options.height ?? 460, maxHeight);
    const left = overlay.value
      ? (window.innerWidth - width.value) / 2
      : bounds!.left - width.value - 12;
    const top = Math.min(
      Math.max(16, bounds?.top ?? 16),
      window.innerHeight - height - 16,
    );
    position.value = {
      left: `${left}px`,
      top: `${Math.max(16, top)}px`,
      width: `${width.value}px`,
      maxHeight: `${maxHeight}px`,
      ...(options.height ? { height: `${height}px` } : {}),
    };
  };
  let frame = 0;
  let disposed = false;
  const queuePosition = () => {
    if (disposed) return;
    if (!frame)
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        updatePosition();
      });
  };
  let mutation: MutationObserver | undefined;
  let resize: ResizeObserver | undefined;
  onMounted(() => {
    updatePosition();
    mutation = new MutationObserver(queuePosition);
    if (anchor())
      mutation.observe(anchor()!, {
        attributes: true,
        attributeFilter: ["style", "class"],
      });
    resize = new ResizeObserver(queuePosition);
    if (anchor()) resize.observe(anchor()!);
    window.addEventListener("resize", queuePosition);
  });
  onBeforeUnmount(() => {
    disposed = true;
    mutation?.disconnect();
    resize?.disconnect();
    window.removeEventListener("resize", queuePosition);
    if (frame) window.cancelAnimationFrame(frame);
  });
  return { overlay, narrow: computed(() => width.value < 480), position };
}
