// Isometric cube scene on WebGL (three.js). Each node is a plate, each pod a
// cube on it: the cube's footprint encodes CPU, its height encodes Memory.
//
// Pods and plates are a few InstancedMeshes, so the whole cluster is a handful
// of draw calls; the CSS 3D scene this replaces built ~5 DOM nodes per pod and
// stalled for seconds on every hover at a few thousand pods. Frames are drawn
// on demand (camera move, data or style change), never in an idle loop.
//
// Colours are the CSS scene's, unchanged: unlit faces coloured like its cube
// faces with their lit edges and inset glow, the cubes' cast haze and top-face
// halo, its plate and label styling with their glows, its warning hatch, and
// its CSS filter values for dim / match / workload states, applied here in the
// same sRGB math.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const { workloadKey } = window.k8sWorkload;
const { findingInfo } = window.k8sPodAudit;
const { worstSeverity } = window.k8sNodeStatus;
const { qosHue } = window.k8sQos;
const { resourceMeta, metricValue, nodeCap, nodeUsed, fmtValue, unitOf } = window.k8sResources;

const isExtended = metric => !!metric && metric !== "cpu" && metric !== "mem";

// Cubes always plot CPU × Memory, so an extended resource is shown as a scope:
// pods requesting it stay lit and everything else dims, through the same
// filter path the query uses. A live query still narrows the lit set.
function extendedScope(match, nodes, metric) {
  if (!isExtended(metric)) return match;
  const queryActive = !!(match && match.active);
  const pods = new Set();
  const dimNodes = new Set(queryActive ? match.dimNodes : []);
  for (const n of nodes) {
    if (nodeCap(n, metric) <= 0) dimNodes.add(n.name);
    for (const p of n.pods) {
      if (metricValue(p, metric) > 0 && (!queryActive || match.pods.has(p))) pods.add(p);
    }
  }
  return { ...match, active: true, pods, dimNodes };
}

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

// The CSS faces' box-shadows: a 1px inset edge in a lighter tint of the hue
// on every face, and a soft inset glow on the two sides. Values are
// [saturation, lightness, alpha] of the hsla() colours in the CSS scene.
const EDGES = { top: [100, 78, 0.95], x: [100, 72, 0.55], z: [100, 72, 0.42] };
// Alphas are below the CSS values: the CSS glows were clipped by 3D sorting
// in ways a WebGL scene does not reproduce, so at full strength they flood it.
const SIDE_GLOW = [100, 60, 0.3];
const GLOW = { cubeShadow: 0.16, halo: 0.3, plateOuter: 0.08, plateInset: 0.04, rim: 0.35 };

// The CSS blur lengths, in its px, turned into Gaussian sigmas in world units.
// A box-shadow blur radius is two sigmas; a blur() filter length is one.
const SIGMA = {
  cubeShadow: 6 * CSS_PX,   // .cube-shadow { filter: blur(6px) }
  topGlow: 8 * CSS_PX,      // cube-top box-shadow 0 0 16px
  sideGlow: 10 * CSS_PX,    // cube-s/-e inset ... 20px -12px
  plateGlow: 17 * CSS_PX,   // plate box-shadow 0 0 34px
  plateInset: 23 * CSS_PX,  // plate box-shadow inset 0 0 46px
};

// Unlit cube faces coloured per instance in sRGB, which is where CSS mixes
// colours: written straight to the output, no linear round trip. FACE picks
// the face kind: 0 top, 1 the ±x (east) sides, 2 the ±z (south) sides.
const FACE_VERT = `
attribute vec3 aBase;
attribute vec3 aEdge;
attribute vec3 aGlow;
varying vec3 vBase;
varying vec3 vEdge;
varying vec3 vGlow;
varying vec2 vUv;
varying vec2 vSize;
void main() {
  vBase = aBase; vEdge = aEdge; vGlow = aGlow;
  vec3 s = vec3(length(instanceMatrix[0].xyz), length(instanceMatrix[1].xyz), length(instanceMatrix[2].xyz));
  // Face-local coordinates in world units: (x, z) on top, (across, up) on a side.
#if FACE == 0
  vUv = (position.xz + 0.5) * s.xz; vSize = s.xz;
#elif FACE == 1
  vUv = vec2((position.z + 0.5) * s.z, position.y * s.y); vSize = s.zy;
#else
  vUv = vec2((position.x + 0.5) * s.x, position.y * s.y); vSize = s.xy;
#endif
  gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
}`;
const FACE_FRAG = `
uniform float edgeAlpha;
uniform float glowAlpha;
uniform float glowSigma;
varying vec3 vBase;
varying vec3 vEdge;
varying vec3 vGlow;
varying vec2 vUv;
varying vec2 vSize;
void main() {
  // Distance to the nearest face edge, in screen pixels: a 1px line at any zoom.
  vec2 px = min(vUv, vSize - vUv) / max(fwidth(vUv), vec2(1e-4));
  float edge = 1.0 - smoothstep(0.5, 1.5, min(px.x, px.y));
  vec3 c = vBase;
#if FACE != 0
  // The inset glow sits on the cube's top edge on the south face and on its
  // foot on the east face, where the CSS faces' rotations put it.
#if FACE == 2
  float d = vSize.y - vUv.y;
#else
  float d = vUv.y;
#endif
  c = mix(c, vGlow, glowAlpha * exp(-d * d / (2.0 * glowSigma * glowSigma)));
#endif
  c = mix(c, vEdge, edgeAlpha * edge);
  gl_FragColor = vec4(c, 1.0);
}`;

