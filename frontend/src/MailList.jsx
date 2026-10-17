import React, { useState } from "react";
import { api, fmtBytes } from "./api.js";

const BTN = [
  ["trash", "Trash", "danger"], ["star", "★ Star"], ["archive", "Archive"], ["mark_read", "Read"],
  ["mark_unread", "Unread"], ["mark_important", "Important"], ["label", "Label…"],
];

/** The mail's cleaned text, so you can decide before deleting. Text only: no pictures, attachments or quoted replies. */
export function Reader({ m, full }) {
  return (
    <div className="reader">
      {!full ? <p className="muted">Loading…</p> : (
        <>
          <div className="meta">
            From {full.sender_email || full.sender || m.sender} · {new Date(full.date || m.date).toLocaleString()} · {fmtBytes(full.size_bytes || 0)}
            {(full.labels || m.labels)?.length ? ` · ${(full.labels || m.labels).join(", ")}` : ""}
          </div>
          <pre className="body">{full.body || full.snippet || "(this mail has no text)"}</pre>
          <p className="muted">Text only: pictures, attachments and quoted replies are not shown. <a href={full.gmail_link || m.gmail_link} target="_blank" rel="noreferrer">Open the full mail in Gmail</a></p>
        </>
      )}
    </div>
  );
}

export function useMailBody(id) {
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState(null);
  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !full) {
      try { setFull(await api.message(id)); } catch (e) { setFull({ body: `Could not load this mail: ${e.message}` }); }
    }
  }
  return { open, full, toggle };
}

export function MailRow({ m, selected, onSelect, onAct }) {
  const { open, full, toggle } = useMailBody(m.message_id);
  return (
    <div className="row">
      {onSelect && <input type="checkbox" checked={!!selected} onChange={() => onSelect(m.message_id)} aria-label="select" />}
      {m.score != null && <span className="score" title={`Importance score (0-100, higher = more important). ${m.reason || ""}`}>{m.score}<small>score</small></span>}
      <div className="main">
        <div className="subj" role="button" tabIndex={0} aria-expanded={open} title="Click to read this mail" onClick={toggle}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } }}>
          <span className="chev">{open ? "▾" : "▸"}</span> {m.subject || "(no subject)"}
        </div>
        <div className="meta">
          {m.sender} · {new Date(m.date).toLocaleDateString()}
          {m.class && m.class !== "fyi" && <> · <span className={`pill ${m.class}`}>{m.class.replace("_", " ")}</span></>}
          {m.reason && <> · {m.reason}</>}
        </div>
      </div>
      <div className="acts">
        {BTN.map(([a, label, cls]) => (
          <button key={a} className={cls} onClick={() => onAct([m.message_id], a)}>{label}</button>
        ))}
        <a className="btn small" href={m.gmail_link} target="_blank" rel="noreferrer">Open in Gmail</a>
        {m.list_unsubscribe && <UnsubLink raw={m.list_unsubscribe} />}
      </div>
      {open && <Reader m={m} full={full} />}
    </div>
  );
}

function UnsubLink({ raw }) {
  const url = (raw.match(/<([^>]+)>/g) || []).map((s) => s.slice(1, -1)).find((u) => u.startsWith("http")) ||
    (raw.match(/<([^>]+)>/) || [])[1];
  if (!url) return null;
  return <a className="btn small" href={url} target="_blank" rel="noreferrer noopener" title="Opens the sender's unsubscribe page">Unsubscribe</a>;
}

export function ConfirmModal({ info, onCancel, onConfirm, action }) {
  return (
    <div className="modal" role="dialog" aria-modal="true">
      <div className="box">
        <h3 style={{ marginTop: 0 }}>{action === "trash" ? "Move to Trash" : `Apply “${action}”`}: {info.count} mails</h3>
        <p className="muted">First {info.first_subjects.length} subjects:</p>
        <ul>{info.first_subjects.map((s, i) => <li key={i}>{s || "(no subject)"}</li>)}</ul>
        {info.blocked
          ? <p className="err">Blocked: the selection contains urgent or needs-reply mail. Deselect it to continue.</p>
          : action === "trash" && <p className="muted">Mail goes to Trash and can be undone. Gmail frees the space after 30 days.</p>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button onClick={onCancel}>Cancel</button>
          <button className="primary" disabled={info.blocked} onClick={onConfirm}>Confirm</button>
        </div>
      </div>
    </div>
  );
}
