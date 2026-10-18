import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import * as d3 from "d3";

const H = 600;
const HOLO = ["#00e5ff", "#ffb300", "#ff3d71", "#7cff6b", "#b388ff", "#ff8a3d", "#29f0b3", "#4da6ff", "#ff5cf0", "#d4ff3d"];
export const holoColor = (i) => HOLO[i % HOLO.length];
const NOISE = "#6b8794";

const VERT = `
attribute float size; attribute float alpha; varying vec3 vColor; varying float vAlpha;
void main(){ vColor = color; vAlpha = alpha; vec4 mv = modelViewMatrix * vec4(position,1.0);
  gl_PointSize = size * (320.0 / -mv.z); gl_Position = projectionMatrix * mv; }`;
const FRAG = `
varying vec3 vColor; varying float vAlpha;
void main(){ float d = length(gl_PointCoord - 0.5); if (d > 0.5) discard;
  float core = smoothstep(0.5, 0.0, d); gl_FragColor = vec4(vColor * (0.6 + core), core * vAlpha); }`;

function labelSprite(text, color) {
  const c = document.createElement("canvas"); c.width = 1024; c.height = 96;
  const g = c.getContext("2d");
  g.font = "600 38px 'Share Tech Mono', ui-monospace, monospace"; g.textAlign = "center"; g.fillStyle = color;
  g.shadowColor = color; g.shadowBlur = 14; g.fillText(text.toUpperCase().slice(0, 40), 512, 56);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthTest: false }));
  s.scale.set(1.5, 0.14, 1);
  return s;
}

/**
 * Holographic 3D cluster view (three.js). Every mail is a glowing node at its stored 3D position; each topic is wrapped in a
 * rotating wireframe shell with a floating label. Drag = orbit, pinch/wheel = zoom, hover = subject, tap a mail = open, tap a ball = fly to it (tap again = go inside, tap empty space = back out), lasso = select.
 */
