import * as THREE from "three";
import { TrackballControls } from "three/addons/controls/TrackballControls.js";

// ── Config from Django template ──────────────────────────────────────────────
const { studyId, cacheId, nFrames, cursorFrac, jobId } = window.VIEWER_CONFIG;

// ── Constants (match desktop constants.py exactly) ───────────────────────────
const PROC_W     = 300;
const PROC_H     = 300;
const Z_SPACING  = 3.0;
const SAG_Z      = 300;   // sagittal plane Z-width
// Normalize frame spacing so the stack always spans exactly SAG_Z in depth.
// This keeps scene size (and therefore camera distance / rotation feel) the
// same regardless of how many frames the study has.
const EFF_Z = nFrames > 1 ? SAG_Z / (nFrames - 1) : Z_SPACING;
const TR_OPACITY = 0.45;

// ── Three.js core ────────────────────────────────────────────────────────────
const canvas   = document.getElementById("three-canvas");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setClearColor(0x0d0d0d);
renderer.localClippingEnabled = true;   // needed for sagittal clip planes

const scene  = new THREE.Scene();
scene.background = new THREE.Color(0x0d0d0d);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000);
const controls = new TrackballControls(camera, renderer.domElement);
controls.rotateSpeed = 2.0;
controls.zoomSpeed   = 1.2;
controls.panSpeed    = 0.8;
controls.dynamicDampingFactor = 0.1;

// Axes helper (bottom-left corner)
const axesHelper = new THREE.AxesHelper(40);
scene.add(axesHelper);

// Resize
function resize() {
  const wrap = canvas.parentElement;
  const w = wrap.clientWidth, h = wrap.clientHeight;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();

// ViewCube render hook — filled in after applyPreset is defined
let renderViewCube = () => {};

// Render loop
(function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
  renderViewCube();
})();

// ── State ────────────────────────────────────────────────────────────────────
let displayMode  = "stack";
let currentFrame = 0;
let sagZOffset   = Math.round(cursorFrac * SAG_Z);   // matches z_offset in desktop
let sagYCenter   = 140;
let sagClipDist  = SAG_Z;
let sagHidden    = false;
let roiMask      = null;   // offscreen canvas or null
let playTimer    = null;
let transOpacity = TR_OPACITY;   // 0.45 default
let sagOpacity   = 0.65;
let transColor   = new THREE.Color(1, 1, 1);   // white = no tint
let sagColor     = new THREE.Color(1, 1, 1);

// Mesh references
let stackMeshes = [];
let sagMesh     = null;

// Texture cache — avoid re-fetching loaded textures
const texCache = new Map();
const loader   = new THREE.TextureLoader();

function getTexture(idx, plane) {
  const key = `${idx}:${plane}`;
  if (!texCache.has(key)) {
    const tex = loader.load(`/api/frames/${studyId}/${idx}/${plane}/?cache_id=${encodeURIComponent(cacheId)}`);
    tex.colorSpace = THREE.NoColorSpace;    // medical frames are linear — skip sRGB decode
    texCache.set(key, tex);
  }
  return texCache.get(key);
}

// ── Shader material (frame texture + optional ROI mask) ───────────────────────
const VERT_SHADER = `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0);
}`;

const FRAG_SHADER = `
uniform sampler2D tFrame;
uniform sampler2D tMask;
uniform float     uOpacity;
uniform bool      uHasMask;
uniform bool      uBrightAlpha;
uniform vec3      uColor;
varying vec2 vUv;
void main() {
  vec4 f = texture2D(tFrame, vUv);
  // uBrightAlpha: alpha scales with pixel brightness (sagittal plane, matches desktop).
  // Otherwise: binary mask alpha × flat opacity (transverse planes).
  float alpha = uBrightAlpha ? f.r * uOpacity : f.a * uOpacity;
  if (uHasMask) {
    vec4 m = texture2D(tMask, vUv);
    alpha *= m.r;
  }
  gl_FragColor = vec4(f.rgb * uColor, alpha);
}`;

// Shared white mask (no-op when ROI is off)
const whiteMaskTex = (function() {
  const c = document.createElement("canvas");
  c.width = c.height = 4;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, 4, 4);
  return new THREE.CanvasTexture(c);
})();

function makeMaterial(frameTex, opacity, clippingPlanes = [], brightAlpha = false, color = null) {
  const maskTex = roiMask ? new THREE.CanvasTexture(roiMask) : whiteMaskTex;
  const c = color ?? new THREE.Color(1, 1, 1);
  return new THREE.ShaderMaterial({
    uniforms: {
      tFrame:      { value: frameTex },
      tMask:       { value: maskTex },
      uOpacity:    { value: opacity },
      uHasMask:    { value: !!roiMask },
      uBrightAlpha:{ value: brightAlpha },
      uColor:      { value: new THREE.Vector3(c.r, c.g, c.b) },
    },
    vertexShader:   VERT_SHADER,
    fragmentShader: FRAG_SHADER,
    transparent:    true,
    depthWrite:     false,
    side:           THREE.DoubleSide,
    clippingPlanes,
  });
}

// ── Sagittal clip planes ──────────────────────────────────────────────────────
function makeSagClipPlanes() {
  const zMin = Math.max(0,    sagZOffset - sagClipDist);
  const zMax = Math.min(SAG_Z, sagZOffset + sagClipDist);
  return [
    new THREE.Plane(new THREE.Vector3(0, 0,  1), -zMin),
    new THREE.Plane(new THREE.Vector3(0, 0, -1),  zMax),
  ];
}

// ── Scene builders ────────────────────────────────────────────────────────────
function buildStack() {
  clearStackMeshes();
  for (let i = 0; i < nFrames; i++) {
    const geo  = new THREE.PlaneGeometry(PROC_W, PROC_H);
    const mat  = makeMaterial(getTexture(i, "trans"), transOpacity, [], false, transColor);
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(PROC_W / 2, PROC_H / 2, i * EFF_Z);
    scene.add(mesh);
    stackMeshes.push(mesh);
  }
  buildSagittal();
}

