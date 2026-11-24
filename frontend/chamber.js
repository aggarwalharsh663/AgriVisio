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
    const opening = { left: -0.72, right: 0.72, back: -1.02, front: 1.02 };
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
      [[opening.left + 0.08, box.top, opening.back + 0.08],
        [opening.right - 0.08, box.top, opening.back + 0.08],
        [opening.right - 0.08, box.top, opening.front - 0.08],
        [opening.left + 0.08, box.top, opening.front - 0.08],
        [opening.left + 0.08, box.top, opening.back + 0.08]],
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

  function drawRealisticLens(cx, cz, baseY, outerRadius, lensHeight) {
    const segments = 24;
    const topY = baseY + lensHeight;
    const glassY = topY - 0.002;

    // 1. Cylindrical metallic side bezel with directional light shading
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
      const dot = Math.cos(angle - 0.75);
      const intensity = Math.round(90 + dot * 55);
      const facetColor = "rgb(" + intensity + ", " + (intensity + 6) + ", " + (intensity + 12) + ")";
      drawPolygon([topCircle[i], topCircle[next], baseCircle[next], baseCircle[i]], facetColor);
    }

    // 2. Polished titanium top lip
    drawPolygon(topCircle, "#94a3b8", "#cbd5e1");
    drawLine(topCircle.concat([topCircle[0]]), "#e2e8f0", 1);

    // 3. Stepped inner anti-reflection baffle ring
    const innerRadius = outerRadius * 0.78;
    const innerCircle = [];
    for (let step = 0; step < segments; step += 1) {
      const angle = (step / segments) * Math.PI * 2;
      innerCircle.push([cx + Math.cos(angle) * innerRadius, topY - 0.001, cz + Math.sin(angle) * innerRadius]);
    }
    drawPolygon(innerCircle, "#090d16", "#1e293b");

    // 4. Optical glass front element with multi-coat AR tint
    const glassRadius = innerRadius * 0.88;
    const glassCircle = [];
    for (let step = 0; step < segments; step += 1) {
      const angle = (step / segments) * Math.PI * 2;
      glassCircle.push([cx + Math.cos(angle) * glassRadius, glassY, cz + Math.sin(angle) * glassRadius]);
    }
    drawPolygon(glassCircle, "#020617");

    // 5. Crescent anti-reflective coating glare (emerald & cyan flare)
    const screenCenter = project(cx, glassY, cz);
    const screenRadius = outerRadius * 62;
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
    flare.addColorStop(0, "rgba(56, 189, 248, 0.5)");
    flare.addColorStop(0.35, "rgba(52, 211, 153, 0.3)");
    flare.addColorStop(0.7, "rgba(99, 102, 241, 0.15)");
    flare.addColorStop(1, "rgba(2, 6, 23, 0.95)");
    context.fillStyle = flare;
    context.fill();

    // Deep aperture pupil
    context.beginPath();
    context.arc(screenCenter.x, screenCenter.y, screenRadius * 0.36, 0, Math.PI * 2);
    context.fillStyle = "#020408";
    context.fill();

    // Specular light reflection glints
    context.beginPath();
    context.arc(screenCenter.x - screenRadius * 0.28, screenCenter.y - screenRadius * 0.28, Math.max(1.8, screenRadius * 0.12), 0, Math.PI * 2);
    context.fillStyle = "rgba(255, 255, 255, 0.95)";
    context.fill();

    context.beginPath();
    context.arc(screenCenter.x + screenRadius * 0.22, screenCenter.y + screenRadius * 0.24, Math.max(1, screenRadius * 0.06), 0, Math.PI * 2);
    context.fillStyle = "rgba(255, 255, 255, 0.45)";
    context.fill();

    context.restore();
  }

  function drawTripodPhone(box) {
    const mastX = box.right + 0.45;
    const mastZ = 0.05;
    const phoneTopY = box.top + 0.08;
    const phoneBottomY = box.top;
    const phoneMidY = (phoneTopY + phoneBottomY) / 2;

    context.save();
    context.globalAlpha = 1;
    context.globalCompositeOperation = "source-over";

    // 1. Heavy-duty vertical lab stand column & articulated arm
    drawLine([[mastX, 0.15, mastZ], [mastX, phoneTopY + 0.22, mastZ]], "#1e242b", 12);
    drawLine([[mastX, 0.15, mastZ], [mastX, phoneTopY + 0.22, mastZ]], "#475569", 8);
    drawLine([[mastX, 0.15, mastZ], [mastX, phoneTopY + 0.22, mastZ]], "#94a3b8", 2); // Highlight line

    // Cast-iron base collar at chamber floor
    drawLine([[mastX - 0.25, 0.15, mastZ], [mastX + 0.25, 0.15, mastZ]], "#1e293b", 6);
    drawLine([[mastX, 0.15, mastZ - 0.25], [mastX, 0.15, mastZ + 0.25]], "#1e293b", 6);

    // Articulated dual-rod boom arm extending to phone clamp
    const armY = phoneTopY + 0.12;
    drawLine([[mastX, armY, mastZ], [0.65, armY, mastZ]], "#334155", 6);
    drawLine([[mastX, armY - 0.04, mastZ], [0.65, armY - 0.04, mastZ]], "#1e293b", 4);
    // Swivel joint collar
    drawLine([[mastX, armY + 0.03, mastZ], [mastX, armY - 0.06, mastZ]], "#64748b", 10);
    // Articulation clamp to phone center
    drawLine([[0.65, armY, mastZ], [0, phoneTopY + 0.03, mastZ]], "#475569", 5);

    // 2. Realistic Smartphone Dimensions & Rounded Rectangles
    const phone = {
      left: -0.74,
      right: 0.74,
      back: -1.24,
      front: 1.24,
      radius: 0.16,
    };

    // Soft drop shadow cast by phone on chamber roof
    const shadowPts = makeRoundedRect3D(phone.left - 0.02, phone.right + 0.02, phone.back - 0.02, phone.front + 0.02, phone.radius + 0.02, box.top, 5);
    drawPolygon(shadowPts, "rgba(0, 0, 0, 0.4)");

    const bottomPoints = makeRoundedRect3D(phone.left, phone.right, phone.back, phone.front, phone.radius, phoneBottomY, 5);
    const topPoints = makeRoundedRect3D(phone.left, phone.right, phone.back, phone.front, phone.radius, phoneTopY, 5);

    // 3. Metallic Chassis Side Rails (Brushed Aerospace Titanium)
    for (let i = 0; i < topPoints.length; i += 1) {
      const next = (i + 1) % topPoints.length;
      const p1 = topPoints[i];
      const p2 = topPoints[next];
      const dx = p2[0] - p1[0];
      const dz = p2[2] - p1[2];
      const nx = dz;
      const nz = -dx;
      const len = Math.hypot(nx, nz) || 1;
      const dot = (nx / len) * 0.6 + (nz / len) * 0.8;
      const bright = Math.round(52 + dot * 36);
      const sideColor = "rgb(" + bright + ", " + (bright + 5) + ", " + (bright + 9) + ")";
      drawPolygon([topPoints[i], topPoints[next], bottomPoints[next], bottomPoints[i]], sideColor, "#0f172a");
    }

    // Antenna Bands
    [-0.6, 0.6].forEach(function (zPos) {
      drawLine([[phone.left - 0.002, phoneBottomY, zPos], [phone.left - 0.002, phoneTopY, zPos]], "#090d16", 2);
      drawLine([[phone.right + 0.002, phoneBottomY, zPos], [phone.right + 0.002, phoneTopY, zPos]], "#090d16", 2);
    });

    // Side tactile buttons
    // Power / Sleep key on right side
    drawLine([[phone.right + 0.016, phoneMidY, -0.15], [phone.right + 0.016, phoneMidY, 0.15]], "#94a3b8", 3.5);
    drawLine([[phone.right + 0.016, phoneMidY, -0.15], [phone.right + 0.016, phoneMidY, 0.15]], "#cbd5e1", 1.5);
    // Action button and Volume rockers on left side
    drawLine([[phone.left - 0.016, phoneMidY, -0.58], [phone.left - 0.016, phoneMidY, -0.44]], "#94a3b8", 3.5);
    drawLine([[phone.left - 0.016, phoneMidY, -0.32], [phone.left - 0.016, phoneMidY, -0.08]], "#94a3b8", 3.5);
    drawLine([[phone.left - 0.016, phoneMidY, 0.02], [phone.left - 0.016, phoneMidY, 0.26]], "#94a3b8", 3.5);

    // 4. Matte AG Frosted Glass Rear Panel
    const pTopLeft = project(phone.left, phone.topY, phone.back);
    const pBottomRight = project(phone.right, phone.topY, phone.front);
    const glassFinish = context.createLinearGradient(
      pTopLeft.x, pTopLeft.y, pBottomRight.x, pBottomRight.y
    );
    glassFinish.addColorStop(0, "#2c333a");
    glassFinish.addColorStop(0.3, "#3d464f");
    glassFinish.addColorStop(0.65, "#1e2328");
    glassFinish.addColorStop(1, "#282e35");
    drawPolygon(topPoints, glassFinish, "#334155");

    // Chamfered polished titanium perimeter edge highlight
    drawLine(topPoints.concat([topPoints[0]]), "#94a3b8", 1.5);

    // Inner 2.5D curved glass reflection border
    const innerGlassPts = makeRoundedRect3D(phone.left + 0.03, phone.right - 0.03, phone.back + 0.03, phone.front - 0.03, phone.radius - 0.03, phone.topY + 0.001, 5);
    drawLine(innerGlassPts.concat([innerGlassPts[0]]), "rgba(255, 255, 255, 0.09)", 1);

    // Centered minimalist optical brand glyph (subtle reflection)
    const logoCenter = project(0, phone.topY + 0.001, 0.18);
    context.save();
    context.beginPath();
    context.arc(logoCenter.x, logoCenter.y, 7, 0, Math.PI * 2);
    context.strokeStyle = "rgba(255, 255, 255, 0.22)";
    context.lineWidth = 1.2;
    context.stroke();
    context.beginPath();
    context.arc(logoCenter.x, logoCenter.y, 3, 0, Math.PI * 2);
    context.fillStyle = "rgba(255, 255, 255, 0.3)";
    context.fill();
    context.restore();

    // 5. Elevated Squircle Camera Island (Camera Plateau)
    const island = {
      left: -0.66,
      right: 0.06,
      back: -1.16,
      front: -0.42,
      radius: 0.11,
      y: phoneTopY + 0.032,
    };

    // Soft drop-shadow beneath camera island on the glass back
    const islandShadow = makeRoundedRect3D(island.left - 0.015, island.right + 0.015, island.back - 0.015, island.front + 0.015, island.radius + 0.015, phoneTopY + 0.001, 4);
    drawPolygon(islandShadow, "rgba(0, 0, 0, 0.45)");

    const islandBasePts = makeRoundedRect3D(island.left, island.right, island.back, island.front, island.radius, phoneTopY, 4);
    const islandTopPts = makeRoundedRect3D(island.left, island.right, island.back, island.front, island.radius, island.y, 4);

    // Island sides with metallic chamfer
    for (let i = 0; i < islandTopPts.length; i += 1) {
      const next = (i + 1) % islandTopPts.length;
      drawPolygon([islandTopPts[i], islandTopPts[next], islandBasePts[next], islandBasePts[i]], "#283038", "#1e242b");
    }

    // Island frosted plateau surface
    const islandScreenCorners = [
      project(island.left, island.y, island.back),
      project(island.right, island.y, island.front),
    ];
    const islandGrad = context.createLinearGradient(
      islandScreenCorners[0].x, islandScreenCorners[0].y,
      islandScreenCorners[1].x, islandScreenCorners[1].y
    );
    islandGrad.addColorStop(0, "#222930");
    islandGrad.addColorStop(0.5, "#2b343d");
    islandGrad.addColorStop(1, "#1a1f25");
    drawPolygon(islandTopPts, islandGrad, "#475569");
    drawLine(islandTopPts.concat([islandTopPts[0]]), "#64748b", 1.2);

    // 6. Triple High-End Camera Lenses
    const lensRadius = 0.096;
    const lensHeight = 0.022;
    // Primary Wide Camera Lens
    drawRealisticLens(-0.44, -0.96, island.y, lensRadius, lensHeight);
    // Ultra-Wide Camera Lens
    drawRealisticLens(-0.16, -0.96, island.y, lensRadius, lensHeight);
    // Telephoto Camera Lens
    drawRealisticLens(-0.44, -0.62, island.y, lensRadius, lensHeight);

    // 7. TrueTone Quad-LED Flash Module
    const flashRadius = 0.052;
    const flashPts = [];
    for (let s = 0; s < 18; s += 1) {
      const a = (s / 18) * Math.PI * 2;
      flashPts.push([-0.16 + Math.cos(a) * flashRadius, island.y + 0.005, -0.62 + Math.sin(a) * flashRadius]);
    }
    drawPolygon(flashPts, "#334155", "#64748b");
    const flashInnerPts = [];
    for (let s = 0; s < 18; s += 1) {
      const a = (s / 18) * Math.PI * 2;
      flashInnerPts.push([-0.16 + Math.cos(a) * (flashRadius * 0.78), island.y + 0.006, -0.62 + Math.sin(a) * (flashRadius * 0.78)]);
    }
    drawPolygon(flashInnerPts, "#fef3c7", "#f59e0b");

    // 8. LiDAR Sensor & Acoustic Mic
    const lidarRadius = 0.038;
    const lidarPts = [];
    for (let s = 0; s < 16; s += 1) {
      const a = (s / 16) * Math.PI * 2;
      lidarPts.push([-0.16 + Math.cos(a) * lidarRadius, island.y + 0.004, -0.48 + Math.sin(a) * lidarRadius]);
    }
    drawPolygon(lidarPts, "#020617", "#1e1b4b");

    const micPts = [];
    for (let s = 0; s < 12; s += 1) {
      const a = (s / 12) * Math.PI * 2;
      micPts.push([-0.44 + Math.cos(a) * 0.016, island.y + 0.003, -0.48 + Math.sin(a) * 0.016]);
    }
    drawPolygon(micPts, "#090d16", "#334155");

    // 9. Precision Studio Grip Clamps on the phone's flanks
    const clampZ = 0.08;
    // Left clamp jaw
    drawPolygon([
      [phone.left - 0.035, phoneBottomY - 0.01, clampZ - 0.12],
      [phone.left + 0.035, phoneBottomY - 0.01, clampZ - 0.12],
      [phone.left + 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.left - 0.035, phoneTopY + 0.018, clampZ - 0.12],
    ], "#1e293b", "#475569");
    drawPolygon([
      [phone.left - 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.left + 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.left + 0.035, phoneTopY + 0.018, clampZ + 0.12],
      [phone.left - 0.035, phoneTopY + 0.018, clampZ + 0.12],
    ], "#334155", "#64748b");

    // Right clamp jaw
    drawPolygon([
      [phone.right - 0.035, phoneBottomY - 0.01, clampZ - 0.12],
      [phone.right + 0.035, phoneBottomY - 0.01, clampZ - 0.12],
      [phone.right + 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.right - 0.035, phoneTopY + 0.018, clampZ - 0.12],
    ], "#1e293b", "#475569");
    drawPolygon([
      [phone.right - 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.right + 0.035, phoneTopY + 0.018, clampZ - 0.12],
      [phone.right + 0.035, phoneTopY + 0.018, clampZ + 0.12],
      [phone.right - 0.035, phoneTopY + 0.018, clampZ + 0.12],
    ], "#334155", "#64748b");

    // Central mounting thumbscrew knob
    const knobCenter = project(0, phoneTopY + 0.03, clampZ);
    context.save();
    context.beginPath();
    context.arc(knobCenter.x, knobCenter.y, 5, 0, Math.PI * 2);
    context.fillStyle = "#64748b";
    context.fill();
    context.strokeStyle = "#94a3b8";
    context.lineWidth = 1.5;
    context.stroke();
    context.restore();

    // Primary wide lens center as optical origin
    const cameraLens = project(-0.44, island.y + lensHeight, -0.96);
    context.restore();
    return cameraLens;
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

  canvas.addEventListener("pointerdown", function (event) {
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
    resetView: function () {
      yaw = -0.65;
      pitch = 0.68;
    },
  };
})();
