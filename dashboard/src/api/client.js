const API_BASE = "/api";
const WS_BASE = `ws://${window.location.host}/ws`;

export async function fetchGraph() {
  const res = await fetch(`${API_BASE}/graph`);
  if (!res.ok) throw new Error("Failed to fetch graph");
  return await res.json();
}

export async function fetchStatus() {
  const res = await fetch(`${API_BASE}/plant/status`);
  if (!res.ok) throw new Error("Failed to fetch status");
  return await res.json();
}

export async function fetchUserProfile() {
  const res = await fetch(`${API_BASE}/auth/me`);
  if (!res.ok) throw new Error("Failed to fetch user");
  return await res.json();
}

export async function fetchInvestigation(nodeId, timestamp) {
  const res = await fetch(`${API_BASE}/investigate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ node_id: nodeId, timestamp })
  });
  if (!res.ok) throw new Error("Failed to investigate");
  return await res.json();
}

export function createAlertWebSocket(onMessage) {
  const ws = new WebSocket(`${WS_BASE}/alerts`);
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    onMessage(data);
  };
  ws.onerror = (error) => console.error("WebSocket Error:", error);
  return ws;
}

export async function fetchSettings() {
  const res = await fetch(`${API_BASE}/settings`);
  if (!res.ok) throw new Error("Failed to fetch settings");
  return await res.json();
}

export async function updateSettings(settings) {
  const res = await fetch(`${API_BASE}/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings)
  });
  if (!res.ok) throw new Error("Failed to update settings");
  return await res.json();
}
