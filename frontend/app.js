(() => {
	const form = document.querySelector("#scan-form");
	const scanButton = document.querySelector("#scan-button");
	const scanError = document.querySelector("#scan-error");
	const message = document.querySelector("#app-message");
	const slider = document.querySelector("#grain-count");
	const scanSource = document.querySelector("#scan-source");
	const photoInput = document.querySelector("#photo-upload");
	const uploadField = document.querySelector("#upload-field");
	let photoPreviewUrl = null;
	const stepButtons = [...document.querySelectorAll(".step-item")];
	const canvas = document.querySelector("#detection-canvas");
	const context = canvas.getContext("2d");
	let batch = null;
	let activeStep = 1;
	let furthestStep = 1;
	let frameRequest = 0;
	let chartInstances = [];
	let selectedGrainId = null;
	let grainSort = "id";
	let calibratedPhoto = null;
	let scanStartedAt = 0;
	let scanDuration = 0;
	let detectedCount = 0;
	let detectedByClass = {};

	const categories = [
		{ key: "good", label: "Good", color: "#348255", css: "good-dot" },
		{ key: "broken", label: "Broken", color: "#d74e4e", css: "broken-dot" },
		{ key: "chalky", label: "Chalky", color: "#e99735", css: "chalky-dot" },
		{ key: "discolored", label: "Discolored", color: "#8951b4", css: "discolored-dot" },
	];
	const classColor = Object.fromEntries(categories.map(({ key, color }) => [key, color]));

	function announce(text) {
		message.textContent = text;
	}

	function setStep(step) {
		if (step < 1 || step > furthestStep) return;
		if (activeStep === 3 && step !== 3) cancelAnimationFrame(frameRequest);
		activeStep = step;
		document.querySelectorAll(".step-panel").forEach((panel, index) => {
			panel.hidden = index + 1 !== step;
		});
		stepButtons.forEach((button, index) => {
			const number = index + 1;
			button.classList.toggle("is-active", number === step);
			button.classList.toggle("is-complete", number < step);
			button.setAttribute("aria-disabled", number > furthestStep ? "true" : "false");
			if (number === step) button.setAttribute("aria-current", "step");
			else button.removeAttribute("aria-current");
		});
		document.querySelector("#progress-fill").style.width = `${((step - 1) / 3) * 100}%`;
		announce(`Step ${step} of 4: ${["Chamber", "Calibration", "Detection", "Report"][step - 1]}`);
		window.scrollTo({ top: 0, behavior: "smooth" });
		if (step === 3) startDetection();
		if (step === 4) renderReportCharts();
	}

	function isUploadedPhoto(record = batch) {
		return Boolean(record?.images?.raw?.includes("/uploaded-photo.jpg"));
	}

	function updateScanSource() {
		const uploading = scanSource.value === "upload";
		uploadField.hidden = !uploading;
		photoInput.disabled = !uploading;
		photoInput.required = uploading;
		document.querySelector("#chamber-figure").hidden = uploading;
		document.querySelector("#grain-count-field").hidden = uploading;
		document.querySelector("#scan-button .button-label").textContent = uploading ? "Analyze photo" : "Pour rice and scan";
	}

	function setLoading(loading) {
		scanButton.disabled = loading;
		scanButton.classList.toggle("is-loading", loading);
		scanButton.querySelector(".button-label").textContent = loading ? "Analyzing simulated sample…" : scanSource.value === "upload" ? "Analyze photo" : "Pour rice and scan";
		form.setAttribute("aria-busy", String(loading));
	}

	async function runChamberEffects() {
		const chamber = window.Chamber;
		if (!chamber) return;
		const lighting = document.querySelector("#lighting").value;
		if (typeof chamber.setLighting === "function") chamber.setLighting(lighting);
		if (typeof chamber.pourRice === "function") chamber.pourRice();
		if (typeof chamber.cameraFlash === "function") {
			await new Promise((resolve) => chamber.cameraFlash(resolve));
		}
	}

	function showFriendlyError(text) {
		scanError.textContent = text;
		scanError.hidden = false;
		announce(text);
	}

	function renderCalibration() {
		document.querySelector("#raw-image").src = batch.images.raw;
		document.querySelector("#calibrated-image").src = batch.images.calibrated;
		document.querySelector(".image-tag-left").textContent = isUploadedPhoto()
			? "UPLOADED · UNCALIBRATED"
			: "CALIBRATED";
		document.querySelector(".image-tag-right").textContent = isUploadedPhoto()
			? "SAME PHOTO"
			: "RAW · SIMULATED";
		document.querySelector("#calibrated-image").alt = isUploadedPhoto()
			? "Uncalibrated uploaded rice photo"
			: "Color-calibrated simulated rice sample photo";
		if (isUploadedPhoto()) {
			document.querySelector("#error-before").textContent = "N/A";
			document.querySelector("#error-after").textContent = "N/A";
			document.querySelector("#improvement-text").textContent = "No card · estimated scale";
			return;
		}
		const before = Number(batch.calibration.patch_error_before);
		const after = Number(batch.calibration.patch_error_after);
		document.querySelector("#error-before").textContent = Number.isFinite(before) ? before.toFixed(1) : "—";
		document.querySelector("#error-after").textContent = Number.isFinite(after) ? after.toFixed(1) : "—";
		const reduction = before > 0 ? Math.max(0, Math.round((1 - after / before) * 100)) : 0;
		document.querySelector("#improvement-text").textContent = `${reduction}% lower color error`;
		document.querySelector("#comparison-range").value = "50";
		updateComparison(50);
	}

	function renderDetectionCounters() {
		document.querySelector("#total-grains").textContent = String(detectedCount);
		for (const { key } of categories) {
			document.querySelector(`#count-${key}`).textContent = String(detectedByClass[key]);
		}
	}

	function grainBounds(grain) {
		const [centerX, centerY] = grain.center;
		const bbox = grain.bbox;
		if (Array.isArray(bbox) && bbox.length === 4 && bbox[2] > 0 && bbox[3] > 0) return bbox;
		const width = Math.max(20, Number(grain.length_mm || 5.8) / 0.12);
		const height = Math.max(12, Number(grain.width_mm || 1.9) / 0.12);
		return [centerX - width / 2, centerY - height / 2, width, height];
	}

	function drawDetections(scanX, revealedCount) {
		if (!calibratedPhoto || !canvas.width || !canvas.height) return;
		context.clearRect(0, 0, canvas.width, canvas.height);
		context.drawImage(calibratedPhoto, 0, 0, canvas.width, canvas.height);
		const grains = batch.grains;
		for (let index = 0; index < revealedCount; index += 1) {
			const grain = grains[index];
			const [x, y, width, height] = grainBounds(grain);
			context.save();
			context.strokeStyle = classColor[grain.label] || "#ffffff";
			context.lineWidth = Math.max(2, canvas.width / 640);
			context.shadowColor = "rgba(0, 0, 0, .7)";
			context.shadowBlur = 5;
			context.strokeRect(x, y, width, height);
			context.restore();
			if (grain.id === selectedGrainId) {
				context.save();
				context.strokeStyle = "#ffffff";
				context.lineWidth = Math.max(3, canvas.width / 400);
				context.setLineDash([9, 5]);
				context.strokeRect(x - 4, y - 4, width + 8, height + 8);
				context.restore();
			}
		}
		if (Number.isFinite(scanX) && revealedCount < grains.length) {
			context.save();
			context.strokeStyle = "rgba(210, 255, 223, .95)";
			context.shadowColor = "#77f5a0";
			context.shadowBlur = 17;
			context.lineWidth = Math.max(2, canvas.width / 480);
			context.beginPath();
			context.moveTo(scanX, 0);
			context.lineTo(scanX, canvas.height);
			context.stroke();
			context.restore();
		}
	}

	function startDetection() {
		if (!batch) return;
		cancelAnimationFrame(frameRequest);
		document.querySelector("#detection-next").disabled = true;
		const image = new Image();
		document.querySelector("#scan-empty").hidden = false;
		document.querySelector("#detection-complete").hidden = true;
		detectedCount = 0;
		detectedByClass = Object.fromEntries(categories.map(({ key }) => [key, 0]));
		batch.grains.sort((left, right) => left.center[0] - right.center[0]);
		document.querySelector("#quality-list").innerHTML = categories.map(({ key, label, css }) => `<div class="quality-row"><i class="legend-dot ${css}"></i><span>${label}</span><strong id="count-${key}">0</strong></div>`).join("");
		renderDetectionCounters();
		image.onload = () => {
			calibratedPhoto = image;
			canvas.width = image.naturalWidth;
			canvas.height = image.naturalHeight;
			canvas.style.aspectRatio = `${canvas.width} / ${canvas.height}`;
			document.querySelector("#scan-empty").hidden = true;
			const speed = Number(document.querySelector("#scan-speed").value);
			scanDuration = 6500 / Math.max(1, speed);
			scanStartedAt = performance.now();
			frameRequest = requestAnimationFrame(animateDetection);
		};
		image.onerror = () => {
			document.querySelector("#scan-empty").textContent = "Could not load the simulated photo. Replay to try again.";
		};
		image.src = batch.images.calibrated;
	}

	function animateDetection(now) {
		if (activeStep !== 3 || !batch || !calibratedPhoto) return;
		const progress = Math.min(1, (now - scanStartedAt) / scanDuration);
		const scanX = progress * canvas.width;
		const grains = batch.grains;
		let revealedCount = 0;
		while (revealedCount < grains.length && grains[revealedCount].center[0] <= scanX) {
			revealedCount += 1;
		}
		while (detectedCount < revealedCount) {
			detectedByClass[grains[detectedCount].label] = (detectedByClass[grains[detectedCount].label] || 0) + 1;
			detectedCount += 1;
		}
		renderDetectionCounters();
		drawDetections(scanX, revealedCount);
		document.querySelector("#scan-progress-label").textContent = `Scanning ${revealedCount} of ${grains.length} grains`;
		document.querySelector("#scan-progress-fill").style.width = `${progress * 100}%`;
		if (progress < 1) {
			frameRequest = requestAnimationFrame(animateDetection);
		} else {
			document.querySelector("#detection-complete").textContent = `Detection complete: ${grains.length} grains`;
			document.querySelector("#detection-complete").hidden = false;
			document.querySelector("#scan-progress-label").textContent = "Detection complete";
			document.querySelector("#detection-next").disabled = false;
			drawDetections(canvas.width, grains.length);
			announce(`Detection complete: ${grains.length} grains`);
		}
	}

	function destroyCharts() {
		chartInstances.forEach((chart) => chart.destroy());
		chartInstances = [];
	}

	function makeHistogram(values, binCount = 8) {
		if (!values.length) return { labels: ["No grain measurements"], counts: [0] };
		const minimum = Math.min(...values);
		const maximum = Math.max(...values);
		const range = maximum - minimum || 1;
		const width = range / binCount;
		const counts = Array(binCount).fill(0);
		for (const value of values) {
			const index = Math.min(binCount - 1, Math.floor((value - minimum) / width));
			counts[index] += 1;
		}
		return {
			labels: counts.map((_, index) => `${(minimum + width * index).toFixed(1)}–${(minimum + width * (index + 1)).toFixed(1)}`),
			counts,
		};
	}

	function summarizeMeasurements(grains, key) {
		const values = grains
			.map((grain) => grain[key])
			.filter((value) => typeof value === "number" && Number.isFinite(value));
		if (!values.length) return null;
		const average = values.reduce((sum, value) => sum + value, 0) / values.length;
		const variance = values.reduce(
			(sum, value) => sum + (value - average) ** 2,
			0,
		) / values.length;
		return {
			average,
			minimum: Math.min(...values),
			maximum: Math.max(...values),
			standardDeviation: Math.sqrt(variance),
		};
	}

	function rgbToLab(rgb) {
		const linear = rgb.map((channel) => {
			const value = channel / 255;
			return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
		});
		const x = (linear[0] * 0.4124564 + linear[1] * 0.3575761 + linear[2] * 0.1804375) / 0.95047;
		const y = linear[0] * 0.2126729 + linear[1] * 0.7151522 + linear[2] * 0.072175;
		const z = (linear[0] * 0.0193339 + linear[1] * 0.119192 + linear[2] * 0.9503041) / 1.08883;
		const pivot = (value) => value > 0.0088564517
			? Math.cbrt(value)
			: value / (3 * 0.1379310345 ** 2) + 4 / 29;
		const fx = pivot(x);
		const fy = pivot(y);
		const fz = pivot(z);
		return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
	}

	function formatShare(count, total) {
		const percent = total ? (count / total * 100).toFixed(2) : "0.00";
		return `${count} (${percent}%)`;
	}

	function renderRiceMetrics(grains, uploadedPhoto) {
		const total = grains.length;
		const lengthStats = summarizeMeasurements(grains, "length_mm");
		const widthStats = summarizeMeasurements(grains, "width_mm");
		const setValue = (id, value) => {
			document.querySelector(`#${id}`).textContent =
				Number.isFinite(value) ? value.toFixed(2) : "—";
		};
		for (const [prefix, stats] of [["length", lengthStats], ["width", widthStats]]) {
			for (const [suffix, key] of [
				["mean", "average"],
				["min", "minimum"],
				["max", "maximum"],
				["std", "standardDeviation"],
			]) {
				setValue(`rice-${prefix}-${suffix}`, stats?.[key]);
			}
		}

		const count = (predicate) => grains.filter(predicate).length;
		for (const [id, matches] of [
			["rice-short", count((grain) => grain.length_mm < 5.5)],
			["rice-long", count((grain) => grain.length_mm >= 6.6)],
			["rice-thin", count((grain) => grain.width_mm < 1.7)],
			["rice-thick", count((grain) => grain.width_mm >= 2.0)],
			["rice-broken-count", count((grain) => grain.label === "broken")],
			["rice-damaged", count((grain) => grain.label === "broken" || grain.label === "discolored")],
			["rice-discolored", count((grain) => grain.label === "discolored")],
			["rice-chalky", count((grain) => grain.chalk_fraction >= 0.33)],
		]) {
			document.querySelector(`#${id}`).textContent = formatShare(matches, total);
		}

		setValue("rice-lb-ratio", summarizeMeasurements(grains, "lb_ratio")?.average);
		const totalAreaProxy = grains.reduce(
			(sum, grain) => sum + grain.length_mm * grain.width_mm,
			0,
		);
		const brokenAreaProxy = grains.reduce(
			(sum, grain) => sum + (
				grain.label === "broken" ? grain.length_mm * grain.width_mm : 0
			),
			0,
		);
		document.querySelector("#rice-broken-weight").textContent = totalAreaProxy
			? `${(brokenAreaProxy / totalAreaProxy * 100).toFixed(2)}%`
			: "—";

		const labValues = grains
			.filter((grain) => Array.isArray(grain.mean_rgb) && grain.mean_rgb.length === 3)
			.map((grain) => rgbToLab(grain.mean_rgb));
		if (labValues.length) {
			const whiteness = labValues.map(([lightness, a, b]) =>
				100 - Math.hypot(100 - lightness, a, b)
			);
			const chroma = labValues.map(([, a, b]) => Math.hypot(a, b));
			setValue("rice-whiteness", whiteness.reduce((sum, value) => sum + value, 0) / whiteness.length);
			setValue("rice-color-index", chroma.reduce((sum, value) => sum + value, 0) / chroma.length);
		} else {
			setValue("rice-whiteness", null);
			setValue("rice-color-index", null);
		}
		document.querySelector("#rice-dimension-note").textContent = uploadedPhoto
			? "Estimated mm · assumed scale 0.12 mm/pixel."
			: "Millimetres · simulated measurements.";
		document.querySelector("#rice-color-note").textContent = uploadedPhoto
			? "Estimated from uploaded-photo RGB; color is uncalibrated."
			: "Estimated from calibrated simulated-grain RGB.";
	}

	function renderReportCharts() {
		if (!batch) return;
		const summary = batch.summary;
		const created = new Date(batch.created_at);
		const uploadedPhoto = isUploadedPhoto();
		renderRiceMetrics(batch.grains, uploadedPhoto);
		document.querySelector("#report-subtitle").textContent = uploadedPhoto
			? `${batch.variety} · uploaded photo · estimated dimensions`
			: `${batch.variety} · ${batch.lighting.replaceAll("_", " ")} lighting · simulated chamber`;
		document.querySelector("#report-sample").textContent = batch.sample_name;
		document.querySelector("#report-variety").textContent = batch.variety;
		document.querySelector("#report-count").textContent = summary.total_grains;
		document.querySelector("#report-batch").textContent = batch.batch_id;
		document.querySelector("#report-date").textContent = Number.isNaN(created.valueOf()) ? "Date unavailable" : created.toLocaleString();
		document.querySelector("#identity-batch").textContent = batch.batch_id;
		document.querySelector("#mean-length").textContent = Number(summary.mean_length_mm).toFixed(2);
		document.querySelector("#mean-width").textContent = Number(summary.mean_width_mm).toFixed(2);
		document.querySelector("#summary-good").textContent = `${summary.percent.good ?? 0}%`;
		document.querySelector("#mean-confidence").textContent = `${Math.round(Number(summary.mean_confidence || 0) * 100)}%`;
		document.querySelector("#confidence-warning").hidden = !(Number(summary.low_confidence_count) > 0);
		const flaggedIds = batch.grains.filter((grain) => grain.low_confidence).map((grain) => grain.id);
		document.querySelector("#warning-count").textContent = `${summary.low_confidence_count} grain${summary.low_confidence_count === 1 ? " needs" : "s need"} manual review${flaggedIds.length ? ` · #${flaggedIds.join(", #")}` : ""}.`;
		document.querySelector("#report-legend").innerHTML = categories.map(({ key, label, css }) => `<div class="quality-row"><i class="legend-dot ${css}"></i><span>${label} · ${summary.percent[key] ?? 0}%</span><strong>${summary.counts[key] ?? 0}</strong></div>`).join("");
		document.querySelector("#mean-length-note").textContent = uploadedPhoto ? "estimated mm · assumed scale" : "millimetres";
		document.querySelector("#mean-width-note").textContent = uploadedPhoto ? "estimated mm · assumed scale" : "millimetres";
		document.querySelector("#length-chart-footnote").textContent = uploadedPhoto
			? "Estimated millimetres · assumed 0.12 mm/pixel"
			: "Length in millimetres · simulated measurements";
		document.querySelector("#validation-note").textContent = uploadedPhoto
			? "not available for uploaded photos (no ground truth)"
			: "validated against simulator ground truth";
		document.querySelector("#report-note-text").innerHTML = uploadedPhoto
			? "<strong>Uploaded photo · estimated measurements.</strong> Millimetres assume 0.12 mm/pixel because no calibration card was provided. Grain classes come from a model trained on simulated samples; validation against photo ground truth is unavailable. The camera and chamber remain simulated; this is not a laboratory result."
			: "<strong>Simulated hardware demo.</strong> Camera, chamber lighting, grain detection, and measurements are simulated. This report is for demonstration and is not a laboratory result.";
		document.querySelector("#validation-content").hidden = uploadedPhoto;
		document.querySelector("#validation-accuracy").textContent = uploadedPhoto
			? "N/A"
			: `${(Number(batch.validation.accuracy || 0) * 100).toFixed(1)}%`;
		renderValidation();
		renderGrainTable();
		renderQrCode();
		renderReportCharts.create();
	}

	function renderValidation() {
		const validation = batch.validation;
		if (isUploadedPhoto()) return;
		const labels = validation.labels || categories.map(({ key }) => key);
		const displayClass = (key) => categories.find(({ key: categoryKey }) => categoryKey === key)?.label || key;
		const matrix = validation.confusion_matrix || [];
		const values = matrix.flat().map(Number);
		const maximum = Math.max(1, ...values);
		const table = document.createElement("table");
		table.className = "matrix-table";
		const head = document.createElement("thead");
		const headingRow = document.createElement("tr");
		const corner = document.createElement("th");
		corner.textContent = "Actual ↓ / predicted →";
		headingRow.append(corner);
		labels.forEach((label) => { const th = document.createElement("th"); th.textContent = displayClass(label); headingRow.append(th); });
		head.append(headingRow);
		table.append(head);
		const body = document.createElement("tbody");
		labels.forEach((label, rowIndex) => {
			const row = document.createElement("tr");
			const rowLabel = document.createElement("th");
			rowLabel.textContent = displayClass(label);
			row.append(rowLabel);
			labels.forEach((_, columnIndex) => {
				const value = Number(matrix[rowIndex]?.[columnIndex] || 0);
				const cell = document.createElement("td");
				cell.textContent = String(value);
				const intensity = Math.round(12 + (value / maximum) * 46);
				cell.style.backgroundColor = rowIndex === columnIndex ? `hsl(137 39% ${94 - intensity / 2}%)` : `hsl(5 58% ${97 - intensity / 3}%)`;
				row.append(cell);
			});
			body.append(row);
		});
		table.append(body);
		document.querySelector("#confusion-matrix").replaceChildren(table);
		document.querySelector("#per-class-metrics").innerHTML = labels.map((label) => {
			const valuesForClass = validation.per_class?.[label] || {};
			const displayLabel = categories.find(({ key }) => key === label)?.label || label;
			return `<div class="class-metric-row"><strong>${displayLabel}</strong><span>Precision <b>${(Number(valuesForClass.precision || 0) * 100).toFixed(0)}%</b></span><span>Recall <b>${(Number(valuesForClass.recall || 0) * 100).toFixed(0)}%</b></span></div>`;
		}).join("");
	}

	function renderGrainTable() {
		const filter = document.querySelector("#grain-filter").value;
		const sort = grainSort;
		const grains = batch.grains.filter((grain) => filter === "all" || grain.label === filter);
		grains.sort((left, right) => {
			const a = left[sort];
			const b = right[sort];
			if (sort === "id") return Number(a) - Number(b);
			if (sort === "label") return String(a).localeCompare(String(b)) || left.id - right.id;
			return Number(b) - Number(a) || Number(left.id) - Number(right.id);
		});
		const body = document.querySelector("#grain-table-body");
		body.replaceChildren();
		for (const grain of grains) {
			const row = document.createElement("tr");
			row.tabIndex = 0;
			row.className = `grain-row${grain.id === selectedGrainId ? " is-highlighted" : ""}`;
			row.setAttribute("aria-label", `Grain ${grain.id}, ${grain.label}, confidence ${Math.round(Number(grain.confidence) * 100)} percent. Select to highlight on detection image.`);
			const confidence = Math.round(Number(grain.confidence || 0) * 100);
			const cells = [String(grain.id), categories.find(({ key }) => key === grain.label)?.label || grain.label, Number(grain.length_mm).toFixed(2), Number(grain.width_mm).toFixed(2)];
			cells.forEach((value, index) => {
				const cell = document.createElement("td");
				if (index === 1) {
					const badge = document.createElement("span");
					badge.className = `class-badge ${grain.label}`;
					badge.textContent = value;
					cell.append(badge);
				} else cell.textContent = value;
				row.append(cell);
			});
			const confidenceCell = document.createElement("td");
			const confidenceWrap = document.createElement("span");
			confidenceWrap.className = "confidence-cell";
			const bar = document.createElement("i");
			bar.className = "confidence-bar";
			bar.innerHTML = `<i style="width:${confidence}%"></i>`;
			const confidenceText = document.createElement("b");
			confidenceText.textContent = `${confidence}%`;
			confidenceWrap.append(bar, confidenceText);
			confidenceCell.append(confidenceWrap);
			row.append(confidenceCell);
			const reason = document.createElement("td");
			reason.className = "reason-cell";
			reason.textContent = grain.reason || (grain.low_confidence ? "Low confidence; review this grain." : "No additional notes.");
			row.append(reason);
			const selectGrain = () => {
				selectedGrainId = selectedGrainId === grain.id ? null : grain.id;
				document.querySelectorAll(".grain-row").forEach((item) => item.classList.toggle("is-highlighted", Number(item.dataset.grainId) === selectedGrainId));
				row.dataset.grainId = grain.id;
				row.classList.toggle("is-highlighted", selectedGrainId === grain.id);
				drawDetections(canvas.width, batch.grains.length);
			};
			row.dataset.grainId = grain.id;
			row.addEventListener("click", selectGrain);
			row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectGrain(); } });
			body.append(row);
		}
		document.querySelector("#grain-table-count").textContent = `Showing ${grains.length} of ${batch.grains.length} detected grains`;
	}

	function renderQrCode() {
		const qr = document.querySelector("#batch-qr");
		qr.replaceChildren();
		if (typeof window.QRCode === "function") {
			new window.QRCode(qr, { text: `${window.location.origin}/api/batches/${encodeURIComponent(batch.batch_id)}`, width: 92, height: 92, colorDark: "#153d2b", colorLight: "#ffffff", correctLevel: window.QRCode.CorrectLevel.M });
		} else {
			qr.textContent = "QR unavailable";
		}
	}

	renderReportCharts.create = function createCharts() {
		destroyCharts();
		if (typeof window.Chart !== "function") return;
		const chartText = "#536259";
		const qualityChart = new window.Chart(document.querySelector("#quality-chart"), {
			type: "doughnut",
			data: { labels: categories.map(({ label }) => label), datasets: [{ data: categories.map(({ key }) => Number(batch.summary.counts[key] || 0)), backgroundColor: categories.map(({ color }) => color), borderColor: "#ffffff", borderWidth: 3, hoverOffset: 7 }] },
			options: { responsive: true, maintainAspectRatio: false, cutout: "67%", plugins: { legend: { display: false }, tooltip: { callbacks: { label: (item) => `${item.label}: ${item.raw} grains` } } } },
		});
		const histogram = makeHistogram(batch.grains.map((grain) => Number(grain.length_mm)).filter(Number.isFinite));
		const lengthChart = new window.Chart(document.querySelector("#length-chart"), {
			type: "bar",
			data: { labels: histogram.labels, datasets: [{ label: "Grains", data: histogram.counts, backgroundColor: "rgba(74, 155, 102, .76)", borderColor: "#348255", borderWidth: 1, borderRadius: 5, maxBarThickness: 38 }] },
			options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { title: { display: true, text: "Length (mm)", color: chartText }, grid: { display: false }, ticks: { color: chartText, maxRotation: 0, autoSkip: true } }, y: { beginAtZero: true, title: { display: true, text: "Grain count", color: chartText }, grid: { color: "#e9eee8" }, ticks: { precision: 0, color: chartText } } } },
		});
		chartInstances = [qualityChart, lengthChart];
	};

	function updateComparison(value) {
		document.querySelector("#comparison").style.setProperty("--compare-position", `${value}%`);
	}

	async function loadHistory() {
		const status = document.querySelector("#history-status");
		const body = document.querySelector("#history-table-body");
		status.textContent = "Loading batch history…";
		try {
			const response = await fetch("/api/batches");
			if (!response.ok) throw new Error("History is temporarily unavailable.");
			const history = await response.json();
			document.querySelector("#history-count").textContent = String(history.length);
			body.replaceChildren();
			for (const item of history) {
				const row = document.createElement("tr");
				const values = [item.batch_id, item.sample_name, item.variety, new Date(item.created_at).toLocaleString(), String(item.summary?.total_grains ?? 0)];
				values.forEach((value) => { const cell = document.createElement("td"); cell.textContent = value; row.append(cell); });
				const action = document.createElement("td");
				const button = document.createElement("button");
				button.className = "history-open";
				button.type = "button";
				button.textContent = item.batch_id === batch.batch_id ? "Current" : "Open report";
				button.disabled = item.batch_id === batch.batch_id;
				button.addEventListener("click", () => openHistoryBatch(item.batch_id));
				action.append(button);
				row.append(action);
				body.append(row);
			}
			status.textContent = history.length ? `${history.length} simulated assessment${history.length === 1 ? "" : "s"} in local history.` : "No saved batches yet.";
		} catch (error) {
			status.textContent = error.message || "History could not be loaded. Check the simulated backend and retry.";
		}
	}

	async function openHistoryBatch(batchId) {
		try {
			const response = await fetch(`/api/batches/${encodeURIComponent(batchId)}`);
			if (!response.ok) throw new Error("This batch could not be opened.");
			batch = await response.json();
			furthestStep = 4;
			renderCalibration();
			renderReportCharts();
			document.querySelector("#tab-report").click();
			const preview = new Image();
			preview.onload = () => {
				if (batch?.batch_id !== batchId) return;
				calibratedPhoto = preview;
				canvas.width = preview.naturalWidth;
				canvas.height = preview.naturalHeight;
				drawDetections(canvas.width, batch.grains.length);
			};
			preview.src = batch.images.calibrated;
			announce(`Opened saved assessment ${batch.batch_id}`);
		} catch (error) {
			document.querySelector("#history-status").textContent = error.message;
		}
	}

	slider.addEventListener("input", () => {
		document.querySelector("#grain-count-value").textContent = `${slider.value} grains`;
	});
	document.querySelector("#comparison-range").addEventListener("input", (event) => updateComparison(event.currentTarget.value));
	scanSource.addEventListener("change", updateScanSource);
	photoInput.addEventListener("change", () => {
		if (photoPreviewUrl) URL.revokeObjectURL(photoPreviewUrl);
		photoPreviewUrl = null;
		const photo = photoInput.files[0];
		const preview = document.querySelector("#photo-preview");
		if (!photo) {
			preview.hidden = true;
			preview.removeAttribute("src");
			return;
		}
		photoPreviewUrl = URL.createObjectURL(photo);
		preview.src = photoPreviewUrl;
		preview.hidden = false;
	});
	document.querySelector("#scan-speed").addEventListener("change", () => { if (activeStep === 3) startDetection(); });
	document.querySelector("#replay-detection").addEventListener("click", startDetection);
	document.querySelector("#grain-filter").addEventListener("change", renderGrainTable);
	document.querySelector("#grain-sort").addEventListener("change", (event) => { grainSort = event.currentTarget.value; renderGrainTable(); });
	document.querySelectorAll("[data-sort]").forEach((button) => button.addEventListener("click", () => {
		grainSort = button.dataset.sort;
		document.querySelector("#grain-sort").value = grainSort;
		renderGrainTable();
	}));
	document.querySelector("#tab-report").addEventListener("click", () => switchReportTab("report"));
	document.querySelector("#tab-history").addEventListener("click", () => { switchReportTab("history"); loadHistory(); });
	document.querySelector("#refresh-history").addEventListener("click", loadHistory);
	document.querySelector("#download-report").addEventListener("click", () => window.print());

	function switchReportTab(tab) {
		const isHistory = tab === "history";
		document.querySelector("#tab-report").classList.toggle("is-selected", !isHistory);
		document.querySelector("#tab-report").setAttribute("aria-selected", String(!isHistory));
		document.querySelector("#tab-history").classList.toggle("is-selected", isHistory);
		document.querySelector("#tab-history").setAttribute("aria-selected", String(isHistory));
		document.querySelector("#report-view").hidden = isHistory;
		document.querySelector("#history-view").hidden = !isHistory;
		if (!isHistory) renderReportCharts();
	}

	form.addEventListener("submit", async (event) => {
		event.preventDefault();
		scanError.hidden = true;
		const payload = {
			sample_name: document.querySelector("#sample-name").value.trim(),
			variety: document.querySelector("#variety").value.trim(),
			lighting: document.querySelector("#lighting").value,
			n_grains: Number(slider.value),
		};
		if (!payload.sample_name || !payload.variety) { showFriendlyError("Please enter both a sample name and a rice variety to continue."); return; }
		const uploadingPhoto = scanSource.value === "upload";
		const photo = photoInput.files[0];
		if (uploadingPhoto && !photo) { showFriendlyError("Choose a rice photo before starting the analysis."); return; }
		if (uploadingPhoto && photo.size > 10 * 1024 * 1024) { showFriendlyError("Choose a photo that is 10 MB or smaller."); return; }
		setLoading(true);
		try {
			await runChamberEffects();
			const response = await fetch("/api/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
			const result = await response.json().catch(() => ({}));
			if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The scan service could not complete the request.");
			if (!result.images || !result.calibration || !result.summary || !Array.isArray(result.grains)) throw new Error("The scan returned incomplete results. Please try again.");
			batch = result;
			selectedGrainId = null;
			renderCalibration();
			furthestStep = 4;
			setStep(2);
		} catch (error) {
			const detail = error instanceof TypeError ? "We couldn’t reach the scan service. Check that the simulated backend is running, then try again." : error.message;
			showFriendlyError(detail || "Something went wrong while scanning. Please try again.");
		} finally { setLoading(false); }
	});

	stepButtons.forEach((button) => button.addEventListener("click", () => {
		const destination = Number(button.dataset.step);
		if (destination <= furthestStep) setStep(destination);
	}));
	document.querySelectorAll("[data-next]").forEach((button) => button.addEventListener("click", () => setStep(activeStep + 1)));
	document.querySelectorAll("[data-back]").forEach((button) => button.addEventListener("click", () => setStep(activeStep - 1)));
	document.querySelector("#new-scan").addEventListener("click", () => {
		batch = null;
		selectedGrainId = null;
		furthestStep = 1;
		form.reset();
		updateScanSource();
		if (photoPreviewUrl) URL.revokeObjectURL(photoPreviewUrl);
		photoPreviewUrl = null;
		document.querySelector("#photo-preview").hidden = true;
		document.querySelector("#photo-preview").removeAttribute("src");
		document.querySelector("#grain-count-value").textContent = `${slider.value} grains`;
		scanError.hidden = true;
		destroyCharts();
		switchReportTab("report");
		setStep(1);
	});
})();
