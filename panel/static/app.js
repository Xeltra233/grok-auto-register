const $ = (id) => document.getElementById(id);

const MODE_LABELS = {
  mixed_equal: "混合均衡（订阅+免费，不偏科）",
  mixed_custom_first: "混合优先订阅源",
  mixed_free_first: "混合优先免费源",
  custom_only: "仅订阅源",
  free_only: "仅免费源",
};
const ENDPOINT_LABELS = {
  http_random: "HTTP 随机端口",
  http_stable: "HTTP 低延迟端口",
  socks5_random: "SOCKS5 随机端口",
  socks5_stable: "SOCKS5 低延迟端口",
};
const BUCKET_LABELS = {
  pending: "待推送（本机）",
  uploaded: "已推送（本机文件）",
  quarantine: "隔离",
  root: "根目录",
};
const SOURCE_LABELS = {
  remote: "远端 CPA",
  local: "本地凭证",
  remote_then_local: "远端优先/本地回退",
};

const state = {
  overview: null,
  creds: [],
  selected: new Set(),
  live: false,
  lastSync: 0,
  pollTimer: null,
  es: null,
  activeTab: localStorage.getItem("grok_panel_tab") || "overview",
  formsHydrated: false,
  formsDirty: false,
};

const toastState = { seq: 0, items: [] };

function ensureToastStack() {
  let stack = $("toastStack");
  if (!stack) {
    stack = document.createElement("div");
    stack.id = "toastStack";
    stack.className = "toast-stack";
    stack.setAttribute("aria-live", "polite");
    document.body.appendChild(stack);
  }
  return stack;
}

function dismissToast(id) {
  const stack = ensureToastStack();
  const el = stack.querySelector(`[data-toast-id="${id}"]`);
  if (!el) return;
  el.classList.add("hide");
  setTimeout(() => {
    el.remove();
    toastState.items = toastState.items.filter((x) => x !== id);
  }, 160);
}

function toast(msg, ok = true, opts = {}) {
  const stack = ensureToastStack();
  const id = ++toastState.seq;
  const type = opts.type || (ok === false ? "err" : (ok === "info" ? "info" : "ok"));
  const title = opts.title || (type === "err" ? "失败" : type === "info" ? "提示" : "成功");
  const ttl = Number(opts.ttl || (type === "err" ? 4200 : 2800));
  const icon = type === "err" ? "!" : type === "info" ? "i" : "✓";

  const el = document.createElement("div");
  el.className = `toast-item ${type}`;
  el.dataset.toastId = String(id);
  el.innerHTML = `
    <div class="toast-icon">${icon}</div>
    <div class="toast-body">
      <div class="toast-title"></div>
      <div class="toast-desc"></div>
    </div>
    <button class="toast-close" type="button" aria-label="关闭">×</button>
  `;
  el.querySelector(".toast-title").textContent = title;
  const desc = el.querySelector(".toast-desc");
  const text = String(msg || "");
  if (text && text !== title) {
    desc.textContent = text;
  } else {
    desc.remove();
  }
  el.querySelector(".toast-close").onclick = () => dismissToast(id);
  stack.appendChild(el);
  toastState.items.push(id);
  // keep at most 4 toasts
  while (toastState.items.length > 4) {
    dismissToast(toastState.items[0]);
  }
  if (ttl > 0) {
    setTimeout(() => dismissToast(id), ttl);
  }
  return id;
}

