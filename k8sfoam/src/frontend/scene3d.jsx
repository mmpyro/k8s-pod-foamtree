// Isometric cube scene on WebGL (three.js). Each node is a plate, each pod a
// cube on it: the cube's footprint encodes CPU, its height encodes Memory.
//
// Pods and plates are a few InstancedMeshes, so the whole cluster is a handful
// of draw calls; the CSS 3D scene this replaces built ~5 DOM nodes per pod and
// stalled for seconds on every hover at a few thousand pods. Frames are drawn
// on demand (camera move, data or style change), never in an idle loop.
//
// Colours are the CSS scene's, unchanged: unlit faces coloured like its cube
// faces, its plate and label styling, its warning hatch, and its CSS filter
// values for dim / match / workload states, applied here in the same sRGB math.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const { workloadKey } = window.k8sWorkload;
const { findingInfo } = window.k8sPodAudit;
const { worstSeverity } = window.k8sNodeStatus;
const { qosHue } = window.k8sQos;

const PLATE = 160;
const PLATE_GAP = 28;

const clamp = (lo, v, hi) => Math.max(lo, Math.min(hi, v));
// The CSS scene's cubeDims(), in its 250px-plate pixels, scaled to this plate.
// Both sqrt-scaled so a pod ten times larger reads as noticeably bigger
// without dwarfing the plate; sized from the pod's effective request.
const CSS_PX = PLATE / 250;
const footprint = cpu => clamp(18, 10 + Math.sqrt(cpu) * 1.05, 48) * CSS_PX;
const tall = memMib => clamp(8, Math.sqrt(memMib) * 1.05, 78) * CSS_PX;
// rotateX(55deg) rotateZ(45deg) in the CSS scene is a 35° view elevation.
const CAMERA = [1, Math.tan((35 * Math.PI) / 180) * Math.SQRT2, 1];

function utilColor(u) {
  return u > 0.85 ? "#ef4444" : u > 0.6 ? "#f59e0b" : u > 0.3 ? "#10b981" : "#60a5fa";
}

