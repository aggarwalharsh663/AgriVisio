(function () {
  "use strict";

  const backBtn = document.getElementById("error-back-btn");
  if (backBtn) {
    backBtn.addEventListener("click", function () {
      if (window.history.length > 1) {
        window.history.back();
      } else {
        window.location.href = "/";
      }
    });
  }

  const pathSpan = document.getElementById("error-path-display");
  if (pathSpan) {
    pathSpan.textContent = window.location.pathname || "/";
  }

  const timeSpan = document.getElementById("error-time-display");
  if (timeSpan) {
    timeSpan.textContent = new Date().toISOString();
  }

  // Match dark mode preference
  try {
    const savedTheme = localStorage.getItem("agrivisio_theme");
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    if (savedTheme === "dark" || (!savedTheme && prefersDark)) {
      document.documentElement.classList.add("dark");
    }
  } catch (e) {}
})();
