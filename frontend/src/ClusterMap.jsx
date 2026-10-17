import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";

export const GREY = "#9a9a93";
export const topicColor = (index, total) => d3.interpolateRainbow(total ? (index * 0.61803) % 1 : 0); // golden-ratio spacing keeps neighbours distinct

const H = 560;

/**
 * 2D map of all mail (stored UMAP coordinates). Zoom/pan with wheel and drag, hover = subject + sender,
 * click = open the mail, lasso mode = draw around dots to select them. Drawn on a <canvas> so 50k dots stay smooth.
 */
export default function ClusterMap({ points, topics, focus, selected, onSelect, onOpen }) {
  const wrap = useRef(), canvas = useRef();
  const [size, setSize] = useState({ w: 900 });
  const [tip, setTip] = useState(null);
  const [lasso, setLasso] = useState(false);
  const tf = useRef(d3.zoomIdentity);
  const poly = useRef(null);
  const [tick, setTick] = useState(0);
  const redraw = () => setTick((t) => t + 1);

  const colorOf = useMemo(() => {
    const idx = new Map(topics.map((t, i) => [t.topic_id, i]));
    return (t) => (t == null ? GREY : topicColor(idx.get(t) ?? 0, topics.length));
  }, [topics]);

  // scale stored coordinates into the canvas once
  const scaled = useMemo(() => {
    if (!points.length) return { pts: [], tree: null };
    const xe = d3.extent(points, (p) => p.x), ye = d3.extent(points, (p) => p.y);
    const pad = 24, w = size.w;
    const sx = d3.scaleLinear(xe, [pad, w - pad]), sy = d3.scaleLinear(ye, [H - pad, pad]);
    const pts = points.map((p) => ({ ...p, px: sx(p.x), py: sy(p.y) }));
    return { pts, tree: d3.quadtree().x((d) => d.px).y((d) => d.py).addAll(pts) };
  }, [points, size.w]);

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.max(320, Math.floor(e.contentRect.width)) }));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  // draw
  useEffect(() => {
    const c = canvas.current, dpr = window.devicePixelRatio || 1;
    c.width = size.w * dpr; c.height = H * dpr;
    const g = c.getContext("2d");
    g.scale(dpr, dpr);
    g.clearRect(0, 0, size.w, H);
    const { x, y, k } = tf.current;
    const r = Math.max(1.6, 2.6 * Math.sqrt(k));
    for (const p of scaled.pts) {
      const dim = focus != null && (focus === 0 ? p.t != null : p.t !== focus);
      g.globalAlpha = dim ? 0.1 : 0.85;
      g.fillStyle = colorOf(p.t);
      g.beginPath();
      g.arc(p.px * k + x, p.py * k + y, r, 0, 6.2832);
      g.fill();
    }
    g.globalAlpha = 1;
    g.lineWidth = 1.2; g.strokeStyle = "#111";
    for (const p of scaled.pts) {
      if (!selected.has(p.i)) continue;
      g.fillStyle = colorOf(p.t);
      g.beginPath(); g.arc(p.px * k + x, p.py * k + y, r + 1.5, 0, 6.2832); g.fill(); g.stroke();
    }
    if (poly.current?.length > 1) {
      g.setLineDash([6, 4]); g.strokeStyle = "#2f6fed"; g.fillStyle = "rgba(47,111,237,.12)";
      g.beginPath(); poly.current.forEach(([a, b], i) => (i ? g.lineTo(a, b) : g.moveTo(a, b))); g.closePath(); g.fill(); g.stroke();
      g.setLineDash([]);
    }
  }, [scaled, size.w, focus, selected, colorOf, tick]);

  // zoom / pan (off while lassoing)
  useEffect(() => {
    const sel = d3.select(canvas.current);
    const z = d3.zoom().scaleExtent([1, 40]).filter((e) => !lasso && (e.type === "wheel" || e.type === "dblclick" || e.button === 0))
      .on("zoom", (e) => { tf.current = e.transform; redraw(); });
    sel.call(z);
    return () => sel.on(".zoom", null);
  }, [lasso]);

  const local = (e) => { const b = canvas.current.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };
  const nearest = (e) => {
    if (!scaled.tree) return null;
    const [mx, my] = local(e), { x, y, k } = tf.current;
    return scaled.tree.find((mx - x) / k, (my - y) / k, 9 / k) || null;
  };

  const onMove = (e) => {
    if (lasso) { if (poly.current) { poly.current.push(local(e)); redraw(); } return; }
    const p = nearest(e);
    setTip(p ? { p, left: e.clientX - wrap.current.getBoundingClientRect().left, top: e.clientY - wrap.current.getBoundingClientRect().top } : null);
  };
  const onDown = (e) => { if (lasso) { canvas.current.setPointerCapture(e.pointerId); poly.current = [local(e)]; } };
  const onUp = (e) => {
    if (lasso && poly.current) {
      const path = poly.current; poly.current = null;
      if (path.length > 2) {
        const { x, y, k } = tf.current;
        onSelect(new Set(scaled.pts.filter((p) => (focus == null || (focus === 0 ? p.t == null : p.t === focus)) && d3.polygonContains(path, [p.px * k + x, p.py * k + y])).map((p) => p.i)));
      }
      redraw();
    }
  };
  const onClick = (e) => { if (!lasso) { const p = nearest(e); if (p) onOpen(p.i); } };

  return (
    <div ref={wrap} style={{ position: "relative" }}>
      <div className="filters">
        <button className={lasso ? "primary" : ""} onClick={() => setLasso(!lasso)} aria-pressed={lasso}>
          {lasso ? "Lasso on: drag around dots" : "Lasso select"}
        </button>
        <button onClick={() => { tf.current = d3.zoomIdentity; d3.select(canvas.current).call(d3.zoom().transform, d3.zoomIdentity); redraw(); }}>Reset view</button>
        <span className="muted">wheel = zoom · drag = pan · hover = subject · click = open</span>
      </div>
      <canvas ref={canvas} style={{ width: "100%", height: H, touchAction: "none", cursor: lasso ? "crosshair" : tip ? "pointer" : "grab", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8 }}
        onPointerMove={onMove} onPointerDown={onDown} onPointerUp={onUp} onClick={onClick} onPointerLeave={() => setTip(null)} role="img" aria-label="Map of all mail by topic" />
      {tip && (
        <div className="tip" style={{ left: Math.min(tip.left + 12, size.w - 240), top: tip.top + 52 }}>
          <b>{tip.p.s || "(no subject)"}</b><br /><span className="muted">{tip.p.f}</span>
        </div>
      )}
    </div>
  );
}
