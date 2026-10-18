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
  let flashAmount = 0;
  let sceneMessage = "Camera and lighting are simulated";
  let pouring = false;
  let pourCallback = null;

  function colorText(color) {
    return "rgb(" + color.map(Math.round).join(", ") + ")";
  }

  function resizeCanvas() {
    const bounds = canvas.getBoundingClientRect();
    const pixelRatio = window.devicePixelRatio || 1;
    const cssWidth = bounds.width || WIDTH;
    const cssHeight = cssWidth * HEIGHT / WIDTH;

    canvas.width = Math.round(cssWidth * pixelRatio);
    canvas.height = Math.round(cssHeight * pixelRatio);
    context.setTransform(
      canvas.width / WIDTH,
      0,
      0,
      canvas.height / HEIGHT,
      0,
      0,
    );
  }

  function drawChamber() {
    const wall = "#c5c9c3";
    const metal = "#59666d";
    const floorY = 438;

    context.fillStyle = "#edf2ef";
    context.fillRect(0, 0, WIDTH, HEIGHT);

    // This side view leaves the top open for the camera and ring light.
    context.fillStyle = "#d7ddd8";
    context.beginPath();
    context.moveTo(155, 130);
    context.lineTo(730, 130);
    context.lineTo(730, floorY);
    context.lineTo(155, floorY);
    context.closePath();
    context.fill();
    context.strokeStyle = wall;
    context.lineWidth = 8;
    context.strokeRect(155, 130, 575, floorY - 130);

    context.fillStyle = metal;
    context.fillRect(118, floorY, 664, 18);
    context.fillStyle = "#aab3af";
    context.fillRect(196, floorY - 24, 485, 17);
    context.fillStyle = "#87928d";
    context.fillRect(210, floorY - 7, 460, 7);

    // Draw the ring light on the upper wall.
    const centerX = 350;
    const centerY = 168;
    context.save();
    context.shadowColor = colorText(lightingColor);
    context.shadowBlur = 28;
    context.strokeStyle = colorText(lightingColor);
    context.lineWidth = 13;
    context.beginPath();
    context.arc(centerX, centerY, 30, 0, Math.PI * 2);
    context.stroke();
    context.restore();
    context.strokeStyle = "#778188";
    context.lineWidth = 4;
    context.beginPath();
    context.arc(centerX, centerY, 39, 0, Math.PI * 2);
    context.stroke();

    // Camera body and lens face the simulated tray.
    context.fillStyle = "#35434b";
    context.fillRect(555, 145, 82, 48);
    context.fillStyle = "#17262e";
    context.beginPath();
    context.arc(555, 169, 18, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = "#67a3bb";
    context.beginPath();
    context.arc(555, 169, 9, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = "#202c31";
    context.font = "16px sans-serif";
    context.fillText("SIMULATED CAMERA", 637, 175);

    // Label is part of the canvas drawing so the demo is self-contained.
    context.fillStyle = "#1d3930";
    context.font = "bold 24px sans-serif";
    context.textAlign = "center";
    context.fillText("SIMULATED IMAGING CHAMBER", WIDTH / 2, 58);
    context.font = "16px sans-serif";
    context.fillText(sceneMessage, WIDTH / 2, 505);
    context.textAlign = "start";

    return floorY - 27;
  }

  function addRice() {
    for (let index = 0; index < 120; index += 1) {
      grains.push({
        x: 385 + Math.random() * 115,
        y: 196 - Math.random() * 90,
        vx: (Math.random() - 0.5) * 3.5,
        vy: Math.random() * 1.5,
        angle: Math.random() * Math.PI,
        spin: (Math.random() - 0.5) * 0.12,
        bounce: 0,
        settled: false,
      });
    }
  }

  function drawRice(floorY) {
    for (let index = grains.length - 1; index >= 0; index -= 1) {
      const grain = grains[index];

      if (!grain.settled) {
        grain.vy += 0.18;
        grain.x += grain.vx;
        grain.y += grain.vy;
        grain.angle += grain.spin;

        if (grain.y >= floorY) {
          grain.y = floorY;
          grain.vy *= -0.42;
          grain.vx *= 0.82;
          grain.bounce += 1;
          if (Math.abs(grain.vy) < 0.8 || grain.bounce > 5) {
            grain.vy = 0;
            grain.vx *= 0.55;
            grain.settled = true;
          }
        }
        if (grain.x < 215 || grain.x > 660) {
          grain.vx *= -0.7;
        }
      }

      context.save();
      context.translate(grain.x, grain.y);
      context.rotate(grain.angle);
      context.fillStyle = index % 7 === 0 ? "#f4ead0" : "#e7dfc7";
      context.beginPath();
      context.ellipse(0, 0, 7, 2.5, 0, 0, Math.PI * 2);
      context.fill();
      context.restore();
    }

    if (pouring && grains.every(function (grain) { return grain.settled; })) {
      pouring = false;
      sceneMessage = "Simulated rice settled on the tray";
      const callback = pourCallback;
      pourCallback = null;
      if (callback) callback();
    }
  }

  function drawFlash() {
    if (flashAmount <= 0) return;
    context.save();
    context.globalAlpha = flashAmount;
    const glow = context.createRadialGradient(555, 169, 4, 555, 169, 310);
    glow.addColorStop(0, "#ffffff");
    glow.addColorStop(1, "rgba(255, 255, 255, 0)");
    context.fillStyle = glow;
    context.fillRect(180, 120, 480, 320);
    context.restore();
    flashAmount = Math.max(0, flashAmount - 0.035);
  }

  function animate() {
    lightingColor = lightingColor.map(function (channel, index) {
      return channel + (targetLightingColor[index] - channel) * 0.08;
    });
    const floorY = drawChamber();
    drawRice(floorY);
    drawFlash();
    window.requestAnimationFrame(animate);
  }

  function pourRice(onDone) {
    if (pouring) {
      throw new Error("Rice is already being poured.");
    }
    grains.length = 0;
    addRice();
    pouring = true;
    pourCallback = typeof onDone === "function" ? onDone : null;
    sceneMessage = "Pouring 120 simulated grains...";
  }

  function setLighting(name) {
    if (!Object.prototype.hasOwnProperty.call(LIGHT_COLORS, name)) {
      throw new Error("Unknown simulated lighting: " + name);
    }
    targetLightingColor = LIGHT_COLORS[name].slice();
    sceneMessage = "Simulated lighting: " + name.replace("_", " ");
  }

  function cameraFlash(onDone) {
    flashAmount = 0.8;
    sceneMessage = "Simulated camera flash";
    window.setTimeout(function () {
      if (typeof onDone === "function") onDone();
    }, 220);
  }

  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
  window.requestAnimationFrame(animate);

  window.Chamber = {
    pourRice: pourRice,
    setLighting: setLighting,
    cameraFlash: cameraFlash,
  };
})();
