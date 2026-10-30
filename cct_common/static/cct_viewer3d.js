// cct_viewer3d.js — the 3D preview's shared core (cct_common/static, 0.29.0;
// served as static/cct/cct_viewer3d.js). Gear, Sprocket, Worm, Bevel and
// Planetary each grew their viewer3d.js from one another; what they share is
// here, and each keeps its own loaders and motion on top:
//
//   import * as THREE from "./three.module.js";
//   import { OrbitControls } from "./jsm/controls/OrbitControls.js";
//   import { createViewer, b64ToBuffer } from "./cct/cct_viewer3d.js";
//   const v = createViewer(container, { THREE, OrbitControls, oblique: [0.55, -0.9, 0.65],
//                                       onTick: (dt) => { if (spinning) step(dt * rate); } });
//   const token = v.newLoad();  … if (!v.isCurrent(token)) return { stale: true };
//   v.frame(sphere.center, sphere.radius);   v.snap("top", group);
//
// Three.js comes in as an option, so this file doesn't care where an app keeps
// its copy. The model's axis is z (camera.up), as in every app.

// Preset directions round the model's bounding sphere. A value is a direction,
// or [direction, up] (Sprocket's). The up axis stays +z: the OrbitControls fix
// their orbit axis from camera.up when they're made, so "top" looks down from a
// hair in front instead of flipping up to +y. "oblique" comes from the options.
export const SNAPS = {
  top: [0, -0.001, 1], bottom: [0, -0.001, -1],
  front: [0, -1, 0], back: [0, 1, 0], left: [-1, 0, 0], right: [1, 0, 0],
};

export function b64ToBuffer(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

export function createViewer(container, {
  THREE, OrbitControls,
  clearColor = 0xf4f6f9, fov = 35, oblique = [0.55, -0.9, 0.65],
  clipping = false,          // renderer.localClippingEnabled, for a section view
  onTick = null,             // onTick(dt): each frame, dt in s (≤ 0.1), before the controls
} = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setClearColor(clearColor);
  if (clipping) renderer.localClippingEnabled = true;
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(fov, 1, 0.1, 100000);
  camera.up.set(0, 0, 1);                        // the part's axis is z
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a9199, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 1.6);
  key.position.set(1, -1.5, 2);
  camera.add(key);                               // light follows the view
  scene.add(camera);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  const resize = () => {
    const w = container.clientWidth, h = container.clientHeight || w * 0.75;
    renderer.setSize(w, h, false);
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = h + "px";
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(container);
  resize();

  let lastRadius = 0, lastFrame = 0, latest = 0;
  const snaps = { ...SNAPS, oblique };

  const viewer = {
    THREE, renderer, scene, camera, controls,
    onTick,

    // Only the newest load may reach the screen: take a token as a load starts,
    // and drop its result if another load has started since.
    newLoad() { return ++latest; },
    isCurrent(token) { return token === latest; },

    // Keep the person's view while they tweak; refit when the size jumps (> 20 %).
    frame(center, radius) {
      if (!lastRadius || Math.abs(radius - lastRadius) / lastRadius > 0.2) {
        controls.target.copy(center);
        const dist = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.1;
        camera.position.set(center.x + dist * 0.35, center.y - dist * 0.55, center.z + dist * 0.75);
        camera.near = dist / 1000;
        camera.far = dist * 10;
        camera.updateProjectionMatrix();
      }
      lastRadius = radius;
    },

    // Jump to a preset view of `obj` (SNAPS, or "oblique"), far enough for the
    // whole of it to fill the view. False for no model or an unknown view.
    snap(view, obj) {
      const preset = snaps[view];
      if (!obj || !preset) return false;
      const [dirArr, up] = Array.isArray(preset[0]) ? preset : [preset, null];
      const sphere = new THREE.Box3().setFromObject(obj).getBoundingSphere(new THREE.Sphere());
      const dist = sphere.radius / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.15;
      const dir = new THREE.Vector3(...dirArr).normalize();
      if (up) camera.up.set(...up);
      controls.target.copy(sphere.center);
      camera.position.copy(sphere.center).addScaledVector(dir, dist);
      camera.near = dist / 1000;
      camera.far = dist * 10;
      camera.updateProjectionMatrix();
      controls.update();
      return true;
    },

    // For the browser harnesses: where the camera is and what it looks at.
    cameraState() {
      return { position: camera.position.toArray(), target: controls.target.toArray() };
    },
  };

  (function loop(t) {
    requestAnimationFrame(loop);
    const dt = lastFrame ? Math.min(0.1, (t - lastFrame) / 1000) : 0;
    lastFrame = t;
    if (viewer.onTick) viewer.onTick(dt);
    controls.update();
    renderer.render(scene, camera);
  })(0);

  return viewer;
}
