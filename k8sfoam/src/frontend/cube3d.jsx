// Isometric cube scene. Each node is a plate, each pod a cube on it:
// the cube's footprint encodes CPU, its height encodes Memory. Unlike the
// 2D treemap there is no active metric here — a cube shows both at once.

// Plate footprint plus the scene gap, used to keep the scene block near-square.
const PLATE_W = 250;
const SLOT_W = 310;
const SLOT_H = 252;

function clamp(min, v, max) {
  return Math.max(min, Math.min(max, v));
}

// Footprint → CPU (millicores), height → Memory (MiB). Both sqrt-scaled so a
// pod ten times larger reads as noticeably bigger without dwarfing the plate.
//
// Sized from the pod's effective request rather than a container sum, matching
// treemap.jsx — init containers run sequentially before the regular ones, so
// summing every container would double-count them and the two views would
// disagree about the same pod.
function cubeDims(pod) {
  return {
    base: clamp(18, 10 + Math.sqrt(pod.cpu) * 1.05, 48),
    height: clamp(8, Math.sqrt(pod.mem) * 1.05, 78),
  };
}

function utilColor(u) {
  return u > 0.85 ? "#ef4444" : u > 0.6 ? "#f59e0b" : u > 0.3 ? "#10b981" : "#60a5fa";
}

// Three faces are enough: the scene is rotated so the north and west walls and
// the underside never face the camera.
//
// Two traps live in this transform stack, both easy to reintroduce:
//   * .cube-s must be rotateX(+90deg). At -90deg it extrudes down through the
//     plate and every cube reads as a hollow shell.
//   * no backface-visibility:hidden on the side faces — once rotated they face
//     away from the camera and get culled, leaving only flat top rhombi.
function Cube({ pod, hue, matched, dim, onHover, onLeave }) {
  const { base, height } = cubeDims(pod);

  return (
    <div
      className={`cube ${dim ? "is-dim" : ""} ${matched ? "is-match" : ""}`}
      style={{ width: base, height: base }}
      onMouseEnter={e => onHover(pod, e)}
      onMouseMove={e => onHover(pod, e)}
      onMouseLeave={onLeave}
    >
      <div
        className="cube-shadow"
        style={{ width: base, height: base, background: `hsla(${hue}, 100%, 50%, .28)` }}
      />
      <div
        className="cube-face cube-top"
        style={{
          width: base, height: base,
          transform: `translateZ(${height}px)`,
          background: `hsl(${hue} 96% 58%)`,
          boxShadow: `inset 0 0 0 1px hsla(${hue}, 100%, 78%, .95), 0 0 16px hsla(${hue}, 100%, 60%, .5)`,
        }}
      />
      <div
        className="cube-face cube-s"
        style={{
          width: base, height: height, top: base,
          background: `hsl(${hue} 88% 15%)`,
          boxShadow: `inset 0 0 0 1px hsla(${hue}, 100%, 72%, .42), inset 0 -14px 20px -12px hsla(${hue}, 100%, 60%, .55)`,
        }}
      />
      <div
        className="cube-face cube-e"
        style={{
          width: height, height: base, left: base,
          background: `hsl(${hue} 88% 24%)`,
          boxShadow: `inset 0 0 0 1px hsla(${hue}, 100%, 72%, .55), inset 14px 0 20px -12px hsla(${hue}, 100%, 60%, .55)`,
        }}
      />
    </div>
  );
}

