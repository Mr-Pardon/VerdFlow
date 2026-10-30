export interface StarterResource {
  id: string;
  name: string;
  kind: "assets" | "strategies";
  version: string;
  bytes: number;
  page_url: string;
}
export interface StarterManifest { revision: string; resources: StarterResource[] }
export interface StarterImportItem {
  id: string;
  name: string;
  status: "pending" | "downloading" | "importing" | "imported" | "existing" | "failed" | "cancelled" | "blocked" | "skipped";
  downloaded_bytes: number;
  total_bytes: number;
  message: string;
}
export interface StarterImportOperation {
  operation_id: string;
  status: "preparing" | "awaiting_confirmation" | "running" | "cancelling" | "completed" | "cancelled" | "partial" | "failed";
  items: StarterImportItem[];
  choices?: ImportChoice[];
  warnings?: string[];
  error?: string;
}
export interface ImportChoice {
  id: string;
  name: string;
  kind: "assets" | "strategies" | "plans";
  version: string;
  bytes: number;
  optional: boolean;
  conflict: boolean;
  blocked: boolean;
  message: string;
  page_url: string;
}
