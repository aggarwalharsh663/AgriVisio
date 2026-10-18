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

  function drawTripodPhone(box) {
    const mastX = box.right + 0.5;
    const mastZ = 0;
    const phoneY = box.top + 0.12;

    drawLine([[mastX, 0.15, mastZ], [mastX, phoneY + 0.15, mastZ]], "#4a5357", 8);
    drawLine([[mastX, 1.2, mastZ], [mastX - 0.65, 0.15, 0.55]], "#4a5357", 5);
    drawLine([[mastX, 1.2, mastZ], [mastX + 0.65, 0.15, 0.55]], "#4a5357", 5);
    drawLine([[mastX, 1.2, mastZ], [mastX, 0.15, -0.72]], "#4a5357", 5);
    drawLine([[mastX, phoneY + 0.12, mastZ], [0, phoneY + 0.12, mastZ]], "#59666d", 7);

    // Phone lies over the roof with its rear cameras aimed down through the aperture.
    const phone = { left: -0.78, right: 0.78, back: -1.08, front: 1.08, y: phoneY };
    const phoneCorners = [
      [phone.left, phone.y, phone.back],
      [phone.right, phone.y, phone.back],
      [phone.right, phone.y, phone.front],
      [phone.left, phone.y, phone.front],
    ].map(function (point) {
      return project(point[0], point[1], point[2]);
    });
    const phoneFinish = context.createLinearGradient(
      phoneCorners[0].x,
      phoneCorners[0].y,
      phoneCorners[2].x,
      phoneCorners[2].y,
    );
    phoneFinish.addColorStop(0, "#596165");
    phoneFinish.addColorStop(0.35, "#303639");
    phoneFinish.addColorStop(0.72, "#202629");
    phoneFinish.addColorStop(1, "#454c50");
    drawPolygon(
      [[phone.left, phone.y, phone.back], [phone.right, phone.y, phone.back],
        [phone.right, phone.y, phone.front], [phone.left, phone.y, phone.front]],
      phoneFinish,
      "#111619",
    );
    drawPolygon(
      [[phone.left, phone.y, phone.back], [phone.left + 0.055, phone.y - 0.06, phone.back],
        [phone.left + 0.055, phone.y - 0.06, phone.front],
        [phone.left, phone.y, phone.front]],
      "#171c1f",
      "#111619",
    );
    drawLine(
      [[phone.left, phone.y, phone.back], [phone.right, phone.y, phone.back],
        [phone.right, phone.y, phone.front], [phone.left, phone.y, phone.front],
        [phone.left, phone.y, phone.back]],
      "#c0c8ca",
      2,
    );
    const cameraBumpCenter = { x: -0.32, z: -0.48 };
    // The camera housing hangs below the phone and into the roof aperture.
    const cameraY = phone.y - 0.5;
    drawLine(
      [[cameraBumpCenter.x, phone.y, cameraBumpCenter.z],
        [cameraBumpCenter.x, cameraY, cameraBumpCenter.z]],
      "#111719",
      8,
    );
    const cameraBump = project(cameraBumpCenter.x, cameraY, cameraBumpCenter.z);
    context.fillStyle = "#202629";
    context.beginPath();
    context.roundRect(cameraBump.x - 22, cameraBump.y - 18, 44, 36, 10);
    context.fill();
    context.strokeStyle = "#687277";
    context.lineWidth = 1.5;
    context.stroke();
    [[-8, -6], [8, -6], [-8, 8]].forEach(function (offset) {
      context.beginPath();
      context.arc(cameraBump.x + offset[0], cameraBump.y + offset[1], 5.2, 0, Math.PI * 2);
      context.fillStyle = "#101517";
      context.fill();
      context.strokeStyle = "#77868c";
      context.lineWidth = 1;
      context.stroke();
      context.beginPath();
      context.arc(cameraBump.x + offset[0], cameraBump.y + offset[1], 3.2, 0, Math.PI * 2);
      context.fillStyle = "#537c90";
      context.fill();
      context.strokeStyle = "#9baeb5";
      context.lineWidth = 0.8;
      context.stroke();
      context.beginPath();
      context.arc(cameraBump.x + offset[0] - 1, cameraBump.y + offset[1] - 1, 1.2, 0, Math.PI * 2);
      context.fillStyle = "#c8e4ef";
      context.fill();
    });
    context.beginPath();
    context.arc(cameraBump.x + 8, cameraBump.y + 8, 2.5, 0, Math.PI * 2);
    context.fillStyle = "#d9c982";
    context.fill();
    context.beginPath();
    context.roundRect(cameraBump.x + 11, cameraBump.y - 4, 4, 8, 2);
    context.fillStyle = "#e9e5d3";
    context.fill();
    drawLine([[0.34, phone.y, -0.58], [0.34, phone.y, -0.28]], "#c0c8ca", 2);
    drawLine([[0.34, phone.y, 0.02], [0.34, phone.y, 0.3]], "#c0c8ca", 2);
    return project(cameraBumpCenter.x, cameraY, cameraBumpCenter.z);
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
