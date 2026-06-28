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
  const newSetBtn = document.getElementById("new-set-btn");
  const overlay = document.getElementById("overlay");
  const emptyEl = document.getElementById("empty");

  let rotation = [];
  let meta = {};      // active rotation's window summary (window_days/available/viewed)
  let index = 0;      // index of the currently shown photo
  let front = 0;      // which <img> is currently visible
  let current = null; // current photo object
  let timer = null;

  async function fetchRotation() {
    const res = await fetch("/api/rotation", { cache: "no-store" });
    if (!res.ok) throw new Error("rotation fetch failed: " + res.status);
    return res.json();
  }

  // The API returns { photos: [...], window_days, available, viewed }.
  function applyRotation(data) {
    rotation = data.photos || [];
    meta = data;
    emptyEl.hidden = rotation.length !== 0;
  }

  async function loadRotation() {
    try {
      applyRotation(await fetchRotation());
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

  function setOverlay(photo) {
    counterEl.textContent = `${index + 1} / ${rotation.length}`;
    dateEl.textContent = photo.date;
    yearsEl.textContent =
      photo.years_ago > 0 ? `(${photo.years_ago} yr${photo.years_ago > 1 ? "s" : ""} ago)` : "";
    pathEl.textContent = photo.rel_path;
    windowEl.textContent = windowText();
    deleteBtn.setAttribute("aria-pressed", photo.marked ? "true" : "false");
  }

  function show(i) {
    if (rotation.length === 0) return;
    index = (i % rotation.length + rotation.length) % rotation.length; // wrap both ways
    const photo = rotation[index];
    current = photo;
    const back = 1 - front;
    const backImg = imgs[back];
    backImg.onload = () => {
      backImg.classList.add("visible");
      imgs[front].classList.remove("visible");
      front = back;
    };
    backImg.src = photo.url;
    setOverlay(photo);
  }

  async function next() {
    // Past the end: refetch so x-hour reselection is picked up, then restart.
    if (index + 1 >= rotation.length) {
      await loadRotation();
      if (rotation.length === 0) return;
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
    await loadRotation();
    if (rotation.length === 0) {
      emptyEl.hidden = false;
      return;
    }
    show(0);
    startTimer();
  }

  deleteBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (!current) return;
    const res = await fetch(`/api/mark/${current.id}`, { method: "POST" });
    if (!res.ok) return;
    const data = await res.json();
    current.marked = data.marked;
    // keep the rotation copy in sync too
    const inRot = rotation.find((p) => p.id === current.id);
    if (inRot) inRot.marked = data.marked;
    deleteBtn.setAttribute("aria-pressed", data.marked ? "true" : "false");
  });

  // Force a fresh subset right now, without waiting for the rotation window.
  newSetBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    try {
      const res = await fetch("/api/rotation/reselect", { method: "POST" });
      if (!res.ok) return;
      applyRotation(await res.json());
    } catch (e) {
      console.error(e);
      return;
    }
    if (rotation.length === 0) return;
    index = 0;
    show(0);
    startTimer();
  });

  // Click the left half for previous, the right half for next.
  // Clicks on the overlay buttons are ignored (they stop propagation / are excluded).
  document.body.addEventListener("click", (ev) => {
    if (ev.target.closest("#delete-btn") || ev.target.closest("#new-set-btn")) return;
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
