import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, fmtBytes } from "./api.js";
import Bubbles from "./Bubbles.jsx";
import ClusterMap, { GREY, topicColor } from "./ClusterMap.jsx";
import Filters, { EMPTY } from "./Filters.jsx";
import { ConfirmModal, MailRow } from "./MailList.jsx";

function usePoll(fn, ms, deps = []) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const load = useCallback(() => fn().then((d) => { setData(d); setErr(null); }).catch(setErr), deps); // eslint-disable-line
  useEffect(() => { load(); if (!ms) return; const t = setInterval(load, ms); return () => clearInterval(t); }, [load, ms]);
  return [data, load, err];
}

export default function App() {
  const [me, setMe] = useState(undefined);
  useEffect(() => { api.me().then(setMe).catch(() => setMe(null)); }, []);
  if (me === undefined) return <div className="wrap muted">Loading…</div>;
  if (me === null) return <Login />;
  return <Dashboard email={me.email} />;
}

function Login() {
  return (
    <div className="wrap" style={{ textAlign: "center", paddingTop: 80 }}>
      <h1>Smart Inbox</h1>
      <p className="muted">Sign in with Google to group and clean your Gmail. The app never sees your password.</p>
      <a className="btn primary" href="/api/auth/login">Sign in with Google</a>
    </div>
  );
}

function Dashboard({ email }) {
  const [tab, setTab] = useState("needs");
  const [toast, setToast] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [version, setVersion] = useState(0); // bump to refetch everything after an action
  const [status, reloadStatus] = usePoll(api.syncStatus, 3000);
  const [summary] = usePoll(api.summary, 0, [version, status?.running]);
  const refresh = () => setVersion((v) => v + 1);

  /** Single mail -> run now. Several -> preview + confirm first. Always offer Undo afterwards. */
  const act = useCallback(async (ids, action, param) => {
    try {
      if (action === "label" && !param) { param = prompt("Label name?"); if (!param) return; }
      if (ids.length > 1) {
        const info = await api.preview(ids);
        return setConfirm({ ids, action, param, info });
      }
      await doAct(ids, action, param, false);
    } catch (e) { setToast({ text: e.message }); }
  }, []); // eslint-disable-line

  async function doAct(ids, action, param, confirmed) {
    const r = await api.act(ids, action, param, confirmed);
    setConfirm(null);
    setToast({ text: `${action}: ${ids.length} mail${ids.length > 1 ? "s" : ""}${r.refilled ? ` · ${r.refilled} older pulled in` : ""}`, batch: r.batch_id });
    refresh();
  }

  if (status && !status.connected) return <div className="wrap"><p>Gmail is not connected. <a href="/api/auth/login">Sign in again</a>.</p></div>;
  const firstSync = status && status.running && (!summary || summary.total === 0);

  return (
    <div className="wrap">
      <header className="top">
        <h2 style={{ margin: 0 }}>Smart Inbox</h2>
        <div className="stats" aria-label="summary">
          <div className="stat"><b>{summary?.total ?? "–"}</b><span>total mails</span></div>
          <div className="stat"><b>{summary?.grouped ?? "–"}</b><span>grouped</span></div>
          <div className="stat"><b>{summary?.needs_review ?? "–"}</b><span>need review</span></div>
        </div>
        <div>
          <span className="muted">{email} </span>
          <button onClick={async () => { await api.startSync(); reloadStatus(); }} disabled={status?.running}>
            {status?.running ? "Syncing…" : "Sync now"}
          </button>{" "}
          <button onClick={async () => { await api.logout(); location.reload(); }}>Sign out</button>
        </div>
      </header>

      {status?.running && (
        <div className="card" role="status">
          <b>{firstSync ? "Setting up your inbox…" : "Syncing…"}</b>
          <div className="bar"><div style={{ width: status.total ? `${(100 * status.fetched) / status.total}%` : "5%" }} /></div>
          <span className="muted">{status.fetched} of {status.total || "?"} mails read, then grouping</span>
        </div>
      )}
      {status?.error && <p className="err">Last sync failed: {status.error}</p>}
      {!status?.running && summary?.total === 0 && (
        <div className="card">No mail yet. Press <b>Sync now</b> to read your inbox for the first time.</div>
      )}

      <nav className="tabs">
        {[["needs", "Needs action"], ["groups", "Groups"], ["map", "Topic map"], ["digest", "Daily digest"], ["storage", "Storage"], ["trash", "Trash"], ["log", "Action log"], ["settings", "Account"]].map(([k, l]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
        ))}
      </nav>

      {tab === "needs" && <NeedsAction version={version} act={act} />}
      {tab === "groups" && <Groups version={version} act={act} />}
      {tab === "map" && <TopicMap version={version} act={act} />}
      {tab === "digest" && <Digest version={version} />}
      {tab === "storage" && <Storage version={version} />}
      {tab === "trash" && <Trash version={version} onDone={(text) => { setToast({ text }); refresh(); }} />}
      {tab === "log" && <Log version={version} onUndo={async (b) => { await api.undo(b); refresh(); }} />}
      {tab === "settings" && <Account />}

      {confirm && (
        <ConfirmModal info={confirm.info} action={confirm.action} onCancel={() => setConfirm(null)}
          onConfirm={() => doAct(confirm.ids, confirm.action, confirm.param, true).catch((e) => setToast({ text: e.message }))} />
      )}
      {toast && <Toast toast={toast} onClose={() => setToast(null)} onUndo={async () => { await api.undo(toast.batch); setToast({ text: "Undone" }); refresh(); }} />}
    </div>
  );
}

