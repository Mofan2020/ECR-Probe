const $ = (sel) => document.querySelector(sel);
const api = () => window.pywebview.api;

let commandGroups = [];
let selected = null;
let pollTimer = null;

/* ---------------------------------------------------------------- 工具 */

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function copyText(text, label) {
  if (!text) return;
  // 主路径走 Python 侧：file:// 下 navigator.clipboard 常被拒
  try {
    const res = await api().copy_text(text);
    if (res && res.ok) {
      flash(label);
      return;
    }
  } catch (err) { /* 落到下面的兜底 */ }

  try {
    await navigator.clipboard.writeText(text);
    flash(label);
  } catch (err) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    flash(label);
  }
}

function flash(label) {
  const el = $(`[data-copy="${label}"]`);
  if (!el) return;
  const old = el.textContent;
  el.textContent = "已复制";
  setTimeout(() => { el.textContent = old; }, 1200);
}

function setOut(id, text) {
  const el = $(`#${id}`);
  el.textContent = text || "";
  el.scrollTop = 0;
}

/* ---------------------------------------------------------------- 日志 */

async function refreshLog() {
  const res = await api().get_log();
  if (!res || !res.ok) return;
  const box = $("#log");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.textContent = res.lines.join("\n");
  if (atBottom) box.scrollTop = box.scrollHeight;
}

/* ---------------------------------------------------------------- 命令 */

async function loadCommands() {
  const res = await api().get_commands();
  if (!res.ok) return;
  commandGroups = res.groups;
  $("#cmdCount").textContent = `(${res.total})`;
  renderCommands("");
  if (res.spelling) {
    const p = document.createElement("p");
    p.className = "small";
    p.textContent = res.spelling;
    document.querySelector("footer").appendChild(p);
  }
}

function renderCommands(filter) {
  const needle = filter.trim().toLowerCase();
  const host = $("#groups");
  host.innerHTML = "";
  let shown = 0;

  for (const group of commandGroups) {
    const items = group.items.filter((c) =>
      !needle ||
      c.name.toLowerCase().includes(needle) ||
      (c.note || "").toLowerCase().includes(needle)
    );
    if (!items.length) continue;

    const title = document.createElement("div");
    title.className = "group-title";
    title.textContent = `${group.title} · ${items.length}`;
    host.appendChild(title);

    for (const item of items) {
      const row = document.createElement("div");
      row.className = "cmd";
      row.title = `${item.name}\n${item.note || ""}`;
      row.dataset.name = item.name;
      const dir = item.direction ? `<span class="dir">${esc(item.direction)}</span>  ` : "";
      row.innerHTML = dir + esc(item.name) + (item.note ? `  <span class="note">${esc(item.note)}</span>` : "");
      row.addEventListener("click", () => pick(item, row));
      host.appendChild(row);
      shown += 1;
    }
  }

  if (!shown) {
    const empty = document.createElement("div");
    empty.className = "hint";
    empty.textContent = "没匹配的命令";
    host.appendChild(empty);
  }
}

function pick(item, row) {
  selected = item;
  document.querySelectorAll(".cmd.active").forEach((el) => el.classList.remove("active"));
  if (row) row.classList.add("active");

  $("#method").value = item.name;
  $("#sendHint").textContent = item.note || "";
  fillParams();
}

function fillParams() {
  if (!selected) return;
  const hint = selected.params || {};
  $("#params").value = Object.keys(hint).length ? JSON.stringify(hint, null, 2) : "";
}

/* ---------------------------------------------------------------- 目标 */

async function loadLocal() {
  const res = await api().local_info();
  if (res && res.ok) {
    $("#localAddrs").textContent = "本机地址：" + res.addresses.join("  ");
  }
}

async function discover() {
  const box = $("#hits");
  box.innerHTML = "<div class='hint'>读监听表…</div>";
  $("#discover").disabled = true;
  try {
    const res = await api().discover();
    box.innerHTML = "";
    if (!res.ok) {
      box.innerHTML = `<div class='hint'>${esc(res.error)}</div>`;
      return;
    }
    if (!res.ports.length) {
      box.innerHTML = `<div class='hint'>没认出易启学进程（本机共 ${res.listeners_total} 个 TCP 监听）。点「扫描端口」按候选端口试。</div>`;
      return;
    }
    $("#ports").value = res.ports.map((p) => p.port).join(",");
    for (const p of res.ports) {
      const el = document.createElement("div");
      el.className = "hit";
      el.innerHTML = `<span>${esc(p.port)}</span><span class="tag">${esc(p.process || "pid " + p.pid)}</span>`;
      el.title = "点一下探测这个端口";
      el.addEventListener("click", () => probePort(p.port));
      box.appendChild(el);
    }
  } finally {
    $("#discover").disabled = false;
    refreshLog();
  }
}

