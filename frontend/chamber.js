(function () {
  "use strict";

  const canvas = document.getElementById("chamber-canvas");
  if (!canvas) {
    throw new Error('Canvas with id "chamber-canvas" was not found.');
  }

  const context = canvas.getContext("2d");
  if (!context) {
    throw new Error("Canvas 2D drawing is not available.");
  }

  const WIDTH = 900;
  const HEIGHT = 560;
  const LIGHT_COLORS = {
    ring_light: [248, 244, 216],
    daylight: [255, 240, 207],
    tube_light: [216, 234, 255],
  };
  const grains = [];
  let lightingColor = LIGHT_COLORS.ring_light.slice();
  let targetLightingColor = LIGHT_COLORS.ring_light.slice();
  let yaw = -0.65;
  let pitch = 0.68;
  let dragging = false;
  let lastPointer = null;
  let flashAmount = 0;
  let sceneMessage = "Camera and lighting are simulated";
  let pouring = false;
  let pourCallback = null;
  let sourceTrayPresent = true;
  let sourceTrayRemoving = false;
  let sourceTrayOffset = 0;

  function colorText(color) {
    return "rgb(" + color.map(Math.round).join(", ") + ")";
  }

  function resizeCanvas() {
    const bounds = canvas.getBoundingClientRect();
    const pixelRatio = window.devicePixelRatio || 1;
    const cssWidth = bounds.width || WIDTH;

    canvas.width = Math.round(cssWidth * pixelRatio);
    canvas.height = Math.round((cssWidth * HEIGHT / WIDTH) * pixelRatio);
    context.setTransform(
      canvas.width / WIDTH,
      0,
      0,
      canvas.height / HEIGHT,
      0,
      0,
    );
  }

  // Turn a point in the chamber into a point on the canvas.
  function project(x, y, z) {
    const rotatedX = x * Math.cos(yaw) - z * Math.sin(yaw);
    const depth = x * Math.sin(yaw) + z * Math.cos(yaw);
    const scale = 78;
    return {
      x: WIDTH / 2 + rotatedX * scale,
      y: 378 - (y * Math.cos(pitch) - depth * Math.sin(pitch)) * scale,
    };
  }

  function drawPolygon(points, fill, stroke) {
    context.beginPath();
    points.forEach(function (point, index) {
      const screenPoint = project(point[0], point[1], point[2]);
      if (index === 0) {
        context.moveTo(screenPoint.x, screenPoint.y);
      } else {
        context.lineTo(screenPoint.x, screenPoint.y);
      }
    });
    context.closePath();
    context.fillStyle = fill;
    context.fill();
    if (stroke) {
      context.strokeStyle = stroke;
      context.lineWidth = 3;
      context.stroke();
    }
  }

  function drawLine(points, color, width) {
    context.beginPath();
    points.forEach(function (point, index) {
      const screenPoint = project(point[0], point[1], point[2]);
      if (index === 0) {
        context.moveTo(screenPoint.x, screenPoint.y);
      } else {
        context.lineTo(screenPoint.x, screenPoint.y);
      }
    });
    context.strokeStyle = color;
    context.lineWidth = width;
    context.lineCap = "round";
    context.stroke();
  }

  function drawChamber() {
    const left = -2.8;
    const right = 2.8;
    const front = 1.9;
    const back = -1.9;
    const floor = 0.15;
    const top = 3.15;

    context.fillStyle = "#edf2ef";
    context.fillRect(0, 0, WIDTH, HEIGHT);

    // Draw the enclosed chamber interior before the front panel.
    drawPolygon(
      [[left, floor, back], [right, floor, back], [right, floor, front], [left, floor, front]],
      "#171a1b",
      "#59666d",
    );
    drawPolygon(
      [[left, floor, back], [right, floor, back], [right, top, back], [left, top, back]],
      "rgba(194, 207, 198, 0.9)",
      "#7b8981",
    );
    drawPolygon(
      [[left, floor, front], [left, floor, back], [left, top, back], [left, top, front]],
      "rgba(161, 177, 168, 0.82)",
      "#7b8981",
    );
    drawPolygon(
      [[right, floor, back], [right, floor, front], [right, top, front], [right, top, back]],
      "rgba(176, 190, 181, 0.82)",
      "#7b8981",
    );

    const box = { floor, left, right, front, back, top };
    // Closed front and roof are drawn before the equipment visible inside.
    drawPolygon(
      [[left, floor, front], [right, floor, front], [right, top, front], [left, top, front]],
      "rgba(174, 187, 178, 0.92)",
      "#68766e",
    );
    drawTopPanel(box);

    // Rice lands on a separate black tray inside the closed box.
    drawPolygon(
      [[-2.25, floor + 0.03, -1.35], [2.25, floor + 0.03, -1.35],
        [2.25, floor + 0.03, 1.35], [-2.25, floor + 0.03, 1.35]],
      "#090b0c",
      "#343a3b",
    );

    drawLedStrip(boxSideStripPoints(left));
    drawLedStrip(boxSideStripPoints(right));
    if (sourceTrayPresent) {
      drawSourceTray(floor, sourceTrayOffset);
      if (sourceTrayRemoving) {
        sourceTrayOffset -= 0.14;
        if (sourceTrayOffset <= -5.5) {
          sourceTrayPresent = false;
          sourceTrayRemoving = false;
          sourceTrayOffset = -5.5;
          sceneMessage = "Rice transferred; empty tray removed";
          const callback = pourCallback;
          pourCallback = null;
          if (callback) callback();
        }
      }
    }

    // These labels stay level and readable while the chamber rotates.
    context.fillStyle = "#1d3930";
    context.font = "bold 24px sans-serif";
    context.textAlign = "left";
    context.fillText("SIMULATED IMAGING CHAMBER - 3D VIEW", 26, 48);
    context.font = "15px sans-serif";
    context.fillText("Drag the chamber to rotate", 26, 76);
    context.textAlign = "center";
    context.fillText(sceneMessage, WIDTH / 2, 520);
    context.textAlign = "start";

    return { floor, left, right, front, back, top };
  }

  function boxSideStripPoints(x) {
    return [[x, 2.86, -1.48], [x, 2.86, 1.48]];
  }

  function drawLedStrip(points) {
    const start = points[0];
    const end = points[1];
    const centerX = (start[0] + end[0]) / 2;
    const centerY = (start[1] + end[1]) / 2;
    const centerZ = (start[2] + end[2]) / 2;

    // Dark aluminum channel, glowing diffuser, and individual LED diodes.
    drawLine(points, "#505b5e", 20);
    context.save();
    context.shadowColor = colorText(lightingColor);
    context.shadowBlur = 20;
    drawLine(points, colorText(lightingColor), 9);
    context.restore();
    drawLine(points, "#fff8df", 4);
    for (let step = -4; step <= 4; step += 1) {
      const fraction = (step + 4) / 8;
      const z = start[2] + (end[2] - start[2]) * fraction;
      const led = project(centerX, centerY, z);
      context.beginPath();
      context.arc(led.x, led.y, 3.3, 0, Math.PI * 2);
      context.fillStyle = "#fffdf0";
      context.fill();
    }
  }

  function drawSourceTray(floor, offset) {
    const left = -2.38 + offset;
    const lip = -0.92 + offset;
    const back = -0.58;
    const front = 0.58;
    const highY = floor + 0.72;
    const lipY = floor + 0.42;

    drawLine([[left + 0.15, floor + 0.05, back], [left + 0.15, highY - 0.06, back]], "#545d5e", 4);
    drawLine([[left + 0.15, floor + 0.05, front], [left + 0.15, highY - 0.06, front]], "#545d5e", 4);
    drawPolygon(
      [[left, highY, back], [lip, lipY, back], [lip, lipY, front], [left, highY, front]],
      "#b8c1bb",
      "#626d67",
    );
    drawLine([[left, highY, back], [lip, lipY, back]], "#e2e5df", 5);
    drawLine([[left, highY, front], [lip, lipY, front]], "#66736b", 5);
    drawLine([[lip, lipY, back], [lip, lipY, front]], "#66736b", 5);
  }

  function drawTopPanel(box) {
    const opening = { left: -0.66, right: 0.66, back: -1.21, front: 0.98 };
    const outline = [
      [box.left, box.top, box.back],
      [box.right, box.top, box.back],
      [box.right, box.top, box.front],
      [box.left, box.top, box.front],
    ];
    const cutout = [
      [opening.left, box.top, opening.back],
      [opening.right, box.top, opening.back],
      [opening.right, box.top, opening.front],
      [opening.left, box.top, opening.front],
    ];

    context.beginPath();
    [outline, cutout].forEach(function (points) {
      points.forEach(function (point, index) {
        const screenPoint = project(point[0], point[1], point[2]);
        if (index === 0) context.moveTo(screenPoint.x, screenPoint.y);
        else context.lineTo(screenPoint.x, screenPoint.y);
      });
      context.closePath();
    });
    context.fillStyle = "#c5cec7";
    context.fill("evenodd");
    context.strokeStyle = "#68766e";
    context.lineWidth = 4;
    context.stroke();

    drawLine(cutout.concat([cutout[0]]), "#59665e", 5);
    drawLine(
      [[opening.left + 0.05, box.top, opening.back + 0.05],
        [opening.right - 0.05, box.top, opening.back + 0.05],
        [opening.right - 0.05, box.top, opening.front - 0.05],
        [opening.left + 0.05, box.top, opening.front - 0.05],
        [opening.left + 0.05, box.top, opening.back + 0.05]],
      "#e2e8e3",
      2,
    );
  }

  function makeRoundedRect3D(left, right, back, front, radius, y, segsPerCorner) {
    const points = [];
    const corners = [
      { cx: right - radius, cz: front - radius, startAngle: 0 },
      { cx: left + radius, cz: front - radius, startAngle: Math.PI / 2 },
      { cx: left + radius, cz: back + radius, startAngle: Math.PI },
      { cx: right - radius, cz: back + radius, startAngle: (3 * Math.PI) / 2 },
    ];
    for (let c = 0; c < corners.length; c += 1) {
      const corner = corners[c];
      for (let s = 0; s < segsPerCorner; s += 1) {
        const angle = corner.startAngle + (s / segsPerCorner) * (Math.PI / 2);
        points.push([
          corner.cx + Math.cos(angle) * radius,
          y,
          corner.cz + Math.sin(angle) * radius,
        ]);
      }
    }
    return points;
  }

  let phoneFlipped = false; // false: Camera on Top, Screen on Bottom; true: Screen on Top, Camera on Bottom

  const appleLogoPath = (function () {
    try {
      if (typeof Path2D !== "undefined") {
        return new Path2D(
          "M155.8 133.3c-2.3 5.4-5.3 10.3-8.9 14.8-4.9 6.1-9.9 10.7-15 13.7-5.1 3-10.5 4.5-16.1 4.5-4 0-8.6-.9-13.8-2.8-5.2-1.9-9.8-2.8-13.7-2.8-4.3 0-9.2 1-14.7 2.9-5.5 2-10 2.9-13.6 2.9-5.9 0-11.6-1.6-17.1-4.8-5.5-3.2-10.7-8-15.6-14.4-7.4-9.6-13.2-20.9-17.4-33.8-4.2-12.9-6.3-25.2-6.3-36.9 0-15.3 4.1-28.1 12.3-38.4 8.2-10.3 18.7-15.5 31.5-15.5 4.9 0 10.4 1.2 16.4 3.7 6 2.5 9.9 3.7 11.6 3.7 1.8 0 5.8-1.2 12.1-3.7 6.3-2.5 11.8-3.7 16.6-3.7 11.3 0 21 3.9 29.1 11.7 4.7 4.5 8.2 9.6 10.6 15.3-10.1 6.1-15.1 14.6-15.1 25.5 0 9.4 3.5 17.3 10.6 23.6 7.1 6.3 15.5 9.8 25.3 10.5-2.2 6.5-5.1 13.5-8.7 21zm-28.7-109.9c0 6.6-2.4 12.8-7.3 18.7-4.8 5.8-10.8 9.5-18 11.1-.3-1.4-.4-2.8-.4-4.2 0-6.6 2.7-13.1 8-19.5 5.3-6.4 11.6-10.2 18.9-11.4.1 1.8.2 3.6.2 5.3z"
        );
      }
    } catch (e) {
      // Fallback handled
    }
    return null;
  })();

  function getSurfaceTransform(originX, y, originZ) {
    const p0 = project(originX, y, originZ);
    const px = project(originX + 1, y, originZ);
    const pz = project(originX, y, originZ + 1);
    return {
      p0: p0,
      vx: { x: px.x - p0.x, y: px.y - p0.y },
      vz: { x: pz.x - p0.x, y: pz.y - p0.y },
    };
  }

  function drawAppleLogo(cx, cz, y) {
    if (Math.abs(Math.sin(pitch)) < 0.08) return;
    const xform = getSurfaceTransform(cx, y, cz);
    context.save();
    context.transform(xform.vx.x, xform.vx.y, xform.vz.x, xform.vz.y, xform.p0.x, xform.p0.y);

    const scaleFactor = 0.23 / 161.0;
    context.scale(scaleFactor, scaleFactor);
    context.translate(-84.1, -90.8);

    const logoGrad = context.createLinearGradient(20, 20, 150, 160);
    logoGrad.addColorStop(0, "#ffffff");
    logoGrad.addColorStop(0.25, "#e2e8f0");
    logoGrad.addColorStop(0.5, "#94a3b8");
    logoGrad.addColorStop(0.75, "#cbd5e1");
    logoGrad.addColorStop(1, "#f8fafc");

    if (appleLogoPath) {
      context.fillStyle = logoGrad;
      context.fill(appleLogoPath);
      context.strokeStyle = "rgba(255, 255, 255, 0.4)";
      context.lineWidth = 1.2;
      context.stroke(appleLogoPath);
    } else {
      context.beginPath();
      context.arc(84.1, 90.8, 55, 0, Math.PI * 2);
      context.fillStyle = logoGrad;
      context.fill();
    }
    context.restore();
  }

  function drawIPhoneMarking(text, cx, cz, y, size) {
    if (Math.abs(Math.sin(pitch)) < 0.08) return;
    const xform = getSurfaceTransform(cx, y, cz);
    context.save();
    context.transform(xform.vx.x, xform.vx.y, xform.vz.x, xform.vz.y, xform.p0.x, xform.p0.y);
    context.scale(0.007, 0.007);
    context.fillStyle = "rgba(226, 232, 240, 0.45)";
    context.font = "bold " + (size || 14) + "px -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(text, 0, 0);
    context.restore();
  }

  function drawRealisticLens(cx, cz, baseY, outerRadius, lensHeight, lensType, facingDown) {
    const segments = 32;
    const dir = facingDown ? -1 : 1;
    const topY = baseY + lensHeight;
    const baffleY = topY - 0.003 * dir;
    const glassY = topY - 0.006 * dir;

    const baseCircle = [];
    const topCircle = [];
    for (let step = 0; step < segments; step += 1) {
      const angle = (step / segments) * Math.PI * 2;
      const cosA = Math.cos(angle);
      const sinA = Math.sin(angle);
      baseCircle.push([cx + cosA * outerRadius, baseY, cz + sinA * outerRadius]);
      topCircle.push([cx + cosA * outerRadius, topY, cz + sinA * outerRadius]);
    }

    for (let i = 0; i < segments; i += 1) {
      const next = (i + 1) % segments;
      const angle = (i / segments) * Math.PI * 2;
      const nx = Math.cos(angle + Math.PI / segments);
      const nz = Math.sin(angle + Math.PI / segments);
      const viewDir = nx * Math.sin(yaw) + nz * Math.cos(yaw);
      if (viewDir < -0.15) continue;

      const lightDot = nx * Math.cos(0.85 + yaw) + nz * Math.sin(0.85 + yaw);
      const intensity = Math.round(115 + lightDot * 65);
      const facetColor = "rgb(" + intensity + ", " + (intensity + 6) + ", " + (intensity + 14) + ")";
      drawPolygon(
        facingDown
          ? [baseCircle[i], baseCircle[next], topCircle[next], topCircle[i]]
          : [topCircle[i], topCircle[next], baseCircle[next], baseCircle[i]],
        facetColor
      );
    }

    drawPolygon(topCircle, "#94a3b8", "#cbd5e1");
    drawLine(topCircle.concat([topCircle[0]]), "#f8fafc", 1.4);

    const innerRadius = outerRadius * 0.82;
    const baffleCircle = [];
    for (let step = 0; step < segments; step += 1) {
      const angle = (step / segments) * Math.PI * 2;
      baffleCircle.push([cx + Math.cos(angle) * innerRadius, baffleY, cz + Math.sin(angle) * innerRadius]);
    }
    drawPolygon(baffleCircle, "#0b0f17", "#1e293b");
    drawLine(baffleCircle.concat([baffleCircle[0]]), "#334155", 0.8);

    const glassRadius = innerRadius * 0.88;
    const glassCircle = [];
    for (let step = 0; step < segments; step += 1) {
      const angle = (step / segments) * Math.PI * 2;
      glassCircle.push([cx + Math.cos(angle) * glassRadius, glassY, cz + Math.sin(angle) * glassRadius]);
    }
    drawPolygon(glassCircle, "#020408");

    const screenCenter = project(cx, glassY, cz);
    const screenRadius = outerRadius * 78;
    context.save();
    context.beginPath();
    glassCircle.forEach(function (pt, idx) {
      const sp = project(pt[0], pt[1], pt[2]);
      if (idx === 0) context.moveTo(sp.x, sp.y);
      else context.lineTo(sp.x, sp.y);
    });
    context.closePath();
    context.clip();

    const flare = context.createLinearGradient(
      screenCenter.x - screenRadius,
      screenCenter.y - screenRadius,
      screenCenter.x + screenRadius,
      screenCenter.y + screenRadius
    );
    flare.addColorStop(0, "rgba(6, 182, 212, 0.70)");
    flare.addColorStop(0.32, "rgba(16, 185, 129, 0.55)");
    flare.addColorStop(0.65, "rgba(99, 102, 241, 0.35)");
    flare.addColorStop(1, "rgba(2, 6, 23, 0.96)");
    context.fillStyle = flare;
    context.fill();

    if (lensType === "telephoto") {
      const prismHalf = screenRadius * 0.30;
      context.fillStyle = "#010204";
      context.fillRect(screenCenter.x - prismHalf, screenCenter.y - prismHalf, prismHalf * 2, prismHalf * 2);
      context.strokeStyle = "rgba(148, 163, 184, 0.45)";
      context.lineWidth = 1;
      context.strokeRect(screenCenter.x - prismHalf, screenCenter.y - prismHalf, prismHalf * 2, prismHalf * 2);
    } else if (lensType === "ultrawide") {
      context.beginPath();
      context.arc(screenCenter.x, screenCenter.y, screenRadius * 0.45, 0, Math.PI * 2);
      context.fillStyle = "#020408";
      context.fill();
      context.strokeStyle = "rgba(56, 189, 248, 0.5)";
      context.lineWidth = 1.2;
      context.stroke();
    } else {
      context.beginPath();
      context.arc(screenCenter.x, screenCenter.y, screenRadius * 0.42, 0, Math.PI * 2);
      context.fillStyle = "#010307";
      context.fill();
      context.strokeStyle = "rgba(16, 185, 129, 0.5)";
      context.lineWidth = 1.2;
      context.stroke();
    }

    context.beginPath();
    context.arc(
      screenCenter.x - screenRadius * 0.30,
      screenCenter.y - screenRadius * 0.30,
      Math.max(2.2, screenRadius * 0.13),
      0,
      Math.PI * 2
    );
    context.fillStyle = "rgba(255, 255, 255, 0.96)";
    context.fill();

    context.beginPath();
    context.arc(
      screenCenter.x + screenRadius * 0.24,
      screenCenter.y + screenRadius * 0.24,
      Math.max(1.4, screenRadius * 0.08),
      0,
      Math.PI * 2
    );
    context.fillStyle = "rgba(255, 255, 255, 0.55)";
    context.fill();

    context.restore();
  }

  function drawTrueToneFlash(cx, cz, islandY, facingDown) {
    const flashRadius = 0.050;
    const dir = facingDown ? -1 : 1;
    const flashY = islandY + 0.006 * dir;
    const outerPts = [];
    for (let s = 0; s < 24; s += 1) {
      const a = (s / 24) * Math.PI * 2;
      outerPts.push([cx + Math.cos(a) * flashRadius, flashY, cz + Math.sin(a) * flashRadius]);
    }
    drawPolygon(outerPts, "#334155", "#94a3b8");
    drawLine(outerPts.concat([outerPts[0]]), "#cbd5e1", 1);

    const innerRadius = flashRadius * 0.82;
    const innerPts = [];
    for (let s = 0; s < 24; s += 1) {
      const a = (s / 24) * Math.PI * 2;
      innerPts.push([cx + Math.cos(a) * innerRadius, flashY + 0.002 * dir, cz + Math.sin(a) * innerRadius]);
    }
    drawPolygon(innerPts, "#1e293b");

    const screenCenter = project(cx, flashY + 0.003 * dir, cz);
    const screenRad = innerRadius * 78;

    context.save();
    context.beginPath();
    innerPts.forEach(function (pt, idx) {
      const sp = project(pt[0], pt[1], pt[2]);
      if (idx === 0) context.moveTo(sp.x, sp.y);
      else context.lineTo(sp.x, sp.y);
    });
    context.closePath();
    context.clip();

    context.beginPath();
    context.arc(screenCenter.x, screenCenter.y, screenRad, Math.PI, 0);
    context.closePath();
    context.fillStyle = "#f59e0b";
    context.fill();

    context.beginPath();
    context.arc(screenCenter.x, screenCenter.y, screenRad, 0, Math.PI);
    context.closePath();
    context.fillStyle = "#f8fafc";
    context.fill();

    context.beginPath();
    context.arc(screenCenter.x, screenCenter.y, screenRad * 0.5, 0, Math.PI * 2);
    context.strokeStyle = "rgba(255, 255, 255, 0.75)";
    context.lineWidth = 1;
    context.stroke();

    context.beginPath();
    context.arc(screenCenter.x, screenCenter.y, Math.max(1.5, screenRad * 0.22), 0, Math.PI * 2);
    context.fillStyle = "#0f172a";
    context.fill();
    context.restore();
  }

  function drawLidarScanner(cx, cz, islandY, facingDown) {
    const lidarRadius = 0.046;
    const dir = facingDown ? -1 : 1;
    const lidarY = islandY + 0.005 * dir;
    const lidarPts = [];
    for (let s = 0; s < 24; s += 1) {
      const a = (s / 24) * Math.PI * 2;
      lidarPts.push([cx + Math.cos(a) * lidarRadius, lidarY, cz + Math.sin(a) * lidarRadius]);
    }
    drawPolygon(lidarPts, "#030712", "#1e1b4b");
    drawLine(lidarPts.concat([lidarPts[0]]), "#475569", 1);

    const innerRadius = lidarRadius * 0.82;
    const innerPts = [];
    for (let s = 0; s < 20; s += 1) {
      const a = (s / 20) * Math.PI * 2;
      innerPts.push([cx + Math.cos(a) * innerRadius, lidarY + 0.002 * dir, cz + Math.sin(a) * innerRadius]);
    }
    drawPolygon(innerPts, "#090514", "rgba(139, 92, 246, 0.5)");
  }

  function drawAcousticMic(cx, cz, islandY, facingDown) {
    const micRadius = 0.018;
    const dir = facingDown ? -1 : 1;
    const micY = islandY + 0.004 * dir;
    const micPts = [];
    for (let s = 0; s < 16; s += 1) {
      const a = (s / 16) * Math.PI * 2;
      micPts.push([cx + Math.cos(a) * micRadius, micY, cz + Math.sin(a) * micRadius]);
    }
    drawPolygon(micPts, "#020617", "#64748b");
  }

  // --- IPHONE DISPLAY SCREEN (Front Face: OLED, Dynamic Island & Live Viewfinder) ---
  function drawIPhoneScreen(phone, screenY) {
    const screenPts = makeRoundedRect3D(
      phone.left + 0.02,
      phone.right - 0.02,
      phone.back + 0.02,
      phone.front - 0.02,
      phone.radius - 0.02,
      screenY,
      6
    );
    drawPolygon(screenPts, "#020408");
    drawLine(screenPts.concat([screenPts[0]]), "#1e293b", 1);

    const xform = getSurfaceTransform(0, screenY, 0);
    context.save();
    context.transform(xform.vx.x, xform.vx.y, xform.vz.x, xform.vz.y, xform.p0.x, xform.p0.y);

    const scale = 220;
    context.scale(1 / scale, 1 / scale);

    const w = 276;
    const h = 524;
    const halfW = w / 2;
    const halfH = h / 2;

    // Viewfinder screen: live illuminated chamber preview
    const vfTop = -halfH + 46;
    const vfHeight = h - 130;
    const vfGrad = context.createLinearGradient(0, vfTop, 0, vfTop + vfHeight);
    vfGrad.addColorStop(0, "#08100e");
    vfGrad.addColorStop(0.5, "#142520");
    vfGrad.addColorStop(1, "#0a1411");
    context.fillStyle = vfGrad;
    context.fillRect(-halfW + 6, vfTop, w - 12, vfHeight);

    // Rice grain silhouettes in live optical viewfinder
    context.fillStyle = "rgba(254, 240, 138, 0.45)";
    [
      [-42, -50, 14, 5, 0.3], [22, -32, 16, 6, -0.4], [-12, 18, 15, 5, 0.5],
      [48, 38, 13, 5, -0.2], [-48, 58, 16, 6, 0.6], [12, -78, 14, 5, -0.5]
    ].forEach(function (g) {
      context.beginPath();
      context.ellipse(g[0], g[1], g[2], g[3], g[4], 0, Math.PI * 2);
      context.fill();
    });

    // Yellow autofocus target reticle
    context.strokeStyle = "#eab308";
    context.lineWidth = 1.8;
    const boxSize = 38;
    context.strokeRect(-boxSize / 2, -boxSize / 2, boxSize, boxSize);
    context.beginPath();
    context.arc(0, 0, 2.5, 0, Math.PI * 2);
    context.fillStyle = "#eab308";
    context.fill();

    // Autofocus HUD pill
    context.fillStyle = "rgba(0, 0, 0, 0.7)";
    context.fillRect(-58, boxSize / 2 + 5, 116, 17);
    context.fillStyle = "#fef08a";
    context.font = "bold 9.5px -apple-system, sans-serif";
    context.textAlign = "center";
    context.fillText("4K · 120 GRAINS DETECTED", 0, boxSize / 2 + 17);

    // Dynamic Island
    context.fillStyle = "#000000";
    const pillW = 74;
    const pillH = 22;
    const pillY = -halfH + 15;
    context.beginPath();
    context.arc(-pillW / 2 + pillH / 2, pillY + pillH / 2, pillH / 2, Math.PI / 2, (3 * Math.PI) / 2);
    context.arc(pillW / 2 - pillH / 2, pillY + pillH / 2, pillH / 2, -Math.PI / 2, Math.PI / 2);
    context.closePath();
    context.fill();
    context.strokeStyle = "rgba(255, 255, 255, 0.12)";
    context.lineWidth = 1;
    context.stroke();

    // Front sensor glint
    context.beginPath();
    context.arc(pillW / 2 - 14, pillY + pillH / 2, 3.5, 0, Math.PI * 2);
    context.fillStyle = "#083344";
    context.fill();
    context.beginPath();
    context.arc(pillW / 2 - 14, pillY + pillH / 2, 1.2, 0, Math.PI * 2);
    context.fillStyle = "#38bdf8";
    context.fill();

    // iOS Status bar (Clock & Connectivity)
    context.fillStyle = "#ffffff";
    context.font = "bold 11px -apple-system, sans-serif";
    context.textAlign = "left";
    context.fillText("9:41", -halfW + 18, -halfH + 28);
    context.textAlign = "right";
    context.fillText("5G 100%", halfW - 18, -halfH + 28);

    // Camera Mode Bar: "PHOTO" in yellow
    context.font = "bold 11px -apple-system, sans-serif";
    context.textAlign = "center";
    context.fillStyle = "#facc15";
    context.fillText("PHOTO", 0, halfH - 62);
    context.fillStyle = "rgba(255, 255, 255, 0.6)";
    context.fillText("PORTRAIT", 65, halfH - 62);
    context.fillText("VIDEO", -65, halfH - 62);

    // Zoom Buttons: 0.5 · 1x · 2 · 5
    const zoomY = halfH - 90;
    [-45, -15, 15, 45].forEach(function (zx, i) {
      context.beginPath();
      context.arc(zx, zoomY, 11, 0, Math.PI * 2);
      context.fillStyle = i === 1 ? "rgba(255, 255, 255, 0.25)" : "rgba(0, 0, 0, 0.5)";
      context.fill();
      context.fillStyle = i === 1 ? "#facc15" : "#ffffff";
      context.font = "bold 9px -apple-system, sans-serif";
      context.fillText(["0.5", "1x", "2", "5"][i], zx, zoomY + 3);
    });

    // Shutter button at bottom
    const shutterY = halfH - 32;
    context.beginPath();
    context.arc(0, shutterY, 22, 0, Math.PI * 2);
    context.strokeStyle = "#ffffff";
    context.lineWidth = 2.5;
    context.stroke();
    context.beginPath();
    context.arc(0, shutterY, 18, 0, Math.PI * 2);
    context.fillStyle = "#ffffff";
    context.fill();

    context.restore();
  }

  // --- IPHONE BACK PANEL & TRIPLE CAMERA (Rear Face: Matte AG Glass, Apple Logo & Camera Island) ---
  function drawIPhoneBackPanel(phone, faceY, facingDown) {
    const dir = facingDown ? -1 : 1;
    const topPoints = makeRoundedRect3D(phone.left, phone.right, phone.back, phone.front, phone.radius, faceY, 6);

    const pTopLeft = project(phone.left, faceY, phone.back);
    const pBottomRight = project(phone.right, faceY, phone.front);
    const glassFinish = context.createLinearGradient(
      pTopLeft.x, pTopLeft.y, pBottomRight.x, pBottomRight.y
    );
    glassFinish.addColorStop(0, "#262c34");
    glassFinish.addColorStop(0.35, "#3d4653");
    glassFinish.addColorStop(0.7, "#242932");
    glassFinish.addColorStop(1, "#2f3640");
    drawPolygon(topPoints, glassFinish);

    drawLine(topPoints.concat([topPoints[0]]), "#94a3b8", 1.4);

    const innerGlassPts = makeRoundedRect3D(
      phone.left + 0.025,
      phone.right - 0.025,
      phone.back + 0.025,
      phone.front - 0.025,
      phone.radius - 0.025,
      faceY + 0.001 * dir,
      6
    );
    drawLine(innerGlassPts.concat([innerGlassPts[0]]), "rgba(255, 255, 255, 0.15)", 1);

    // Apple Logo & iPhone typography
    drawAppleLogo(0, 0.14, faceY + 0.001 * dir, facingDown);
    drawIPhoneMarking("iPhone", 0, 0.88, faceY + 0.001 * dir, 13);

    // Elevated Squircle Camera Island with 3D Sculpted Bevel Walls
    const islandHeight = 0.048;
    const islandY = faceY + islandHeight * dir;
    const island = {
      left: -0.58,
      right: 0.08,
      back: -1.18,
      front: -0.46,
      radius: 0.13,
      topY: islandY,
    };

    const islandShadow = makeRoundedRect3D(
      island.left - 0.015,
      island.right + 0.015,
      island.back - 0.015,
      island.front + 0.015,
      island.radius + 0.015,
      faceY + 0.001 * dir,
      6
    );
    drawPolygon(islandShadow, "rgba(0, 0, 0, 0.52)");

    const islandBasePts = makeRoundedRect3D(island.left, island.right, island.back, island.front, island.radius, faceY, 6);
    const islandTopPts = makeRoundedRect3D(island.left, island.right, island.back, island.front, island.radius, island.topY, 6);

    for (let i = 0; i < islandTopPts.length; i += 1) {
      const next = (i + 1) % islandTopPts.length;
      const p1 = islandTopPts[i];
      const p2 = islandTopPts[next];
      const dx = p2[0] - p1[0];
      const dz = p2[2] - p1[2];
      const nx = dz;
      const nz = -dx;
      const len = Math.hypot(nx, nz) || 1;
      const unitNx = nx / len;
      const unitNz = nz / len;

      const lightDot = unitNx * Math.cos(0.85 + yaw) + unitNz * Math.sin(0.85 + yaw);
      const intensity = Math.round(55 + lightDot * 42);
      const wallColor = "rgb(" + intensity + ", " + (intensity + 6) + ", " + (intensity + 12) + ")";
      drawPolygon(
        facingDown
          ? [islandBasePts[i], islandBasePts[next], islandTopPts[next], islandTopPts[i]]
          : [islandTopPts[i], islandTopPts[next], islandBasePts[next], islandBasePts[i]],
        wallColor
      );
    }

    const islandScreenCorners = [
      project(island.left, island.topY, island.back),
      project(island.right, island.topY, island.front),
    ];
    const islandGrad = context.createLinearGradient(
      islandScreenCorners[0].x, islandScreenCorners[0].y,
      islandScreenCorners[1].x, islandScreenCorners[1].y
    );
    islandGrad.addColorStop(0, "#2c333c");
    islandGrad.addColorStop(0.5, "#3d4754");
    islandGrad.addColorStop(1, "#232831");
    drawPolygon(islandTopPts, islandGrad);
    drawLine(islandTopPts.concat([islandTopPts[0]]), "#cbd5e1", 1.4);

    const lensRadius = 0.108;
    const lensHeight = 0.054 * dir;

    drawRealisticLens(-0.40, -0.99, island.topY, lensRadius, lensHeight, "wide", facingDown);
    drawRealisticLens(-0.40, -0.65, island.topY, lensRadius, lensHeight, "telephoto", facingDown);
    drawRealisticLens(-0.13, -0.82, island.topY, lensRadius, lensHeight, "ultrawide", facingDown);

    drawTrueToneFlash(-0.13, -1.04, island.topY, facingDown);
    drawLidarScanner(-0.13, -0.61, island.topY, facingDown);
    drawAcousticMic(-0.40, -0.49, island.topY, facingDown);

    return project(-0.40, island.topY + lensHeight, -0.99);
  }

  function drawTripodPhone(box) {
    const mastX = box.right + 0.45;
    const mastZ = 0.05;
    const phoneTopY = box.top + 0.09;
    const phoneBottomY = box.top;
    const phoneMidY = (phoneTopY + phoneBottomY) / 2;

    context.save();
    context.globalAlpha = 1;
    context.globalCompositeOperation = "source-over";

    // 1. Heavy-duty vertical lab stand column & articulated boom arm
    drawLine([[mastX, 0.15, mastZ], [mastX, phoneTopY + 0.24, mastZ]], "#1e242b", 12);
    drawLine([[mastX, 0.15, mastZ], [mastX, phoneTopY + 0.24, mastZ]], "#475569", 8);
    drawLine([[mastX, 0.15, mastZ], [mastX, phoneTopY + 0.24, mastZ]], "#94a3b8", 2);

    // Cast-iron base collar at chamber floor
    drawLine([[mastX - 0.25, 0.15, mastZ], [mastX + 0.25, 0.15, mastZ]], "#1e293b", 6);
    drawLine([[mastX, 0.15, mastZ - 0.25], [mastX, 0.15, mastZ + 0.25]], "#1e293b", 6);

    // Articulated boom arm extending to phone clamp
    const armY = phoneTopY + 0.14;
    drawLine([[mastX, armY, mastZ], [0.65, armY, mastZ]], "#334155", 6);
    drawLine([[mastX, armY - 0.04, mastZ], [0.65, armY - 0.04, mastZ]], "#1e293b", 4);
    // Swivel joint collar on boom arm
    drawLine([[mastX, armY + 0.03, mastZ], [mastX, armY - 0.06, mastZ]], "#64748b", 10);
    // Articulation link to clamp
    drawLine([[0.65, armY, mastZ], [0, phoneTopY + 0.03, mastZ + 0.22]], "#475569", 5);

    // Swivel tightening knob on arm elbow (NOT on phone glass!)
    const elbowKnob = project(0.65, armY, mastZ);
    context.save();
    context.beginPath();
    context.arc(elbowKnob.x, elbowKnob.y, 6, 0, Math.PI * 2);
    context.fillStyle = "#64748b";
    context.fill();
    context.strokeStyle = "#94a3b8";
    context.lineWidth = 1.5;
    context.stroke();
    context.restore();

    // 2. Realistic iPhone Pro Geometry
    const phone = {
      left: -0.68,
      right: 0.68,
      back: -1.24,
      front: 1.24,
      radius: 0.22,
    };

    // Soft drop shadow cast by phone on chamber roof
    const shadowPts = makeRoundedRect3D(
      phone.left - 0.02,
      phone.right + 0.02,
      phone.back - 0.02,
      phone.front + 0.02,
      phone.radius + 0.02,
      box.top,
      6
    );
    drawPolygon(shadowPts, "rgba(0, 0, 0, 0.38)");

    const bottomPoints = makeRoundedRect3D(phone.left, phone.right, phone.back, phone.front, phone.radius, phoneBottomY, 6);
    const topPoints = makeRoundedRect3D(phone.left, phone.right, phone.back, phone.front, phone.radius, phoneTopY, 6);

    // 3. FAR FACE RENDERING (Painter's algorithm: draw far face behind chassis)
    let cameraLens;
    if (pitch >= 0) {
      // Viewing from ABOVE: Bottom face is further away in depth
      if (!phoneFlipped) {
        cameraLens = drawIPhoneBackPanel(phone, phoneBottomY, true);
      } else {
        drawIPhoneScreen(phone, phoneBottomY);
      }
    } else {
      // Viewing from BELOW: Top face is further away in depth
      if (!phoneFlipped) {
        drawIPhoneScreen(phone, phoneTopY);
      } else {
        cameraLens = drawIPhoneBackPanel(phone, phoneTopY, false);
      }
    }

    // 4. Smooth Contoured Aerospace Titanium Chassis Rails (ZERO SPIKES)
    // Draw continuous smooth side quads without stroked seams
    for (let i = 0; i < topPoints.length; i += 1) {
      const next = (i + 1) % topPoints.length;
      const p1 = topPoints[i];
      const p2 = topPoints[next];
      const dx = p2[0] - p1[0];
      const dz = p2[2] - p1[2];
      const nx = dz;
      const nz = -dx;
      const len = Math.hypot(nx, nz) || 1;
      const unitNx = nx / len;
      const unitNz = nz / len;

      const viewDot = unitNx * Math.sin(yaw) + unitNz * Math.cos(yaw);
      if (viewDot < -0.1) continue;

      const lightDot = unitNx * Math.cos(0.75 + yaw) + unitNz * Math.sin(0.75 + yaw);
      const bright = Math.round(96 + lightDot * 48);
      const sideColor = "rgb(" + bright + ", " + (bright + 5) + ", " + (bright + 10) + ")";
      drawPolygon([topPoints[i], topPoints[next], bottomPoints[next], bottomPoints[i]], sideColor);
    }

    // Smooth continuous top and bottom perimeter highlight rims (eliminates corner spikes)
    drawLine(topPoints.concat([topPoints[0]]), "#cbd5e1", 1.4);
    drawLine(bottomPoints.concat([bottomPoints[0]]), "#475569", 1.2);

    // PVD Antenna Injection Lines
    [-0.72, 0.72].forEach(function (zPos) {
      drawLine([[phone.left - 0.002, phoneBottomY, zPos], [phone.left - 0.002, phoneTopY, zPos]], "#1e293b", 2.2);
      drawLine([[phone.right + 0.002, phoneBottomY, zPos], [phone.right + 0.002, phoneTopY, zPos]], "#1e293b", 2.2);
    });

    // Precision Tactile Buttons
    // Left: Action button & Volume
    drawLine([[phone.left - 0.016, phoneMidY, -0.64], [phone.left - 0.016, phoneMidY, -0.52]], "#cbd5e1", 3.5);
    drawLine([[phone.left - 0.016, phoneMidY, -0.38], [phone.left - 0.016, phoneMidY, -0.20]], "#cbd5e1", 3.5);
    drawLine([[phone.left - 0.016, phoneMidY, -0.08], [phone.left - 0.016, phoneMidY, 0.10]], "#cbd5e1", 3.5);

    // Right: Side / Power button & Sapphire Camera Control touch pad
    drawLine([[phone.right + 0.016, phoneMidY, -0.16], [phone.right + 0.016, phoneMidY, 0.16]], "#cbd5e1", 3.5);
    drawLine([[phone.right + 0.012, phoneMidY, 0.48], [phone.right + 0.012, phoneMidY, 0.76]], "#64748b", 2.5);
    drawLine([[phone.right + 0.014, phoneMidY, 0.49], [phone.right + 0.014, phoneMidY, 0.75]], "#94a3b8", 1.2);

    // Bottom: USB-C port cutout
    drawLine([[0 - 0.06, phoneMidY, phone.front + 0.002], [0 + 0.06, phoneMidY, phone.front + 0.002]], "#020617", 3);

    // 5. NEAR FACE RENDERING (Painter's algorithm: draw near face in foreground)
    if (pitch >= 0) {
      // Viewing from ABOVE: Top face is closer to viewer
      if (!phoneFlipped) {
        drawIPhoneScreen(phone, phoneTopY);
      } else {
        cameraLens = drawIPhoneBackPanel(phone, phoneTopY, false);
      }
    } else {
      // Viewing from BELOW: Bottom face is closer to viewer
      if (!phoneFlipped) {
        cameraLens = drawIPhoneBackPanel(phone, phoneBottomY, true);
      } else {
        drawIPhoneScreen(phone, phoneBottomY);
      }
    }

    // 6. Studio Lab Clamps Gripping Phone at Waist (Left & Right Flanks)
    const clampZ = 0.28;
    // Left clamp jaw
    drawPolygon([
      [phone.left - 0.035, phoneBottomY - 0.01, clampZ - 0.12],
      [phone.left + 0.035, phoneBottomY - 0.01, clampZ - 0.12],
      [phone.left + 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.left - 0.035, phoneTopY + 0.018, clampZ - 0.12],
    ], "#1e293b");
    drawPolygon([
      [phone.left - 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.left + 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.left + 0.035, phoneTopY + 0.018, clampZ + 0.12],
      [phone.left - 0.035, phoneTopY + 0.018, clampZ + 0.12],
    ], "#334155");

    // Right clamp jaw
    drawPolygon([
      [phone.right - 0.035, phoneBottomY - 0.01, clampZ - 0.12],
      [phone.right + 0.035, phoneBottomY - 0.01, clampZ - 0.12],
      [phone.right + 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.right - 0.035, phoneTopY + 0.018, clampZ - 0.12],
    ], "#1e293b");
    drawPolygon([
      [phone.right - 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.right + 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.right + 0.035, phoneTopY + 0.018, clampZ + 0.12],
      [phone.right - 0.035, phoneTopY + 0.018, clampZ + 0.12],
    ], "#334155");

    context.restore();
    return cameraLens;
  }

  // --- INTERACTIVE ON-CANVAS HUD TOGGLE ---
  function drawFlipButton() {
    const btnW = 190;
    const btnH = 28;
    const btnX = 26;
    const btnY = 94;

    context.save();
    context.beginPath();
    const r = 14;
    context.moveTo(btnX + r, btnY);
    context.lineTo(btnX + btnW - r, btnY);
    context.arc(btnX + btnW - r, btnY + r, r, -Math.PI / 2, Math.PI / 2);
    context.lineTo(btnX + r, btnY + btnH);
    context.arc(btnX + r, btnY + r, r, Math.PI / 2, (3 * Math.PI) / 2);
    context.closePath();

    context.fillStyle = "rgba(15, 23, 42, 0.88)";
    context.fill();
    context.strokeStyle = "rgba(148, 163, 184, 0.5)";
    context.lineWidth = 1.2;
    context.stroke();

    context.font = "bold 11px -apple-system, BlinkMacSystemFont, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";

    context.fillStyle = "#38bdf8";
    context.fillText("⇄", btnX + 16, btnY + btnH / 2);

    context.fillStyle = "#f8fafc";
    const label = phoneFlipped ? "Flip: Camera Facing Below" : "Flip: Camera Facing Top";
    context.fillText(label, btnX + btnW / 2 + 8, btnY + btnH / 2);
    context.restore();
  }

  function addRice() {
    for (let index = 0; index < 120; index += 1) {
      grains.push({
        sourceX: -2.2 + Math.random() * 0.72,
        sourceZ: -0.42 + Math.random() * 0.84,
        targetX: -0.35 + Math.random() * 2.25,
        targetZ: -1.05 + Math.random() * 2.1,
        progress: 0,
        delay: index * 0.32,
        angle: Math.random() * Math.PI,
        spin: (Math.random() - 0.5) * 0.16,
        length: 0.11 + Math.random() * 0.04,
        settled: false,
      });
    }
  }

  function drawRice(box) {
    const sourceHighY = box.floor + 0.72;
    const sourceLipY = box.floor + 0.42;
    const sourceStartX = -2.38;
    const sourceLipX = -0.92;
    const targetY = box.floor + 0.16;

    grains.forEach(function (grain) {
      if (!grain.settled) {
        grain.delay = Math.max(0, grain.delay - 1);
        if (grain.delay === 0) {
          grain.progress = Math.min(1, grain.progress + 0.025);
        }
        const progress = grain.progress;
        const eased = progress * progress * (3 - 2 * progress);
        grain.sourceY = sourceHighY
          - ((grain.sourceX - sourceStartX) / (sourceLipX - sourceStartX))
            * (sourceHighY - sourceLipY);
        grain.x = grain.sourceX + (grain.targetX - grain.sourceX) * eased;
        grain.y = grain.sourceY + (targetY - grain.sourceY) * eased
          + Math.sin(progress * Math.PI) * 0.035;
        grain.z = grain.sourceZ + (grain.targetZ - grain.sourceZ) * eased;
        grain.angle += grain.spin;
        grain.settled = progress >= 1;
      }

      const screenPoint = project(grain.x, grain.y, grain.z);
      context.save();
      context.translate(screenPoint.x, screenPoint.y);
      context.rotate(grain.angle - yaw * 0.35);
      const length = grain.length * 78;
      const width = length * 0.36;
      const shade = context.createLinearGradient(0, -width, 0, width);
      shade.addColorStop(0, "#fff9e8");
      shade.addColorStop(0.48, "#e8dcb9");
      shade.addColorStop(1, "#c6b68e");
      context.shadowColor = "rgba(30, 28, 20, 0.28)";
      context.shadowBlur = 2;
      context.shadowOffsetY = 1;
      context.fillStyle = shade;
      context.beginPath();
      context.moveTo(-length, 0);
      context.quadraticCurveTo(-length * 0.72, -width, 0, -width / 2);
      context.quadraticCurveTo(length * 0.72, -width, length, 0);
      context.quadraticCurveTo(length * 0.72, width, 0, width / 2);
      context.quadraticCurveTo(-length * 0.72, width, -length, 0);
      context.fill();
      context.shadowColor = "transparent";
      context.strokeStyle = "rgba(130, 111, 75, 0.38)";
      context.lineWidth = 0.7;
      context.stroke();
      context.beginPath();
      context.moveTo(-length * 0.62, 0);
      context.quadraticCurveTo(0, width * 0.08, length * 0.62, 0);
      context.strokeStyle = "rgba(255, 255, 244, 0.7)";
      context.lineWidth = 0.65;
      context.stroke();
      context.restore();
    });

    if (pouring && grains.every(function (grain) { return grain.settled; })) {
      pouring = false;
      sourceTrayRemoving = true;
      sceneMessage = "Removing the empty source tray...";
    }
  }

  function drawFlash(lens) {
    if (flashAmount <= 0) return;
    context.save();
    context.globalAlpha = flashAmount;
    const glow = context.createRadialGradient(lens.x, lens.y, 4, lens.x, lens.y, 300);
    glow.addColorStop(0, "#ffffff");
    glow.addColorStop(1, "rgba(255, 255, 255, 0)");
    context.fillStyle = glow;
    context.fillRect(100, 100, 700, 360);
    context.restore();
    flashAmount = Math.max(0, flashAmount - 0.035);
  }

  function animate() {
    lightingColor = lightingColor.map(function (channel, index) {
      return channel + (targetLightingColor[index] - channel) * 0.08;
    });
    const box = drawChamber();
    drawRice(box);
    const cameraLens = drawTripodPhone(box);
    drawFlash(cameraLens);
    drawFlipButton();
    window.requestAnimationFrame(animate);
  }

  function pourRice(onDone) {
    if (pouring) throw new Error("Rice is already being poured.");
    restartRice(onDone);
  }

  function restartRice(onDone) {
    grains.length = 0;
    addRice();
    sourceTrayPresent = true;
    sourceTrayRemoving = false;
    sourceTrayOffset = 0;
    pouring = true;
    pourCallback = typeof onDone === "function" ? onDone : null;
    sceneMessage = "Pouring 120 simulated grains...";
  }

  function setLighting(name) {
    if (!Object.prototype.hasOwnProperty.call(LIGHT_COLORS, name)) {
      throw new Error("Unknown simulated lighting: " + name);
    }
    targetLightingColor = LIGHT_COLORS[name].slice();
    sceneMessage = (name === "ring_light" ? "Simulated LED strips" : "Simulated lighting")
      + ": " + name.replace("_", " ");
  }

  function cameraFlash(onDone) {
    flashAmount = 0.8;
    sceneMessage = "Simulated camera flash";
    window.setTimeout(function () {
      if (typeof onDone === "function") onDone();
    }, 220);
  }

  let pointerDownPos = null;
  canvas.addEventListener("pointerdown", function (event) {
    const rect = canvas.getBoundingClientRect();
    const clickX = (event.clientX - rect.left) * (WIDTH / rect.width);
    const clickY = (event.clientY - rect.top) * (HEIGHT / rect.height);
    pointerDownPos = { x: clickX, y: clickY, time: Date.now() };

    // Check if clicked the Flip button
    const btnW = 190;
    const btnH = 28;
    const btnX = 26;
    const btnY = 94;
    if (clickX >= btnX && clickX <= btnX + btnW && clickY >= btnY && clickY <= btnY + btnH) {
      phoneFlipped = !phoneFlipped;
      return;
    }

    dragging = true;
    lastPointer = { x: event.clientX, y: event.clientY };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", function (event) {
    if (!dragging || !lastPointer) return;
    yaw += (event.clientX - lastPointer.x) * 0.01;
    pitch = Math.max(-0.35, Math.min(0.65, pitch + (event.clientY - lastPointer.y) * 0.006));
    lastPointer = { x: event.clientX, y: event.clientY };
  });
  canvas.addEventListener("pointerup", function () {
    dragging = false;
    lastPointer = null;
  });
  canvas.addEventListener("pointercancel", function () {
    dragging = false;
    lastPointer = null;
  });

  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  canvas.style.touchAction = "none";
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
  window.requestAnimationFrame(animate);

  window.Chamber = {
    pourRice: pourRice,
    restartRice: restartRice,
    setLighting: setLighting,
    cameraFlash: cameraFlash,
    flipPhone: function () { phoneFlipped = !phoneFlipped; return phoneFlipped; },
    resetView: function () {
      yaw = -0.65;
      pitch = 0.68;
    },
  };
})();
