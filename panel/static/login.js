function $(id) { return document.getElementById(id); }

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const res = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...opts,
    headers,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    const err = new Error(data.error || res.statusText || "request failed");
    err.status = res.status;
    throw err;
  }
  return data;
}

function showError(msg) {
  const el = $("loginError");
  if (!msg) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.classList.remove("hidden");
  el.textContent = msg;
}

async function main() {
  try {
    const st = await api("/api/auth/status");
    if (!st.auth_required && !st.token_required) {
      $("loginTip").textContent = "未设置环境变量密码，可直接进入面板。";
      // auto enter
      location.replace("/");
      return;
    }
    if (st.authenticated) {
      location.replace("/");
      return;
    }
    $("loginTip").textContent = "请输入面板密码（环境变量 GROK_PANEL_PASSWORD）。";
  } catch (e) {
    $("loginTip").textContent = "无法连接面板服务，请确认已启动。";
  }

  const doLogin = async () => {
    showError("");
    const password = $("passwordInput").value;
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      location.replace("/");
    } catch (e) {
      showError(e.message || "登录失败");
    }
  };

  $("btnLogin").onclick = doLogin;
  $("passwordInput").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") doLogin();
  });
}

main();
