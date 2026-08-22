(function () {
  "use strict";

  const SLIDE_MS = (window.SLIDE_SECONDS || 60) * 1000;

  const imgs = [document.getElementById("img-a"), document.getElementById("img-b")];
  const dateEl = document.getElementById("date");
  const yearsEl = document.getElementById("years-ago");
  const counterEl = document.getElementById("counter");
  const pathEl = document.getElementById("path");
  const windowEl = document.getElementById("window-info");
  const deleteBtn = document.getElementById("delete-btn");
  const privateBtn = document.getElementById("private-btn");
  const newSetBtn = document.getElementById("new-set-btn");
  const rotateBtns = {
    90: document.getElementById("rotate-90-btn"),
    180: document.getElementById("rotate-180-btn"),
    270: document.getElementById("rotate-270-btn"),
  };
  const overlay = document.getElementById("overlay");
  const emptyEl = document.getElementById("empty");
  const dwellFill = document.getElementById("dwell-fill");

  let subset = [];
  let meta = {};      // active subset's window summary (window_days/available/viewed)
  let index = 0;      // index of the currently shown photo
  let front = 0;      // which <img> is currently visible
  let current = null; // current photo object
  let timer = null;

  async function fetchSubset() {
    const res = await fetch("/api/subset", { cache: "no-store" });
    if (!res.ok) throw new Error("subset fetch failed: " + res.status);
    return res.json();
  }

  // The API returns { photos: [...], window_days, available, viewed }.
  function applySubset(data) {
    subset = data.photos || [];
    meta = data;
    emptyEl.hidden = subset.length !== 0;
  }

  async function loadSubset() {
    try {
      applySubset(await fetchSubset());
    } catch (e) {
      console.error(e);
    }
  }

  function windowText() {
    if (!meta.window_days) return "";
    let s = `±${meta.window_days}d window`;
    if (meta.available > 0) s += ` · ${meta.viewed}/${meta.available} viewed`;
    return s;
  }

  const ROT_CLASSES = ["rot-0", "rot-90", "rot-180", "rot-270"];
  function applyRotateClass(imgEl, deg) {
    imgEl.classList.remove(...ROT_CLASSES);
    imgEl.classList.add("rot-" + (((deg % 360) + 360) % 360));
  }

  // Restart the fill animation from empty so it always tracks the current dwell.
  function resetDwellBar() {
    dwellFill.style.transition = "none";
    dwellFill.style.width = "0%";
    void dwellFill.offsetWidth; // force reflow so the next transition isn't coalesced
    dwellFill.style.transition = `width ${SLIDE_MS}ms linear`;
    dwellFill.style.width = "100%";
  }

  function setOverlay(photo) {
    counterEl.textContent = `${index + 1} / ${subset.length}`;
    dateEl.textContent = photo.date;
    yearsEl.textContent =
      photo.years_ago > 0 ? `(${photo.years_ago} yr${photo.years_ago > 1 ? "s" : ""} ago)` : "";
    pathEl.textContent = photo.rel_path;
    pathEl.classList.remove("copied");
    windowEl.textContent = windowText();
    deleteBtn.setAttribute("aria-pressed", photo.marked ? "true" : "false");
    privateBtn.setAttribute("aria-pressed", photo.private ? "true" : "false");
  }

  function show(i) {
    if (subset.length === 0) return;
    index = (i % subset.length + subset.length) % subset.length; // wrap both ways
    const photo = subset[index];
    current = photo;
    const back = 1 - front;
    const backImg = imgs[back];
    backImg.onload = () => {
      backImg.classList.add("visible");
      imgs[front].classList.remove("visible");
      front = back;
    };
    backImg.src = photo.url;
    applyRotateClass(backImg, photo.rotate_deg || 0);
    setOverlay(photo);
    resetDwellBar();
  }

  async function next() {
    // Past the end: refetch so x-hour reselection is picked up, then restart.
    if (index + 1 >= subset.length) {
      await loadSubset();
      if (subset.length === 0) return;
      show(0);
    } else {
      show(index + 1);
    }
  }

  function prev() {
    show(index - 1); // wraps to the last photo at the start
  }

  function startTimer() {
    clearInterval(timer);
    timer = setInterval(next, SLIDE_MS);
  }

  // Manual navigation also resets the dwell timer so the photo doesn't jump away.
  function goNext() {
    next();
    startTimer();
  }
  function goPrev() {
    prev();
    startTimer();
  }

  async function start() {
    await loadSubset();
    if (subset.length === 0) {
      emptyEl.hidden = false;
      return;
    }
    show(0);
    startTimer();
  }

  async function queueRotate(deg) {
    if (!current) return;
    const res = await fetch(`/api/rotate/${current.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deg }),
    });
    if (!res.ok) return;
    const data = await res.json();
    current.rotate_deg = data.rotate_deg;
    const inSubset = subset.find((p) => p.id === current.id);
    if (inSubset) inSubset.rotate_deg = data.rotate_deg;
    applyRotateClass(imgs[front], data.rotate_deg);
  }
  rotateBtns[90].addEventListener("click", (ev) => { ev.stopPropagation(); queueRotate(90); });
  rotateBtns[180].addEventListener("click", (ev) => { ev.stopPropagation(); queueRotate(180); });
  rotateBtns[270].addEventListener("click", (ev) => { ev.stopPropagation(); queueRotate(270); });

  deleteBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (!current) return;
    const res = await fetch(`/api/mark/${current.id}`, { method: "POST" });
    if (!res.ok) return;
    const data = await res.json();
    current.marked = data.marked;
    // keep the subset copy in sync too
    const inSubset = subset.find((p) => p.id === current.id);
    if (inSubset) inSubset.marked = data.marked;
    deleteBtn.setAttribute("aria-pressed", data.marked ? "true" : "false");
  });

  privateBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (!current) return;
    const res = await fetch(`/api/private/${current.id}`, { method: "POST" });
    if (!res.ok) return;
    const data = await res.json();
    current.private = data.private;
    // keep the subset copy in sync too
    const inSubset = subset.find((p) => p.id === current.id);
    if (inSubset) inSubset.private = data.private;
    privateBtn.setAttribute("aria-pressed", data.private ? "true" : "false");
  });

  // Force a fresh subset right now, without waiting for the refresh window.
  newSetBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    try {
      const res = await fetch("/api/subset/reselect", { method: "POST" });
      if (!res.ok) return;
      applySubset(await res.json());
    } catch (e) {
      console.error(e);
      return;
    }
    if (subset.length === 0) return;
    index = 0;
    show(0);
    startTimer();
  });

  // Click the path to copy it to the clipboard.
  // navigator.clipboard needs a secure context (https or localhost), which this
  // LAN-served-over-http app isn't, so fall back to the older execCommand copy.
  function legacyCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (e) {
      ok = false;
    }
    document.body.removeChild(ta);
    return ok;
  }

  async function copyToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (e) {
        // fall through to legacy path
      }
    }
    return legacyCopy(text);
  }

  let copiedTimer = null;
  pathEl.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (!current) return;
    const ok = await copyToClipboard(current.rel_path);
    if (!ok) return;
    pathEl.classList.add("copied");
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => pathEl.classList.remove("copied"), 1200);
  });

  // Click the left half for previous, the right half for next.
  // Clicks on the overlay buttons are ignored (they stop propagation / are excluded).
  document.body.addEventListener("click", (ev) => {
    if (ev.target.closest("#controls")) return;
    if (ev.clientX < window.innerWidth / 2) goPrev();
    else goNext();
  });

  // Keyboard arrows as a bonus (handy when a keyboard is attached).
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowLeft") goPrev();
    else if (ev.key === "ArrowRight") goNext();
  });

  // Reveal the overlay on touch/tap (phones have no hover); auto-hide after a bit.
  let hideTimer = null;
  function flashOverlay() {
    overlay.classList.add("show");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => overlay.classList.remove("show"), 4000);
  }
  document.body.addEventListener("pointerdown", flashOverlay);

  start();
})();