function faceMaterial(kind) {
  return new THREE.ShaderMaterial({
    defines: { FACE: { top: 0, x: 1, z: 2 }[kind] },
    uniforms: {
      edgeAlpha: { value: EDGES[kind][2] },
      glowAlpha: { value: kind === "top" ? 0 : SIDE_GLOW[2] },
      glowSigma: { value: SIGMA.sideGlow },
    },
    vertexShader: FACE_VERT,
    fragmentShader: FACE_FRAG,
  });
}

// Soft glow quads — the CSS box-shadows and the blurred cube shadow. Each
// instance is a flat square whose scale is the lit box plus three sigmas of
// falloff on every side; aColor is the sRGB glow colour already multiplied by
// its alpha. Blending keeps the brighter of glow and what is already drawn,
// per channel, and leaves the canvas alpha alone: a glow lights the dark plate
// and page but never stacks with its neighbours' or washes out a lit face,
// which additive blending did wherever cubes stand close together.
// MODE 0 lights the whole box, 1 only outside it (a shadow the element hides
// in CSS), 2 only inside it from the edges in (an inset shadow).
const GLOW_VERT = `
attribute vec3 aColor;
uniform float pad;
varying vec3 vColor;
varying vec2 vP;
varying float vHalf;
void main() {
  vColor = aColor;
  float s = length(instanceMatrix[0].xyz);
  vP = position.xz * s;
  vHalf = s * 0.5 - pad;
  gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
}`;
const GLOW_FRAG = `
uniform float sigma;
varying vec3 vColor;
varying vec2 vP;
varying float vHalf;
void main() {
  vec2 q = abs(vP) - vHalf;
#if MODE == 2
  float d = -max(q.x, q.y);
#else
  float d = length(max(q, 0.0));
#if MODE == 1
  if (d <= 0.0) discard;
#endif
#endif
  float a = exp(-d * d / (2.0 * sigma * sigma));
  gl_FragColor = vec4(vColor * a, 0.0);
}`;

function glowMaterial(mode, sigma) {
  return new THREE.ShaderMaterial({
    defines: { MODE: mode },
    uniforms: { sigma: { value: sigma }, pad: { value: mode === 2 ? 0 : sigma * 3 } },
    vertexShader: GLOW_VERT,
    fragmentShader: GLOW_FRAG,
    transparent: true,
    depthWrite: false,
    blending: THREE.CustomBlending,
    blendEquation: THREE.MaxEquation,
    blendSrc: THREE.OneFactor,
    blendDst: THREE.OneFactor,
    blendSrcAlpha: THREE.ZeroFactor,
    blendDstAlpha: THREE.OneFactor,
  });
}