// Palette tokens from styles.css, so the scene follows the stylesheet.
function token(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// sRGB triples in 0..1 — CSS does its colour math in sRGB, so this does too.
function hsl(h, s, l) {
  s /= 100; l /= 100;
  const k = n => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
  const f = n => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
  return [f(0), f(8), f(4)];
}
function hex(h) {
  const n = parseInt(h.replace("#", ""), 16);
  return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
}
const mix = (under, over, alpha) => under.map((u, i) => u + (over[i] - u) * alpha);

// CSS brightness() then saturate() (Filter Effects spec matrices), clamped
// after each step like the browser does.
function cssFilter([r, g, b], brightness, saturate) {
  [r, g, b] = [r, g, b].map(v => clamp(0, v * brightness, 1));
  const s = saturate;
  return [
    (0.213 + 0.787 * s) * r + (0.715 - 0.715 * s) * g + (0.072 - 0.072 * s) * b,
    (0.213 - 0.213 * s) * r + (0.715 + 0.285 * s) * g + (0.072 - 0.072 * s) * b,
    (0.213 - 0.213 * s) * r + (0.715 - 0.715 * s) * g + (0.072 + 0.928 * s) * b,
  ].map(v => clamp(0, v, 1));
}

// [brightness, saturate, opacity] per pod, mirroring the CSS cascade:
// query scope wins over workload state, and workload state wins over a match.
function podFilter(cube, look) {
  const outOfScope = look.plateDim(cube.node) || (look.queryActive && !look.matched(cube));
  if (outOfScope) return look.highlight ? [0.45, 0.3, 0.12] : [1, 1, 0.12];
  if (look.highlight) {
    const peer = cube.wl === look.highlight;
    if (look.pinned) return peer ? [1.4, 1.2, 1] : [0.45, 0.3, 0.38];
    return peer ? [1.2, 1, 1] : [0.7, 0.6, 0.7];
  }
  return look.queryActive ? [1.4, 1.3, 1] : [1, 1, 1];
}

// The plate is hsla(h,80%,6%,.92) over the page background; the faces fade
// over that composite, which is what opacity does in the CSS scene.
const plateSurface = (bg, h) => mix(bg, hsl(h, 80, 6), 0.92);
const FACES = { top: [96, 58], x: [88, 24], z: [88, 15] }; // CSS .cube-top / -e / -s

function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

// Plates on a near-square grid, pods on a grid inside each plate, biggest
// footprint first. Pure: positions only, no three.js.
function layout(nodes) {
  const cols = Math.max(1, Math.ceil(Math.sqrt(nodes.length)));
  const rows = Math.max(1, Math.ceil(nodes.length / cols));
  const plates = [], cubes = [];
  nodes.forEach((node, idx) => {
    const x = (idx % cols) * (PLATE + PLATE_GAP);
    const z = Math.floor(idx / cols) * (PLATE + PLATE_GAP);
    plates.push({ node, idx, x, z });
    const pods = [...node.pods].sort((a, b) => b.cpu - a.cpu);
    const per = Math.max(1, Math.ceil(Math.sqrt(pods.length)));
    const cell = PLATE / per;
    pods.forEach((pod, j) => {
      cubes.push({
        pod, node, nodeIdx: idx, wl: workloadKey(pod.name),
        w: Math.min(cell * 0.86, footprint(pod.cpu)), h: tall(pod.mem),
        x: x - PLATE / 2 + cell * ((j % per) + 0.5),
        z: z - PLATE / 2 + cell * (Math.floor(j / per) + 0.5),
      });
    });
  });
  const width = cols * (PLATE + PLATE_GAP) - PLATE_GAP;
  const depth = rows * (PLATE + PLATE_GAP) - PLATE_GAP;
  return { plates, cubes, center: { x: (width - PLATE) / 2, z: (depth - PLATE) / 2 }, size: Math.hypot(width, depth) };
}

// Some faces of a unit box standing on y=0, as their own geometry, so each
// face kind gets exactly its CSS colour (BoxGeometry face order: +x -x +y -y +z -z).
function boxFaces(faces) {
  const src = new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0).toNonIndexed();
  const pos = src.getAttribute("position").array, out = [];
  for (const f of faces) out.push(...pos.slice(f * 18, f * 18 + 18));
  src.dispose();
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(out, 3));
  return g;
}

function patternTexture(draw, repeat) {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  draw(c.getContext("2d"));
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeat, repeat);
  return tex;
}