function Toast({ toast, onClose, onUndo }) {
  useEffect(() => { const t = setTimeout(onClose, 12000); return () => clearTimeout(t); }, [toast, onClose]);
  return (
    <div className="toast" role="status">
      {toast.text}
      {toast.batch && <button onClick={onUndo}>Undo</button>}
      <button onClick={onClose} aria-label="dismiss">✕</button>
    </div>
  );
}

function NeedsAction({ version, act }) {
  const [rows, , err] = usePoll(api.needsAction, 0, [version]);
  if (err) return <p className="err">{err.message}</p>;
  if (!rows) return <p className="muted">Loading…</p>;
  if (!rows.length) return <div className="card muted">Nothing needs action right now.</div>;
  return <div className="card">{rows.map((m) => <MailRow key={m.message_id} m={m} onAct={act} />)}</div>;
}

function Groups({ version, act }) {
  const [filters, setFilters] = useState(EMPTY);
  const [picked, setPicked] = useState(null);
  const [groups, , err] = usePoll(() => api.groups(filters), 0, [filters, version]);
  const pick = useCallback((g) => setPicked(g), []);
  return (
    <>
      <Filters value={filters} onChange={setFilters} />
      {err && <p className="err">{err.message}</p>}
      {!picked ? (
        <div className="card">
          <div className="legend">Bubble size = mail count · colour = importance <i /> low → high · click a bubble to open it</div>
          {groups && groups.length ? <Bubbles groups={groups} onPick={pick} /> : <p className="muted">No groups match these filters.</p>}
          {groups && groups.some((g) => g.gmail_total > g.count) && (
            <p className="muted">Some groups are capped at 360 kept mails; older mail is pulled in as you clean.</p>
          )}
        </div>
      ) : (
        <Drill group={picked} filters={filters} version={version} act={act} onBack={() => setPicked(null)} />
      )}
    </>
  );
}

function Drill({ group, filters, version, act, onBack }) {
  const [mails] = usePoll(() => api.groupMessages(group.cluster_id, filters), 0, [group.cluster_id, filters, version]);
  const [subjects] = usePoll(() => api.groupSubjects(group.cluster_id), 0, [group.cluster_id, version]);
  const [sel, setSel] = useState(new Set());
  const [view, setView] = useState("mails");
  useEffect(() => setSel(new Set()), [version]);
  const toggle = (id) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const ids = [...sel];
  return (
    <div className="card">
      <button onClick={onBack}>← All groups</button>
      <h3>{group.label} <span className="muted">· {mails?.length ?? group.count} mails{group.gmail_total > group.count ? ` of ${group.gmail_total} in Gmail` : ""} · {fmtBytes(group.bytes)}</span></h3>
      <nav className="tabs">
        <button className={view === "mails" ? "on" : ""} onClick={() => setView("mails")}>Mails</button>
        <button className={view === "subjects" ? "on" : ""} onClick={() => setView("subjects")}>By subject</button>
      </nav>
      <div className="filters">
        <b>{ids.length} selected</b>
        <button disabled={!mails} onClick={() => setSel(new Set(mails.map((m) => m.message_id)))}>Select all</button>
        <button disabled={!ids.length} onClick={() => setSel(new Set())}>Clear</button>
        {["trash", "archive", "mark_read", "star"].map((a) => (
          <button key={a} className={a === "trash" ? "danger" : ""} disabled={ids.length < 2} onClick={() => act(ids, a)}>{a.replace("_", " ")} selected</button>
        ))}
      </div>
      {view === "mails" && (mails ? mails.map((m) => <MailRow key={m.message_id} m={m} selected={sel.has(m.message_id)} onSelect={toggle} onAct={act} />) : <p className="muted">Loading…</p>)}
      {view === "subjects" && (subjects || []).map((s) => (
        <div className="row" key={s.subject}>
          <div className="main"><div className="subj">{s.subject || "(no subject)"}</div><div className="meta">{s.count} mails · {fmtBytes(s.bytes)}</div></div>
          <button onClick={() => setSel(new Set([...sel, ...s.message_ids]))}>Select all {s.count}</button>
        </div>
      ))}
    </div>
  );
}

