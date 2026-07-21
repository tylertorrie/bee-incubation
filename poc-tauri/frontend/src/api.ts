import type { Incubator, Tray } from "./types";

// The local Python (FastAPI) backend that reuses the existing app logic.
// In the packaged Tauri app this is launched as a sidecar on the same port.
const BASE = "http://127.0.0.1:8756";

export async function getIncubators(): Promise<Incubator[]> {
  const r = await fetch(`${BASE}/api/incubators`);
  if (!r.ok) throw new Error(`incubators: ${r.status}`);
  return r.json();
}

export async function getTrays(params: {
  incubator_id?: number;
  status?: string;
}): Promise<Tray[]> {
  const q = new URLSearchParams();
  if (params.incubator_id != null) q.set("incubator_id", String(params.incubator_id));
  if (params.status) q.set("status", params.status);
  const r = await fetch(`${BASE}/api/trays?${q.toString()}`);
  if (!r.ok) throw new Error(`trays: ${r.status}`);
  return r.json();
}
