(function () {
  "use strict";

  const groupsEl = document.getElementById("groups");
  const emptyEl = document.getElementById("empty");
  const statsEl = document.getElementById("stats");
  const refreshBtn = document.getElementById("refresh-btn");

  function fmtBytes(n) {
    if (!n) return "?";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(1)} ${units[i]}`;
  }

  // POSIX double-quote-safe: wrap in "..." and escape the chars the shell
  // still treats specially inside double quotes.
  function quote(path) {
    return `"${path.replace(/[\\"$`]/g, "\\$&")}"`;
  }

  function legacyCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }

  async function copyToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (e) { /* fall through to legacy path */ }
    }
    return legacyCopy(text);
  }

  async function fetchStats() {
    const res = await fetch("/api/stats", { cache: "no-store" });
    if (!res.ok) return;
    const s = await res.json();
    statsEl.textContent =
      `${s.total_groups} group(s) · ${s.pending} pending · ${s.kept} kept · ` +
      `${s.marked_delete} marked delete · ${s.purged} purged`;
  }

  async function decide(groupId, imageId, decision) {
    const res = await fetch("/api/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group_id: groupId, image_id: imageId, decision }),
    });
    if (!res.ok) throw new Error("decide failed: " + res.status);
    return res.json();
  }

  async function applyRecommended(groupId) {
    const res = await fetch(`/api/groups/${groupId}/apply_recommended`, { method: "POST" });
    if (!res.ok) throw new Error("apply_recommended failed: " + res.status);
    return res.json();
  }

  function memberCard(group, member) {
    const div = document.createElement("div");
    div.className = `member decision-${member.decision}`;

    const badges = [`${member.width}x${member.height}`, fmtBytes(member.file_size)];
    if (member.source_ext) badges.push(member.source_ext);
    if (member.source_size) badges.push(fmtBytes(member.source_size));
    if (member.distance) badges.push(`d=${member.distance}`);

    div.innerHTML = `
      <img loading="lazy" src="/thumb/${member.image_id}" alt="${member.rel_path}">
      <div class="badges">
        ${member.recommended_keep ? '<span class="recommended">recommended keep</span>' : ""}
        ${member.is_raw ? '<span class="raw">RAW</span>' : ""}
        ${badges.map((b) => `<span>${b}</span>`).join("")}
      </div>
      <div class="path">${member.rel_path}</div>
      <div class="actions">
        <button class="btn small keep-btn" type="button">Keep</button>
        <button class="btn small delete-btn" type="button">Delete</button>
      </div>
    `;

    const keepBtn = div.querySelector(".keep-btn");
    const deleteBtn = div.querySelector(".delete-btn");
    keepBtn.classList.toggle("active", member.decision === "keep");
    deleteBtn.classList.toggle("active", member.decision === "delete");

    keepBtn.addEventListener("click", async () => {
      const next = member.decision === "keep" ? "pending" : "keep";
      await decide(group.group_id, member.image_id, next);
      loadGroups();
    });
    deleteBtn.addEventListener("click", async () => {
      const next = member.decision === "delete" ? "pending" : "delete";
      await decide(group.group_id, member.image_id, next);
      loadGroups();
    });

    return div;
  }

  function groupCard(group) {
    const div = document.createElement("div");
    div.className = "group";

    const head = document.createElement("div");
    head.className = "group-head";
    head.innerHTML = `
      <span class="method ${group.method}">${group.method}</span>
      <span>group ${group.group_id} · ${group.members.length} photos</span>
      <span class="spacer"></span>
      <button class="btn small apply-btn" type="button">Apply recommended</button>
    `;
    head.querySelector(".apply-btn").addEventListener("click", async () => {
      await applyRecommended(group.group_id);
      loadGroups();
    });
    div.appendChild(head);

    const members = document.createElement("div");
    members.className = "members";
    for (const member of group.members) {
      members.appendChild(memberCard(group, member));
    }
    div.appendChild(members);

    const pathsSection = document.createElement("div");
    pathsSection.className = "group-paths-section";

    const pathsTitle = document.createElement("div");
    pathsTitle.className = "group-paths-title";
    pathsTitle.textContent = "Paths (click to copy for your image manager)";
    pathsSection.appendChild(pathsTitle);

    const pathsText = group.members.map((m) => quote(m.abs_path)).join(" ");
    const pathsEl = document.createElement("pre");
    pathsEl.className = "group-paths";
    pathsEl.title = "Click to copy";
    pathsEl.textContent = pathsText;
    let copiedTimer = null;
    pathsEl.addEventListener("click", async () => {
      if (!(await copyToClipboard(pathsText))) return;
      pathsEl.classList.add("copied");
      clearTimeout(copiedTimer);
      copiedTimer = setTimeout(() => pathsEl.classList.remove("copied"), 1200);
    });
    pathsSection.appendChild(pathsEl);

    div.appendChild(pathsSection);

    return div;
  }

  async function loadGroups() {
    const res = await fetch("/api/groups", { cache: "no-store" });
    if (!res.ok) throw new Error("groups fetch failed: " + res.status);
    const { groups } = await res.json();

    groupsEl.innerHTML = "";
    emptyEl.hidden = groups.length !== 0;
    for (const group of groups) {
      groupsEl.appendChild(groupCard(group));
    }
    fetchStats();
  }

  refreshBtn.addEventListener("click", loadGroups);
  loadGroups().catch((e) => console.error(e));
})();