// The CSS plate-label: dark chip, hue border and dot, dim lowercase name,
// utilisation in its traffic-light colour, a severity mark when unhealthy.
function labelSprite({ name, util, hue, sev }, height) {
  const px = 44, pad = 18, measure = document.createElement("canvas").getContext("2d");
  const font = weight => `${weight} ${px}px ${token("--font-mono", "ui-monospace, monospace")}`;
  const utilText = `${Math.round(util * 100)}%`;
  measure.font = font(400);
  const nameW = measure.measureText(name).width;
  measure.font = font(600);
  const utilW = measure.measureText(utilText).width;
  const markW = sev ? px * 0.6 : 0;
  const dotW = px * 0.5 + pad * 0.6;
  const c = document.createElement("canvas");
  c.width = Math.ceil(pad + dotW + nameW + pad + utilW + (sev ? pad * 0.6 + markW : 0) + pad);
  c.height = px + 26;
  const g = c.getContext("2d"), mid = c.height / 2;
  g.fillStyle = "rgba(4,6,12,.92)";
  g.fillRect(0, 0, c.width, c.height);
  g.strokeStyle = `hsla(${hue}, 100%, 62%, .4)`;
  g.lineWidth = 3;
  g.strokeRect(1.5, 1.5, c.width - 3, c.height - 3);
  let x = pad;
  g.textBaseline = "middle";
  g.fillStyle = `hsl(${hue} 90% 62%)`;
  g.beginPath();
  g.arc(x + px * 0.25, mid, px * 0.14, 0, Math.PI * 2);
  g.fill();
  x += dotW;
  g.font = font(400);
  g.fillStyle = token("--text-dim", "#9aa3b8");
  g.fillText(name, x, mid);
  x += nameW + pad;
  g.font = font(600);
  g.fillStyle = utilColor(util);
  g.fillText(utilText, x, mid);
  x += utilW;
  if (sev) {
    x += pad * 0.6;
    g.fillStyle = token({ danger: "--danger", warn: "--warn", info: "--info" }[sev], "#f59e0b");
    g.beginPath();
    g.moveTo(x + markW / 2, mid - markW / 2);
    g.lineTo(x + markW, mid + markW / 2);
    g.lineTo(x, mid + markW / 2);
    g.fill();
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  s.scale.set((c.width / c.height) * height, height, 1);
  s.renderOrder = 10;
  return s;
}

function Scene3D({
  nodes, match, zoom, hueOf, colorBy, memUnit, fmtMem, onFocus,
  highlight, highlightActive, onPodSelect, onPodHover,
}) {
  const hostRef = React.useRef(null);
  const world = React.useRef(null);
  const [tip, setTip] = React.useState(null);
  const [ok] = React.useState(webglAvailable);

  // hueOf is a fresh closure on every parent render; key the effects on the
  // hues it yields instead, so only a colour-scheme change recolours.
  const hueKey = nodes.map((_, i) => hueOf(i)).join();

  // Handlers read the latest props without re-binding pointer listeners.
  const props = React.useRef({});
  props.current = { onFocus, onPodSelect, onPodHover };

  // One renderer, camera, controls and shared textures for the component's lifetime.
  React.useEffect(() => {
    if (!ok) return;
    const host = hostRef.current;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    host.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const root = new THREE.Group();
    scene.add(root);
    const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, -20000, 20000);
    cam.position.set(...CAMERA).multiplyScalar(4000);
    const controls = new OrbitControls(cam, renderer.domElement);
    controls.maxPolarAngle = Math.PI * 0.47;

    // The CSS plate's 26px tech grid and its 6px-on/6px-off warning hatch,
    // scaled from the 250px CSS plate. The hatch is white, tinted per plate
    // with the severity colour; its two bands are .24 and .06 alpha.
    const grid = patternTexture(g => {
      g.fillStyle = "rgba(255,255,255,.05)";
      g.fillRect(0, 0, 64, 3);
      g.fillRect(0, 0, 3, 64);
    }, 250 / 26);
    const hatch = patternTexture(g => {
      g.fillStyle = "rgba(255,255,255,.06)";
      g.fillRect(0, 0, 64, 32);
      g.fillStyle = "rgba(255,255,255,.24)";
      g.fillRect(0, 32, 64, 32);
    }, 250 / 12);

    const w = {
      renderer, scene, root, cam, controls, grid, hatch, cubes: [], plates: [], labels: [], meshes: null,
      fit: 1, fittedFor: -1, bg: hex(token("--bg", "#07080c")),
    };
    let frame = 0;
    w.render = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        renderer.render(scene, cam);
      });
    };
    controls.addEventListener("change", w.render);

    const resize = () => {
      const { clientWidth: cw, clientHeight: ch } = host;
      renderer.setSize(cw, ch);
      Object.assign(cam, { left: -cw / 2, right: cw / 2, top: ch / 2, bottom: -ch / 2 });
      cam.updateProjectionMatrix();
      w.render();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(host);

    // Picking: pod faces first, then plates.
    const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
    const pick = e => {
      if (!w.meshes) return null;
      const r = renderer.domElement.getBoundingClientRect();
      ptr.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ptr, cam);
      const { top, x, z, plates } = w.meshes;
      const hit = ray.intersectObjects([top, x, z, plates], false)[0];
      if (!hit) return null;
      if (hit.object === plates) return { node: w.plates[hit.instanceId].node };
      return { pod: w.cubes[hit.instanceId].pod };
    };
    // Hover drives both the tooltip and the peer preview, at most once a frame.
    let hoverFrame = 0, down = null;
    const onMove = e => {
      if (hoverFrame) return;
      hoverFrame = requestAnimationFrame(() => {
        hoverFrame = 0;
        const hit = pick(e);
        setTip(hit ? { ...hit, x: e.clientX, y: e.clientY } : null);
        props.current.onPodHover(hit && hit.pod ? workloadKey(hit.pod.name) : null);
      });
    };
    const onLeave = () => { setTip(null); props.current.onPodHover(null); };
    const onDown = e => { down = { x: e.clientX, y: e.clientY }; };
    // A drag orbits the camera; only a still click selects.
    const onUp = e => {
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return;
      const hit = pick(e);
      if (hit && hit.pod) props.current.onPodSelect(workloadKey(hit.pod.name));
      else if (hit && hit.node) props.current.onFocus(hit.node);
    };
    const el = renderer.domElement;
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", onLeave);
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);

    world.current = w;
    resize();
    return () => {
      ro.disconnect();
      cancelAnimationFrame(frame);
      cancelAnimationFrame(hoverFrame);
      controls.dispose();
      w.dispose && w.dispose();
      grid.dispose();
      hatch.dispose();
      renderer.dispose();
      el.remove();
      world.current = null;
    };
  }, [ok]);

  // Rebuild geometry when the data changes.
  React.useEffect(() => {
    const w = world.current;
    if (!w) return;
    const { root } = w;
    const next = layout(nodes);

    w.dispose && w.dispose();
    const n = Math.max(1, next.cubes.length), np = Math.max(1, next.plates.length);
    const unlit = () => new THREE.MeshBasicMaterial();
    const top = new THREE.InstancedMesh(boxFaces([2]), unlit(), n);
    const x = new THREE.InstancedMesh(boxFaces([0, 1]), unlit(), n);
    const z = new THREE.InstancedMesh(boxFaces([4, 5]), unlit(), n);
    const rims = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), unlit(), np);
    const plates = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), unlit(), np);
    const flat = () => new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2);
    const overlay = map => new THREE.MeshBasicMaterial({ map, transparent: true, depthWrite: false });
    const grids = new THREE.InstancedMesh(flat(), overlay(w.grid), np);
    const hatches = new THREE.InstancedMesh(flat(), overlay(w.hatch), np);
    for (const mesh of [top, x, z]) mesh.count = next.cubes.length;
    for (const mesh of [rims, plates, grids, hatches]) mesh.count = next.plates.length;
    const m = new THREE.Matrix4();
    next.plates.forEach((p, i) => {
      // The 1px CSS border as a slightly larger, lower slab around the plate.
      m.makeScale(PLATE + 2, 2, PLATE + 2).setPosition(p.x, -1.1, p.z);
      rims.setMatrixAt(i, m);
      m.makeScale(PLATE, 2, PLATE).setPosition(p.x, -1, p.z);
      plates.setMatrixAt(i, m);
    });
    next.cubes.forEach((c, i) => {
      m.makeScale(c.w, c.h, c.w).setPosition(c.x, 0, c.z);
      top.setMatrixAt(i, m);
      x.setMatrixAt(i, m);
      z.setMatrixAt(i, m);
    });
    root.add(rims, plates, grids, hatches, z, x, top);

    const labels = next.plates.map(p => {
      const util = Math.max(p.node.cpuUsed / (p.node.cpuCapacity || 1), p.node.memUsed / (p.node.memCapacity || 1));
      const s = labelSprite({
        name: p.node.name.replace(/\.ec2\.internal$/, "").toLowerCase(), util,
        hue: hueOf(p.idx), sev: worstSeverity(p.node.warnings),
      }, 11);
      s.position.set(p.x, 6, p.z - PLATE / 2 - 10);
      return s;
    });
    if (labels.length) root.add(...labels);
    root.position.set(-next.center.x, 0, -next.center.z);

    Object.assign(w, { cubes: next.cubes, plates: next.plates, labels, meshes: { top, x, z, rims, plates, grids, hatches } });
    w.dispose = () => {
      const meshes = [rims, plates, grids, hatches, z, x, top];
      root.remove(...meshes, ...labels);
      for (const o of meshes) { o.geometry.dispose(); o.material.dispose(); o.dispose(); }
      for (const s of labels) { s.material.map.dispose(); s.material.dispose(); }
    };

    // Re-fit the camera only when the node count changed, so a refresh never
    // throws away the user's orbit.
    if (nodes.length !== w.fittedFor) {
      w.fittedFor = nodes.length;
      const host = hostRef.current;
      w.fit = Math.min(host.clientWidth, host.clientHeight) / (next.size * 1.05);
      w.controls.target.set(0, 0, 0);
      w.cam.position.set(...CAMERA).multiplyScalar(4000);
      w.cam.zoom = w.fit * zoom;
      w.cam.updateProjectionMatrix();
      w.controls.update();
    }
    w.recolor && w.recolor();
    w.render();
  }, [nodes, hueKey]);

  // Colour-only updates (query, workload highlight) never touch geometry.
  React.useEffect(() => {
    const w = world.current;
    if (!w) return;
    const queryActive = !!(match && match.active);
    const look = {
      highlight, queryActive, pinned: highlightActive,
      matched: c => match.pods.has(c.pod),
      plateDim: n => queryActive && match.dimNodes.has(n.name),
    };
    const sevColor = {
      danger: hex(token("--danger", "#ef4444")), warn: hex(token("--warn", "#f59e0b")), info: hex(token("--info", "#60a5fa")),
    };
    w.recolor = () => {
      if (!w.meshes) return;
      const c = new THREE.Color(), m = new THREE.Matrix4();
      const set = (mesh, i, rgb) => mesh.setColorAt(i, c.setRGB(rgb[0], rgb[1], rgb[2], THREE.SRGBColorSpace));
      // A pod's colours depend only on its node hue and one of six filter
      // states, so compute each combination once, not once per pod.
      const faces = new Map();
      w.cubes.forEach((cube, i) => {
        const h = colorBy === "qos" ? qosHue(cube.pod.qos) : hueOf(cube.nodeIdx);
        const f = podFilter(cube, look), key = `${h}|${f}`;
        if (!faces.has(key)) {
          const under = plateSurface(w.bg, h);
          faces.set(key, ["top", "x", "z"].map(face => mix(under, cssFilter(hsl(h, ...FACES[face]), f[0], f[1]), f[2])));
        }
        const [t, fx, fz] = faces.get(key);
        set(w.meshes.top, i, t);
        set(w.meshes.x, i, fx);
        set(w.meshes.z, i, fz);
      });
      w.plates.forEach((p, i) => {
        const h = hueOf(p.idx), dim = look.plateDim(p.node), sev = worstSeverity(p.node.warnings);
        set(w.meshes.plates, i, plateSurface(w.bg, h));
        set(w.meshes.rims, i, mix(w.bg, hsl(h, 100, 62), 0.5));
        // A plate the query ruled out drops its inlay, warning hatch included.
        m.makeScale(dim ? 0 : PLATE, 1, dim ? 0 : PLATE).setPosition(p.x, 0.05, p.z);
        w.meshes.grids.setMatrixAt(i, m);
        set(w.meshes.grids, i, [1, 1, 1]);
        m.makeScale(dim || !sev ? 0 : PLATE, 1, dim || !sev ? 0 : PLATE).setPosition(p.x, 0.1, p.z);
        w.meshes.hatches.setMatrixAt(i, m);
        set(w.meshes.hatches, i, sev ? sevColor[sev] : [0, 0, 0]);
        w.labels[i].material.opacity = dim ? 0.38 : 1;
      });
      for (const mesh of Object.values(w.meshes)) if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      w.meshes.grids.instanceMatrix.needsUpdate = w.meshes.hatches.instanceMatrix.needsUpdate = true;
    };
    w.recolor();
    w.render();
  }, [nodes, hueKey, colorBy, match, highlight, highlightActive]);

  React.useEffect(() => {
    const w = world.current;
    if (!w) return;
    w.cam.zoom = w.fit * zoom;
    w.cam.updateProjectionMatrix();
    w.render();
  }, [zoom]);

  if (!ok) {
    return (
      <div className="scene-3d scene-nogl">
        <p>The 3D view needs WebGL, which this browser has turned off or does not support. The 2D map shows the same data.</p>
      </div>
    );
  }
  return (
    <div className="scene-3d">
      <div className="scene-gl" ref={hostRef} />
      <SceneLegend colorBy={colorBy} />
      <SceneTooltip tip={tip} memUnit={memUnit} fmtMem={fmtMem} />
    </div>
  );
}