// A per-instance sRGB colour attribute (three's instanceColor is linear).
function colorAttr(geometry, name, count) {
  const a = new THREE.InstancedBufferAttribute(new Float32Array(count * 3), 3);
  geometry.setAttribute(name, a);
  return a;
}
const setRGB = (attr, i, rgb) => attr.setXYZ(i, rgb[0], rgb[1], rgb[2]);
const scaled = (rgb, k) => rgb.map(v => v * k);

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
function labelSprite({ name, util, hue, sev, text }, height) {
  const px = 44, pad = 18, measure = document.createElement("canvas").getContext("2d");
  const font = weight => `${weight} ${px}px ${token("--font-mono", "ui-monospace, monospace")}`;
  const utilText = text || `${Math.round(util * 100)}%`;
  measure.font = font(400);
  const nameW = measure.measureText(name).width;
  measure.font = font(600);
  const utilW = measure.measureText(utilText).width;
  const markW = sev ? px * 0.6 : 0;
  const dotW = px * 0.5 + pad * 0.6;
  // The chip sits inside a margin that holds its CSS box-shadow glow.
  const chipW = Math.ceil(pad + dotW + nameW + pad + utilW + (sev ? pad * 0.6 + markW : 0) + pad);
  const chipH = px + 26, glow = 36;
  const c = document.createElement("canvas");
  c.width = chipW + glow * 2;
  c.height = chipH + glow * 2;
  const g = c.getContext("2d"), mid = c.height / 2;
  g.shadowColor = `hsla(${hue}, 100%, 50%, .12)`;
  g.shadowBlur = glow;
  g.fillStyle = "rgba(4,6,12,.92)";
  g.fillRect(glow, glow, chipW, chipH);
  g.shadowColor = "transparent";
  g.strokeStyle = `hsla(${hue}, 100%, 62%, .4)`;
  g.lineWidth = 3;
  g.strokeRect(glow + 1.5, glow + 1.5, chipW - 3, chipH - 3);
  let x = glow + pad;
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
  const unit = height / chipH;
  s.scale.set(c.width * unit, c.height * unit, 1);
  s.renderOrder = 10;
  return s;
}