function buildSingle(idx) {
  clearStackMeshes();
  const geo  = new THREE.PlaneGeometry(PROC_W, PROC_H);
  const mat  = makeMaterial(getTexture(idx, "trans"), transOpacity, [], false, transColor);
  const mesh = new THREE.Mesh(geo, mat);
  // In single mode the transverse plane sits at z_offset (matching draw_single in desktop)
  mesh.position.set(PROC_W / 2, PROC_H / 2, sagZOffset);
  scene.add(mesh);
  stackMeshes.push(mesh);
  buildSagittal();
}

function buildSagittal() {
  if (sagMesh) { scene.remove(sagMesh); sagMesh = null; }

  // Plane(SAG_Z × PROC_H), rotated so it stands vertical along Z axis.
  // Use -PI/2 so U=0 (left of image) → Z=0 and U=1 (right) → Z=SAG_Z,
  // matching the cursor_frac direction (sagZOffset = cursor_frac * SAG_Z).
  const geo = new THREE.PlaneGeometry(SAG_Z, PROC_H);
  geo.applyMatrix4(new THREE.Matrix4().makeRotationY(-Math.PI / 2));

  const mat = makeMaterial(
    getTexture(currentFrame, "sag"),
    sagOpacity,
    makeSagClipPlanes(),
    true,       // brightAlpha: sagittal alpha ∝ pixel brightness (matches desktop)
    sagColor,
  );
  sagMesh = new THREE.Mesh(geo, mat);
  sagMesh.position.set(PROC_W / 2, sagYCenter, SAG_Z / 2);
  sagMesh.visible = !sagHidden;
  scene.add(sagMesh);
}

function clearStackMeshes() {
  stackMeshes.forEach(m => scene.remove(m));
  stackMeshes = [];
}

function redraw() {
  if (displayMode === "stack") buildStack();
  else                         buildSingle(currentFrame);
}

// ── Camera presets ────────────────────────────────────────────────────────────
const cx = PROC_W / 2;
const cy = PROC_H / 2;
const cz = SAG_Z / 2;          // always 150 — scene depth is always SAG_Z
const d  = Math.max(PROC_W, PROC_H, SAG_Z) * 2.0;  // always 600

const CAM_PRESETS = {
  perspective: { pos: [cx + d*0.55, cy - d*0.45, cz - d*0.45], up: [0, 1, 0] },
  transverse:  { pos: [cx, cy, cz - d],                         up: [0, 1, 0] },
  sagittal_v:  { pos: [cx - d, cy, cz],                         up: [0, 1, 0] },
  top:         { pos: [cx, cy - d, cz],                         up: [0, 0, -1] },
};

function applyPreset(name) {
  const p = CAM_PRESETS[name];
  camera.position.set(...p.pos);
  camera.up.set(...p.up);
  controls.target.set(cx, cy, cz);
  // TrackballControls caches the up vector internally — reset it
  controls.up0.copy(camera.up);
  controls.update();
}

// Snap camera so it looks FROM the direction of a clicked corner/edge.
// localPos is the cube-local position of the corner/edge mesh (e.g. [1,-1,-1]).
// The corner that faces you in the ViewCube is the one the camera should move TOWARD,
// so it stays facing you isometrically → camera at center + dir * dist.
function applyViewDir(localPos) {
  const dir  = localPos.clone().normalize();
  const dist = camera.position.distanceTo(controls.target);
  // Move camera to the same side as the visible corner, looking back at center
  camera.position.set(cx + dir.x * dist, cy + dir.y * dist, cz + dir.z * dist);
  // Up: if looking steeply down/up (|Y| > 0.8) use -Z to avoid gimbal flip; else +Y
  camera.up.set(0, Math.abs(dir.y) > 0.8 ? 0 : 1, Math.abs(dir.y) > 0.8 ? -1 : 0);
  controls.target.set(cx, cy, cz);
  controls.up0.copy(camera.up);
  controls.update();
}

