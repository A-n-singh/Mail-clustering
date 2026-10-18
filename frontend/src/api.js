const BASE = "/api";

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function call(path, opts = {}) {
  const r = await fetch(BASE + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new ApiError(r.status, msg);
  }
  return r.json();
}

export const qs = (o) => {
  const p = new URLSearchParams();
  Object.entries(o).forEach(([k, v]) => { if (v !== "" && v != null && v !== 0) p.set(k, v); });
  const s = p.toString();
  return s ? "?" + s : "";
};

export const api = {
  me: () => call("/me"),
  summary: () => call("/summary"),
  needsAction: () => call("/needs-action"),
  groups: (f) => call("/groups" + qs(f)),
  groupMessages: (id, f) => call(`/groups/${id}/messages` + qs(f)),
  groupSubjects: (id) => call(`/groups/${id}/subjects`),
  storage: () => call("/storage"),
  trash: () => call("/trash"),
  topics: () => call("/topics"),
  map: () => call("/map"),
  topicsStatus: () => call("/topics/status"),
  recluster: () => call("/topics/recluster", { method: "POST" }),
  topicMessages: (id) => call(`/topics/${id}/messages`),
  hier: (path) => call(`/hier/${path}`),
  hierMessages: (path, sub) => call(`/hier/${path}/messages${sub == null ? "" : `?sub=${sub}`}`),
  message: (id) => call(`/messages/${id}`),
  purge: (confirm) => call("/trash/purge", { method: "POST", body: { confirm } }),
  digest: () => call("/digest"),
  syncStatus: () => call("/sync/status"),
  startSync: () => call("/sync", { method: "POST" }),
  stopSync: () => call("/sync/stop", { method: "POST" }),
  preview: (ids) => call("/actions/preview", { method: "POST", body: { message_ids: ids, action: "trash" } }),
  act: (ids, action, param, confirmed = false) =>
    call("/actions", { method: "POST", body: { message_ids: ids, action, param, confirmed } }),
  undo: (batch) => call(`/actions/${batch}/undo`, { method: "POST" }),
  log: () => call("/actions/log"),
  deleteAccount: () => call("/account", { method: "DELETE" }),
  logout: () => fetch(BASE + "/auth/logout", { method: "POST", credentials: "include" }),
};

export const fmtBytes = (n) => {
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB"]; let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return `${n.toFixed(n < 10 ? 1 : 0)} ${u[i]}`;
};