function Scene3D({
  nodes, match, zoom, hueOf, colorBy, memUnit, fmtMem, metric, onFocus,
  highlight, highlightActive, onPodSelect, onPodHover, captureRef, verdicts,
}) {
  const scope = React.useMemo(() => extendedScope(match, nodes, metric), [match, nodes, metric]);
  const ext = isExtended(metric) ? metric : null;
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

    // PNG capture at `scale`× the screen resolution. The drawing buffer is
    // not preserved, so the frame is copied out in the same task it was
    // rendered in, onto the page background (the renderer is transparent).
    const capture = scale => {
      const dpr = window.devicePixelRatio;
      renderer.setPixelRatio(dpr * scale);
      resize();
      renderer.render(scene, cam);
      const src = renderer.domElement;
      const out = document.createElement("canvas");
      out.width = src.width;
      out.height = src.height;
      const g = out.getContext("2d");
      g.fillStyle = token("--bg", "#07080c");
      g.fillRect(0, 0, out.width, out.height);
      g.drawImage(src, 0, 0);
      renderer.setPixelRatio(dpr);
      resize();
      return out;
    };
    if (captureRef) captureRef.current = capture;

    world.current = w;
    resize();
    return () => {
      if (captureRef && captureRef.current === capture) captureRef.current = null;
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
    const top = new THREE.InstancedMesh(boxFaces([2]), faceMaterial("top"), n);
    const x = new THREE.InstancedMesh(boxFaces([0, 1]), faceMaterial("x"), n);
    const z = new THREE.InstancedMesh(boxFaces([4, 5]), faceMaterial("z"), n);
    for (const mesh of [top, x, z]) {
      mesh.userData.attrs = ["aBase", "aEdge", "aGlow"].map(name => colorAttr(mesh.geometry, name, n));
    }
    const rims = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), unlit(), np);
    const plates = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), unlit(), np);
    const flat = () => new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2);
    const overlay = map => new THREE.MeshBasicMaterial({ map, transparent: true, depthWrite: false });
    const grids = new THREE.InstancedMesh(flat(), overlay(w.grid), np);
    const hatches = new THREE.InstancedMesh(flat(), overlay(w.hatch), np);
    const glowMesh = (mode, sigma, count) => {
      const mesh = new THREE.InstancedMesh(flat(), glowMaterial(mode, sigma), count);
      mesh.userData.color = colorAttr(mesh.geometry, "aColor", count);
      return mesh;
    };
    // The coloured haze each cube casts on its plate, the halo around its top
    // face, and the plate's outer and inset glow.
    const shadows = glowMesh(0, SIGMA.cubeShadow, n);
    const halos = glowMesh(1, SIGMA.topGlow, n);
    const plateGlows = glowMesh(0, SIGMA.plateGlow, np);
    const plateInsets = glowMesh(2, SIGMA.plateInset, np);
    for (const mesh of [top, x, z, shadows, halos]) mesh.count = next.cubes.length;
    for (const mesh of [rims, plates, grids, hatches, plateGlows, plateInsets]) mesh.count = next.plates.length;
    const m = new THREE.Matrix4();
    next.plates.forEach((p, i) => {
      // The 1px CSS border as a slightly larger, lower slab around the plate.
      m.makeScale(PLATE + 2, 2, PLATE + 2).setPosition(p.x, -1.1, p.z);
      rims.setMatrixAt(i, m);
      m.makeScale(PLATE, 2, PLATE).setPosition(p.x, -1, p.z);
      plates.setMatrixAt(i, m);
      const g = PLATE + SIGMA.plateGlow * 6;
      m.makeScale(g, 1, g).setPosition(p.x, -2.2, p.z);
      plateGlows.setMatrixAt(i, m);
      m.makeScale(PLATE, 1, PLATE).setPosition(p.x, 0.12, p.z);
      plateInsets.setMatrixAt(i, m);
    });
    next.cubes.forEach((c, i) => {
      m.makeScale(c.w, c.h, c.w).setPosition(c.x, 0, c.z);
      top.setMatrixAt(i, m);
      x.setMatrixAt(i, m);
      z.setMatrixAt(i, m);
      // .cube-shadow sits 3px down-right of the cube in the CSS plate plane.
      const sh = c.w + SIGMA.cubeShadow * 6;
      m.makeScale(sh, 1, sh).setPosition(c.x + 3 * CSS_PX, 0.2, c.z + 3 * CSS_PX);
      shadows.setMatrixAt(i, m);
      const ha = c.w + SIGMA.topGlow * 6;
      m.makeScale(ha, 1, ha).setPosition(c.x, c.h + 0.05, c.z);
      halos.setMatrixAt(i, m);
    });
    root.add(plateGlows, rims, plates, grids, hatches, plateInsets, shadows, z, x, top, halos);

    const labels = next.plates.map(p => {
      // With an extended resource selected, a plate that has it reports its own
      // used/allocatable (e.g. 3/4 GPU) instead of the CPU/Memory peak.
      const cap = ext ? nodeCap(p.node, ext) : 0;
      const util = cap > 0
        ? nodeUsed(p.node, ext) / cap
        : Math.max(p.node.cpuUsed / (p.node.cpuCapacity || 1), p.node.memUsed / (p.node.memCapacity || 1));
      const text = cap > 0
        ? `${fmtValue(nodeUsed(p.node, ext), ext, memUnit)}/${fmtValue(cap, ext, memUnit, true)} ${unitOf(ext, memUnit)}`
        : null;
      const s = labelSprite({
        name: p.node.name.replace(/\.ec2\.internal$/, "").toLowerCase(), util, text,
        hue: hueOf(p.idx), sev: worstSeverity(p.node.warnings),
      }, 11);
      s.position.set(p.x, 6, p.z - PLATE / 2 - 10);
      return s;
    });
    if (labels.length) root.add(...labels);
    root.position.set(-next.center.x, 0, -next.center.z);

    Object.assign(w, {
      cubes: next.cubes, plates: next.plates, labels,
      meshes: { top, x, z, rims, plates, grids, hatches, shadows, halos, plateGlows, plateInsets },
    });
    w.dispose = () => {
      const meshes = [plateGlows, rims, plates, grids, hatches, plateInsets, shadows, z, x, top, halos];
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
    // metric/memUnit feed the plate labels, which are baked into sprites here.
  }, [nodes, hueKey, ext, memUnit]);

  // Colour-only updates (query, workload highlight) never touch geometry.
  React.useEffect(() => {
    const w = world.current;
    if (!w) return;
    const queryActive = !!(scope && scope.active);
    const look = {
      highlight, queryActive, pinned: highlightActive,
      matched: c => scope.pods.has(c.pod),
      plateDim: n => queryActive && scope.dimNodes.has(n.name),
    };
    const sevColor = {
      danger: hex(token("--danger", "#ef4444")), warn: hex(token("--warn", "#f59e0b")), info: hex(token("--info", "#60a5fa")),
    };
    // Simulation verdicts recolour the plate rim and outer glow, like the 2D outline.
    const simColor = {
      ok: hex(token("--ok", "#10b981")), fail: hex(token("--danger", "#ef4444")), drained: hex(token("--text-soft", "#6b7388")),
    };
    w.recolor = () => {
      if (!w.meshes) return;
      const c = new THREE.Color(), m = new THREE.Matrix4();
      const set = (mesh, i, rgb) => mesh.setColorAt(i, c.setRGB(rgb[0], rgb[1], rgb[2], THREE.SRGBColorSpace));
      // A pod's colours depend only on its node hue and one of six filter
      // states, so compute each combination once, not once per pod.
      const faces = new Map();
      const faceMeshes = [w.meshes.top, w.meshes.x, w.meshes.z];
      w.cubes.forEach((cube, i) => {
        const h = colorBy === "qos" ? qosHue(cube.pod.qos) : hueOf(cube.nodeIdx);
        const f = podFilter(cube, look), key = `${h}|${f}`;
        if (!faces.has(key)) {
          // Every layer of a face goes through the same filter, then fades
          // over the plate, as the whole CSS face element did.
          const under = plateSurface(w.bg, h);
          const look1 = (sat, light) => mix(under, cssFilter(hsl(h, sat, light), f[0], f[1]), f[2]);
          const glow = (sat, light, alpha) => scaled(cssFilter(hsl(h, sat, light), f[0], f[1]), alpha * f[2]);
          faces.set(key, {
            faces: ["top", "x", "z"].map(face => [
              look1(...FACES[face]),
              look1(EDGES[face][0], EDGES[face][1]),
              look1(SIDE_GLOW[0], SIDE_GLOW[1]),
            ]),
            shadow: glow(100, 50, GLOW.cubeShadow),
            halo: glow(100, 60, GLOW.halo),
          });
        }
        const c = faces.get(key);
        faceMeshes.forEach((mesh, k) => mesh.userData.attrs.forEach((attr, j) => setRGB(attr, i, c.faces[k][j])));
        setRGB(w.meshes.shadows.userData.color, i, c.shadow);
        setRGB(w.meshes.halos.userData.color, i, c.halo);
      });
      w.plates.forEach((p, i) => {
        const h = hueOf(p.idx), dim = look.plateDim(p.node), sev = worstSeverity(p.node.warnings);
        const verdict = verdicts && simColor[verdicts.get(p.node.name)];
        set(w.meshes.plates, i, plateSurface(w.bg, h));
        setRGB(w.meshes.plateGlows.userData.color, i,
          verdict ? scaled(verdict, 0.45) : scaled(hsl(h, 100, 50), GLOW.plateOuter));
        setRGB(w.meshes.plateInsets.userData.color, i, scaled(hsl(h, 100, 55), dim ? 0 : GLOW.plateInset));
        set(w.meshes.rims, i, verdict || mix(w.bg, hsl(h, 100, 62), GLOW.rim));
        // A plate the query ruled out drops its inlay, warning hatch included.
        m.makeScale(dim ? 0 : PLATE, 1, dim ? 0 : PLATE).setPosition(p.x, 0.05, p.z);
        w.meshes.grids.setMatrixAt(i, m);
        set(w.meshes.grids, i, [1, 1, 1]);
        m.makeScale(dim || !sev ? 0 : PLATE, 1, dim || !sev ? 0 : PLATE).setPosition(p.x, 0.1, p.z);
        w.meshes.hatches.setMatrixAt(i, m);
        set(w.meshes.hatches, i, sev ? sevColor[sev] : [0, 0, 0]);
        w.labels[i].material.opacity = dim ? 0.38 : 1;
      });
      for (const mesh of Object.values(w.meshes)) {
        if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
        for (const attr of mesh.userData.attrs || []) attr.needsUpdate = true;
        if (mesh.userData.color) mesh.userData.color.needsUpdate = true;
      }
      w.meshes.grids.instanceMatrix.needsUpdate = w.meshes.hatches.instanceMatrix.needsUpdate = true;
    };
    w.recolor();
    w.render();
  }, [nodes, hueKey, colorBy, scope, highlight, highlightActive, verdicts]);

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
      <SceneLegend colorBy={colorBy} ext={ext} />
      <SceneTooltip tip={tip} memUnit={memUnit} fmtMem={fmtMem} />
    </div>
  );
}

// Annotated reference cube. Abstract swatches are not enough here — without the
// brackets there is no way to tell which axis carries which resource.
function SceneLegend({ colorBy, ext }) {
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
        {ext && <div>Lit cubes → request {resourceMeta(ext).label}</div>}
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
        {Object.entries(n.extCap || {}).filter(([, cap]) => cap > 0).map(([name, cap]) => (
          <div key={name} className="cube-tip-row">
            <span>{fmtValue(nodeUsed(n, name), name, memUnit)} / {fmtValue(cap, name, memUnit, true)}</span> {unitOf(name, memUnit)}
          </div>
        ))}
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
      {Object.entries(p.ext || {}).map(([name, v]) => (
        <div key={name} className="cube-tip-row">
          <span>{fmtValue(v, name, memUnit)}</span> {unitOf(name, memUnit)}
        </div>
      ))}
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