// ── ViewCube ──────────────────────────────────────────────────────────────────
{
  // Canvas is 110 px; ortho frustum ±2.4 so the orbit ring fits around the cube
  const VC_SIZE    = 110;
  const vcCanvas   = document.getElementById("viewcube-canvas");
  const vcRenderer = new THREE.WebGLRenderer({ canvas: vcCanvas, alpha: true, antialias: true });
  vcRenderer.setPixelRatio(window.devicePixelRatio);
  vcRenderer.setSize(VC_SIZE, VC_SIZE);
  vcRenderer.setClearColor(0x000000, 0);

  const vcScene = new THREE.Scene();
  // Frustum wide enough to show ring (radius 1.9) with a small margin
  const vcCam   = new THREE.OrthographicCamera(-2.4, 2.4, 2.4, -2.4, 0.1, 10);
  vcCam.position.set(0, 0, 5);

  vcScene.add(new THREE.AmbientLight(0xffffff, 0.50));
  const vcDir = new THREE.DirectionalLight(0xffffff, 0.80);
  vcDir.position.set(2, 3, 4);
  vcScene.add(vcDir);

  // ── Face config ─────────────────────────────────────────────────────────────
  // BoxGeometry material order: +X, -X, +Y, -Y, +Z, -Z
  // When camera inverts its quaternion onto the cube:
  //   transverse (cam looks +Z) → +Z face (FRONT) visible
  //   sagittal_v (cam looks +X) → +X face (RIGHT) visible
  //   top        (cam looks +Y) → +Y face (TOP)   visible
  const FACE_CFG = [
    { label: "RIGHT",  bg: "#0e1a28", fg: "#4a88bb", preset: "sagittal_v" },
    { label: "LEFT",   bg: "#0e1a28", fg: "#2a4a66", preset: null         },
    { label: "TOP",    bg: "#0d2010", fg: "#4aa04a", preset: "top"        },
    { label: "BOT",    bg: "#0e0e0e", fg: "#282828", preset: null         },
    { label: "FRONT",  bg: "#071520", fg: "#00b4d8", preset: "transverse" },
    { label: "BACK",   bg: "#0e0e0e", fg: "#282828", preset: null         },
  ];

  function makeFaceTex(cfg, hover) {
    const c = document.createElement("canvas"); c.width = c.height = 128;
    const ctx = c.getContext("2d");
    ctx.fillStyle = hover ? "#1a2d42" : cfg.bg;
    ctx.fillRect(0, 0, 128, 128);
    ctx.strokeStyle = hover ? "#4a80bb" : "#202020"; ctx.lineWidth = 4;
    ctx.strokeRect(2, 2, 124, 124);
    ctx.fillStyle = hover ? "#ffffff" : cfg.fg;
    ctx.font = "bold 18px 'Segoe UI', sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(cfg.label, 64, 64);
    return new THREE.CanvasTexture(c);
  }

  const vcMats = FACE_CFG.map(cfg => {
    const m = new THREE.MeshLambertMaterial({ map: makeFaceTex(cfg, false), transparent: true, opacity: 0.93 });
    m._n = m.map;
    m._h = makeFaceTex(cfg, true);
    return m;
  });

  const vcCube = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2), vcMats);
  vcScene.add(vcCube);

  // Corner sphere indicators (8 corners) — tiny spheres for visual grab targets
  const cornDirs = [-1, 1];
  const vcCornerMat = new THREE.MeshBasicMaterial({ color: 0x224466, transparent: true, opacity: 0.75 });
  const vcCornerMatH = new THREE.MeshBasicMaterial({ color: 0x66aaff, transparent: true, opacity: 1.0 });
  const vcCorners = [];
  for (const sx of cornDirs) for (const sy of cornDirs) for (const sz of cornDirs) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.11, 8, 8), vcCornerMat);
    m.position.set(sx, sy, sz);
    m._isCorner = true;
    vcCube.add(m);   // child of cube — auto-rotates with it
    vcCorners.push(m);
  }

  // Edge cylinders — 12 edges for visual grab targets
  const vcEdgeMat  = new THREE.MeshBasicMaterial({ color: 0x1a3050, transparent: true, opacity: 0.70 });
  const vcEdgeMatH = new THREE.MeshBasicMaterial({ color: 0x4488cc, transparent: true, opacity: 1.00 });
  const EDGE_DEFS = [
    // along X (4 edges)
    [[ 0,  1,  1], [1,0,0]], [[ 0, -1,  1], [1,0,0]],
    [[ 0,  1, -1], [1,0,0]], [[ 0, -1, -1], [1,0,0]],
    // along Y (4 edges)
    [[ 1,  0,  1], [0,1,0]], [[-1,  0,  1], [0,1,0]],
    [[ 1,  0, -1], [0,1,0]], [[-1,  0, -1], [0,1,0]],
    // along Z (4 edges)
    [[ 1,  1,  0], [0,0,1]], [[-1,  1,  0], [0,0,1]],
    [[ 1, -1,  0], [0,0,1]], [[-1, -1,  0], [0,0,1]],
  ];
  const vcEdges = [];
  for (const [[px,py,pz],[ax,ay,az]] of EDGE_DEFS) {
    const geo = new THREE.CylinderGeometry(0.07, 0.07, 2, 6);
    const m   = new THREE.Mesh(geo, vcEdgeMat);
    m.position.set(px, py, pz);
    if (ax) m.rotation.z = Math.PI / 2;
    if (az) m.rotation.x = Math.PI / 2;
    m._isEdge = true;
    vcCube.add(m);   // child of cube — auto-rotates with it, preserving local axis alignment
    vcEdges.push(m);
  }

  // Orbit ring — flat torus in XY plane (appears as circle from vcCam)
  const vcRingMatN = new THREE.MeshBasicMaterial({ color: 0x183050, transparent: true, opacity: 0.65, side: THREE.DoubleSide });
  const vcRingMatH = new THREE.MeshBasicMaterial({ color: 0x3a88cc, transparent: true, opacity: 0.92, side: THREE.DoubleSide });
  const vcRing = new THREE.Mesh(new THREE.TorusGeometry(1.9, 0.09, 10, 56), vcRingMatN);
  vcScene.add(vcRing);

  // ── Hover state ─────────────────────────────────────────────────────────────
  let vcFaceHov = -1, vcRingHov = false, vcCornerHov = null, vcEdgeHov = null;

  function vcClearHover() {
    if (vcFaceHov >= 0) { vcMats[vcFaceHov].map = vcMats[vcFaceHov]._n; vcMats[vcFaceHov].needsUpdate = true; }
    vcFaceHov = -1;
    if (vcRingHov) vcRing.material = vcRingMatN; vcRingHov = false;
    if (vcCornerHov) vcCornerHov.material = vcCornerMat; vcCornerHov = null;
    if (vcEdgeHov)   vcEdgeHov.material   = vcEdgeMat;   vcEdgeHov   = null;
  }

  function vcDoHover(faceIdx, ringH, cornerMesh, edgeMesh) {
    // Reset all
    if (vcFaceHov >= 0   && vcFaceHov !== faceIdx) { vcMats[vcFaceHov].map = vcMats[vcFaceHov]._n; vcMats[vcFaceHov].needsUpdate = true; }
    if (vcRingHov        && !ringH)                  vcRing.material = vcRingMatN;
    if (vcCornerHov      && vcCornerHov !== cornerMesh) vcCornerHov.material = vcCornerMat;
    if (vcEdgeHov        && vcEdgeHov   !== edgeMesh)   vcEdgeHov.material   = vcEdgeMat;
    // Apply new
    vcFaceHov   = faceIdx;   if (faceIdx >= 0)    { vcMats[faceIdx].map = vcMats[faceIdx]._h; vcMats[faceIdx].needsUpdate = true; }
    vcRingHov   = ringH;     if (ringH)             vcRing.material   = vcRingMatH;
    vcCornerHov = cornerMesh; if (cornerMesh)       cornerMesh.material = vcCornerMatH;
    vcEdgeHov   = edgeMesh;   if (edgeMesh)         edgeMesh.material   = vcEdgeMatH;
  }

  // ── Raycasting ───────────────────────────────────────────────────────────────
  const vcRay = new THREE.Raycaster();
  vcRay.params.Line = { threshold: 0.1 };

  function vcCast(cx, cy) {
    const rect = vcCanvas.getBoundingClientRect();
    vcRay.setFromCamera(
      new THREE.Vector2((cx - rect.left) / VC_SIZE * 2 - 1, -(cy - rect.top) / VC_SIZE * 2 + 1),
      vcCam
    );
    const cubeHits   = vcRay.intersectObject(vcCube);
    const ringHits   = vcRay.intersectObject(vcRing);
    const cornerHits = vcRay.intersectObjects(vcCorners);
    const edgeHits   = vcRay.intersectObjects(vcEdges);
    return { cubeHits, ringHits, cornerHits, edgeHits };
  }

  function localPt(hit) { return vcCube.worldToLocal(hit.point.clone()); }
  function faceIdx(hit) { return Math.floor(hit.faceIndex / 2); }

  // ── Drag state ───────────────────────────────────────────────────────────────
  let vcDrag = null;   // null | "cube" | "ring"
  let vcDragMoved = false, vcLX = 0, vcLY = 0;

  vcCanvas.addEventListener("mousedown", e => {
    const { cubeHits, ringHits, cornerHits, edgeHits } = vcCast(e.clientX, e.clientY);
    if (!cubeHits.length && !ringHits.length && !cornerHits.length && !edgeHits.length) return;
    vcDrag = (ringHits.length && (!cubeHits.length || ringHits[0].distance < cubeHits[0].distance))
             ? "ring" : "cube";
    vcDragMoved = false;
    vcLX = e.clientX; vcLY = e.clientY;
    controls.enabled = false;
    e.preventDefault(); e.stopPropagation();
  });

  function vcRotateCamera(dx, dy, yAxisOnly) {
    const sens   = 0.010;
    const offset = camera.position.clone().sub(controls.target);
    // Horizontal drag → orbit around world Y
    const qY = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -dx * sens);
    offset.applyQuaternion(qY);
    camera.up.applyQuaternion(qY).normalize();
    if (!yAxisOnly) {
      // Vertical drag → orbit around camera's right axis
      const right = new THREE.Vector3()
        .crossVectors(camera.getWorldDirection(new THREE.Vector3()), camera.up)
        .normalize();
      const qX = new THREE.Quaternion().setFromAxisAngle(right, -dy * sens);
      offset.applyQuaternion(qX);
      camera.up.applyQuaternion(qX).normalize();
    }
    camera.position.copy(controls.target).add(offset);
    controls.up0.copy(camera.up);
    controls.update();
  }

  window.addEventListener("mousemove", e => {
    if (!vcDrag) return;
    const dx = e.clientX - vcLX, dy = e.clientY - vcLY;
    vcLX = e.clientX; vcLY = e.clientY;
    if (Math.abs(dx) + Math.abs(dy) > 1) vcDragMoved = true;
    vcRotateCamera(dx, dy, vcDrag === "ring");
  });

  window.addEventListener("mouseup", e => {
    if (!vcDrag) return;
    controls.enabled = true;
    if (!vcDragMoved) {
      // Treat as a click — determine what was hit
      const { cubeHits, cornerHits, edgeHits } = vcCast(e.clientX, e.clientY);
      if (cornerHits.length) {
        // Corner local position = [±1, ±1, ±1] — normalise to get exact 3/4 view direction
        applyViewDir(cornerHits[0].object.position);
      } else if (edgeHits.length) {
        // Edge local position has one zero component, e.g. [0,1,1] or [1,0,-1]
        // Normalising gives the bisecting view direction for that edge
        applyViewDir(edgeHits[0].object.position);
      } else if (cubeHits.length) {
        const lp = localPt(cubeHits[0]);
        const hi = [Math.abs(lp.x), Math.abs(lp.y), Math.abs(lp.z)].filter(v => v > 0.60).length;
        if (hi >= 2) {
          // Click landed on the face but very close to an edge/corner — use surface point
          applyViewDir(lp);
        } else {
          const p = FACE_CFG[faceIdx(cubeHits[0])].preset;
          if (p) applyPreset(p);
        }
      }
    }
    vcDrag = null; vcDragMoved = false;
  });

  // ── Hover (mousemove on canvas) ──────────────────────────────────────────────
  vcCanvas.addEventListener("mousemove", e => {
    if (vcDrag) { vcCanvas.style.cursor = "grabbing"; return; }
    const { cubeHits, ringHits, cornerHits, edgeHits } = vcCast(e.clientX, e.clientY);

    // Priority: corner > edge > ring > face
    if (cornerHits.length) {
      vcDoHover(-1, false, cornerHits[0].object, null);
      vcCanvas.style.cursor = "pointer";
    } else if (edgeHits.length && (!cubeHits.length || edgeHits[0].distance < cubeHits[0].distance + 0.05)) {
      vcDoHover(-1, false, null, edgeHits[0].object);
      vcCanvas.style.cursor = "pointer";
    } else if (ringHits.length && (!cubeHits.length || ringHits[0].distance < cubeHits[0].distance)) {
      vcDoHover(-1, true, null, null);
      vcCanvas.style.cursor = "grab";
    } else if (cubeHits.length) {
      const fi = faceIdx(cubeHits[0]);
      const lp = localPt(cubeHits[0]);
      const hi = [Math.abs(lp.x), Math.abs(lp.y), Math.abs(lp.z)].filter(v => v > 0.60).length;
      vcDoHover(hi >= 2 ? -1 : fi, false, null, null);
      vcCanvas.style.cursor = (hi >= 2 || FACE_CFG[fi].preset) ? "pointer" : "grab";
    } else {
      vcClearHover();
      vcCanvas.style.cursor = "default";
    }
  });

  vcCanvas.addEventListener("mouseleave", () => {
    if (!vcDrag) vcClearHover();
    vcCanvas.style.cursor = "default";
  });

  // ── Per-frame render ─────────────────────────────────────────────────────────
  renderViewCube = function () {
    // Cube mirrors main camera orientation (inverse quaternion).
    // Corners and edges are children of vcCube so they auto-follow it —
    // no manual quaternion sync needed.
    vcCube.quaternion.copy(camera.quaternion).invert();
    vcRenderer.render(vcScene, vcCam);
  };
}

