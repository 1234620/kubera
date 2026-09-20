/* A rotatable wireframe Earth on a canvas.

   No library. A globe is an orthographic projection plus drag handling, and both
   are short enough to write directly — which also means no runtime dependency
   and no texture image to load.

   Coastlines come from `web/data/land.json`: Natural Earth 110m land (public
   domain), reduced to 57 landmasses and 4,494 points at one decimal place. One
   decimal is about 11km at the equator, far finer than a one-pixel stroke, so
   the simplification is invisible and the file is 57KB.

   Drawn as STROKED coastlines rather than filled continents, deliberately. In
   monochrome a filled landmass is a grey blob and Africa stops being
   recognisable; an outline reads instantly and suits the near-black page. It
   also sidesteps polygon clipping at the horizon — a stroke can simply break
   where it goes over the edge, whereas a fill would need the horizon stitched
   into the path.

   Colours are read from the CSS custom properties, so a theme change reaches
   the canvas too. */

const LAND_URL = "data/land.json";

const AUTO_SPIN_DEG_PER_SEC = 4.5;   // slow enough to notice only if you look
const DRAG_SENSITIVITY = 0.32;       // degrees of rotation per pixel dragged
const INERTIA_DECAY = 0.94;          // per frame, so a flick coasts to a stop
const MAX_PITCH = 78;                // stop short of the poles, which look bad
const GRATICULE_STEP = 30;           // degrees between grid lines

const DEG = Math.PI / 180;

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Orthographic projection: lon/lat to screen, plus whether the point faces us.
 *
 * `cosC` is the cosine of the angular distance from the centre of the visible
 * disc. Positive means the point is on the near side of the sphere; negative
 * means it is round the back and must not be drawn.
 */
function project(lon, lat, yaw, pitch, radius, cx, cy) {
  const lambda = (lon - yaw) * DEG;
  const phi = lat * DEG;
  const pitchRad = pitch * DEG;

  const cosPhi = Math.cos(phi);
  const sinPhi = Math.sin(phi);
  const cosLambda = Math.cos(lambda);

  const cosC = Math.sin(pitchRad) * sinPhi + Math.cos(pitchRad) * cosPhi * cosLambda;

  return {
    x: cx + radius * cosPhi * Math.sin(lambda),
    y: cy - radius * (Math.cos(pitchRad) * sinPhi - Math.sin(pitchRad) * cosPhi * cosLambda),
    visible: cosC > 0,
    depth: cosC,
  };
}

/** Stroke a lon/lat path, breaking it wherever it crosses the horizon. */
function strokePath(ctx, points, state) {
  const { yaw, pitch, radius, cx, cy } = state;
  let drawing = false;

  ctx.beginPath();
  for (const [lon, lat] of points) {
    const point = project(lon, lat, yaw, pitch, radius, cx, cy);
    if (!point.visible) {
      drawing = false;
      continue;
    }
    if (drawing) {
      ctx.lineTo(point.x, point.y);
    } else {
      ctx.moveTo(point.x, point.y);
      drawing = true;
    }
  }
  ctx.stroke();
}

function graticule() {
  const lines = [];
  for (let lon = -180; lon < 180; lon += GRATICULE_STEP) {
    const meridian = [];
    for (let lat = -90; lat <= 90; lat += 3) meridian.push([lon, lat]);
    lines.push(meridian);
  }
  for (let lat = -60; lat <= 60; lat += GRATICULE_STEP) {
    const parallel = [];
    for (let lon = -180; lon <= 180; lon += 3) parallel.push([lon, lat]);
    lines.push(parallel);
  }
  return lines;
}

