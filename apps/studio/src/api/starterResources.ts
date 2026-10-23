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
  status: "pending" | "downloading" | "importing" | "imported" | "existing" | "failed" | "cancelled";
  downloaded_bytes: number;
  total_bytes: number;
  message: string;
}
export interface StarterImportOperation {
  operation_id: string;
  status: "running" | "cancelling" | "completed" | "cancelled";
  items: StarterImportItem[];
}