// ── Camera slots (persisted in localStorage) ──────────────────────────────────
const SLOTS_KEY = `biplane_cam_slots_${studyId}`;

function loadSlots() {
  try { return JSON.parse(localStorage.getItem(SLOTS_KEY)) || new Array(9).fill(null); }
  catch { return new Array(9).fill(null); }
}
function saveSlots(slots) {
  localStorage.setItem(SLOTS_KEY, JSON.stringify(slots));
}

let camSlots = loadSlots();

function buildSlotsUI() {
  const grid = document.getElementById("cam-slots");
  grid.innerHTML = "";
  for (let i = 0; i < 9; i++) {
    const btn = document.createElement("button");
    btn.className = "slot-btn";
    btn.dataset.idx = i;
    refreshSlotBtn(btn, i);

    btn.addEventListener("click", () => slotClicked(i));
    btn.addEventListener("contextmenu", e => {
      e.preventDefault();
      slotContextMenu(i, e.clientX, e.clientY);
    });
    grid.appendChild(btn);
  }
}

function refreshSlotBtn(btn, i) {
  const filled = camSlots[i] !== null;
  btn.classList.toggle("filled", filled);
  btn.innerHTML = filled
    ? `<span class="dot">●</span><span>${i+1}</span>`
    : `<span>${i+1}</span>`;
  btn.title = filled
    ? `Slot ${i+1}: saved — click to go · right-click for options`
    : `Slot ${i+1}: empty — click to save current view`;
}