export async function mountGlobe(canvas) {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const ctx = canvas.getContext("2d");
  const grid = graticule();

  let land = [];
  try {
    land = await fetch(LAND_URL).then((response) => response.json());
  } catch {
    // The hero still works without coastlines -- grid and rim alone read as a
    // globe -- so a failed fetch degrades rather than blanking the page.
    land = [];
  }

  const state = { yaw: 18, pitch: 12, radius: 0, cx: 0, cy: 0 };
  let velocity = 0;
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  let lastFrame = performance.now();

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    state.cx = rect.width / 2;
    state.cy = rect.height / 2;
    state.radius = Math.min(rect.width, rect.height) / 2 - 2;
  }

  function draw() {
    const { cx, cy, radius } = state;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (radius <= 0) return;

    const accent = token("--accent");

    // The sphere: a dark disc lit from the upper left, so the globe reads as a
    // solid object rather than a flat ring of lines.
    const body = ctx.createRadialGradient(
      cx - radius * 0.4, cy - radius * 0.45, radius * 0.1,
      cx, cy, radius
    );
    body.addColorStop(0, token("--globe-core"));
    body.addColorStop(0.55, token("--globe-mid"));
    body.addColorStop(1, token("--globe-edge"));
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fill();

    ctx.lineJoin = "round";
    ctx.lineCap = "round";

    // Graticule first, faint, so coastlines sit on top of it.
    ctx.globalAlpha = 0.1;
    ctx.strokeStyle = accent;
    ctx.lineWidth = 0.6;
    for (const line of grid) strokePath(ctx, line, state);

    // Coastlines.
    ctx.globalAlpha = 0.62;
    ctx.lineWidth = 1;
    for (const ring of land) strokePath(ctx, ring, state);

    // Rim light: the terminator, which is what makes it look spherical.
    ctx.globalAlpha = 0.28;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  function frame(now) {
    const elapsed = Math.min((now - lastFrame) / 1000, 0.1);
    lastFrame = now;

    if (dragging) {
      // Held still: no drift, so the user is in control of what they are looking at.
    } else if (Math.abs(velocity) > 0.01) {
      state.yaw += velocity;
      velocity *= INERTIA_DECAY;
    } else if (!reduced) {
      state.yaw += AUTO_SPIN_DEG_PER_SEC * elapsed;
    }

    state.yaw = ((state.yaw % 360) + 360) % 360;
    draw();
    requestAnimationFrame(frame);
  }

  function pointerDown(event) {
    dragging = true;
    velocity = 0;
    lastX = event.clientX;
    lastY = event.clientY;
    canvas.setPointerCapture?.(event.pointerId);
    canvas.classList.add("grabbing");
  }

  function pointerMove(event) {
    if (!dragging) return;
    const dx = event.clientX - lastX;
    const dy = event.clientY - lastY;
    lastX = event.clientX;
    lastY = event.clientY;

    state.yaw += dx * DRAG_SENSITIVITY;
    state.pitch = Math.max(-MAX_PITCH, Math.min(MAX_PITCH, state.pitch + dy * DRAG_SENSITIVITY));
    velocity = dx * DRAG_SENSITIVITY * 0.5;
  }

  function pointerUp(event) {
    dragging = false;
    canvas.classList.remove("grabbing");
    // Releasing the capture matters: held open, the canvas keeps receiving every
    // subsequent pointer event and the PAGE STOPS SCROLLING. Easy to miss,
    // because the drag itself works perfectly.
    if (event && canvas.hasPointerCapture?.(event.pointerId)) {
      canvas.releasePointerCapture(event.pointerId);
    }
  }

  // Keyboard control, because a drag-only object is unreachable without a
  // pointer and this is the page's one interactive ornament.
  function keyDown(event) {
    const step = event.shiftKey ? 15 : 5;
    if (event.key === "ArrowLeft") state.yaw -= step;
    else if (event.key === "ArrowRight") state.yaw += step;
    else if (event.key === "ArrowUp") state.pitch = Math.min(MAX_PITCH, state.pitch + step);
    else if (event.key === "ArrowDown") state.pitch = Math.max(-MAX_PITCH, state.pitch - step);
    else return;
    velocity = 0;
    event.preventDefault();
  }

  canvas.addEventListener("pointerdown", pointerDown);
  canvas.addEventListener("pointermove", pointerMove);
  canvas.addEventListener("pointerup", pointerUp);
  canvas.addEventListener("pointercancel", pointerUp);
  canvas.addEventListener("keydown", keyDown);
  new ResizeObserver(resize).observe(canvas);

  resize();
  requestAnimationFrame(frame);
}