async function locate() {
  const host = $("#host").value.trim();
  const spec = $("#ports").value.trim();
  const box = $("#hits");
  box.innerHTML = "<div class='hint'>扫描中，端口多的话会慢…</div>";
  $("#locate").disabled = true;
  try {
    const res = await api().locate(host, spec);
    box.innerHTML = "";
    if (!res.ok) {
      box.innerHTML = `<div class='hint'>${esc(res.error)}</div>`;
      return;
    }
    if (!res.found) {
      const how = res.source === "process" ? "进程命中" : "端口扫描";
      box.innerHTML = `<div class='hint'>${how}：开着 ${res.open_ports.length} 个端口，没有握手成功的。</div>`;
      return;
    }
    for (const hit of res.hits) {
      const el = document.createElement("div");
      el.className = "hit";
      const tag = hit.accepted
        ? "<span class='tag'>握手成功</span>"
        : `<span class='tag no'>${esc(hit.error || "HTTP " + hit.status)}</span>`;
      const fp = hit.matched ? " <span class='fp'>WebSocket++</span>" : "";
      el.innerHTML = `<span>${esc(hit.port)}</span><span>${esc(hit.path)}</span>${tag}${fp}`;
      el.addEventListener("click", () => {
        $("#path").value = hit.path;
        $("#ports").value = String(hit.port);
        connect(hit.port, hit.path);
      });
      box.appendChild(el);
    }
  } finally {
    $("#locate").disabled = false;
    refreshLog();
  }
}

async function probePort(port) {
  const host = $("#host").value.trim();
  const box = $("#hits");
  box.innerHTML = `<div class='hint'>探测 ${esc(port)}…</div>`;
  const res = await api().probe_one(host, port);
  box.innerHTML = "";
  if (!res.ok) {
    box.innerHTML = `<div class='hint'>${esc(res.error)}</div>`;
  } else if (!res.open) {
    box.innerHTML = "<div class='hint'>端口没开</div>";
  } else {
    for (const hit of res.hits) {
      const el = document.createElement("div");
      el.className = "hit";
      const tag = hit.accepted
        ? "<span class='tag'>握手成功</span>"
        : `<span class='tag no'>${esc(hit.error || "HTTP " + hit.status)}</span>`;
      el.innerHTML = `<span>${esc(hit.path)}</span>${tag}`;
      el.addEventListener("click", () => { $("#path").value = hit.path; connect(port, hit.path); });
      box.appendChild(el);
    }
  }
  refreshLog();
}

/* ---------------------------------------------------------------- 连接 */

async function connect(explicitPort, explicitPath) {
  const host = $("#host").value.trim();
  const port = explicitPort ?? parseInt($("#ports").value.trim().split(",")[0].split("-")[0], 10);
  const path = explicitPath ?? ($("#path").value.trim() || "/");
  if (!Number.isFinite(port)) {
    setStatus("端口填得不对", false);
    return;
  }
  $("#connect").disabled = true;
  try {
    const res = await api().connect(host, port, path);
    if (res.ok) {
      setStatus(`已连接 ${res.target}`, true);
    } else {
      setStatus(res.error || "连接失败", false);
      if (res.response) setOut("reply", res.response);
    }
  } finally {
    $("#connect").disabled = false;
    refreshLog();
  }
}

async function disconnect() {
  await api().disconnect();
  setStatus("未连接", false);
  refreshLog();
}

function setStatus(text, on) {
  const el = $("#status");
  el.textContent = text;
  el.classList.toggle("on", !!on);
  el.title = text;
}

async function updateStatus() {
  const res = await api().status();
  if (res && res.connected) setStatus(`已连接 ${res.target}`, true);
}

/* ---------------------------------------------------------------- 发送 */

