import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fmtBytes } from "./api.js";
import { GREY, topicColor } from "./ClusterMap.jsx";
import Hologram, { holoColor } from "./Hologram.jsx";
import Filters, { EMPTY } from "./Filters.jsx";
import Explain from "./Explain.jsx";
import { ConfirmModal, MailRow, Reader, useMailBody } from "./MailList.jsx";

const NONE = new Set();
const noop = () => {};

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
  const [tab, setTab] = useState("map");
  const [toast, setToast] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [version, setVersion] = useState(0); // bump to refetch everything after an action
  const [status, reloadStatus] = usePoll(api.syncStatus, 3000);
  // while a sync runs, keep the header numbers live (every 4s) and refresh the open views (every 12s)
  const [summary] = usePoll(api.summary, status?.running ? 4000 : 0, [version, status?.running]);
  const refresh = () => setVersion((v) => v + 1);
  const [stopping, setStopping] = useState(false);
  useEffect(() => { if (!status?.running) setStopping(false); }, [status?.running]);
  async function stopSync() {
    setStopping(true);
    try { await api.stopSync(); } catch (e) { setStopping(false); setToast({ text: e.message }); }
    reloadStatus();
  }
  useEffect(() => {
    if (!status?.running && !status?.refilling) return;
    const t = setInterval(() => setVersion((v) => v + 1), 12000);
    return () => clearInterval(t);
  }, [status?.running, status?.refilling]);

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
        <div className="brand"><h2>Smart Inbox</h2><small><i />NEURAL MAIL CORE // ONLINE</small></div>
        <div className="stats" aria-label="summary">
          <Gauge label="total mails" value={summary?.total} max={summary?.total || 1} color="#00e5ff" decor />
          <Gauge label="grouped" value={summary?.grouped} max={summary?.total || 1} color="#7cff6b" />
          <Gauge label="need review" value={summary?.needs_review} max={summary?.total || 1} color="#ffb300" />
        </div>
        <div>
          <span className="muted">{email} </span>
          <button onClick={async () => { await api.startSync(); reloadStatus(); }} disabled={status?.running}>
            {status?.running ? "Syncing…" : "Sync now"}
          </button>{" "}
          {status?.running && <><button className="danger" onClick={stopSync} disabled={stopping}>{stopping ? "Stopping…" : "Stop"}</button>{" "}</>}
          <button onClick={async () => { await api.logout(); location.reload(); }}>Sign out</button>
        </div>
      </header>

      {status?.running && (
        <div className="card" role="status">
          <b>{firstSync ? "Setting up your inbox…" : "Syncing…"}</b>
          <div className="bar"><div style={{ width: status.total ? `${(100 * status.fetched) / status.total}%` : "5%" }} /></div>
          <span className="muted">{status.fetched} of {status.total || "?"} mails read; groups and topics grow in small batches as they load</span>
          <div className="muted" style={{ marginTop: 6 }}>{stopping ? "Stopping… mails already read are kept." : "You can press Stop at any time; mails already read are kept."}</div>
        </div>
      )}
      {!status?.running && status?.refilling && (
        <p className="muted" role="status">Mail left your inbox view, so older mail is being loaded in small batches to keep it full.</p>
      )}
      {!status?.running && status?.stopped && (
        <p className="muted">Sync stopped. {status.fetched} mails from that run were kept. Press <b>Sync now</b> to continue where it stopped.</p>
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

function Gauge({ label, value, max, color, decor }) {
  const r = 26, c = 2 * Math.PI * r, f = value == null ? 0 : Math.min(1, value / Math.max(max, 1));
  return (
    <div className="gauge" style={{ color }}>
      <svg viewBox="0 0 64 64" aria-hidden="true">
        <circle className="track" cx="32" cy="32" r={r} />
        <circle className="val" cx="32" cy="32" r={r} strokeDasharray={c} strokeDashoffset={c * (1 - f)} />
        {decor && <circle className="spin" cx="32" cy="32" r="31" />}
      </svg>
      <div><b>{value ?? "–"}</b><span>{label}</span></div>
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
  return <div className="card"><Explain id="needs">{rows.map((m) => <MailRow key={m.message_id} m={m} onAct={act} />)}</Explain></div>;
}

function Groups({ version, act }) {
  const [filters, setFilters] = useState(EMPTY);
  const [picked, setPicked] = useState(null);
  const [groups, , err] = usePoll(() => api.groups(filters), 0, [filters, version]);
  const [holo, , holoErr] = usePoll(() => api.groupsHolo(filters), 0, [filters, version]);
  const pick = useCallback((g) => setPicked(g), []);
  const byId = useMemo(() => new Map((groups || []).map((g) => [g.cluster_id, g])), [groups]);
  const groupOfMail = useMemo(() => new Map((holo?.points || []).map((p) => [p.i, p.t])), [holo]);
  const goInside = (cid) => { const g = byId.get(cid); if (g) pick(g); };
  return (
    <>
      <Filters value={filters} onChange={setFilters} />
      {(err || holoErr) && <p className="err">{(err || holoErr).message}</p>}
      {!picked ? (
        <div className="card">
          <Explain id="groups">
          <div className="legend">Each ball is one sender group, every dot is a mail · tap a ball to fly to it, tap it again to go inside</div>
          {holo && holo.points.length
            ? <Hologram points={holo.points} topics={holo.topics} focus={null} selected={NONE} onSelect={noop}
                onOpen={(mid) => goInside(groupOfMail.get(mid))} onDrill={goInside} />
            : <p className="muted">{holo ? "No groups match these filters." : "Loading…"}</p>}
          {holo && holo.topics.length > 0 && (
            <div className="topic-legend" role="group" aria-label="Groups">
              {holo.topics.map((t, i) => (
                <button key={t.topic_id} onClick={() => goInside(t.topic_id)}><i style={{ background: holoColor(i) }} />{t.label} ({t.count})</button>
              ))}
            </div>
          )}
          {groups && groups.some((g) => g.gmail_total > g.count) && (
            <p className="muted">Only the newest mail is kept here (up to 1,000 in total, 360 per group); older mail is pulled in as you clean.</p>
          )}
          </Explain>
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
  const [inner] = usePoll(() => api.groupHolo(group.cluster_id, filters), 0, [group.cluster_id, filters, version]);
  const [open, setOpen] = useState(null);
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
      {inner && inner.points.length >= 3 && (
        <>
          <div className="legend">Inside {group.label}: one ball per kind of subject · drag to orbit, lasso to select dots, tap a dot to read it</div>
          <Hologram points={inner.points} topics={inner.topics} focus={null} selected={sel} onSelect={setSel}
            onOpen={async (mid) => setOpen(await api.message(mid))} far />
        </>
      )}
      {open && (
        <div className="sidepanel" role="region" aria-label="Mail">
          <button onClick={() => setOpen(null)} style={{ float: "right" }}>✕</button>
          <MailRow m={open} onAct={act} />
          <p className="muted" style={{ whiteSpace: "pre-wrap" }}>{open.preview || open.snippet}</p>
        </div>
      )}
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
        <SubjectRow key={s.subject} s={s} onSelectAll={() => setSel(new Set([...sel, ...s.message_ids]))} />
      ))}
    </div>
  );
}

function TopicMap({ version, act }) {
  const [info, reloadInfo] = usePoll(api.topics, 0, [version]);
  const [map, reloadMap] = usePoll(api.map, 0, [version]);
  const [status, reloadStatus] = usePoll(api.topicsStatus, 3000, [version]);
  const [focus, setFocus] = useState(null);   // topic id, 0 = review bucket, null = all
  const [view, setView] = useState("holo");    // holo | list
  const [sel, setSel] = useState(new Set());
  const [open, setOpen] = useState(null);
  // "hologram inside hologram": trail = the groups you went into; mode "kinds" = all mail grouped by what the subject says
  const [mode, setMode] = useState("topics");
  const [trail, setTrail] = useState([]);       // [{ path, label }]
  const curPath = trail.length ? trail[trail.length - 1].path : mode === "kinds" ? "k" : null;
  const nested = curPath != null;
  const [hier, , hierErr] = usePoll(() => (curPath ? api.hier(curPath) : Promise.resolve(null)), 0, [curPath, version]);
  const hierReady = nested && hier && hier.path === curPath;
  const leaf = hierReady && hier.leaf;
  const [list] = usePoll(() => {
    if (leaf) return api.hierMessages(curPath);
    if (view !== "list" || focus == null) return Promise.resolve(null);
    return nested ? api.hierMessages(curPath, focus) : api.topicMessages(focus);
  }, 0, [view, focus, version, curPath, leaf]);
  useEffect(() => { setSel(new Set()); setOpen(null); }, [version]);
  const goTo = (nextMode, nextTrail) => { setMode(nextMode); setTrail(nextTrail); setFocus(null); setSel(new Set()); setOpen(null); };
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && status && !status.running) { reloadInfo(); reloadMap(); setTrail([]); setFocus(null); }
    wasRunning.current = !!status?.running;
  }, [status, reloadInfo, reloadMap]);

  const topics = nested ? (hierReady && !leaf ? hier.topics : []) : (info?.topics ?? []);
  const points = nested ? (hierReady && !leaf ? hier.points : []) : (map?.points ?? []);
  const noise = nested ? (hier?.noise ?? 0) : (info?.noise ?? 0);
  const noTopics = !nested && info && !topics.length;
  const childPath = (id) => (curPath ? `${curPath}.${id}` : `t${id}`);
  const goInside = (id) => {
    const t = topics.find((x) => x.topic_id === id);
    if (!t || id === 0) return;
    goTo(mode, [...trail, { path: childPath(id), label: t.label }]);
  };
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
      {!noTopics && (nested || info) && (
        <>
          <nav className="tabs" aria-label="Group by">
            <button className={mode === "topics" ? "on" : ""} title="Topics from sender name + subject + snippet (the original map)" onClick={() => goTo("topics", [])}>Topics</button>
            <button className={mode === "kinds" ? "on" : ""} title="All mail grouped by what the subject says, across senders" onClick={() => goTo("kinds", [])}>Kinds (by subject)</button>
          </nav>
          {nested && (
            <nav className="crumbs" aria-label="Where you are">
              <button onClick={() => goTo(mode, [])}>{mode === "kinds" ? "All kinds" : "All topics"}</button>
              {trail.map((c, i) => (
                <React.Fragment key={c.path}>
                  <span className="muted"> › </span>
                  <button className={i === trail.length - 1 ? "on" : ""} onClick={() => goTo(mode, trail.slice(0, i + 1))}>{c.label}</button>
                </React.Fragment>
              ))}
              {trail.length > 0 && <button onClick={() => goTo(mode, trail.slice(0, -1))}>← Back</button>}
            </nav>
          )}
          <nav className="tabs">
            <button className={view === "holo" ? "on" : ""} onClick={() => setView("holo")}>Hologram 3D</button>
            <button className={view === "list" ? "on" : ""} onClick={() => { setView("list"); if (focus == null) setFocus(topics[0]?.topic_id ?? 0); }}>List</button>
            <button disabled={status?.running} onClick={async () => { await api.recluster(); reloadStatus(); }}>{status?.running ? "Rebuilding…" : "Rebuild topics"}</button>
          </nav>
          {nested && hierErr && <p className="err">{hierErr.message}</p>}
          {nested && !hierReady && !hierErr && <p className="muted">Grouping these mails by meaning… the first time this can take up to a minute.</p>}
          {leaf && (
            <>
              <p className="muted">{hier.reason} Showing the {hier.count} mails.</p>
              {list ? list.map((m) => <MailRow key={m.message_id} m={m} onAct={act} />) : <p className="muted">Loading…</p>}
            </>
          )}
          {(!nested || (hierReady && !leaf)) && <div className="topic-legend" role="group" aria-label="Topics">
            <button className={focus == null ? "on" : ""} onClick={() => setFocus(null)}>All ({points.length})</button>
            {topics.map((t, i) => (
              <button key={t.topic_id} className={focus === t.topic_id ? "on" : ""} title={t.summary} onClick={() => setFocus(focus === t.topic_id ? null : t.topic_id)}>
                <i style={{ background: color(t.topic_id, i) }} />{t.label} ({t.count})
              </button>
            ))}
            <button className={focus === 0 ? "on" : ""} title="Mail that fits no topic, most important first" onClick={() => setFocus(focus === 0 ? null : 0)}>
              <i style={{ background: GREY }} />Review bucket ({noise})
            </button>
          </div>}
        </>
      )}

      {!noTopics && view === "holo" && (nested ? hierReady && !leaf : map) && (
        <>
        <Explain id="map">
          <Hologram points={points} topics={topics} focus={focus} selected={sel} onSelect={setSel} onOpen={openMail} onDrill={goInside} far={nested} />
        </Explain>
          {focus != null && (
            <div className="filters" style={{ marginTop: 10 }}>
              <button onClick={() => setSel(new Set(points.filter((p) => (focus === 0 ? p.t == null : p.t === focus)).map((p) => p.i)))}>
                Select all in this {focus === 0 ? "bucket" : "topic"}
              </button>
              {focus !== 0 && <button className="primary" onClick={() => goInside(focus)}>Go inside this {mode === "kinds" ? "kind" : "topic"}</button>}
            </div>
          )}
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

      {!noTopics && !leaf && view === "list" && (list ? list.map((m) => <MailRow key={m.message_id} m={m} onAct={act} />) : <p className="muted">Loading…</p>)}

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

/** One subject pattern ("Your order # has shipped" x140): read one sample before deciding to trash them all. */
function SubjectRow({ s, onSelectAll }) {
  const { open, full, toggle } = useMailBody(s.message_ids[0]);
  return (
    <div className="row">
      <div className="main">
        <div className="subj" role="button" tabIndex={0} aria-expanded={open} onClick={toggle}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } }}>
          <span className="chev">{open ? "▾" : "▸"}</span> {s.subject || "(no subject)"}
        </div>
        <div className="meta">{s.count} mails · {fmtBytes(s.bytes)} · click to read one sample</div>
      </div>
      <button onClick={onSelectAll}>Select all {s.count}</button>
      {open && <Reader m={{}} full={full} />}
    </div>
  );
}