function Plate({ node, match, hue, onFocus, onHover, onLeave }) {
  // No active metric in this view, so the plate reports whichever resource is
  // under more pressure — that is the number that decides schedulability.
  const util = Math.max(
    node.cpuUsed / (node.cpuCapacity || 1),
    node.memUsed / (node.memCapacity || 1)
  );

  // Largest footprints first so they land at the back of the rotated field and
  // don't occlude the smaller cubes in front of them.
  const pods = [...node.pods].sort((a, b) => cubeDims(b).base - cubeDims(a).base);

  // Same dim model as the 2D card: a node ruled out by a node: glob dims whole,
  // otherwise unmatched cubes fade one by one.
  const queryActive = !!(match && match.active);
  const plateDim = queryActive && match.dimNodes.has(node.name);
  const podMatched = pod => queryActive && match.pods.has(pod);

  return (
    <div
      className={`plate ${plateDim ? "is-dim" : ""}`}
      onClick={() => onFocus(node)}
      style={{
        background: `hsla(${hue}, 80%, 6%, .92)`,
        borderColor: `hsla(${hue}, 100%, 62%, .5)`,
        boxShadow: `0 0 34px hsla(${hue}, 100%, 50%, .16), inset 0 0 46px hsla(${hue}, 100%, 55%, .07)`,
      }}
    >
      <div
        className="plate-label"
        style={{
          borderColor: `hsla(${hue}, 100%, 62%, .4)`,
          boxShadow: `0 0 16px hsla(${hue}, 100%, 50%, .22)`,
        }}
      >
        <span className="plate-dot" style={{ background: `hsl(${hue} 90% 62%)` }} />
        <span className="plate-name">{node.name.replace(/\.ec2\.internal$/, "")}</span>
        <span className="plate-util" style={{ color: utilColor(util) }}>
          {Math.round(util * 100)}%
        </span>
      </div>

      <div className="cube-field">
        {pods.map((p, i) => (
          <Cube key={`${p.name}-${i}`} pod={p} hue={hue}
                matched={podMatched(p)}
                dim={queryActive && !plateDim && !podMatched(p)}
                onHover={onHover} onLeave={onLeave} />
        ))}
      </div>
    </div>
  );
}

// Annotated reference cube. Abstract swatches are not enough here — without the
// brackets there is no way to tell which axis carries which resource.
function SceneLegend() {
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
        <div className="legend-foot">One cube per pod · color = node</div>
      </div>
    </div>
  );
}

function CubeTooltip({ tip, memUnit, fmtMem }) {
  if (!tip) return null;
  return (
    <div className="cube-tip" style={{ left: tip.x + 14, top: tip.y + 14 }}>
      <div className="cube-tip-name">{tip.pod.name}</div>
      <div className="cube-tip-row">
        <span>{(tip.pod.cpu / 1000).toFixed(2)}</span> cores
      </div>
      <div className="cube-tip-row">
        <span>{fmtMem(tip.pod.mem, memUnit)}</span> {memUnit}
      </div>
      <div className="cube-tip-foot">
        {tip.pod.containers.length} container{tip.pod.containers.length === 1 ? "" : "s"}
      </div>
    </div>
  );
}

function Scene3D({ nodes, match, zoom, hueOf, memUnit, fmtMem, onFocus }) {
  const scrollRef = React.useRef(null);
  const [tip, setTip] = React.useState(null);

  // A 45° rotation turns a tall column layout into a long diagonal ribbon, so
  // keep the un-rotated block near-square before it gets spun.
  const cols = Math.max(3, Math.round(Math.sqrt((nodes.length * SLOT_H) / SLOT_W)));
  const sceneW = cols * SLOT_W;

  // The rotated scene is far wider and taller than the viewport; start centred
  // rather than pinned to the top-left corner, where there is nothing to see.
  React.useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2;
    el.scrollTop = (el.scrollHeight - el.clientHeight) / 2;
  }, [zoom, nodes.length, cols]);

  const onHover = (pod, e) => setTip({ pod, x: e.clientX, y: e.clientY });
  const onLeave = () => setTip(null);

  return (
    <div className="scene-3d">
      <div className="scene-scroll" ref={scrollRef}>
        <div className="scene-wrap">
          <div
            className="scene"
            style={{
              width: sceneW,
              transform: `scale(${zoom}) rotateX(55deg) rotateZ(45deg)`,
            }}
          >
            {nodes.map((n, idx) => (
              <Plate
                key={n.id}
                node={n}
                match={match}
                hue={hueOf(idx)}
                onFocus={onFocus}
                onHover={onHover}
                onLeave={onLeave}
              />
            ))}
          </div>
        </div>
      </div>

      <SceneLegend />
      <CubeTooltip tip={tip} memUnit={memUnit} fmtMem={fmtMem} />
    </div>
  );
}

window.k8sCube3D = { Scene3D, Plate, Cube, cubeDims };