async function send() {
  const method = $("#method").value.trim();
  if (!method) {
    $("#sendState").textContent = "先选命令或手填 method";
    return;
  }
  $("#send").disabled = true;
  $("#sendState").textContent = "发送中…";
  $("#fired").innerHTML = "";
  try {
    const res = await api().send(method, $("#params").value);
    setOut("payload", res.payload || "");
    if (!res.ok) {
      setOut("reply", res.error || "发送失败");
      $("#sendState").textContent = "失败";
    } else {
      setOut("reply", res.reply || "（对端没回东西）");
      $("#sendState").textContent = "已发出";
      renderFired(res.fired || []);
    }
  } finally {
    $("#send").disabled = false;
    refreshLog();
  }
}

function renderFired(list) {
  const box = $("#fired");
  box.innerHTML = "";
  for (const item of list) {
    const el = document.createElement("div");
    el.className = "fired-item" + (item.ok ? "" : " bad");
    el.textContent = `规则 ${item.trigger} → ${item.action} ×${item.repeat}${item.ok ? "" : "（失败）"}`;
    if (item.note) el.title = item.note;
    box.appendChild(el);
  }
}

/* ---------------------------------------------------------------- 规则 */

async function loadRules() {
  const res = await api().get_rules();
  if (!res.ok) return;
  renderRules(res.rules);
}

function renderRules(rules) {
  const box = $("#rules");
  box.innerHTML = "";
  $("#ruleCount").textContent = `(${rules.length})`;

  for (const rule of rules) {
    const el = document.createElement("div");
    el.className = "rule" + (rule.enabled ? "" : " off");

    const head = document.createElement("div");
    head.className = "rule-head";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = rule.enabled;
    cb.addEventListener("change", async () => {
      const res = await api().toggle_rule(rule.id);
      if (res.ok) renderRules(res.rules);
    });

    const name = document.createElement("div");
    name.className = "rule-name";
    name.textContent = rule.trigger;
    name.title = rule.trigger;
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = " → ";
    const action = document.createElement("span");
    action.textContent = rule.action;

    const del = document.createElement("button");
    del.className = "rule-del";
    del.textContent = "×";
    del.title = "删除";
    del.addEventListener("click", async () => {
      const res = await api().remove_rule(rule.id);
      if (res.ok) renderRules(res.rules);
    });

    head.append(cb, name, del);
    name.append(arrow, action);

    const meta = document.createElement("div");
    meta.className = "rule-meta";
    meta.textContent = `×${rule.repeat}${rule.delay_ms ? ` · 间隔 ${rule.delay_ms}ms` : ""} · ${JSON.stringify(rule.params || {})}`;

    el.append(head, meta);
    if (rule.note) {
      const note = document.createElement("div");
      note.className = "rule-note";
      note.textContent = rule.note;
      el.appendChild(note);
    }
    box.appendChild(el);
  }
}

async function addRule() {
  const trigger = $("#rTrigger").value.trim();
  const action = $("#rAction").value.trim();
  if (!trigger || !action) return;
  const res = await api().add_rule(
    trigger,
    action,
    $("#rParams").value.trim(),
    parseInt($("#rRepeat").value, 10) || 1,
    parseInt($("#rDelay").value, 10) || 0
  );
  if (res.ok) {
    renderRules(res.rules);
    $("#rTrigger").value = "";
    $("#rAction").value = "";
    $("#rParams").value = "";
  } else {
    window.alert(res.error || "添加失败");
  }
}

async function resetRules() {
  const res = await api().reset_rules();
  if (res.ok) renderRules(res.rules);
}

/* ---------------------------------------------------------------- 启动 */

function boot() {
  $("#warnOk").addEventListener("click", () => $("#warn").classList.add("hide"));
  $("#discover").addEventListener("click", discover);
  $("#locate").addEventListener("click", locate);
  $("#connect").addEventListener("click", () => connect());
  $("#disconnect").addEventListener("click", disconnect);
  $("#send").addEventListener("click", send);
  $("#fillParams").addEventListener("click", fillParams);
  $("#addRule").addEventListener("click", addRule);
  $("#resetRules").addEventListener("click", resetRules);
  $("#clearLog").addEventListener("click", async () => {
    await api().clear_log();
    refreshLog();
  });
  $("#search").addEventListener("input", (e) => renderCommands(e.target.value));

  const source = { payload: () => $("#payload").textContent, reply: () => $("#reply").textContent, log: () => $("#log").textContent };
  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.copy;
      copyText(source[key] ? source[key]() : "", key);
    });
  });

  loadCommands();
  loadLocal();
  loadRules();
  refreshLog();
  updateStatus();
  pollTimer = setInterval(refreshLog, 2500);
}

window.addEventListener("pywebviewready", boot);
