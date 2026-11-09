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
		{ key: "good", label: "Good", color: "#10b981", css: "good-dot" },
		{ key: "broken", label: "Broken", color: "#f43f5e", css: "broken-dot" },
		{ key: "chalky", label: "Chalky", color: "#f59e0b", css: "chalky-dot" },
		{ key: "discolored", label: "Discolored", color: "#a855f7", css: "discolored-dot" },
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
		const source = scanSource.value;
		const uploading = source === "upload";
		const scanning = source === "scanner";
		uploadField.hidden = !uploading;
		photoInput.disabled = !uploading;
		photoInput.required = uploading;
		const chamberFigure = document.querySelector("#chamber-figure");
		if (chamberFigure) chamberFigure.hidden = uploading || scanning;
		const grainCountField = document.querySelector("#grain-count-field");
		if (grainCountField) grainCountField.hidden = uploading || scanning;
		const lightingField = document.querySelector(".lighting-field");
		if (lightingField) lightingField.hidden = uploading || scanning;
		let label;
		if (uploading) {
			label = "Upload & analyze photo";
		} else if (scanning) {
			label = "Scan from flatbed scanner";
		} else {
			label = "Pour rice and scan";
		}
		document.querySelector("#scan-button .button-label").textContent = label;
		document.querySelectorAll("[data-source]").forEach((card) => {
			card.classList.toggle("is-selected", card.dataset.source === source);
		});
	}

	function setLightingChoice(value) {
		const lighting = document.querySelector("#lighting");
		lighting.value = value;
		document.querySelectorAll("[data-lighting]").forEach((card) => {
			card.classList.toggle("is-selected", card.dataset.lighting === value);
		});
		if (window.Chamber && typeof window.Chamber.setLighting === "function") {
			window.Chamber.setLighting(value);
		}
	}

	function setLoading(loading) {
		scanButton.disabled = loading;
		scanButton.classList.toggle("is-loading", loading);
		const source = scanSource.value;
		let label;
		if (loading) {
			label = source === "upload" ? "Analyzing photo..." : source === "scanner" ? "Scanning from flatbed..." : "Scanning sample...";
		} else {
			label = source === "upload" ? "Upload & analyze photo" : source === "scanner" ? "Scan from flatbed scanner" : "Pour rice and scan";
		}
		scanButton.querySelector(".button-label").textContent = label;
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
		const isUpload = isUploadedPhoto();
		const divider = document.querySelector("#comparison-divider");
		const range = document.querySelector("#comparison-range");
		const caption = document.querySelector(".compare-caption");
		const tagRight = document.querySelector(".image-tag-right");
		const tagLeft = document.querySelector(".image-tag-left");

		if (isUpload) {
			if (divider) divider.hidden = true;
			if (range) range.hidden = true;
			if (caption) caption.hidden = true;
			if (tagRight) tagRight.hidden = true;
			if (tagLeft) tagLeft.textContent = "Uploaded Photo - Standard View";
			document.querySelector("#error-before").textContent = "N/A";
			document.querySelector("#error-after").textContent = "N/A";
			document.querySelector("#improvement-text").textContent = "Direct camera capture - 0.12 mm/px scale";
			return;
		}

		if (divider) divider.hidden = false;
		if (range) range.hidden = false;
		if (caption) caption.hidden = false;
		if (tagRight) tagRight.hidden = false;
		if (tagLeft) tagLeft.textContent = "Calibrated";
		if (tagRight) tagRight.textContent = "Raw - simulated";

		const before = Number(batch.calibration?.patch_error_before);
		const after = Number(batch.calibration?.patch_error_after);
		document.querySelector("#error-before").textContent = Number.isFinite(before) ? before.toFixed(1) : "-";
		document.querySelector("#error-after").textContent = Number.isFinite(after) ? after.toFixed(1) : "-";
		const reduction = before > 0 ? Math.max(0, Math.round((1 - after / before) * 100)) : 0;
		document.querySelector("#improvement-text").textContent = `${reduction}% lower color error`;
		document.querySelector("#comparison-range").value = "50";
		updateComparison(50);
	}

	function renderDetectionCounters() {
		document.querySelector("#total-grains").textContent = String(detectedCount);
		const total = Math.max(1, detectedCount);
		for (const { key } of categories) {
			document.querySelector(`#count-${key}`).textContent = String(detectedByClass[key]);
			const bar = document.querySelector(`#bar-${key}`);
			if (bar) bar.style.width = `${Math.round((detectedByClass[key] / total) * 100)}%`;
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
		document.querySelector("#quality-list").innerHTML = categories.map(({ key, label, css }) => `<div class="quality-row"><i class="legend-dot ${css}"></i><div class="quality-meta"><span>${label}</span><span class="quality-track ${key}"><i id="bar-${key}"></i></span></div><strong id="count-${key}">0</strong></div>`).join("");
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

	function renderReportCharts() {
		if (!batch) return;
		const summary = batch.summary;
		const created = new Date(batch.created_at);
		const uploadedPhoto = isUploadedPhoto();
		document.querySelector("#report-subtitle").textContent = uploadedPhoto
			? `${batch.variety} · uploaded photo · estimated dimensions`
			: `${batch.variety} · ${batch.lighting.replaceAll("_", " ")} lighting · simulated chamber`;
		document.querySelector("#report-sample").textContent = batch.sample_name;
		document.querySelector("#report-variety").textContent = batch.variety;
		document.querySelector("#report-count").textContent = summary.total_grains;
		document.querySelector("#report-batch").textContent = batch.batch_id;
		document.querySelector("#report-date").textContent = Number.isNaN(created.valueOf()) ? "Date unavailable" : created.toLocaleString();
		document.querySelector("#identity-batch").textContent = batch.batch_id;
		document.querySelector("#fingerprint-hash").textContent = batch.fingerprint?.hash || (typeof batch.fingerprint === "string" ? batch.fingerprint : "—");
		// Display mock and calibration warnings
		const mockBanner = document.querySelector("#mock-warning-banner");
		if (mockBanner) {
			const isMock = Boolean(batch.is_mock || batch.simulated || batch.mock_mode);
			mockBanner.style.display = isMock ? "flex" : "none";
			mockBanner.hidden = !isMock;
		}
		const uncalBanner = document.querySelector("#uncalibrated-warning-banner");
		if (uncalBanner) {
			const isUncal = Boolean(batch.calibration?.status === "uncalibrated" || (uploadedPhoto && !batch.calibration?.calibrated));
			uncalBanner.style.display = isUncal ? "flex" : "none";
			uncalBanner.hidden = !isUncal;
		}

		document.querySelector("#mean-length").textContent = Number(summary.mean_length_mm).toFixed(2);
		document.querySelector("#mean-width").textContent = Number(summary.mean_width_mm).toFixed(2);
		document.querySelector("#summary-good").textContent = `${summary.percent.good ?? 0}%`;
		document.querySelector("#mean-confidence").textContent = `${Math.round(Number(summary.mean_confidence || 0) * 100)}%`;
		const lowConf = Number(summary.low_confidence_count || 0);
		const total = Number(summary.total_grains || 1);
		const flagRatio = lowConf / total;
		// Only trigger the alert banner if a notable proportion (>15%) is low-confidence or overall confidence is low (<75%)
		const shouldWarn = flagRatio > 0.15 || Number(summary.mean_confidence || 1) < 0.75;
		document.querySelector("#confidence-warning").hidden = !shouldWarn;
		if (shouldWarn) {
			const pct = Math.round(flagRatio * 100);
			document.querySelector("#warning-count").textContent = `${lowConf} grains (${pct}% of sample) have borderline optical confidence. You can inspect and sort them in the grain details table below.`;
		}
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
			const rawLabel = categories.find(({ key }) => key === label)?.label || label;
			const displayLabel = escapeHtml(rawLabel);
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
			new window.QRCode(qr, { text: `${window.location.origin}/api/batches/${encodeURIComponent(batch.batch_id)}`, width: 92, height: 92, colorDark: "#000000", colorLight: "#ffffff", correctLevel: window.QRCode.CorrectLevel.M });
		} else {
			qr.textContent = "QR unavailable";
		}
	}

	renderReportCharts.create = function createCharts() {
		destroyCharts();
		if (typeof window.Chart !== "function") return;
		const isDark = document.documentElement.classList.contains("dark");
		const chartText = isDark ? "#94a3b8" : "#64748b";
		const gridColor = isDark ? "rgba(255, 255, 255, 0.08)" : "rgba(15, 23, 42, 0.06)";
		const barColor = isDark ? "rgba(56, 189, 248, 0.75)" : "rgba(37, 99, 235, 0.75)";
		const barBorder = isDark ? "#38bdf8" : "#2563eb";
		const doughnutBorder = isDark ? "#0f172a" : "#ffffff";
		const tooltipBg = isDark ? "#1e293b" : "#0f172a";
		const qualityChart = new window.Chart(document.querySelector("#quality-chart"), {
			type: "doughnut",
			data: {
				labels: categories.map(({ label }) => label),
				datasets: [{
					data: categories.map(({ key }) => Number(batch.summary.counts[key] || 0)),
					backgroundColor: ["#10b981", "#f43f5e", "#f59e0b", "#8b5cf6"],
					borderColor: doughnutBorder,
					borderWidth: 2,
					hoverOffset: 6
				}]
			},
			options: {
				responsive: true,
				maintainAspectRatio: false,
				cutout: "70%",
				plugins: {
					legend: { display: false },
					tooltip: {
						backgroundColor: tooltipBg,
						titleColor: "#ffffff",
						bodyColor: "#e2e8f0",
						borderColor: "rgba(255, 255, 255, 0.12)",
						borderWidth: 1,
						padding: 10,
						cornerRadius: 6,
						callbacks: { label: (item) => `${item.label}: ${item.raw} grains` }
					}
				}
			},
		});
		const histogram = makeHistogram(batch.grains.map((grain) => Number(grain.length_mm)).filter(Number.isFinite));
		const lengthChart = new window.Chart(document.querySelector("#length-chart"), {
			type: "bar",
			data: {
				labels: histogram.labels,
				datasets: [{
					label: "Grains",
					data: histogram.counts,
					backgroundColor: barColor,
					borderColor: barBorder,
					borderWidth: 1,
					borderRadius: 4,
					maxBarThickness: 34
				}]
			},
			options: {
				responsive: true,
				maintainAspectRatio: false,
				plugins: {
					legend: { display: false },
					tooltip: {
						backgroundColor: tooltipBg,
						titleColor: "#ffffff",
						bodyColor: "#e2e8f0",
						borderColor: "rgba(255, 255, 255, 0.12)",
						borderWidth: 1,
						padding: 10,
						cornerRadius: 6,
					}
				},
				scales: {
					x: {
						title: { display: true, text: "Length (mm)", color: chartText, font: { weight: "600", size: 11 } },
						grid: { display: false },
						ticks: { color: chartText, maxRotation: 0, autoSkip: true, font: { family: "'JetBrains Mono', monospace", size: 11 } }
					},
					y: {
						beginAtZero: true,
						title: { display: true, text: "Grain count", color: chartText, font: { weight: "600", size: 11 } },
						grid: { color: gridColor },
						ticks: { precision: 0, color: chartText, font: { family: "'JetBrains Mono', monospace", size: 11 } }
					}
				}
			},
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
	document.querySelectorAll("[data-source]").forEach((card) => {
		card.addEventListener("click", () => {
			scanSource.value = card.dataset.source;
			updateScanSource();
		});
	});
	document.querySelectorAll("[data-lighting]").forEach((card) => {
		card.addEventListener("click", () => setLightingChoice(card.dataset.lighting));
	});
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
	document.querySelector("#download-report").addEventListener("click", downloadReport);

	async function downloadReport() {
		const downloadBtn = document.querySelector("#download-report");
		const originalText = downloadBtn.innerHTML;
		const batchId = batch?.batch_id || document.querySelector("#identity-batch")?.textContent?.trim() || "AS-sample";

		try {
			downloadBtn.disabled = true;
			downloadBtn.textContent = "Preparing...";

			if (window.electronAPI && typeof window.electronAPI.saveReportPdf === "function") {
				downloadBtn.textContent = "Saving PDF...";
				const result = await window.electronAPI.saveReportPdf(batchId);
				if (result?.success) {
					downloadBtn.textContent = "✓ PDF Saved!";
					announce(`Report PDF saved successfully.`);
					setTimeout(() => {
						downloadBtn.disabled = false;
						downloadBtn.innerHTML = originalText;
					}, 2500);
					return;
				} else if (result?.canceled) {
					downloadBtn.disabled = false;
					downloadBtn.innerHTML = originalText;
					return;
				}
			}

			// Web browser or fallback: download standalone clean HTML report & trigger print
			downloadBtn.textContent = "Downloading...";
			const reportDoc = generateStandaloneReportHtml(batch, batchId);
			const blob = new Blob([reportDoc], { type: "text/html;charset=utf-8" });
			const url = URL.createObjectURL(blob);
			const link = document.createElement("a");
			link.href = url;
			link.download = `AgriScan-Report-${batchId}.html`;
			document.body.appendChild(link);
			link.click();
			document.body.removeChild(link);
			URL.revokeObjectURL(url);

			try {
				window.print();
			} catch (e) {}

			downloadBtn.textContent = "✓ Downloaded!";
			announce(`Report downloaded as AgriScan-Report-${batchId}.html`);
			setTimeout(() => {
				downloadBtn.disabled = false;
				downloadBtn.innerHTML = originalText;
			}, 2500);
		} catch (err) {
			console.error("Report download error:", err);
			downloadBtn.textContent = "Error saving";
			setTimeout(() => {
				downloadBtn.disabled = false;
				downloadBtn.innerHTML = originalText;
			}, 2500);
		}
	}

	function generateStandaloneReportHtml(currentBatch, batchId) {
		const sampleName = escapeHtml(currentBatch?.sample_name || document.querySelector("#report-sample")?.textContent || "Sample");
		const variety = escapeHtml(currentBatch?.variety || document.querySelector("#report-variety")?.textContent || "Rice");
		const totalGrains = escapeHtml(currentBatch?.summary?.total_grains || currentBatch?.grains?.length || document.querySelector("#report-count")?.textContent || "0");
		const dateStr = escapeHtml(currentBatch?.created_at ? new Date(currentBatch.created_at).toLocaleString() : new Date().toLocaleString());
		const goodPct = escapeHtml(document.querySelector("#summary-good")?.textContent || "-");
		const meanLength = escapeHtml(document.querySelector("#mean-length")?.textContent || "-");
		const meanWidth = escapeHtml(document.querySelector("#mean-width")?.textContent || "-");
		const fpObj = currentBatch?.fingerprint;
		const fingerprint = escapeHtml((typeof fpObj === "object" && fpObj !== null ? fpObj.hash : fpObj) || document.querySelector("#fingerprint-hash")?.textContent || "-");
		const safeBatchId = escapeHtml(batchId || currentBatch?.batch_id || "Report");

		const isMockData = Boolean(currentBatch?.is_mock || currentBatch?.simulated);
		const isUncalibrated = Boolean(currentBatch?.calibration?.status === "uncalibrated");

		const grains = currentBatch?.grains || [];
		let rowsHtml = "";
		for (const g of grains) {
			const label = categories.find(c => c.key === g.label)?.label || g.label;
			const conf = Math.round(Number(g.confidence || 0) * 100);
			const len = Number(g.length_mm).toFixed(2);
			const wid = Number(g.width_mm).toFixed(2);
			rowsHtml += `<tr>
				<td>${escapeHtml(g.id)}</td>
				<td><strong>${escapeHtml(label)}</strong></td>
				<td>${escapeHtml(len)} mm</td>
				<td>${escapeHtml(wid)} mm</td>
				<td>${conf}%</td>
				<td><small>${escapeHtml(g.reason || "")}</small></td>
			</tr>`;
		}

		return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AgriScan Report - ${safeBatchId}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #ffffff; color: #0f172a; padding: 32px; line-height: 1.5; }
  .header { display: flex; align-items: center; gap: 18px; margin-bottom: 24px; border-bottom: 3px solid #d97706; padding-bottom: 18px; }
  .logo { width: 64px; height: 64px; object-fit: contain; }
  .header-text h1 { font-size: 26px; font-weight: 900; letter-spacing: 0.05em; color: #0f172a; }
  .header-text h1 span { color: #d97706; }
  .header-text p { font-size: 13px; color: #64748b; font-weight: 600; }
  .banner-mock { background: #fef2f2; border: 2px dashed #dc2626; color: #991b1b; padding: 12px; margin-bottom: 20px; text-align: center; font-weight: 800; border-radius: 6px; letter-spacing: 0.05em; }
  .banner-uncal { background: #fffbeb; border: 1px solid #f59e0b; color: #92400e; padding: 10px; margin-bottom: 20px; text-align: center; font-weight: 700; border-radius: 6px; }
  .hero-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; }
  .card span { font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 700; letter-spacing: 0.05em; display: block; margin-bottom: 4px; }
  .card strong { font-size: 22px; color: #0f172a; display: block; }
  .card small { font-size: 12px; color: #64748b; }
  h2 { font-size: 16px; margin: 28px 0 12px; color: #0f172a; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }
  th, td { border: 1px solid #e2e8f0; padding: 8px 12px; text-align: left; }
  th { background: #f1f5f9; font-weight: 700; color: #334155; }
  tr:nth-child(even) { background: #f8fafc; }
  .footer { margin-top: 36px; padding-top: 16px; border-top: 1px solid #e2e8f0; font-size: 11px; color: #94a3b8; display: flex; justify-content: space-between; }
</style>
</head>
<body>
  <div class="header">
    <img src="/assets/emblem.png" class="logo" alt="AgriScan Logo">
    <div class="header-text">
      <h1>AGRI<span>SCAN</span></h1>
      <p>Precision Optical Grain Quality Assessment Report</p>
    </div>
  </div>
  ${isMockData ? '<div class="banner-mock">&#9888; SIMULATED / MOCK DATA &mdash; NOT A PHYSICAL LABORATORY MEASUREMENT</div>' : ''}
  ${isUncalibrated ? '<div class="banner-uncal">&#128207; UNCALIBRATED MEASUREMENT (Scale is an estimate)</div>' : ''}
  <div class="hero-grid">
    <div class="card"><span>Sample Name</span><strong>${sampleName}</strong><small>${variety}</small></div>
    <div class="card"><span>Assessment ID</span><strong>${safeBatchId}</strong><small>${dateStr}</small></div>
    <div class="card"><span>Total Grains</span><strong>${totalGrains}</strong><small>optically checked</small></div>
    <div class="card"><span>Good Grains</span><strong>${goodPct}</strong><small>of batch sample</small></div>
    <div class="card"><span>Mean Length</span><strong>${meanLength} mm</strong><small>calibrated length</small></div>
    <div class="card"><span>Mean Width</span><strong>${meanWidth} mm</strong><small>calibrated width</small></div>
  </div>
  <h2>Detailed Grain Measurements (${grains.length} grains)</h2>
  <table>
    <thead><tr><th>#</th><th>Class</th><th>Length</th><th>Width</th><th>Confidence</th><th>Reason</th></tr></thead>
    <tbody>${rowsHtml}</tbody>
  </table>
  <div class="footer">
    <span>AgriScan Precision Optics &bull; Laboratory Assessment Report</span>
    <span>Fingerprint: <code>${fingerprint}</code></span>
  </div>
</body>
</html>`;
	}

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
		const currentSource = scanSource.value;
		const uploadingPhoto = currentSource === "upload";
		const scanningHardware = currentSource === "scanner";
		const photo = photoInput.files[0];
		if (uploadingPhoto && !photo) { showFriendlyError("Choose a rice photo before starting the analysis."); return; }
		if (uploadingPhoto && photo.size > 10 * 1024 * 1024) { showFriendlyError("Choose a photo that is 10 MB or smaller."); return; }
		setLoading(true);
		try {
			let endpoint;
			let requestOptions;
			if (scanningHardware) {
				endpoint = "/api/scan/hardware";
				requestOptions = {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(payload),
				};
			} else if (uploadingPhoto) {
				const formData = new FormData();
				formData.append("image", photo);
				formData.append("sample_name", payload.sample_name);
				formData.append("variety", payload.variety);
				formData.append("lighting", payload.lighting);
				endpoint = "/api/scan/upload";
				requestOptions = { method: "POST", body: formData };
			} else {
				await runChamberEffects();
				endpoint = "/api/scan";
				requestOptions = {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(payload),
				};
			}
			let response = await fetch(endpoint, requestOptions);
			let result = await response.json().catch(() => ({}));
			if (scanningHardware && !response.ok && result.error === "scanner_not_found") {
				setLoading(false);
				showFriendlyError("No flatbed scanner detected. Please select a rice photo to upload instead.");
				scanSource.value = "upload";
				updateScanSource();
				return;
			}
			if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The scan service could not complete the request.");
			if (!result.images || !result.calibration || !result.summary || !Array.isArray(result.grains)) throw new Error("The scan returned incomplete results. Please try again.");
			batch = result;
			selectedGrainId = null;
			renderCalibration();
			furthestStep = 4;
			setStep(2);
		} catch (error) {
			const detail = error instanceof TypeError ? "We couldn't reach the scan service. Check that the backend is running, then try again." : error.message;
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
		setLightingChoice(document.querySelector("#lighting").value);
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

	function initTheme() {
		const saved = localStorage.getItem("agriscan_theme") || "light";
		applyTheme(saved);
		const toggleBtn = document.querySelector("#theme-toggle");
		if (toggleBtn) {
			toggleBtn.addEventListener("click", () => {
				const current = document.documentElement.classList.contains("dark") ? "dark" : "light";
				const next = current === "dark" ? "light" : "dark";
				localStorage.setItem("agriscan_theme", next);
				applyTheme(next);
				if (typeof renderReportCharts.create === "function") {
					renderReportCharts.create();
				}
			});
		}
	}

	function applyTheme(mode) {
		const isDark = mode === "dark";
		document.documentElement.classList.toggle("dark", isDark);
		const themeMeta = document.querySelector('meta[name="theme-color"]');
		if (themeMeta) {
			themeMeta.setAttribute("content", isDark ? "#0d0f14" : "#f7f5ed");
		}
		const toggleBtn = document.querySelector("#theme-toggle");
		if (toggleBtn) {
			const icon = toggleBtn.querySelector(".toggle-icon");
			const label = toggleBtn.querySelector(".toggle-label");
			if (icon) icon.textContent = isDark ? "🌙" : "☀️";
			if (label) label.textContent = isDark ? "Dark" : "Light";
		}
	}

	initTheme();
	// Default to scanner mode
	scanSource.value = "scanner";
	updateScanSource();
	// Desktop app heartbeat watcher
	function sendHeartbeat() {
		fetch("/api/heartbeat", { method: "POST" }).catch(() => {});
	}
	sendHeartbeat();
	setInterval(sendHeartbeat, 2000);

	setTimeout(() => {
		const splash = document.getElementById("app-splash");
		if (splash) splash.classList.add("is-loaded");
	}, 250);
})();