function slotClicked(i) {
  if (camSlots[i] === null) {
    // Save current camera
    camSlots[i] = {
      pos:    camera.position.toArray(),
      target: controls.target.toArray(),
      up:     camera.up.toArray(),
    };
    saveSlots(camSlots);
    refreshSlotBtn(document.querySelector(`.slot-btn[data-idx="${i}"]`), i);
  } else {
    // Recall
    const s = camSlots[i];
    camera.position.set(...s.pos);
    camera.up.set(...s.up);
    controls.target.set(...s.target);
    controls.up0.copy(camera.up);
    controls.update();
  }
}

function slotContextMenu(i, x, y) {
  removeContextMenu();
  const menu = document.createElement("div");
  menu.className = "ctx-menu";
  menu.style.left = x + "px";
  menu.style.top  = y + "px";

  if (camSlots[i] !== null) {
    addMenuItem(menu, "Go to view",               () => slotClicked(i));
    addMenuItem(menu, "Replace with current view", () => {
      camSlots[i] = {
        pos:    camera.position.toArray(),
        target: controls.target.toArray(),
        up:     camera.up.toArray(),
      };
      saveSlots(camSlots);
      refreshSlotBtn(document.querySelector(`.slot-btn[data-idx="${i}"]`), i);
    });
    addMenuSep(menu);
    addMenuItem(menu, "Delete", () => {
      camSlots[i] = null;
      saveSlots(camSlots);
      refreshSlotBtn(document.querySelector(`.slot-btn[data-idx="${i}"]`), i);
    });
  } else {
    addMenuItem(menu, "Save current view here", () => slotClicked(i));
  }

  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener("click", removeContextMenu, { once: true }), 0);
}

function addMenuItem(menu, label, fn) {
  const el = document.createElement("div");
  el.className = "ctx-item";
  el.textContent = label;
  el.addEventListener("click", () => { fn(); removeContextMenu(); });
  menu.appendChild(el);
}
function addMenuSep(menu) {
  const sep = document.createElement("div");
  sep.className = "ctx-sep";
  menu.appendChild(sep);
}
function removeContextMenu() {
  document.querySelectorAll(".ctx-menu").forEach(m => m.remove());
}

