(() => {
	const form = document.querySelector("#scan-form");
	const scanButton = document.querySelector("#scan-button");
	const scanError = document.querySelector("#scan-error");
	const message = document.querySelector("#app-message");
	const slider = document.querySelector("#grain-count");
	const stepButtons = [...document.querySelectorAll(".step-item")];
	let batch = null;
	let activeStep = 1;
	let furthestStep = 1;

	const categories = [
		{ key: "good", label: "Good", color: "good-dot" },
		{ key: "broken", label: "Broken", color: "broken-dot" },
		{ key: "chalky", label: "Chalky", color: "chalky-dot" },
		{ key: "discolored", label: "Discolored", color: "discolored-dot" },
	];

	function announce(text) {
		message.textContent = text;
	}

	function setStep(step) {
		if (step < 1 || step > furthestStep) return;
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
	}

	function setLoading(loading) {
		scanButton.disabled = loading;
		scanButton.classList.toggle("is-loading", loading);
		scanButton.querySelector(".button-label").textContent = loading ? "Scanning simulated sample…" : "Pour rice and scan";
		form.setAttribute("aria-busy", String(loading));
	}

	async function runChamberEffects() {
		const chamber = window.Chamber;
		if (!chamber) return;
		for (const effect of ["pourRice", "cameraFlash"]) {
			if (typeof chamber[effect] === "function") {
				try {
					await chamber[effect]();
				} catch {
					// Chamber visuals are optional; they must never block a scan.
				}
			}
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
		const before = Number(batch.calibration.patch_error_before);
		const after = Number(batch.calibration.patch_error_after);
		document.querySelector("#error-before").textContent = Number.isFinite(before) ? before.toFixed(1) : "—";
		document.querySelector("#error-after").textContent = Number.isFinite(after) ? after.toFixed(1) : "—";
		const reduction = before > 0 ? Math.max(0, Math.round((1 - after / before) * 100)) : 0;
		document.querySelector("#improvement-text").textContent = `${reduction}% lower color error`;
		const range = document.querySelector("#comparison-range");
		range.value = "50";
		updateComparison(50);
	}

	function renderDetection() {
		const summary = batch.summary;
		document.querySelector("#annotated-image").src = batch.images.annotated;
		document.querySelector("#total-grains").textContent = summary.total_grains;
		document.querySelector("#quality-list").innerHTML = categories.map(({ key, label, color }) => `
			<div class="quality-row"><i class="legend-dot ${color}"></i><span>${label}</span><strong>${summary.counts[key] ?? 0}</strong></div>
		`).join("");
	}

	function renderReport() {
		const summary = batch.summary;
		const created = new Date(batch.created_at);
		document.querySelector("#report-subtitle").textContent = `${batch.variety} · ${batch.lighting.replaceAll("_", " ")} lighting · simulated chamber`;
		document.querySelector("#report-sample").textContent = batch.sample_name;
		document.querySelector("#report-variety").textContent = batch.variety;
		document.querySelector("#report-count").textContent = summary.total_grains;
		document.querySelector("#report-batch").textContent = batch.batch_id;
		document.querySelector("#report-date").textContent = Number.isNaN(created.valueOf()) ? "Date unavailable" : created.toLocaleString();
		document.querySelector("#mean-length").textContent = Number(summary.mean_length_mm).toFixed(2);
		document.querySelector("#mean-width").textContent = Number(summary.mean_width_mm).toFixed(2);
		document.querySelector("#mean-ratio").textContent = Number(summary.mean_lb_ratio).toFixed(2);
		document.querySelector("#quality-bar").innerHTML = categories.map(({ key }) => `<span style="width:${Number(summary.percent[key] ?? 0)}%" title="${key}: ${summary.percent[key] ?? 0}%"></span>`).join("");
		document.querySelector("#report-legend").innerHTML = categories.map(({ key, label, color }) => `
			<div class="quality-row"><i class="legend-dot ${color}"></i><span>${label} · ${summary.percent[key] ?? 0}%</span><strong>${summary.counts[key] ?? 0}</strong></div>
		`).join("");
	}

	function updateComparison(value) {
		const comparison = document.querySelector("#comparison");
		comparison.style.setProperty("--compare-position", `${value}%`);
	}

	slider.addEventListener("input", () => {
		document.querySelector("#grain-count-value").textContent = `${slider.value} grains`;
	});

	document.querySelector("#comparison-range").addEventListener("input", (event) => {
		updateComparison(event.currentTarget.value);
	});

	form.addEventListener("submit", async (event) => {
		event.preventDefault();
		scanError.hidden = true;
		const payload = {
			sample_name: document.querySelector("#sample-name").value.trim(),
			variety: document.querySelector("#variety").value.trim(),
			lighting: document.querySelector("#lighting").value,
			n_grains: Number(slider.value),
		};
		if (!payload.sample_name || !payload.variety) {
			showFriendlyError("Please enter both a sample name and a rice variety to continue.");
			return;
		}

		setLoading(true);
		await runChamberEffects();
		try {
			const response = await fetch("/api/scan", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(payload),
			});
			const result = await response.json().catch(() => ({}));
			if (!response.ok) {
				throw new Error(typeof result.detail === "string" ? result.detail : "The scan service could not complete the request.");
			}
			if (!result.images || !result.calibration || !result.summary) {
				throw new Error("The scan returned incomplete results. Please try again.");
			}
			batch = result;
			renderCalibration();
			renderDetection();
			renderReport();
			furthestStep = 4;
			setStep(2);
		} catch (error) {
			const detail = error instanceof TypeError
				? "We couldn’t reach the scan service. Check that the simulated backend is running, then try again."
				: error.message;
			showFriendlyError(detail || "Something went wrong while scanning. Please try again.");
		} finally {
			setLoading(false);
		}
	});

	stepButtons.forEach((button) => button.addEventListener("click", () => {
		const destination = Number(button.dataset.step);
		if (destination <= furthestStep) setStep(destination);
	}));

	document.querySelectorAll("[data-next]").forEach((button) => button.addEventListener("click", () => setStep(activeStep + 1)));
	document.querySelectorAll("[data-back]").forEach((button) => button.addEventListener("click", () => setStep(activeStep - 1)));

	document.querySelector("#new-scan").addEventListener("click", () => {
		batch = null;
		furthestStep = 1;
		form.reset();
		document.querySelector("#grain-count-value").textContent = `${slider.value} grains`;
		scanError.hidden = true;
		setStep(1);
	});
})();
