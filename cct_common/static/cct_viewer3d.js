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
// its copy. The model's axis is z (camera.up), as in every app. Since 0.30.0
// an app whose view differs passes options (canvas, lights, controls, snaps,
// refit, frameDir, fitMargin, fit, visibleOnly; see createViewer) instead of
// keeping its own frame() and snap().

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

// Every option below defaults to what the core did before it had the option
// (0.30.0), so an app passes only where it differs.
export function createViewer(container, {
  THREE, OrbitControls,
  clearColor = 0xf4f6f9, fov = 35, oblique = [0.55, -0.9, 0.65],
  clipping = false,          // renderer.localClippingEnabled, for a section view
  onTick = null,             // onTick(dt): each frame, dt in s (≤ 0.1), before the controls
  canvas = null,             // draw into this canvas, sized from its own box, its CSS left alone
                             // (Pulley's); without one, a new canvas in `container`
  maxPixelRatio = Infinity,  // cap on window.devicePixelRatio
  lights = null,             // lights(scene, camera, THREE): the app's lights, instead of the
                             // hemisphere and the key light that follows the view
  controls: controlOptions = {},   // set on the OrbitControls once made: dampingFactor,
                                   // screenSpacePanning, minDistance, maxDistance, …
  snaps: snapOptions = {},   // merged over SNAPS: a direction, or [direction, up]
  refit = "size",            // frame(): "size" refits only on a jump (> 20 %); "always", every call
  frameDir = [0.35, -0.55, 0.75],  // frame()'s view, as given: its length scales the distance
  fitMargin = 1.1,           // frame()'s distance: radius / sin(fov / 2) × fitMargin
  fit = "sphere",            // what snap() fits: the bounding sphere's radius, or "halfExtent":
                             // the box's largest half side (Planetary's)
  visibleOnly = false,       // snap() fits only the visible meshes (Bevel's showOnly, Pulley's drive)
} = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true,
                                             ...(canvas ? { canvas } : {}) });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, maxPixelRatio));
  renderer.setClearColor(clearColor);
  if (clipping) renderer.localClippingEnabled = true;
  if (!canvas) container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(fov, 1, 0.1, 100000);
  camera.up.set(0, 0, 1);                        // the part's axis is z
  if (lights) lights(scene, camera, THREE);
  else {
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8a9199, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(1, -1.5, 2);
    camera.add(key);                             // light follows the view
  }
  scene.add(camera);

  // Made with camera.up +z, so dragging orbits the part's axis; a snap's own up
  // turns the view, not that axis.
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  Object.assign(controls, controlOptions);

  const resize = () => {
    let w, h;
    if (canvas) {                                // the page sizes the canvas
      const box = canvas.getBoundingClientRect();
      w = box.width || 400; h = box.height || 300;
      renderer.setSize(w, h, false);
    } else {
      w = container.clientWidth; h = container.clientHeight || w * 0.75;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = h + "px";
    }
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(canvas || container);
  resize();

  let lastRadius = 0, lastFrame = 0, latest = 0;
  const snaps = { ...SNAPS, oblique, ...snapOptions };

  // What snap() fits `obj` to: { center, radius }, or null when there's nothing to see.
  const extent = (obj) => {
    const box = new THREE.Box3();
    if (visibleOnly) {
      obj.updateWorldMatrix(true, true);
      obj.traverseVisible((o) => {
        if (!o.isMesh || !o.geometry) return;
        if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
        box.union(o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld));
      });
    } else box.setFromObject(obj);
    if (box.isEmpty()) return null;
    if (fit === "halfExtent") {
      const size = box.getSize(new THREE.Vector3());
      return { center: box.getCenter(new THREE.Vector3()), radius: Math.max(size.x, size.y, size.z) / 2 };
    }
    return box.getBoundingSphere(new THREE.Sphere());
  };

  const viewer = {
    THREE, renderer, scene, camera, controls,
    onTick,
    resize,                                      // e.g. once the page has laid the canvas out

    // Only the newest load may reach the screen: take a token as a load starts,
    // and drop its result if another load has started since.
    newLoad() { return ++latest; },
    isCurrent(token) { return token === latest; },

    // Keep the person's view while they tweak; refit when the size jumps (> 20 %).
    // With refit "always", every call refits.
    frame(center, radius) {
      if (refit === "always" || !lastRadius || Math.abs(radius - lastRadius) / lastRadius > 0.2) {
        controls.target.copy(center);
        const dist = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * fitMargin;
        camera.position.set(center.x + dist * frameDir[0], center.y + dist * frameDir[1],
                            center.z + dist * frameDir[2]);
        camera.near = dist / 1000;
        camera.far = dist * 10;
        camera.updateProjectionMatrix();
      }
      lastRadius = radius;
    },

    // Jump to a preset view of `obj` (SNAPS, "oblique", the app's snaps), far
    // enough for the whole of it to fill the view. A preset without an up puts
    // +z back, so a view after one with its own up isn't left rolled (0.30.0).
    // False for no model, nothing visible or an unknown view.
    snap(view, obj) {
      const preset = snaps[view];
      if (!obj || !preset) return false;
      const [dirArr, up] = Array.isArray(preset[0]) ? preset : [preset, null];
      const sphere = extent(obj);
      if (!sphere) return false;
      const dist = sphere.radius / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.15;
      const dir = new THREE.Vector3(...dirArr).normalize();
      camera.up.set(...(up || [0, 0, 1]));
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