function TopicMap({ version, act }) {
  const [info, reloadInfo] = usePoll(api.topics, 0, [version]);
  const [map, reloadMap] = usePoll(api.map, 0, [version]);
  const [status, reloadStatus] = usePoll(api.topicsStatus, 3000, [version]);
  const [focus, setFocus] = useState(null);   // topic id, 0 = review bucket, null = all
  const [view, setView] = useState("map");     // map | list
  const [sel, setSel] = useState(new Set());
  const [open, setOpen] = useState(null);
  const [list] = usePoll(() => (view === "list" && focus != null ? api.topicMessages(focus) : Promise.resolve(null)), 0, [view, focus, version]);
  useEffect(() => { setSel(new Set()); setOpen(null); }, [version]);
  const wasRunning = useRef(false);
  useEffect(() => { if (wasRunning.current && status && !status.running) { reloadInfo(); reloadMap(); } wasRunning.current = !!status?.running; }, [status, reloadInfo, reloadMap]);

  const topics = info?.topics ?? [];
  const points = map?.points ?? [];
  const noTopics = info && !topics.length;
  const color = (t, i) => (t == null ? GREY : topicColor(i, topics.length));

  async function openMail(id) { setOpen(await api.message(id)); }
  const ids = [...sel];

  return (
    <div className="card">
      {noTopics && (
        <p>
          {status?.running ? "Building topics…" : status?.messages && status.messages < status.min_messages
            ? `Only ${status.messages} mails so far; topics need at least ${status.min_messages}.`
            : "No topics yet. They are built automatically every week; you can build them now."}{" "}
          <button disabled={status?.running} onClick={async () => { await api.recluster(); reloadStatus(); }}>Build topics now</button>
          {status?.error && <span className="err"> Last run failed: {status.error}</span>}
        </p>
      )}
      {!noTopics && info && (
        <>
          <nav className="tabs">
            <button className={view === "map" ? "on" : ""} onClick={() => setView("map")}>Map</button>
            <button className={view === "list" ? "on" : ""} onClick={() => { setView("list"); if (focus == null) setFocus(topics[0]?.topic_id ?? 0); }}>List</button>
            <button disabled={status?.running} onClick={async () => { await api.recluster(); reloadStatus(); }}>{status?.running ? "Rebuilding…" : "Rebuild topics"}</button>
          </nav>
          <div className="topic-legend" role="group" aria-label="Topics">
            <button className={focus == null ? "on" : ""} onClick={() => setFocus(null)}>All ({points.length})</button>
            {topics.map((t, i) => (
              <button key={t.topic_id} className={focus === t.topic_id ? "on" : ""} title={t.summary} onClick={() => setFocus(focus === t.topic_id ? null : t.topic_id)}>
                <i style={{ background: color(t.topic_id, i) }} />{t.label} ({t.count})
              </button>
            ))}
            <button className={focus === 0 ? "on" : ""} title="Mail that fits no topic, most important first" onClick={() => setFocus(focus === 0 ? null : 0)}>
              <i style={{ background: GREY }} />Review bucket ({info.noise})
            </button>
          </div>
        </>
      )}

      {!noTopics && view === "map" && map && (
        <>
          <ClusterMap points={points} topics={topics} focus={focus}
            selected={sel} onSelect={setSel} onOpen={openMail} />
          {ids.length > 0 && (
            <div className="filters" style={{ marginTop: 10 }}>
              <b>{ids.length} selected</b>
              {ids.length > 1
                ? ["trash", "archive", "mark_read", "star"].map((a) => <button key={a} className={a === "trash" ? "danger" : ""} onClick={() => act(ids, a)}>{a.replace("_", " ")}</button>)
                : <span className="muted">select at least 2 for a bulk action</span>}
              <button onClick={() => setSel(new Set())}>Clear</button>
            </div>
          )}
        </>
      )}

      {!noTopics && view === "list" && (list ? list.map((m) => <MailRow key={m.message_id} m={m} onAct={act} />) : <p className="muted">Loading…</p>)}

      {open && (
        <div className="sidepanel" role="region" aria-label="Mail">
          <button onClick={() => setOpen(null)} style={{ float: "right" }}>✕</button>
          <MailRow m={open} onAct={act} />
          <p className="muted" style={{ whiteSpace: "pre-wrap" }}>{open.preview || open.snippet}</p>
        </div>
      )}
    </div>
  );
}