async function withBusy(btn, fn, busyText) {
  if (!btn) return fn();
  if (btn.dataset.busy === "1") return;
  const old = btn.textContent;
  btn.dataset.busy = "1";
  btn.classList.add("is-loading");
  btn.disabled = true;
  if (busyText) btn.textContent = busyText;
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.classList.remove("is-loading");
    btn.dataset.busy = "0";
    btn.textContent = old;
  }
}
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const res = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...opts,
    headers,
  });
  const ct = res.headers.get("content-type") || "";
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("unauthorized");
  }
  if (ct.includes("application/json")) {
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
    return data;
  }
  if (!res.ok) throw new Error(res.statusText);
  return res;
}
function fmtSize(n) {
  n = Number(n || 0);
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(2) + " MB";
}
function fmtTime(ts) {
  if (!ts) return "-";
  return new Date(Number(ts) * 1000).toLocaleString();
}
function setLive(on, text) {
  state.live = !!on;
  const el = $("liveBadge");
  el.classList.toggle("on", !!on);
  el.classList.toggle("off", !on);
  el.textContent = text || (on ? "LIVE · 实时同步中" : "LIVE · 已断开/轮询");
}
function setLastSync(ts) {
  state.lastSync = ts || Date.now() / 1000;
  $("lastSync").textContent = "上次同步: " + new Date(state.lastSync * 1000).toLocaleTimeString();
}
function switchTab(tab, opts = {}) {
  const next = tab || "overview";
  const animate = opts.animate !== false;
  const prev = state.activeTab;
  if (next === prev && !opts.force) {
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === next);
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      const on = panel.id === `tab-${next}`;
      panel.classList.toggle("active", on);
      panel.classList.remove("leaving");
    });
    return;
  }

  state.activeTab = next;
  localStorage.setItem("grok_panel_tab", state.activeTab);
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === state.activeTab);
  });

  const panels = Array.from(document.querySelectorAll(".tab-panel"));
  const current = panels.find((p) => p.id === `tab-${prev}`);
  const target = panels.find((p) => p.id === `tab-${next}`);

  panels.forEach((panel) => {
    if (panel._tabTimer) {
      clearTimeout(panel._tabTimer);
      panel._tabTimer = null;
    }
  });

  if (!animate || !current || !target || prev === next) {
    panels.forEach((panel) => {
      panel.classList.remove("leaving");
      panel.classList.toggle("active", panel.id === `tab-${next}`);
    });
    return;
  }

  current.classList.remove("active");
  current.classList.add("leaving");
  target.classList.remove("leaving");
  current._tabTimer = setTimeout(() => {
    current.classList.remove("leaving");
    panels.forEach((panel) => {
      panel.classList.toggle("active", panel.id === `tab-${next}`);
      if (panel.id !== `tab-${next}`) panel.classList.remove("leaving");
    });
  }, 130);
}
function fillSelect(sel, items, current, labelOf) {
  const cur = current || sel.value;
  if (document.activeElement === sel) return;
  sel.innerHTML = "";
  (items || []).forEach((item) => {
    const id = typeof item === "string" ? item : item.id;
    const label = typeof item === "string" ? (labelOf ? labelOf(item) : item) : (item.label || labelOf?.(item.id) || item.id);
    const opt = document.createElement("option");
    opt.value = id; opt.textContent = label;
    if (id === cur) opt.selected = true;
    sel.appendChild(opt);
  });
}
function setChecked(id, value) {
  const el = $(id); if (!el || document.activeElement === el) return; el.checked = !!value;
}
function setValue(id, value) {
  const el = $(id); if (!el || document.activeElement === el) return; el.value = value == null ? "" : String(value);
}
function renderPorts(g) {
  const ports = (g && g.ports) || {};
  const st = (g && g.port_status) || {};
  const map = [
    ["http_random", "HTTP 随机"],
    ["http_stable", "HTTP 低延迟"],
    ["socks5_random", "SOCKS 随机"],
    ["socks5_stable", "SOCKS 低延迟"],
    ["webui", "管理页"],
  ];
  $("portChips").innerHTML = map.map(([k, label]) => {
    const on = !!st[k];
    return `<span class="chip ${on ? "on" : "off"}">${label}: ${ports[k] ?? "-"} ${on ? "●" : "○"}</span>`;
  }).join("");
}
function renderCreds(items) {
  const prev = new Set(state.selected);
  state.creds = items || [];
  const body = $("credBody");
  const empty = $("credEmpty");
  body.innerHTML = "";
  if (!state.creds.length) empty.classList.remove("hidden"); else empty.classList.add("hidden");
  state.creds.forEach((it) => {
    const checked = prev.has(it.name);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" data-name="${it.name}" data-bucket="${it.bucket}" class="cred-check" ${checked ? "checked" : ""} /></td>
      <td>${it.name}</td>
      <td>${BUCKET_LABELS[it.bucket] || it.bucket}</td>
      <td>${fmtSize(it.size)}</td>
      <td>${fmtTime(it.mtime)}</td>`;
    body.appendChild(tr);
  });
  body.querySelectorAll(".cred-check").forEach((ch) => {
    ch.onchange = () => {
      if (ch.checked) state.selected.add(ch.dataset.name);
      else state.selected.delete(ch.dataset.name);
    };
  });
}
function selectedNames(uploadedOnly = false) {
  const names = [];
  document.querySelectorAll(".cred-check:checked").forEach((ch) => {
    if (uploadedOnly && ch.dataset.bucket !== "uploaded") return;
    names.push(ch.dataset.name);
  });
  return names;
}
function markFormsDirty() {
  state.formsDirty = true;
}

function wireConfigFormDirtyTracking() {
  const ids = [
    "cfgEmailProvider", "cfgConcurrent", "cfgBrowserRestart", "cfgLogLevel", "cfgEnableNsfw",
    "cfgDuckmailKey", "cfgFreemailBase", "cfgFreemailJwt", "cfgFreemailDomain",
    "cfgIcloudBase", "cfgIcloudAccount", "cfgIcloudLabel",
    "cfgCfBase", "cfgCfKey", "cfgCfMode",
    "cfgLiveInspect", "cfgSuccessRequireLive", "cfgCpaExport", "cfgCpaMintAsync", "cfgCpaProxy",
    "cfgCpaRemoteEnabled", "cfgCpaRemoteBase", "cfgCpaRemoteKey",
    "cfgPoolEnabled", "cfgPoolSource", "cfgPoolMin", "cfgPoolTarget", "cfgPoolInterval",
    "cfgProxyMode", "cfgProxy",
    "cfgRemoteLiveEnabled", "cfgRemoteLiveDelete", "cfgRemoteLiveInterval", "cfgRemoteLiveBatch",
    "cfgRemoteLiveModel", "cfgRemoteLiveMax",
    "cfgLogCleanup", "cfgLogDays", "cfgLogMaxMb",
    "cfgLocalCredRetainEnabled", "cfgLocalCredRetainCount", "cfgLocalCredRetainInterval",
    "poolMode", "endpoint", "bindProxy",
  ];
  ids.forEach((id) => {
    const el = $(id);
    if (!el || el.dataset.dirtyWired === "1") return;
    el.dataset.dirtyWired = "1";
    const evt = (el.tagName === "SELECT" || el.type === "checkbox" || el.type === "radio") ? "change" : "input";
    el.addEventListener(evt, markFormsDirty);
  });
}

function applyConfigForms(cfg = {}, opts = {}) {
  const force = !!(opts && opts.force);
  // SSE/poll must not clobber in-progress edits (e.g. email provider dropdown).
  if (state.formsDirty && !force) return;
  if (state.formsHydrated && !force) return;
  setValue("cfgEmailProvider", cfg.email_provider || "duckmail");
  setValue("cfgConcurrent", cfg.concurrent_count ?? 1);
  setValue("cfgBrowserRestart", cfg.browser_restart_every ?? 10);
  setValue("cfgLogLevel", cfg.log_level || "info");
  setChecked("cfgEnableNsfw", cfg.enable_nsfw);
  setValue("cfgDuckmailKey", cfg.duckmail_api_key || "");
  setValue("cfgFreemailBase", cfg.freemail_api_base || "");
  setValue("cfgFreemailJwt", cfg.freemail_jwt_token || "");
  setValue("cfgFreemailDomain", cfg.freemail_domain || "");
  setValue("cfgIcloudBase", cfg.icloud_hme_api_base || "");
  setValue("cfgIcloudAccount", cfg.icloud_hme_account_id || "");
  setValue("cfgIcloudLabel", cfg.icloud_hme_label || "");
  setValue("cfgCfBase", cfg.cloudflare_api_base || "");
  setValue("cfgCfKey", cfg.cloudflare_api_key || "");
  setValue("cfgCfMode", cfg.cloudflare_auth_mode || "");
  setChecked("cfgLiveInspect", cfg.live_inspect_enabled !== false);
  setChecked("cfgSuccessRequireLive", cfg.success_require_live !== false);
  setChecked("cfgCpaExport", cfg.cpa_export_enabled !== false);
  setChecked("cfgCpaMintAsync", cfg.cpa_mint_async !== false);
  setValue("cfgCpaProxy", cfg.cpa_proxy || "");
  setChecked("cfgCpaRemoteEnabled", cfg.cpa_remote_enabled);
  setValue("cfgCpaRemoteBase", cfg.cpa_remote_base || "");
  setValue("cfgCpaRemoteKey", cfg.cpa_remote_management_key || "");
  setChecked("cfgPoolEnabled", cfg.pool_autoreg_enabled);
  setValue("cfgPoolSource", cfg.pool_autoreg_source || "remote");
  setValue("cfgPoolMin", cfg.pool_autoreg_min_count ?? 5);
  setValue("cfgPoolTarget", cfg.pool_autoreg_target_count ?? cfg.pool_autoreg_min_count ?? 5);
  setValue("cfgPoolInterval", cfg.pool_autoreg_interval_sec ?? 300);
  setValue("cfgProxyMode", cfg.proxy_mode || (cfg.goproxy_bind_register_proxy ? "goproxy" : "custom"));
  setValue("cfgProxy", cfg.proxy || "");
  setChecked("cfgRemoteLiveEnabled", cfg.remote_live_enabled);
  setChecked("cfgRemoteLiveDelete", cfg.remote_live_delete_on_fail !== false);
  setValue("cfgRemoteLiveInterval", cfg.remote_live_interval_sec ?? 7200);
  setValue("cfgRemoteLiveBatch", cfg.remote_live_batch ?? 6);
  setValue("cfgRemoteLiveModel", cfg.remote_live_model || "grok-4.5");
  setValue("cfgRemoteLiveMax", cfg.remote_live_max_files ?? 0);
  setChecked("cfgLogCleanup", cfg.log_cleanup_enabled !== false);
  setValue("cfgLogDays", cfg.log_retain_days ?? 7);
  setValue("cfgLogMaxMb", cfg.log_max_total_mb ?? 512);
  setChecked("cfgLocalCredRetainEnabled", cfg.local_cred_retain_enabled);
  setValue("cfgLocalCredRetainCount", cfg.local_cred_retain_count ?? 100);
  setValue("cfgLocalCredRetainInterval", cfg.local_cred_retain_interval_sec ?? 600);
  state.formsHydrated = true;
  if (force) state.formsDirty = false;
}
function collectRegisterConfig() {
  return {
    email_provider: $("cfgEmailProvider").value,
    concurrent_count: Number($("cfgConcurrent").value || 1),
    browser_restart_every: Number($("cfgBrowserRestart").value || 10),
    log_level: $("cfgLogLevel").value,
    enable_nsfw: $("cfgEnableNsfw").checked,
    duckmail_api_key: $("cfgDuckmailKey").value.trim(),
    freemail_api_base: $("cfgFreemailBase").value.trim(),
    freemail_jwt_token: $("cfgFreemailJwt").value.trim(),
    freemail_domain: $("cfgFreemailDomain").value.trim(),
    icloud_hme_api_base: $("cfgIcloudBase").value.trim(),
    icloud_hme_account_id: $("cfgIcloudAccount").value.trim(),
    icloud_hme_label: $("cfgIcloudLabel").value.trim(),
    cloudflare_api_base: $("cfgCfBase").value.trim(),
    cloudflare_api_key: $("cfgCfKey").value.trim(),
    cloudflare_auth_mode: $("cfgCfMode").value.trim(),
    live_inspect_enabled: $("cfgLiveInspect").checked,
    success_require_live: $("cfgSuccessRequireLive").checked,
    cpa_export_enabled: $("cfgCpaExport").checked,
    cpa_mint_async: $("cfgCpaMintAsync").checked,
    cpa_proxy: $("cfgCpaProxy").value.trim(),
    cpa_remote_enabled: $("cfgCpaRemoteEnabled").checked,
    cpa_remote_base: $("cfgCpaRemoteBase").value.trim(),
    cpa_remote_management_key: $("cfgCpaRemoteKey").value.trim(),
    pool_autoreg_enabled: $("cfgPoolEnabled").checked,
    pool_autoreg_source: $("cfgPoolSource").value,
    pool_autoreg_min_count: Number($("cfgPoolMin").value || 0),
    pool_autoreg_target_count: Number(($("cfgPoolTarget") && $("cfgPoolTarget").value) || ($("cfgPoolMin").value || 0)),
    pool_autoreg_batch: 0,
    pool_autoreg_interval_sec: Number($("cfgPoolInterval").value || 300),
    proxy_mode: $("cfgProxyMode") ? $("cfgProxyMode").value : "custom",
    proxy: $("cfgProxy").value.trim(),
    goproxy_bind_register_proxy: ($("cfgProxyMode") ? $("cfgProxyMode").value : "custom") === "goproxy",
    goproxy_bind_cpa_proxy: ($("cfgProxyMode") ? $("cfgProxyMode").value : "custom") === "goproxy",
    remote_live_enabled: $("cfgRemoteLiveEnabled").checked,
    remote_live_delete_on_fail: $("cfgRemoteLiveDelete").checked,
    remote_live_interval_sec: Number($("cfgRemoteLiveInterval").value || 7200),
    remote_live_batch: Number(($("cfgRemoteLiveBatch") && $("cfgRemoteLiveBatch").value) || 6),
    remote_live_model: $("cfgRemoteLiveModel").value.trim() || "grok-4.5",
    remote_live_max_files: Number($("cfgRemoteLiveMax").value || 0),
    remote_live_proxy: "",
  };
}
function collectSystemConfig() {
  return {
    log_cleanup_enabled: $("cfgLogCleanup").checked,
    log_retain_days: Number($("cfgLogDays").value || 7),
    log_max_total_mb: Number($("cfgLogMaxMb").value || 512),
    local_cred_retain_enabled: $("cfgLocalCredRetainEnabled") ? $("cfgLocalCredRetainEnabled").checked : false,
    local_cred_retain_count: Number(($("cfgLocalCredRetainCount") && $("cfgLocalCredRetainCount").value) || 100),
    local_cred_retain_interval_sec: Number(($("cfgLocalCredRetainInterval") && $("cfgLocalCredRetainInterval").value) || 600),
  };
}
function applyOverview(data, opts = {}) {
  state.overview = data;
  setLastSync(data.ts || Date.now() / 1000);
  $("mProxy").textContent = data.goproxy?.running ? "运行中" : "未运行";
  $("mPool").textContent = data.pool?.total ?? "-";
  $("pillLive").textContent = `测活门槛: ${data.config?.live_inspect_enabled === false ? "关闭" : "开启"}`;
  $("pillBind").textContent = `全局代理: ${(data.config?.proxy_mode || (data.config?.goproxy_bind_register_proxy ? "goproxy" : "custom")) === "goproxy" ? "本机 GoProxy" : "自有代理"}`;
  const used = data.pool?.used_source || data.config?.pool_autoreg_source || "remote";
  $("pillPool").textContent = `账号池: ${data.pool?.total ?? 0} / 触发 ${data.config?.pool_autoreg_min_count ?? 5} / 目标 ${data.config?.pool_autoreg_target_count ?? data.config?.pool_autoreg_min_count ?? 5} / 自动补货${data.config?.pool_autoreg_enabled ? "开" : "关"}`;
  $("pillSource").textContent = `统计来源: ${SOURCE_LABELS[used] || used}${data.pool?.fallback ? "（已回退）" : ""}`;
  $("overviewExtra").textContent = JSON.stringify({
    说明: "凭证库是本机产出；远端只负责推送。绑定=注册代理改走本机 GoProxy 端口。",
    本地代理: data.proxy,
    本机凭证: data.credentials?.counts,
    账号池: data.pool,
    自动补货状态: data.pool_autoreg,
    远端测活: data.remote_live,
    日志清理: data.log_cleanup,
    ts: data.ts,
  }, null, 2);
  const forceSelects = !!(opts && opts.forceForms) || !state.formsDirty;
  if (forceSelects && document.activeElement !== $("poolMode")) {
    fillSelect($("poolMode"), data.modes || Object.entries(MODE_LABELS).map(([id, label]) => ({ id, label })), data.config?.goproxy_pool_mode, (id) => MODE_LABELS[id] || id);
  }
  if (forceSelects && document.activeElement !== $("endpoint")) {
    fillSelect($("endpoint"), data.endpoints || Object.entries(ENDPOINT_LABELS).map(([id, label]) => ({ id, label })), data.config?.goproxy_endpoint, (id) => ENDPOINT_LABELS[id] || id);
  }
  if ($("bindProxy") && document.activeElement !== $("bindProxy")) $("bindProxy").checked = !!data.config?.goproxy_bind_register_proxy;
  if ($("proxyModeHint")) {
    const mode = data.config?.proxy_mode || (data.config?.goproxy_bind_register_proxy ? "goproxy" : "custom");
    $("proxyModeHint").textContent = mode === "goproxy" ? "当前全局代理：本机 GoProxy（注册/CPA 都走本地端口）" : "当前全局代理：自有代理（注册/CPA 都走 proxy 配置）";
  }
  renderPorts(data.goproxy);
  $("proxyStatus").textContent = JSON.stringify({
    运行中: data.goproxy?.running,
    本进程托管: data.goproxy?.managed,
    pid: data.goproxy?.pid,
    二进制: data.goproxy?.binary,
    最近错误: data.goproxy?.last_error,
    端口状态: data.goproxy?.port_status,
    管理页: `http://127.0.0.1:${data.goproxy?.ports?.webui || data.proxy?.ports?.webui || 17878}/`,
    默认密码: "goproxy",
  }, null, 2);
  applyConfigForms(data.config || {}, { force: !!(opts && opts.forceForms) });
  $("registerStatus").textContent = JSON.stringify({
    账号池: data.pool,
    自动补货: data.pool_autoreg,
    注册数量: data.config?.register_count,
    并发: data.config?.concurrent_count,
    邮箱: data.config?.email_provider,
    远端测活: data.remote_live,
  }, null, 2);
  $("systemStatus").textContent = JSON.stringify({
    日志清理: data.config?.log_cleanup_enabled,
    本地凭证保留: {
      启用: data.config?.local_cred_retain_enabled,
      保留数: data.config?.local_cred_retain_count,
      状态: data.local_cred_retain,
    },
    最近日志清理: data.log_cleanup,
    日志循环: data.log_cleanup_loop,
  }, null, 2);
}
async function refresh(full = true, opts = {}) {
  const data = await api("/api/overview");
  applyOverview(data, opts);
  if (full) {
    const creds = await api("/api/credentials?buckets=uploaded,pending");
    renderCreds(creds.items || []);
  }
}
function downloadBase64Zip(filename, b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], { type: "application/zip" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename || "local-credentials.zip";
  a.click();
  URL.revokeObjectURL(a.href);
}
function startSSE() {
  if (state.es) { try { state.es.close(); } catch {} }
  // cookie auth is sent automatically by browser for same-origin EventSource
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
    } catch (e) {}
  });
  es.onerror = () => setLive(false, "LIVE · SSE 断开，回退轮询");
  es.onopen = () => setLive(true, "LIVE · SSE 已连接");
}
function startPollFallback() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(() => {
    refresh(true).catch(() => setLive(false, "LIVE · 同步失败"));
  }, 3000);
}
async function afterMutation(msg) { toast(msg, true, { title: "完成" }); await refresh(true); }
async function ensureAuth() {
  try {
    const st = await api("/api/auth/status");
    if ((st.auth_required || st.token_required) && !st.authenticated) {
      location.href = "/login";
      throw new Error("need login");
    }
  } catch (e) {
    if (String(e.message || "").includes("need login") || e.message === "unauthorized") throw e;
  }
}
async function main() {
  await ensureAuth();
  wireConfigFormDirtyTracking();
  switchTab(state.activeTab, { animate: false, force: true });
  document.querySelectorAll(".nav-btn").forEach((btn) => { btn.onclick = () => switchTab(btn.dataset.tab, { animate: true }); });
  $("btnRefresh").onclick = async () => withBusy($("btnRefresh"), async () => { try { state.formsDirty = false; await refresh(true, { forceForms: true }); toast("已同步后端", true, { title: "同步完成" }); } catch (e) { toast(e.message, false); } }, "同步中...");
  $("btnLogout").onclick = async () => {
    try { await api("/api/auth/logout", { method: "POST", body: "{}" }); } catch (e) {}
    location.href = "/login";
  };
  $("btnSaveRegister").onclick = async () => withBusy($("btnSaveRegister"), async () => {
    try {
      await api("/api/config", { method: "POST", body: JSON.stringify(collectRegisterConfig()) });
      state.formsDirty = false;
      await refresh(true, { forceForms: true });
      toast("设置已保存（未启动任务）", true, { title: "已保存" });
    } catch (e) { toast(e.message, false); }
  }, "保存中...");
  if ($("btnStartManualRegister")) $("btnStartManualRegister").onclick = async () => withBusy($("btnStartManualRegister"), async () => {
    try {
      await api("/api/config", { method: "POST", body: JSON.stringify(collectRegisterConfig()) });
      const conc = Number($("cfgConcurrent").value || 1);
      const res = await api("/api/register/start", { method: "POST", body: JSON.stringify({ count: conc }) });
      $("registerStatus").textContent = JSON.stringify(res, null, 2);
      if (res.triggered) toast(`目标 ${res.register_count} 个，并发 ${res.concurrent_count || conc}`, true, { title: "手动注册已启动" });
      else toast(res.reason || "未启动", false, { title: "手动注册未启动" });
      await refresh(false);
    } catch (e) { toast(e.message, false); }
  }, "启动中...");
  if ($("btnPoolRefill")) $("btnPoolRefill").onclick = async () => withBusy($("btnPoolRefill"), async () => {
    try {
      await api("/api/config", { method: "POST", body: JSON.stringify(collectRegisterConfig()) });
      const res = await api("/api/pool/refill", { method: "POST", body: JSON.stringify({ force: true }) });
      $("registerStatus").textContent = JSON.stringify(res, null, 2);
      if (res.triggered) toast(`当前 ${res.need?.current_total ?? "-"}，补 ${res.register_count || 0} 到目标 ${res.need?.target_count ?? res.need?.min_count ?? "-"}（线程 ${Number($("cfgConcurrent").value || 1)}）`, true, { title: "账号池补货已启动" });
      else toast(res.reason || "无需补货", "info", { title: "未启动补货" });
      await refresh(false);
    } catch (e) { toast(e.message, false); }
  }, "补货中...");
  $("btnSaveSystem").onclick = async () => withBusy($("btnSaveSystem"), async () => {
    try {
      await api("/api/config", { method: "POST", body: JSON.stringify(collectSystemConfig()) });
      state.formsDirty = false;
      await refresh(true, { forceForms: true });
      toast("系统设置已保存", true, { title: "已保存" });
    } catch (e) { toast(e.message, false); }
  }, "保存中...");
  $("btnRemoteLiveCheck").onclick = async () => withBusy($("btnRemoteLiveCheck"), async () => {
    try {
      const res = await api("/api/remote-live/check", { method: "POST", body: JSON.stringify({ force: true, trigger_autoreg: true }) });
      $("registerStatus").textContent = JSON.stringify(res, null, 2);
      if (res.ok === false) toast(res.error || "远端测活失败", false, { title: "远端测活失败" });
      else toast(`检查 ${res.checked || 0}，删除 ${res.deleted_count || 0}`, true, { title: "远端测活完成" });
      await refresh(false);
    } catch (e) { toast(e.message, false); }
  }, "测活中...");
  $("btnPoolCheck").onclick = async () => withBusy($("btnPoolCheck"), async () => {
    try {
      const res = await api("/api/pool/check", { method: "POST", body: JSON.stringify({ force: false }) });
      $("registerStatus").textContent = JSON.stringify(res, null, 2);
      toast(`当前 ${res.counts?.total ?? "-"} / 触发 ${res.need?.min_count ?? "-"} / 目标 ${res.need?.target_count ?? res.need?.min_count ?? "-"}（${res.reason || "ok"}）`, "info", { title: "账号池检查完成" });
      await refresh(false);
    } catch (e) { toast(e.message, false); }
  }, "检查中...");
  const pruneLocalCreds = async (btn) => withBusy(btn, async () => {
    try {
      if ($("cfgLocalCredRetainCount")) {
        await api("/api/config", { method: "POST", body: JSON.stringify(collectSystemConfig()) });
      }
      const keep = Number(($("cfgLocalCredRetainCount") && $("cfgLocalCredRetainCount").value) || 100);
      const res = await api("/api/credentials/prune", { method: "POST", body: JSON.stringify({ force: true, keep }) });
      if ($("systemStatus")) $("systemStatus").textContent = JSON.stringify(res, null, 2);
      toast(res.skipped ? (res.reason || "已跳过") : `前 ${res.before} → 后 ${res.after}，删除 ${res.deleted_count || 0}`, true, { title: "本地凭证清理完成" });
      await refresh(true);
    } catch (e) { toast(e.message, false); }
  }, "清理中...");
  if ($("btnLocalCredPrune")) $("btnLocalCredPrune").onclick = () => pruneLocalCreds($("btnLocalCredPrune"));
  if ($("btnLocalCredPrune2")) $("btnLocalCredPrune2").onclick = () => pruneLocalCreds($("btnLocalCredPrune2"));
  $("btnLogCleanup").onclick = async () => {
    try {
      const res = await api("/api/logs/cleanup", { method: "POST", body: "{}" });
      await afterMutation(`日志清理 ${res.deleted_count || 0} 个`);
    } catch (e) { toast(e.message, false); }
  };
  $("btnProxyStart").onclick = async () => withBusy($("btnProxyStart"), async () => { try { await api("/api/goproxy/start", { method: "POST", body: "{}" }); await afterMutation("本地代理启动请求已发送"); } catch (e) { toast(e.message, false); } }, "启动中...");
  $("btnProxyStop").onclick = async () => withBusy($("btnProxyStop"), async () => { try { await api("/api/goproxy/stop", { method: "POST", body: "{}" }); await afterMutation("本地代理停止请求已发送"); } catch (e) { toast(e.message, false); } }, "停止中...");
  $("btnProxyRestart").onclick = async () => withBusy($("btnProxyRestart"), async () => { try { await api("/api/goproxy/restart", { method: "POST", body: "{}" }); await afterMutation("本地代理重启请求已发送"); } catch (e) { toast(e.message, false); } }, "重启中...");
  $("btnOpenWebui").onclick = () => {
    const ports = state.overview?.goproxy?.ports || state.overview?.proxy?.ports || {};
    const port = ports.webui || 17878;
    window.open(`http://127.0.0.1:${port}/`, "_blank", "noopener,noreferrer");
  };
  $("btnApplyProxy").onclick = async () => {
    try {
      await api("/api/goproxy/mode", { method: "POST", body: JSON.stringify({ mode: $("poolMode").value, restart: true }) });
      await api("/api/goproxy/endpoint", { method: "POST", body: JSON.stringify({ endpoint: $("endpoint").value, bind: $("bindProxy").checked }) });
      await afterMutation((($("cfgProxyMode") ? $("cfgProxyMode").value === "goproxy" : false) ? "已应用：全局走本机 GoProxy 端口" : "已应用模式/端口（当前为自有代理模式）"));
    } catch (e) { toast(e.message, false); }
  };
  $("checkAllVisible").onchange = (e) => {
    document.querySelectorAll(".cred-check").forEach((ch) => {
      ch.checked = e.target.checked;
      if (ch.checked) state.selected.add(ch.dataset.name); else state.selected.delete(ch.dataset.name);
    });
  };
  $("btnSelectAll").onclick = () => {
    document.querySelectorAll(".cred-check").forEach((ch) => {
      const on = ch.dataset.bucket === "uploaded";
      ch.checked = on;
      if (on) state.selected.add(ch.dataset.name); else state.selected.delete(ch.dataset.name);
    });
  };
  $("btnDownload").onclick = async () => {
    try {
      const names = selectedNames(false);
      if (!names.length) return toast("未选择本机凭证", false);
      const res = await api("/api/credentials/download", { method: "POST", body: JSON.stringify({ names }) });
      downloadBase64Zip(res.filename, res.base64);
      toast(`共 ${res.count} 个文件`, true, { title: "下载完成" });
    } catch (e) { toast(e.message, false); }
  };
  $("btnDownloadAllUploaded").onclick = async () => {
    try {
      const res = await api("/api/credentials/download", { method: "POST", body: JSON.stringify({ all: true, buckets: ["uploaded"] }) });
      downloadBase64Zip(res.filename, res.base64);
      toast(`共 ${res.count} 个已推送文件`, true, { title: "下载完成" });
    } catch (e) { toast(e.message, false); }
  };
  $("btnDeleteSelected").onclick = async () => {
    try {
      const names = selectedNames(true);
      if (!names.length) return toast("请选择本机“已推送”凭证", false);
      if (!confirm(`确认删除本机 ${names.length} 个已推送凭证文件？`)) return;
      const res = await api("/api/credentials/delete", { method: "POST", body: JSON.stringify({ names, bucket: "uploaded" }) });
      names.forEach((n) => state.selected.delete(n));
      await afterMutation(`已删除本机凭证 ${res.deleted_count || 0}`);
    } catch (e) { toast(e.message, false); }
  };
  $("btnDeleteAllUploaded").onclick = async () => {
    try {
      if (!confirm("确认一键删除本机全部已推送凭证文件？此操作不可恢复。")) return;
      const res = await api("/api/credentials/delete", { method: "POST", body: JSON.stringify({ all: true }) });
      state.selected.clear();
      await afterMutation(`已删除本机已推送 ${res.deleted_count || 0}`);
    } catch (e) { toast(e.message, false); }
  };
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true).catch(() => {}); });
  await refresh(true);
  startSSE();
  startPollFallback();
}
main().catch((err) => {
  if (String(err?.message || "").includes("need login") || err?.message === "unauthorized") return;
  toast(err.message || String(err), false);
});