// Annotated reference cube. Abstract swatches are not enough here — without the
// brackets there is no way to tell which axis carries which resource.
function SceneLegend({ colorBy }) {
  return (
    <div className="scene-legend">
      <svg viewBox="0 0 168 128" width="150" height="114" fill="none">
        {/* top face */}
        <polygon points="60,30 86,45 60,60 34,45"
          fill="#7c5cff" fillOpacity=".85" stroke="#c4b5fd" strokeWidth="1" />
        {/* south face */}
        <polygon points="34,45 60,60 60,90 34,75"
          fill="#1e1b3a" stroke="#8b7bd8" strokeWidth="1" />
        {/* east face */}
        <polygon points="86,45 60,60 60,90 86,75"
          fill="#2a2450" stroke="#8b7bd8" strokeWidth="1" />

        {/* footprint bracket → CPU */}
        <path d="M34 100 V106 M86 100 V106 M34 103 H86"
          stroke="#9aa3b8" strokeWidth="1" strokeLinecap="round" />
        <text x="60" y="120" fill="#9aa3b8" fontSize="10" fontFamily="monospace"
          textAnchor="middle" letterSpacing="1">CPU</text>

        {/* height bracket → MEM */}
        <path d="M96 45 H102 M96 75 H102 M99 45 V75"
          stroke="#9aa3b8" strokeWidth="1" strokeLinecap="round" />
        <text x="108" y="63" fill="#9aa3b8" fontSize="10" fontFamily="monospace"
          letterSpacing="1">MEM</text>
      </svg>
      <div className="legend-lines">
        <div>Width × depth → CPU request</div>
        <div>Height → Memory request</div>
        <div className="legend-foot">One cube per pod · color = {colorBy === "qos" ? "QoS class" : "node"} · drag to orbit</div>
      </div>
    </div>
  );
}