function Digest({ version }) {
  const [d] = usePoll(api.digest, 0, [version]);
  if (!d) return <p className="muted">Loading…</p>;
  return (
    <div className="card">
      <h3>Needs action</h3>
      {d.needs_action.length ? d.needs_action.map((m) => (
        <div className="row" key={m.message_id}><span className="score">{m.score}</span>
          <div className="main"><div className="subj">{m.subject}</div><div className="meta">{m.sender} · {m.reason}</div></div></div>
      )) : <p className="muted">Nothing.</p>}
      <h3>Biggest groups</h3>
      <ul>{d.groups.map((g) => <li key={g.cluster_id}>{g.label}: {g.count}</li>)}</ul>
    </div>
  );
}

function Storage({ version }) {
  const [s] = usePoll(api.storage, 0, [version]);
  if (!s) return <p className="muted">Loading…</p>;
  return (
    <div className="card">
      <p><b>{fmtBytes(s.kept_bytes)}</b> in tracked mail · <b>{fmtBytes(s.in_trash_bytes)}</b> in Trash (Gmail frees this after 30 days, or empty Trash in Gmail to free it now).</p>
      <h3>Biggest groups</h3>
      {s.biggest_groups.map((g) => <div className="row" key={g.label}><div className="main">{g.label}</div><b>{fmtBytes(g.bytes)}</b></div>)}
    </div>
  );
}

function Trash({ version, onDone }) {
  const [t, reload] = usePoll(api.trash, 0, [version]);
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [err, setErr] = useState(null);
  if (!t) return <p className="muted">Loading…</p>;
  async function purge() {
    try {
      const r = await api.purge(typed);
      setOpen(false); setTyped(""); reload();
      onDone(`Permanently deleted ${r.deleted} mails · freed ${fmtBytes(r.bytes_freed)}${r.skipped_restored ? ` · ${r.skipped_restored} skipped (restored)` : ""}`);
    } catch (e) { setErr(e.message); }
  }
  return (
    <div className="card">
      <p><b>{t.count}</b> mails in Trash · <b>{fmtBytes(t.bytes)}</b>. Gmail frees this by itself 30 days after each mail was trashed.</p>
      {!t.enabled ? (
        <p className="muted">Permanent delete is turned off. To free the space now, set <code>ALLOW_PERMANENT_DELETE=true</code> and sign in again
          (Google will ask for full-mailbox access, which is the only permission that allows it), or empty Trash inside Gmail.</p>
      ) : (
        <button className="danger" disabled={!t.count} onClick={() => { setErr(null); setOpen(true); }}>Delete these {t.count} mails forever…</button>
      )}
      {open && (
        <div className="modal" role="dialog" aria-modal="true">
          <div className="box">
            <h3 style={{ marginTop: 0 }}>Delete {t.count} mails forever?</h3>
            <p>This frees <b>{fmtBytes(t.bytes)}</b> immediately and <b>cannot be undone</b>. Only mail already in Trash is deleted.</p>
            <p className="muted">Type <b>{t.confirm_phrase}</b> to continue:</p>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="confirmation" style={{ width: "100%" }} />
            {err && <p className="err">{err}</p>}
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12 }}>
              <button onClick={() => setOpen(false)}>Cancel</button>
              <button className="danger" disabled={typed !== t.confirm_phrase} onClick={purge}>Delete forever</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Log({ version, onUndo }) {
  const [rows] = usePoll(api.log, 0, [version]);
  if (!rows) return <p className="muted">Loading…</p>;
  const batches = [...new Map(rows.map((r) => [r.batch_id, r])).values()];
  return (
    <div className="card">
      {!batches.length && <p className="muted">No actions yet.</p>}
      {batches.map((b) => {
        const n = rows.filter((r) => r.batch_id === b.batch_id).length;
        return (
          <div className="row" key={b.batch_id}>
            <div className="main">{b.action} · {n} mail{n > 1 ? "s" : ""}<div className="meta">{new Date(b.timestamp).toLocaleString()}</div></div>
            {b.undone ? <span className="muted">undone</span> : <button onClick={() => onUndo(b.batch_id)}>Undo</button>}
          </div>
        );
      })}
    </div>
  );
}

function Account() {
  const [confirming, setConfirming] = useState(false);
  return (
    <div className="card">
      <h3>Disconnect and erase my data</h3>
      <p className="muted">Revokes the Google access token and deletes every stored mail, group, score and log. Your Gmail itself is not changed.</p>
      {!confirming ? <button className="danger" onClick={() => setConfirming(true)}>Delete my data…</button> : (
        <>
          <p className="err">This cannot be undone.</p>
          <button className="danger" onClick={async () => { await api.deleteAccount(); location.reload(); }}>Yes, erase everything</button>{" "}
          <button onClick={() => setConfirming(false)}>Cancel</button>
        </>
      )}
    </div>
  );
}
