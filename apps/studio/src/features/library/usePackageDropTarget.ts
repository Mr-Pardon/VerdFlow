import { ref, type Ref } from "vue";

import { platformBridge } from "../../platform";

export interface DroppedPackagePaths {
  paths: string[];
  rejectedNames: string[];
  pathFailures: string[];
}

interface PackageDropTargetOptions {
  extension: string;
  disabled: () => boolean;
  onDropPaths: (result: DroppedPackagePaths) => Promise<void>;
}

export const usePackageDropTarget = ({
  extension,
  disabled,
  onDropPaths,
}: PackageDropTargetOptions) => {
  const bridge = platformBridge();
  const active = ref(false);
  let dragDepth = 0;

  const available = bridge.capabilities.resolveDroppedFilePath;

  const onDragEnter = (event: DragEvent) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (!available || disabled()) return;
    dragDepth += 1;
    active.value = true;
  };

  const onDragOver = (event: DragEvent) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = available && !disabled() ? "copy" : "none";
    }
  };

  const onDragLeave = (event: DragEvent) => {
    if (!active.value) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) active.value = false;
  };

  const onDrop = async (event: DragEvent) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth = 0;
    active.value = false;
    if (!available || disabled()) return;

    const files = [...(event.dataTransfer?.files ?? [])];
    const accepted = files.filter((file) => hasExtension(file.name, extension));
    const rejectedNames = files
      .filter((file) => !hasExtension(file.name, extension))
      .map((file) => file.name);
    const paths: string[] = [];
    const pathFailures: string[] = [];
    for (const file of accepted) {
      const result = await bridge.resolveDroppedFilePath(file);
      if (result.ok) paths.push(result.value);
      else pathFailures.push(`${file.name}：${result.message}`);
    }
    await onDropPaths({ paths, rejectedNames, pathFailures });
  };

  return {
    active: active as Readonly<Ref<boolean>>,
    available,
    onDragEnter,
    onDragLeave,
    onDragOver,
    onDrop,
  };
};

const isFileDrag = (event: DragEvent) =>
  Boolean(event.dataTransfer?.types.includes("Files"));

const hasExtension = (name: string, extension: string) =>
  name.toLocaleLowerCase().endsWith(`.${extension.toLocaleLowerCase()}`);