// ── Export video (MediaRecorder, produces WebM) ───────────────────────────────
async function exportVideo(fps) {
  const wasPlaying = playTimer !== null;
  stopPlay();

  // Switch to single mode for export
  if (displayMode !== "single") {
    setMode("single");
  }

  const btn = document.getElementById("export-btn");
  btn.disabled = true;
  btn.textContent = "Exporting…";

  const sequence = [
    ...Array.from({length: nFrames}, (_, i) => i),
    ...Array.from({length: nFrames - 2}, (_, i) => nFrames - 2 - i),
  ];

  // Capture via canvas stream
  const stream   = canvas.captureStream(fps);
  const recorder = new MediaRecorder(stream, { mimeType: "video/webm;codecs=vp9" });
  const chunks   = [];
  recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
  recorder.onstop = () => {
    const blob = new Blob(chunks, { type: "video/webm" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url; a.download = `biplane_loop_${studyId}.webm`; a.click();
    URL.revokeObjectURL(url);
    btn.disabled = false;
    btn.textContent = "Export frame loop…";
  };

  recorder.start();

  for (let i = 0; i < sequence.length; i++) {
    currentFrame = sequence[i];
    updateFrameUI();
    buildSingle(currentFrame);
    await new Promise(r => setTimeout(r, 1000 / fps));
  }

  recorder.stop();
}

// ── ROI drawing ───────────────────────────────────────────────────────────────
let roiModal  = null;
let roiCanvas = null;
let roiCtx    = null;
if (!VIEWER_CONFIG.shared) {
roiModal  = document.getElementById("roi-modal");
roiCanvas = document.getElementById("roi-canvas");
roiCtx    = roiCanvas.getContext("2d");
}
let roiPts      = [];
let roiHover    = null;

function openRoiModal() {
  roiPts   = [];
  roiHover = null;
  roiModal.style.display = "flex";

  // Draw current frame onto the ROI canvas
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.onload = () => {
    roiCanvas.width  = 500;
    roiCanvas.height = 500;
    roiCtx.drawImage(img, 0, 0, 500, 500);
    drawRoiOverlay();
  };
  img.src = `/api/frames/${studyId}/${currentFrame}/trans/?cache_id=${encodeURIComponent(cacheId)}`;
}

function drawRoiOverlay() {
  // Redraw base frame first
  // (we keep a copy of the image in a variable)
  const img = new Image();
  img.onload = () => {
    roiCtx.clearRect(0, 0, 500, 500);
    roiCtx.drawImage(img, 0, 0, 500, 500);
    _drawRoiVectors();
  };
  img.src = `/api/frames/${studyId}/${currentFrame}/trans/?cache_id=${encodeURIComponent(cacheId)}`;
}

// Separate the vector drawing so we can call it without reloading the image
let _roiFrameImg = null;

function openRoiModalFull() {
  roiPts   = [];
  roiHover = null;
  roiModal.style.display = "flex";
  _roiFrameImg = new Image();
  _roiFrameImg.crossOrigin = "anonymous";
  _roiFrameImg.onload = _refreshRoiCanvas;
  _roiFrameImg.src = `/api/frames/${studyId}/${currentFrame}/trans/?cache_id=${encodeURIComponent(cacheId)}`;
}

function _refreshRoiCanvas() {
  roiCanvas.width  = 500;
  roiCanvas.height = 500;
  if (_roiFrameImg) {
    roiCtx.clearRect(0, 0, 500, 500);
    roiCtx.drawImage(_roiFrameImg, 0, 0, 500, 500);
  }
  _drawRoiVectors();
}

function _drawRoiVectors() {
  if (!roiPts.length) return;

  // Fill polygon
  if (roiPts.length >= 3) {
    roiCtx.beginPath();
    roiCtx.moveTo(roiPts[0].x, roiPts[0].y);
    roiPts.slice(1).forEach(p => roiCtx.lineTo(p.x, p.y));
    roiCtx.closePath();
    roiCtx.fillStyle = "rgba(80,220,120,0.18)";
    roiCtx.fill();
  }

  // Edges
  roiCtx.strokeStyle = "rgba(60,220,100,1)";
  roiCtx.lineWidth   = 2;
  roiCtx.beginPath();
  roiCtx.moveTo(roiPts[0].x, roiPts[0].y);
  roiPts.slice(1).forEach(p => roiCtx.lineTo(p.x, p.y));
  roiCtx.stroke();

  // Rubber-band to hover
  if (roiHover) {
    roiCtx.strokeStyle = "rgba(60,220,100,0.6)";
    roiCtx.setLineDash([6, 4]);
    roiCtx.beginPath();
    roiCtx.moveTo(roiPts[roiPts.length - 1].x, roiPts[roiPts.length - 1].y);
    roiCtx.lineTo(roiHover.x, roiHover.y);
    roiCtx.stroke();
    roiCtx.setLineDash([]);
  }

  // Close hint
  if (roiPts.length >= 3) {
    roiCtx.strokeStyle = "rgba(60,180,80,0.5)";
    roiCtx.setLineDash([3, 3]);
    roiCtx.beginPath();
    roiCtx.moveTo(roiPts[roiPts.length - 1].x, roiPts[roiPts.length - 1].y);
    roiCtx.lineTo(roiPts[0].x, roiPts[0].y);
    roiCtx.stroke();
    roiCtx.setLineDash([]);
  }

  // Vertices
  roiPts.forEach((p, i) => {
    roiCtx.beginPath();
    roiCtx.arc(p.x, p.y, 5, 0, Math.PI * 2);
    roiCtx.fillStyle = i === 0 ? "#ffd228" : "#3cdc64";
    roiCtx.fill();
    roiCtx.strokeStyle = "rgba(0,0,0,0.5)";
    roiCtx.lineWidth = 1;
    roiCtx.stroke();
  });
}

if (!VIEWER_CONFIG.shared) {
roiCanvas.addEventListener("click", e => {
  const r = roiCanvas.getBoundingClientRect();
  roiPts.push({ x: e.clientX - r.left, y: e.clientY - r.top });
  _refreshRoiCanvas();
});
roiCanvas.addEventListener("mousemove", e => {
  if (!roiPts.length) return;
  const r = roiCanvas.getBoundingClientRect();
  roiHover = { x: e.clientX - r.left, y: e.clientY - r.top };
  _refreshRoiCanvas();
});
roiCanvas.addEventListener("mouseleave", () => {
  roiHover = null;
  _refreshRoiCanvas();
});

document.addEventListener("keydown", e => {
  if (roiModal.style.display !== "none" && e.key === "z") {
    roiPts.pop();
    _refreshRoiCanvas();
  }
});

document.getElementById("roi-undo").addEventListener("click", () => {
  roiPts.pop(); _refreshRoiCanvas();
});
document.getElementById("roi-clear-pts").addEventListener("click", () => {
  roiPts = []; _refreshRoiCanvas();
});
document.getElementById("roi-cancel").addEventListener("click", () => {
  roiModal.style.display = "none";
});
document.getElementById("roi-apply").addEventListener("click", () => {
  if (roiPts.length < 3) return;
  applyRoiMask();
  roiModal.style.display = "none";
});
} // end !shared (ROI event listeners)

function applyRoiMask() {
  // Build 300×300 mask canvas (white inside polygon, black outside)
  const maskCanvas = document.createElement("canvas");
  maskCanvas.width = maskCanvas.height = PROC_W;
  const ctx  = maskCanvas.getContext("2d");
  const scaleX = PROC_W / 500;
  const scaleY = PROC_H / 500;

  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, PROC_W, PROC_H);
  ctx.fillStyle = "#fff";
  ctx.beginPath();
  ctx.moveTo(roiPts[0].x * scaleX, roiPts[0].y * scaleY);
  roiPts.slice(1).forEach(p => ctx.lineTo(p.x * scaleX, p.y * scaleY));
  ctx.closePath();
  ctx.fill();

  roiMask = maskCanvas;
  // Invalidate texture cache entries so meshes pick up new mask
  texCache.clear();
  redraw();
}

if (!VIEWER_CONFIG.shared) {
document.getElementById("roi-draw-btn").addEventListener("click",  openRoiModalFull);
document.getElementById("roi-clear-btn").addEventListener("click", () => {
  roiMask = null;
  texCache.clear();
  redraw();
});
} // end !shared (ROI buttons)

// ── Panel controls wiring ────────────────────────────────────────────────────
function updateFrameUI() {
  document.getElementById("frame-val").textContent = currentFrame + 1;
  document.getElementById("frame-slider").value    = currentFrame;
  document.getElementById("hdr-frame").textContent =
    `Frame ${currentFrame + 1} / ${nFrames}`;
}

function setMode(newMode) {
  displayMode = newMode;
  document.getElementById("hdr-mode").textContent = newMode.toUpperCase();
  if (newMode === "stack") buildStack();
  else                     buildSingle(currentFrame);
}

// Opacity sliders
if (!VIEWER_CONFIG.shared) {
document.getElementById("trans-opacity-slider").addEventListener("input", function() {
  transOpacity = this.value / 100;
  document.getElementById("trans-opacity-val").textContent = transOpacity.toFixed(2);
  redraw();
});
document.getElementById("sag-opacity-slider").addEventListener("input", function() {
  sagOpacity = this.value / 100;
  document.getElementById("sag-opacity-val").textContent = sagOpacity.toFixed(2);
  buildSagittal();
});

// Color pickers
document.getElementById("trans-color-picker").addEventListener("input", function() {
  transColor = new THREE.Color(this.value);
  redraw();
});
document.getElementById("sag-color-picker").addEventListener("input", function() {
  sagColor = new THREE.Color(this.value);
  buildSagittal();
});
} // end !shared

// Frame slider
const frameSlider = document.getElementById("frame-slider");
frameSlider.max = nFrames - 1;
frameSlider.addEventListener("input", () => {
  currentFrame = parseInt(frameSlider.value);
  updateFrameUI();
  if (displayMode === "single") buildSingle(currentFrame);
  buildSagittal();
});

// Mode radios (present in both full and shared templates)
document.querySelectorAll("input[name=mode]").forEach(r =>
  r.addEventListener("change", () => setMode(r.value))
);

// FPS slider (present in both full and shared templates)
const fpsSlider = document.getElementById("fps-slider");
fpsSlider.addEventListener("input", () => {
  document.getElementById("fps-val").textContent = fpsSlider.value;
  if (playTimer) startPlay();  // restart with new fps
});

// Sagittal + Camera + Export (panel only)
let sagZSlider = null, sagYSlider = null, sagClipSlider = null;
if (!VIEWER_CONFIG.shared) {

sagZSlider = document.getElementById("sag-z-slider");
sagZSlider.value = sagZOffset;
document.getElementById("sag-z-val").textContent = sagZOffset;
sagZSlider.addEventListener("input", () => {
  sagZOffset = parseInt(sagZSlider.value);
  document.getElementById("sag-z-val").textContent = sagZOffset;
  if (displayMode === "single") buildSingle(currentFrame);
  else buildSagittal();
});

sagYSlider = document.getElementById("sag-y-slider");
sagYSlider.addEventListener("input", () => {
  sagYCenter = parseInt(sagYSlider.value);
  document.getElementById("sag-y-val").textContent = sagYCenter;
  buildSagittal();
});

sagClipSlider = document.getElementById("sag-clip-slider");
sagClipSlider.addEventListener("input", () => {
  sagClipDist = parseInt(sagClipSlider.value);
  document.getElementById("sag-clip-val").textContent = sagClipDist;
  buildSagittal();
});

document.getElementById("hide-sag").addEventListener("change", e => {
  sagHidden = e.target.checked;
  if (sagMesh) sagMesh.visible = !sagHidden;
});

document.getElementById("sag-reset-btn").addEventListener("click", () => {
  sagYCenter  = 140;
  sagClipDist = SAG_Z;
  sagYSlider.value      = 140;  document.getElementById("sag-y-val").textContent   = 140;
  sagClipSlider.value   = SAG_Z; document.getElementById("sag-clip-val").textContent = SAG_Z;
  buildSagittal();
});

document.querySelectorAll(".cam-btn").forEach(btn =>
  btn.addEventListener("click", () => applyPreset(btn.dataset.preset))
);

document.getElementById("export-btn").addEventListener("click", () =>
  exportVideo(parseInt(fpsSlider.value))
);

} // end !shared

// Playback
const playBtn = document.getElementById("play-btn");
const prevBtn = document.getElementById("prev-btn");
const nextBtn = document.getElementById("next-btn");

function startPlay() {
  stopPlay();
  if (displayMode !== "single") setMode("single");
  const fps = parseInt(fpsSlider.value);
  playBtn.innerHTML = "&#9632;&#160;&#160;Stop";
  playBtn.classList.add("active");
  playTimer = setInterval(() => {
    currentFrame = (currentFrame + 1) % nFrames;
    updateFrameUI();
    buildSingle(currentFrame);
    buildSagittal();
  }, 1000 / fps);
}
function stopPlay() {
  if (playTimer) { clearInterval(playTimer); playTimer = null; }
  playBtn.innerHTML = "&#9654;&#160;&#160;Play";
  playBtn.classList.remove("active");
}

playBtn.addEventListener("click", () => {
  if (playTimer) stopPlay();
  else           startPlay();
});

prevBtn.addEventListener("click", () => {
  stopPlay();
  currentFrame = (currentFrame - 1 + nFrames) % nFrames;
  updateFrameUI();
  if (displayMode === "stack") buildStack();
  else { buildSingle(currentFrame); buildSagittal(); }
});

nextBtn.addEventListener("click", () => {
  stopPlay();
  currentFrame = (currentFrame + 1) % nFrames;
  updateFrameUI();
  if (displayMode === "stack") buildStack();
  else { buildSingle(currentFrame); buildSagittal(); }
});

// ── Loading / WebSocket ────────────────────────────────────────────────────
const overlay  = document.getElementById("loading-overlay");
const phaseEl  = document.getElementById("load-phase");
const pctEl    = document.getElementById("load-pct");
const barEl    = document.getElementById("progress-bar");

function setProgress(pct, label) {
  barEl.style.width    = pct + "%";
  pctEl.textContent    = Math.round(pct) + "%";
  phaseEl.textContent  = label;
}

function onLoadComplete() {
  overlay.style.display = "none";
  applyPreset("perspective");
  if (!VIEWER_CONFIG.shared) buildSlotsUI();
  buildStack();
  updateFrameUI();
}

// jobId is injected by the Django template (set when auto-reloading an expired shared link)
const activeJobId = jobId || new URLSearchParams(window.location.search).get("job_id");

if (nFrames > 0 && !activeJobId) {
  onLoadComplete();
} else if (activeJobId) {
  // Connect WebSocket for live progress
  const wsProto = location.protocol === "https:" ? "wss" : "ws";
  const wsUrl = `${wsProto}://${location.host}/ws/progress/${activeJobId}/`;
  console.log("[ws] connecting to", wsUrl);
  const ws = new WebSocket(wsUrl);

  ws.onopen = () => console.log("[ws] connected");
  ws.onclose = e => console.log("[ws] closed", e.code, e.reason);
  ws.onerror = e => console.log("[ws] error", e);

  ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.phase === "download") {
      const mb_done  = (msg.done  / 1048576).toFixed(1);
      const mb_total = (msg.total / 1048576).toFixed(1);
      const pct = msg.total > 0 ? Math.min((msg.done / msg.total) * 45, 45) : 0;
      const label = msg.total > 0
        ? `Downloading… ${mb_done} / ${mb_total} MB`
        : `Downloading… ${mb_done} MB`;
      setProgress(pct, label);
    } else if (msg.phase === "decode") {
      const pct = msg.total > 0 ? 45 + (msg.done / msg.total) * 50 : 45;
      setProgress(pct, `Decoding frames… ${msg.done}/${msg.total}`);
    } else if (msg.phase === "storing") {
      setProgress(97, "Storing frames…");
    } else if (msg.phase === "complete") {
      setProgress(100, "Done!");
      window.location.href = `/viewer/${studyId}/?cache_id=${encodeURIComponent(cacheId)}`;
    } else if (msg.phase === "error") {
      phaseEl.textContent = "Error: " + msg.msg;
      phaseEl.style.color = "#e74c3c";
      stopPolling();
    } else if (msg.phase === "init") {
      setProgress(0, msg.msg);
    }
  };
  ws.onerror = () => { phaseEl.textContent = "Connection error — please refresh."; };

  // Polling fallback — catches the case where the task finishes
  // before the WebSocket connects (race condition)
  let pollTimer = null;
  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

  let pollSecs = 0;
  pollTimer = setInterval(async () => {
    pollSecs += 2;
    try {
      const resp = await fetch(`/api/status/${studyId}/?cache_id=${encodeURIComponent(cacheId)}`);
      const data = await resp.json();
      if (data.status === "ready") {
        stopPolling();
        window.location.href = `/viewer/${studyId}/?cache_id=${encodeURIComponent(cacheId)}`;
      } else if (data.status === "error") {
        stopPolling();
        phaseEl.textContent = "Error — check server logs.";
        phaseEl.style.color = "#e74c3c";
      } else {
        phaseEl.textContent = `Processing… (${pollSecs}s) — status: ${data.status}`;
      }
    } catch (e) {
      console.error("[poll] error:", e);
    }
  }, 2000);

} else {
  // No frames and no job_id — check if a task is still running before giving up.
  // If status is "ready" but meta is still absent, that is a server-side Redis
  // misconfiguration; reloading would loop forever, so fall through to search.
  (async () => {
    try {
      const resp = await fetch(`/api/status/${studyId}/?cache_id=${encodeURIComponent(cacheId)}`);
      const data = await resp.json();
      if (data.status === "loading" && data.job_id) {
        // Task still running — reconnect to the progress page
        window.location.href = `/viewer/${studyId}/?cache_id=${encodeURIComponent(cacheId)}&job_id=${data.job_id}`;
        return;
      }
    } catch (e) {
      console.error("[status check] error:", e);
    }
    window.location.href = "/";
  })();
}

function getCsrfToken() {
  return document.querySelector("meta[name=csrf-token]")?.content || "";
}

// ── Share button (panel only) ─────────────────────────────────────────────────
if (!VIEWER_CONFIG.shared) {
document.getElementById("share-btn").addEventListener("click", () => {
  const url = `${location.origin}/share/${studyId}/?cache_id=${encodeURIComponent(cacheId)}`;
  navigator.clipboard.writeText(url).then(() => {
    const confirm = document.getElementById("share-confirm");
    confirm.style.display = "block";
    setTimeout(() => { confirm.style.display = "none"; }, 2500);
  });
});
} // end !shared

// ── Initial state ────────────────────────────────────────────────────────────
if (!VIEWER_CONFIG.shared) {
  sagZSlider.value = sagZOffset;
  document.getElementById("sag-z-val").textContent = sagZOffset;
  buildSlotsUI();
}
