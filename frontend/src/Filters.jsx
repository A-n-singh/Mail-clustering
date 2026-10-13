import React from "react";

export const EMPTY = { date_from: "", date_to: "", sender: "", label: "", min_score: 0 };
const LABELS = ["", "INBOX", "UNREAD", "STARRED", "IMPORTANT", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES"];

export default function Filters({ value, onChange }) {
  const set = (k) => (e) => onChange({ ...value, [k]: k === "min_score" ? +e.target.value : e.target.value });
  return (
    <div className="filters">
      <label>From <input type="date" value={value.date_from} onChange={set("date_from")} /></label>
      <label>To <input type="date" value={value.date_to} onChange={set("date_to")} /></label>
      <input placeholder="Sender…" value={value.sender} onChange={set("sender")} aria-label="Sender" />
      <select value={value.label} onChange={set("label")} aria-label="Label">
        {LABELS.map((l) => <option key={l} value={l}>{l || "Any label"}</option>)}
      </select>
      <label>Min importance {value.min_score}
        <input type="range" min="0" max="100" step="5" value={value.min_score} onChange={set("min_score")} />
      </label>
      <button onClick={() => onChange(EMPTY)}>Clear</button>
    </div>
  );
}