function Digest({ version }) {
  const [d] = usePoll(api.digest, 0, [version]);
  if (!d) return <p className="muted">Loading…</p>;
  return (
    <div className="card">
      <Explain id="digest">
      <h3>Needs action</h3>
      {d.needs_action.length ? d.needs_action.map((m) => (
        <div className="row" key={m.message_id}><span className="score">{m.score}</span>
          <div className="main"><div className="subj">{m.subject}</div><div className="meta">{m.sender} · {m.reason}</div></div></div>
      )) : <p className="muted">Nothing.</p>}
      <h3>Biggest groups</h3>
      <ul>{d.groups.map((g) => <li key={g.cluster_id}>{g.label}: {g.count}</li>)}</ul>
      </Explain>
    </div>
  );
}

function Storage({ version }) {
  const [s] = usePoll(api.storage, 0, [version]);
  if (!s) return <p className="muted">Loading…</p>;
  return (
    <div className="card">
      <Explain id="storage">
      <p><b>{fmtBytes(s.kept_bytes)}</b> in tracked mail · <b>{fmtBytes(s.in_trash_bytes)}</b> in Trash (Gmail frees this after 30 days, or empty Trash in Gmail to free it now).</p>
      <h3>Biggest groups</h3>
      {s.biggest_groups.map((g) => <div className="row" key={g.label}><div className="main">{g.label}</div><b>{fmtBytes(g.bytes)}</b></div>)}
      </Explain>
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
      <Explain id="trash">
      <p><b>{t.count}</b> mails in Trash · <b>{fmtBytes(t.bytes)}</b>. Gmail frees this by itself 30 days after each mail was trashed.</p>
      {!t.enabled ? (
        <p className="muted">Permanent delete is turned off. To free the space now, set <code>ALLOW_PERMANENT_DELETE=true</code> and sign in again
          (Google will ask for full-mailbox access, which is the only permission that allows it), or empty Trash inside Gmail.</p>
      ) : (
        <button className="danger" disabled={!t.count} onClick={() => { setErr(null); setOpen(true); }}>Delete these {t.count} mails forever…</button>
      )}
      </Explain>
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
      <Explain id="log">
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
      </Explain>
    </div>
  );
}

function Account() {
  const [confirming, setConfirming] = useState(false);
  return (
    <div className="card">
      <Explain id="account">
      <h3>Disconnect and erase my data</h3>
      <p className="muted">Revokes the Google access token and deletes every stored mail, group, score and log. Your Gmail itself is not changed.</p>
      {!confirming ? <button className="danger" onClick={() => setConfirming(true)}>Delete my data…</button> : (
        <>
          <p className="err">This cannot be undone.</p>
          <button className="danger" onClick={async () => { await api.deleteAccount(); location.reload(); }}>Yes, erase everything</button>{" "}
          <button onClick={() => setConfirming(false)}>Cancel</button>
        </>
      )}
      </Explain>
    </div>
  );
}
