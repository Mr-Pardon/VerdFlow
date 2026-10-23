import type { PlatformBridge } from "./bridge";
import { createDesktopBridge } from "./desktopBridge";
import { createWebDevBridge } from "./webDevBridge";

export type { ApplicationUpdateApi, ApplicationUpdatePreferences, ApplicationUpdateState } from "./applicationUpdates";

export type {
  BridgeFailureReason,
  BridgeResult,
  DtsExplorerLaunchRequest,
  DtsExplorerLaunchResult,
  DesktopPlatformApi,
  ExternalRuntimePathRequest,
  LocalApiConnection,
  LocalApiRequest,
  LocalApiResponse,
  NewProjectDirectoryRequest,
  PlatformBridge,
  PlatformCapabilities,
  SaveFileRequest,
  SaveExportRequest,
} from "./bridge";

let bridge: PlatformBridge | undefined;

export const platformBridge = (): PlatformBridge => {
  bridge ??= window.verdflowPlatform
    ? createDesktopBridge(window.verdflowPlatform)
    : createWebDevBridge();
  return bridge;
};