function SceneTooltip({ tip, memUnit, fmtMem }) {
  if (!tip) return null;
  const style = { left: tip.x + 14, top: tip.y + 14 };
  // Plates have no hover highlight here, so they get a tooltip instead.
  if (tip.node) {
    const n = tip.node;
    return (
      <div className="cube-tip" style={style}>
        <div className="cube-tip-name">{n.name}</div>
        <div className="cube-tip-row"><span>{(n.cpuUsed / 1000).toFixed(1)} / {(n.cpuCapacity / 1000).toFixed(0)}</span> cores</div>
        <div className="cube-tip-row"><span>{fmtMem(n.memUsed, memUnit)} / {fmtMem(n.memCapacity, memUnit, true)}</span> {memUnit}</div>
        <div className="cube-tip-foot">{n.pods.length} pods · click for details</div>
      </div>
    );
  }
  const p = tip.pod;
  return (
    <div className="cube-tip" style={style}>
      <div className="cube-tip-name">{p.name}</div>
      <div className="cube-tip-row">
        <span>{(p.cpu / 1000).toFixed(2)}</span> cores
      </div>
      <div className="cube-tip-row">
        <span>{fmtMem(p.mem, memUnit)}</span> {memUnit}
      </div>
      <div className="cube-tip-foot">
        {p.containers.length} container{p.containers.length === 1 ? "" : "s"}
        {p.qos && ` · ${p.qos}`}
      </div>
      {p.findings.length > 0 && (
        <div className="cube-tip-audit">
          {p.findings.map(f => (
            <div key={f} className={`sev-${findingInfo(f).sev}`}>{findingInfo(f).label}</div>
          ))}
        </div>
      )}
    </div>
  );
}

window.k8sScene3D = { Scene3D };