export default function Hologram({ points, topics, focus, selected, onSelect, onOpen, onDrill, far }) {
  const wrap = useRef(), mount = useRef(), overlay = useRef();
  const api = useRef({});
  const [tip, setTip] = useState(null);
  const [lasso, setLasso] = useState(false);
  const [spin, setSpin] = useState(true);
  const [flyTopic, setFlyTopic] = useState(null);   // the topic the camera has flown to (tap a ball)
  const stateRef = useRef({});
  stateRef.current = { points, focus, selected, lasso, onSelect, onOpen, onDrill, far, flyTopic };

  const topicIndex = useMemo(() => new Map(topics.map((t, i) => [t.topic_id, i])), [topics]);

  // ---- build the scene whenever the data changes ----
  useEffect(() => {
    const el = mount.current;
    if (!el || !points.length) return;
    setFlyTopic(null);
    const w = el.clientWidth;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(w, H);
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x03101a, 0.22);
    const camera = new THREE.PerspectiveCamera(55, w / H, 0.05, 50);
    camera.position.set(0.2, 0.7, stateRef.current.far ? 3.8 : 2.5);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.autoRotate = true; controls.autoRotateSpeed = 0.7; controls.maxDistance = 7; controls.minDistance = 0.8;
    // camera fly: glide the orbit target and camera together; any touch or wheel from the user cancels it
    let fly = null;
    const home = () => new THREE.Vector3(0.2, 0.7, stateRef.current.far ? 3.8 : 2.5);
    const flyTo = (target, dist) => {
      const dir = camera.position.clone().sub(controls.target).normalize();
      fly = { t: target.clone(), p: target.clone().add(dir.multiplyScalar(dist)) };
    };
    const flyHome = () => { fly = { t: new THREE.Vector3(), p: home() }; };
    controls.addEventListener("start", () => { fly = null; });

    // normalise coordinates into a unit-ish ball
    const n = points.length;
    const c = [0, 0, 0];
    points.forEach((p) => { c[0] += p.x; c[1] += p.y; c[2] += p.z || 0; });
    c.forEach((_, i) => (c[i] /= n));
    let maxR = 1e-6;
    points.forEach((p) => { maxR = Math.max(maxR, Math.hypot(p.x - c[0], p.y - c[1], (p.z || 0) - c[2])); });
    const k = 1.35 / maxR;
    const pos = new Float32Array(n * 3), col = new Float32Array(n * 3), size = new Float32Array(n), alpha = new Float32Array(n);
    const P = points.map((p, i) => {
      const v = new THREE.Vector3((p.x - c[0]) * k, (p.y - c[1]) * k, ((p.z || 0) - c[2]) * k);
      pos.set([v.x, v.y, v.z], i * 3);
      const ti = p.t == null ? -1 : topicIndex.get(p.t) ?? 0;
      const color = new THREE.Color(ti < 0 ? NOISE : holoColor(ti));
      col.set([color.r, color.g, color.b], i * 3);
      return v;
    });
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.setAttribute("size", new THREE.BufferAttribute(size, 1));
    geo.setAttribute("alpha", new THREE.BufferAttribute(alpha, 1));
    const cloud = new THREE.Points(geo, new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader: FRAG, vertexColors: true, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
    scene.add(cloud);

    // topic shells + labels
    const shells = [];
    const hits = [];   // invisible spheres: clicking an empty part of a topic goes inside it
    const byTopic = new Map();
    points.forEach((p, i) => { if (p.t != null) (byTopic.get(p.t) || byTopic.set(p.t, []).get(p.t)).push(i); });
    topics.forEach((t, ti) => {
      const idx = byTopic.get(t.topic_id);
      if (!idx || idx.length < 3) return;
      const ctr = idx.reduce((a, i) => a.add(P[i]), new THREE.Vector3()).divideScalar(idx.length);
      const ds = idx.map((i) => P[i].distanceTo(ctr)).sort((a, b) => a - b);
      const r = Math.max(0.17, ds[Math.floor(ds.length * 0.9)] + 0.09);
      const color = new THREE.Color(holoColor(ti));
      const g = new THREE.Group(); g.position.copy(ctr);
      const wire = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(r, 1)),
        new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending }));
      const ring = new THREE.Mesh(new THREE.RingGeometry(r * 1.12, r * 1.14, 64), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.5, side: THREE.DoubleSide, blending: THREE.AdditiveBlending }));
      ring.rotation.x = Math.PI / 2.4;
      const label = labelSprite(`${t.label} · ${t.count}`, holoColor(ti)); label.position.set(0, r + 0.12, 0);
      const hit = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 8), new THREE.MeshBasicMaterial({ visible: false, side: THREE.DoubleSide }));
      hit.userData.topic = t.topic_id;
      g.add(wire, ring, label, hit); scene.add(g); hits.push(hit);
      shells.push({ g, wire, ring, speed: 0.1 + (ti % 5) * 0.04, topic: t.topic_id, ctr, r });
    });

    // set dressing: floor grid, orbit rings, dust
    const grid = new THREE.GridHelper(7, 40, 0x00e5ff, 0x00e5ff); grid.position.y = -1.55;
    grid.material.transparent = true; grid.material.opacity = 0.12; scene.add(grid);
    const rings = [1.55, 1.8, 2.05].map((r, i) => {
      const m = new THREE.Mesh(new THREE.TorusGeometry(r, 0.004, 8, 160), new THREE.MeshBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: 0.22 - i * 0.05, blending: THREE.AdditiveBlending }));
      m.rotation.x = Math.PI / 2 + i * 0.35; m.rotation.y = i * 0.5; scene.add(m); return m;
    });
    const dust = new THREE.Points(new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array(Array.from({ length: 900 }, () => (Math.random() - 0.5) * 9)), 3)),
      new THREE.PointsMaterial({ color: 0x4dd0e1, size: 0.012, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending }));
    scene.add(dust);

    // picking
    const ray = new THREE.Raycaster(); ray.params.Points.threshold = 0.05;
    const mouse = new THREE.Vector2();
    let down = null;
    const rect = () => renderer.domElement.getBoundingClientRect();
    const pick = (e) => {
      const r = rect(); mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(mouse, camera);
      const hits = ray.intersectObject(cloud);
      return hits.length ? hits[0].index : -1;
    };
    const pickShell = (e) => {
      const r = rect(); mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(mouse, camera);
      const h = ray.intersectObjects(hits, false);
      return h.length ? h[0].object.userData.topic : null;
    };
    const dom = renderer.domElement;
    dom.addEventListener("pointermove", (e) => {
      if (stateRef.current.lasso) return;
      const i = pick(e);
      const r = rect();
      setTip(i >= 0 ? { p: points[i], left: e.clientX - r.left, top: e.clientY - r.top } : null);
      dom.style.cursor = i >= 0 ? "pointer" : stateRef.current.onDrill && pickShell(e) != null ? "zoom-in" : "grab";
    });
    dom.addEventListener("pointerdown", (e) => { down = [e.clientX, e.clientY]; });
    dom.addEventListener("pointerup", (e) => {
      if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 8 && !stateRef.current.lasso) {
        const i = pick(e);
        if (i >= 0) stateRef.current.onOpen(points[i].i);
        else {
          const tid = pickShell(e);
          const st = stateRef.current;
          if (tid == null) { if (st.flyTopic != null) { flyHome(); setFlyTopic(null); } }          // tap empty space: back out
          else if (tid === st.flyTopic && st.onDrill) st.onDrill(tid);                              // tap the flown-to ball again: go inside it
          else { const sh = shells.find((x) => x.topic === tid); if (sh) { flyTo(sh.ctr, Math.max(0.9, sh.r * 3.4)); setFlyTopic(tid); } }
        }
      }
      down = null;
    });
    dom.addEventListener("pointerleave", () => setTip(null));

    // loop
    const clock = new THREE.Clock(); let raf, last = performance.now();
    const loop = () => {
      const t = clock.getElapsedTime();
      const now = performance.now(), ease = 1 - Math.exp(-Math.min(0.5, (now - last) / 1000) * 5); last = now;   // flight speed independent of frame rate
      shells.forEach((s) => { s.wire.rotation.y = t * s.speed; s.wire.rotation.x = t * s.speed * 0.6; s.ring.rotation.z = t * s.speed * 2; });
      rings.forEach((m, i) => { m.rotation.z = t * (0.05 + i * 0.03) * (i % 2 ? -1 : 1); });
      dust.rotation.y = t * 0.01;
      if (fly) {
        controls.target.lerp(fly.t, ease); camera.position.lerp(fly.p, ease);
        if (controls.target.distanceTo(fly.t) < 0.005 && camera.position.distanceTo(fly.p) < 0.005) fly = null;
      }
      controls.update(); renderer.render(scene, camera); raf = requestAnimationFrame(loop);
    };
    loop();

    const ro = new ResizeObserver(() => { const nw = el.clientWidth; renderer.setSize(nw, H); camera.aspect = nw / H; camera.updateProjectionMatrix(); });
    ro.observe(el);

    api.current = { geo, P, camera, controls, renderer, size, alpha, n, flyHome };
    refreshAttrs();
    return () => {
      cancelAnimationFrame(raf); ro.disconnect(); controls.dispose();
      scene.traverse((o) => { o.geometry?.dispose?.(); o.material?.map?.dispose?.(); o.material?.dispose?.(); });
      renderer.dispose(); el.removeChild(renderer.domElement); api.current = {};
    };
    // eslint-disable-next-line
  }, [points, topics]);

  // ---- brightness / selection / focus ----
  function refreshAttrs() {
    const a = api.current; if (!a.geo) return;
    const { focus: f, selected: sel } = stateRef.current;
    stateRef.current.points.forEach((p, i) => {
      const dim = f != null && (f === 0 ? p.t != null : p.t !== f);
      const isSel = sel.has(p.i);
      a.alpha[i] = dim ? 0.08 : isSel ? 1 : 0.85;
      a.size[i] = isSel ? 0.15 : dim ? 0.045 : 0.09;
    });
    a.geo.attributes.alpha.needsUpdate = true; a.geo.attributes.size.needsUpdate = true;
  }
  useEffect(refreshAttrs, [focus, selected, points]);
  useEffect(() => { if (api.current.controls) api.current.controls.autoRotate = spin && !lasso; }, [spin, lasso, points]);
  useEffect(() => { if (api.current.controls) api.current.controls.enabled = !lasso; }, [lasso, points]);

  // ---- lasso: draw on an overlay, then project every node to the screen and test it against the outline ----
  const path = useRef(null);
  const o2d = (e) => { const b = overlay.current.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };
  const drawPath = () => {
    const c = overlay.current, g = c.getContext("2d"); g.clearRect(0, 0, c.width, c.height);
    if (path.current?.length > 1) {
      g.setLineDash([8, 5]); g.strokeStyle = "#00e5ff"; g.fillStyle = "rgba(0,229,255,.10)"; g.lineWidth = 2; g.shadowColor = "#00e5ff"; g.shadowBlur = 10;
      g.beginPath(); path.current.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath(); g.fill(); g.stroke();
    }
  };
  const onDown = (e) => { if (!lasso) return; overlay.current.setPointerCapture(e.pointerId); path.current = [o2d(e)]; };
  const onMove = (e) => { if (lasso && path.current) { path.current.push(o2d(e)); drawPath(); } };
  const onUp = () => {
    const poly = path.current; path.current = null;
    const a = api.current;
    if (lasso && poly && poly.length > 2 && a.camera) {
      const cw = overlay.current.width, v = new THREE.Vector3();
      const ids = [];
      stateRef.current.points.forEach((p, i) => {
        const f = stateRef.current.focus;
        if (f != null && (f === 0 ? p.t != null : p.t !== f)) return;
        v.copy(a.P[i]).project(a.camera);
        if (v.z > 1) return;
        if (d3.polygonContains(poly, [(v.x * 0.5 + 0.5) * cw, (-v.y * 0.5 + 0.5) * H])) ids.push(p.i);
      });
      stateRef.current.onSelect(new Set(ids));
    }
    drawPath();
  };
  useEffect(() => { // keep the overlay's pixel size equal to the stage
    const c = overlay.current; if (!c) return;
    const fit = () => { c.width = wrap.current.clientWidth; c.height = H; };
    fit(); const ro = new ResizeObserver(fit); ro.observe(wrap.current); return () => ro.disconnect();
  }, []);

  const noise = points.filter((p) => p.t == null).length;
  return (
    <div ref={wrap} className="holo" style={{ height: H }}>
      <div ref={mount} className="holo-stage" />
      <canvas ref={overlay} className="holo-overlay" style={{ pointerEvents: lasso ? "auto" : "none", cursor: "crosshair" }}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} />
      <i className="br tl" /><i className="br tr" /><i className="br bl" /><i className="br brr" />
      <div className="holo-readout">
        <div>NODES <b>{points.length}</b></div><div>CLUSTERS <b>{topics.length}</b></div>
        <div>UNCLASSIFIED <b>{noise}</b></div><div>SELECTED <b>{selected.size}</b></div>
      </div>
      <div className="holo-controls">
        <button className={lasso ? "primary" : ""} aria-pressed={lasso} onClick={() => setLasso(!lasso)}>{lasso ? "LASSO ARMED" : "Lasso"}</button>
        <button className={spin ? "on" : ""} onClick={() => setSpin(!spin)}>{spin ? "Spin: on" : "Spin: off"}</button>
        <button onClick={() => { api.current.flyHome?.(); setFlyTopic(null); }}>Recenter</button>
      </div>
      <div className="holo-hint">{flyTopic != null
        ? "DRAG ORBIT · PINCH ZOOM · TAP THE BALL AGAIN TO GO INSIDE · TAP EMPTY SPACE TO BACK OUT"
        : "DRAG ORBIT · PINCH OR WHEEL ZOOM · TAP A BALL TO FLY TO IT · TAP A MAIL TO OPEN"}</div>
      {tip && (
        <div className="tip holo-tip" style={{ left: Math.min(tip.left + 14, (wrap.current?.clientWidth || 900) - 250), top: tip.top + 14 }}>
          <b>{tip.p.s || "(no subject)"}</b><br /><span>{tip.p.f}</span>
        </div>
      )}
    </div>
  );
}
