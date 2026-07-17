const $ = (id) => document.getElementById(id);
const state = {
  overview: null,
  creds: [],
  selected: new Set(),
  live: false,
  lastSync: 0,
  pollTimer: null,
  es: null,
};

const toast = (msg, ok=true) => {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  el.style.borderColor = ok ? "rgba(69,214,164,.45)" : "rgba(255,107,138,.45)";
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
};

async function api(path, opts={}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers||{}) },
    cache: "no-store",
    ...opts,
  });
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
    return data;
  }
  if (!res.ok) throw new Error(res.statusText);
  return res;
}

function fmtSize(n){
  n = Number(n||0);
  if (n < 1024) return n + " B";
  if (n < 1024*1024) return (n/1024).toFixed(1) + " KB";
  return (n/1024/1024).toFixed(2) + " MB";
}
function fmtTime(ts){
  if (!ts) return "-";
  return new Date(Number(ts)*1000).toLocaleString();
}
function setLive(on, text) {
  state.live = !!on;
  const el = $("liveBadge");
  el.classList.toggle("on", !!on);
  el.classList.toggle("off", !on);
  el.textContent = text || (on ? "LIVE · 实时同步中" : "LIVE · 已断开/轮询");
}
function setLastSync(ts) {
  state.lastSync = ts || Date.now()/1000;
  $("lastSync").textContent = "上次同步: " + new Date(state.lastSync*1000).toLocaleTimeString();
}

function fillSelect(sel, items, current) {
  const cur = current || sel.value;
  const focus = document.activeElement === sel;
  if (focus) return; // don't clobber user interaction
  sel.innerHTML = "";
  (items||[]).forEach(v => {
    const opt = document.createElement("option");
    opt.value = v; opt.textContent = v;
    if (v === cur) opt.selected = true;
    sel.appendChild(opt);
  });
}

function renderPorts(g) {
  const ports = (g && g.ports) || {};
  const st = (g && g.port_status) || {};
  const map = [
    ["http_random", "HTTP 随机"],
    ["http_stable", "HTTP 低延迟"],
    ["socks5_random", "SOCKS 随机"],
    ["socks5_stable", "SOCKS 低延迟"],
    ["webui", "WebUI"],
  ];
  $("portChips").innerHTML = map.map(([k,label]) => {
    const on = !!st[k];
    return `<span class="chip ${on?"on":"off"}">${label}: ${ports[k] ?? "-"} ${on?"●":"○"}</span>`;
  }).join("");
}

function renderCreds(items) {
  const prev = new Set(state.selected);
  state.creds = items || [];
  const body = $("credBody");
  const empty = $("credEmpty");
  body.innerHTML = "";
  if (!state.creds.length) {
    empty.classList.remove("hidden");
  } else {
    empty.classList.add("hidden");
  }
  state.creds.forEach((it) => {
    const checked = prev.has(it.name);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" data-name="${it.name}" data-bucket="${it.bucket}" class="cred-check" ${checked?"checked":""} /></td>
      <td>${it.name}</td>
      <td>${it.bucket}</td>
      <td>${fmtSize(it.size)}</td>
      <td>${fmtTime(it.mtime)}</td>
    `;
    body.appendChild(tr);
  });
  body.querySelectorAll(".cred-check").forEach(ch => {
    ch.onchange = () => {
      if (ch.checked) state.selected.add(ch.dataset.name);
      else state.selected.delete(ch.dataset.name);
    };
  });
}

function renderBrowsers(browsers) {
  const registered = (browsers && browsers.details && browsers.details.registered) || [];
  const body = $("browserBody");
  const empty = $("browserEmpty");
  body.innerHTML = "";
  $("browserSummaryText").textContent =
    `registered ${browsers?.registered ?? 0} · zombies ${browsers?.zombies ?? 0} · active ${browsers?.active ?? 0}`;
  if (!registered.length) {
    empty.classList.remove("hidden");
  } else {
    empty.classList.add("hidden");
  }
  registered.forEach((it) => {
    const tr = document.createElement("tr");
    const alive = it.alive === true ? "是" : (it.alive === false ? "否" : "-");
    tr.innerHTML = `
      <td>${it.purpose || "-"}</td>
      <td>${it.pid ?? "-"}</td>
      <td>${it.worker_id ?? "-"}</td>
      <td>${alive}</td>
      <td>${it.age_sec ?? "-"}s</td>
      <td title="${it.profile || ""}">${(it.profile || "-").toString().slice(-48)}</td>
    `;
    body.appendChild(tr);
  });
  $("browserDetails").textContent = JSON.stringify(browsers?.details || browsers || {}, null, 2);
}

function selectedNames(uploadedOnly=false) {
  const names = [];
  document.querySelectorAll(".cred-check:checked").forEach(ch => {
    if (uploadedOnly && ch.dataset.bucket !== "uploaded") return;
    names.push(ch.dataset.name);
  });
  return names;
}

function applyOverview(data) {
  state.overview = data;
  setLastSync(data.ts || Date.now()/1000);
  $("mReg").textContent = data.browsers?.registered ?? "-";
  $("mZombie").textContent = data.browsers?.zombies ?? "-";
  $("mProxy").textContent = data.goproxy?.running ? "运行中" : "未运行";
  $("mUploaded").textContent = data.credentials?.counts?.uploaded ?? 0;
  $("pillLive").textContent = `测活门槛: ${data.config?.live_inspect_enabled === false ? "关闭" : "开启"}`;
  $("pillBind").textContent = `代理绑定: ${data.config?.goproxy_bind_register_proxy ? "开启" : "关闭"}`;
  $("overviewExtra").textContent = JSON.stringify({
    proxy: data.proxy,
    browsers: {
      registered: data.browsers?.registered,
      zombies: data.browsers?.zombies,
      project_browsers: data.browsers?.project_browsers,
    },
    log_cleanup: data.log_cleanup,
    browser_cleanup: data.browser_cleanup,
    ts: data.ts,
  }, null, 2);

  fillSelect($("poolMode"), data.modes || [], data.config?.goproxy_pool_mode);
  fillSelect($("endpoint"), data.endpoints || [], data.config?.goproxy_endpoint);
  if (document.activeElement !== $("bindProxy")) {
    $("bindProxy").checked = !!data.config?.goproxy_bind_register_proxy;
  }
  renderPorts(data.goproxy);
  $("proxyStatus").textContent = JSON.stringify({
    running: data.goproxy?.running,
    managed: data.goproxy?.managed,
    pid: data.goproxy?.pid,
    binary: data.goproxy?.binary,
    last_error: data.goproxy?.last_error,
    port_status: data.goproxy?.port_status,
  }, null, 2);
  renderBrowsers(data.browsers || {});
}

async function refresh(full=true) {
  const data = await api("/api/overview");
  applyOverview(data);
  if (full) {
    const creds = await api("/api/credentials?buckets=uploaded,pending");
    renderCreds(creds.items || []);
  }
}

function downloadBase64Zip(filename, b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i=0;i<bin.length;i++) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], {type: "application/zip"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename || "credentials.zip";
  a.click();
  URL.revokeObjectURL(a.href);
}

function startSSE() {
  if (state.es) {
    try { state.es.close(); } catch {}
  }
  const es = new EventSource("/api/stream");
  state.es = es;
  es.addEventListener("overview", async (ev) => {
    try {
      const data = JSON.parse(ev.data);
      applyOverview(data);
      setLive(true, "LIVE · SSE 推送中");
      if (!window.__credTick) window.__credTick = 0;
      window.__credTick += 1;
      if (window.__credTick % 2 === 0) {
        const creds = await api("/api/credentials?buckets=uploaded,pending");
        renderCreds(creds.items || []);
      }
    } catch (e) {
      // ignore parse errors
    }
  });
  es.onerror = () => {
    setLive(false, "LIVE · SSE 断开，回退轮询");
  };
  es.onopen = () => setLive(true, "LIVE · SSE 已连接");
}

function startPollFallback() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(() => {
    refresh(true).catch(() => setLive(false, "LIVE · 同步失败"));
  }, 3000);
}

async function afterMutation(msg) {
  toast(msg);
  await refresh(true);
}

async function main() {
  $("btnRefresh").onclick = async () => {
    try { await refresh(true); toast("已同步后端"); }
    catch(e){ toast(e.message,false); }
  };
  $("btnCleanupBrowsers").onclick = async () => {
    try {
      const res = await api("/api/browsers/cleanup", { method:"POST", body: JSON.stringify({kill:true}) });
      await afterMutation(`清理僵尸 ${res.killed_count || 0} 个`);
    } catch(e){ toast(e.message,false); }
  };
  $("btnLogCleanup").onclick = async () => {
    try {
      const res = await api("/api/logs/cleanup", { method:"POST", body: "{}" });
      await afterMutation(`日志清理 ${res.deleted_count || 0} 个`);
    } catch(e){ toast(e.message,false); }
  };
  $("btnProxyStart").onclick = async () => { try { await api("/api/goproxy/start",{method:"POST",body:"{}"}); await afterMutation("已请求启动"); } catch(e){ toast(e.message,false);} };
  $("btnProxyStop").onclick = async () => { try { await api("/api/goproxy/stop",{method:"POST",body:"{}"}); await afterMutation("已请求停止"); } catch(e){ toast(e.message,false);} };
  $("btnProxyRestart").onclick = async () => { try { await api("/api/goproxy/restart",{method:"POST",body:"{}"}); await afterMutation("已请求重启"); } catch(e){ toast(e.message,false);} };
  $("btnApplyProxy").onclick = async () => {
    try {
      await api("/api/goproxy/mode", { method:"POST", body: JSON.stringify({ mode: $("poolMode").value, restart: true }) });
      await api("/api/goproxy/endpoint", { method:"POST", body: JSON.stringify({ endpoint: $("endpoint").value, bind: $("bindProxy").checked }) });
      await afterMutation("代理设置已应用");
    } catch(e){ toast(e.message,false); }
  };

  $("checkAllVisible").onchange = (e) => {
    document.querySelectorAll(".cred-check").forEach(ch => {
      ch.checked = e.target.checked;
      if (ch.checked) state.selected.add(ch.dataset.name);
      else state.selected.delete(ch.dataset.name);
    });
  };
  $("btnSelectAll").onclick = () => {
    document.querySelectorAll(".cred-check").forEach(ch => {
      const on = ch.dataset.bucket === "uploaded";
      ch.checked = on;
      if (on) state.selected.add(ch.dataset.name);
      else state.selected.delete(ch.dataset.name);
    });
  };
  $("btnDownload").onclick = async () => {
    try {
      const names = selectedNames(false);
      if (!names.length) return toast("未选择凭证", false);
      const res = await api("/api/credentials/download", { method:"POST", body: JSON.stringify({ names }) });
      downloadBase64Zip(res.filename, res.base64);
      toast(`已下载 ${res.count} 个`);
    } catch(e){ toast(e.message,false); }
  };
  $("btnDownloadAllUploaded").onclick = async () => {
    try {
      const res = await api("/api/credentials/download", { method:"POST", body: JSON.stringify({ all: true, buckets: ["uploaded"] }) });
      downloadBase64Zip(res.filename, res.base64);
      toast(`已下载已推送 ${res.count} 个`);
    } catch(e){ toast(e.message,false); }
  };
  $("btnDeleteSelected").onclick = async () => {
    try {
      const names = selectedNames(true);
      if (!names.length) return toast("请选择 uploaded 凭证", false);
      if (!confirm(`确认删除 ${names.length} 个已推送凭证？`)) return;
      const res = await api("/api/credentials/delete", { method:"POST", body: JSON.stringify({ names, bucket: "uploaded" }) });
      names.forEach(n => state.selected.delete(n));
      await afterMutation(`已删除 ${res.deleted_count || 0}`);
    } catch(e){ toast(e.message,false); }
  };
  $("btnDeleteAllUploaded").onclick = async () => {
    try {
      if (!confirm("确认一键删除全部已推送凭证？此操作不可恢复。")) return;
      const res = await api("/api/credentials/delete", { method:"POST", body: JSON.stringify({ all: true }) });
      state.selected.clear();
      await afterMutation(`已删除已推送 ${res.deleted_count || 0}`);
    } catch(e){ toast(e.message,false); }
  };

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh(true).catch(()=>{});
  });

  await refresh(true);
  startSSE();
  startPollFallback();
}

main().catch(err => toast(err.message || String(err), false));
