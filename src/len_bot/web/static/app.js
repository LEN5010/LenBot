// State
let currentTab = "overview";
let pollTimer = null;

// Helper: Toast
function showToast(message, isError = false) {
  const toast = document.getElementById("app-toast");
  toast.innerText = message;
  toast.style.borderColor = isError ? "var(--accent-rose)" : "var(--border-color)";
  toast.style.color = isError ? "var(--accent-rose)" : "var(--text-primary)";
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 3200);
}

// Helper: Fetch with auth
async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  });
  if (res.status === 401) {
    document.getElementById("login-modal").classList.add("active");
    throw new Error("Unauthorized");
  }
  return res;
}

// Navigation
function switchTab(target) {
  currentTab = target;
  document.querySelectorAll(".nav-tab").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.target === target);
  });
  document.querySelectorAll(".page-view").forEach(view => {
    view.classList.toggle("active", view.id === `view-${target}`);
  });

  // Trigger loads based on active tab
  if (target === "overview") {
    loadOverviewStats();
    loadRecentEvents();
  } else if (target === "websocket") {
    loadWsStatus();
  } else if (target === "models") {
    loadModelConfig();
  } else if (target === "persona") {
    loadPersonaSettings();
    loadSocialSettings();
  } else if (target === "plugins") {
    loadPlugins();
  }
}

// Auth Handlers
async function checkAuth() {
  try {
    const res = await fetch("/api/auth/me");
    if (res.ok) {
      const data = await res.json();
      document.getElementById("current-username").innerText = data.username;
      document.getElementById("login-modal").classList.remove("active");
      
      // Default password warning
      if (data.is_default_password) {
        document.getElementById("default-pwd-banner").style.display = "flex";
      } else {
        document.getElementById("default-pwd-banner").style.display = "none";
      }
      
      startPolling();
      loadOverviewStats();
      loadRecentEvents();
    } else {
      document.getElementById("login-modal").classList.add("active");
    }
  } catch (e) {
    document.getElementById("login-modal").classList.add("active");
  }
}

async function handleLogin() {
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  const errorBox = document.getElementById("login-error");
  errorBox.style.display = "none";

  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      document.getElementById("login-modal").classList.remove("active");
      document.getElementById("current-username").innerText = data.username;
      if (data.is_default_password) {
        document.getElementById("default-pwd-banner").style.display = "flex";
      }
      showToast("登录成功！");
      startPolling();
      loadOverviewStats();
      loadRecentEvents();
    } else {
      errorBox.innerText = data.detail || "用户名或密码错误";
      errorBox.style.display = "block";
    }
  } catch (e) {
    errorBox.innerText = "网络请求失败，请稍后重试";
    errorBox.style.display = "block";
  }
}

async function handleLogout() {
  await apiFetch("/api/auth/logout", { method: "POST" });
  document.getElementById("login-modal").classList.add("active");
  showToast("已退出登录");
}

async function changePassword() {
  const current = document.getElementById("pwd-current").value;
  const newPwd = document.getElementById("pwd-new").value;
  const confirmPwd = document.getElementById("pwd-confirm").value;

  if (newPwd !== confirmPwd) {
    showToast("两次新密码输入不一致", true);
    return;
  }

  try {
    const res = await apiFetch("/api/auth/change_password", {
      method: "POST",
      body: JSON.stringify({ current_password: current, new_password: newPwd })
    });
    const data = await res.json();
    if (res.ok) {
      showToast("密码修改成功！");
      document.getElementById("pwd-current").value = "";
      document.getElementById("pwd-new").value = "";
      document.getElementById("pwd-confirm").value = "";
      document.getElementById("default-pwd-banner").style.display = "none";
    } else {
      showToast(data.detail || "密码修改失败", true);
    }
  } catch (e) {
    showToast("请求失败", true);
  }
}

// Overview Data
async function loadOverviewStats() {
  try {
    const res = await apiFetch("/api/overview/stats");
    const data = await res.json();
    const stats = data.stats;

    // Format Uptime
    const sec = Math.floor(stats.uptime_seconds);
    const hrs = Math.floor(sec / 3600);
    const mins = Math.floor((sec % 3600) / 60);
    const secs = sec % 60;
    document.getElementById("stat-uptime").innerText = 
      `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;

    document.getElementById("stat-bot-qq").innerText = stats.bot_qq;
    document.getElementById("stat-bot-name").innerText = stats.identity_name;

    // WebSocket badge
    const badge = document.getElementById("badge-ws-status");
    if (stats.websocket_connected) {
      badge.className = "badge badge-emerald";
      badge.innerText = "CONNECTED";
    } else {
      badge.className = "badge badge-amber";
      badge.innerText = "LISTENING (NO CLIENT)";
    }
    document.getElementById("stat-ws-port").innerText = `Port: ${window.location.port || 11307}`;

    // Models
    document.getElementById("stat-model-normal").innerText = stats.normal_model;
    document.getElementById("stat-model-deliberate").innerText = stats.deliberate_model;

    // Numerical Stats
    document.getElementById("stat-total-events").innerText = stats.total_events.toLocaleString();
    document.getElementById("stat-active-scenes").innerText = stats.active_scenes;
    document.getElementById("stat-open-loops").innerText = stats.active_open_loops;
    document.getElementById("stat-memories").innerText = stats.memory_beliefs_count;

    // Render Scene States
    const scenesContainer = document.getElementById("scenes-list-container");
    if (data.scenes && data.scenes.length > 0) {
      scenesContainer.innerHTML = data.scenes.map(s => `
        <div class="event-row">
          <div>
            <strong style="color:var(--text-primary);">${s.scene_id}</strong>
            <span style="font-size:0.75rem; color:var(--text-muted); margin-left:8px;">v${s.version}</span>
            <div style="font-size:0.78rem; color:var(--text-secondary); margin-top:2px;">
              活跃话题: <strong>${s.active_topic}</strong> | Bot参战: <span style="color:${s.bot_engagement==='active'?'var(--accent-cyan)':'var(--text-muted)'}">${s.bot_engagement}</span>
            </div>
          </div>
          <div>
            <span class="badge ${s.activity === 'hot' ? 'badge-rose' : (s.activity === 'active' ? 'badge-emerald' : 'badge-amber')}">${s.activity.toUpperCase()}</span>
          </div>
        </div>
      `).join("");
    } else {
      scenesContainer.innerHTML = `<div style="color:var(--text-muted); font-size:0.9rem; text-align:center; padding:20px;">暂无活跃场景</div>`;
    }

  } catch (e) {
    console.error("Error loading overview stats:", e);
  }
}

async function loadRecentEvents() {
  try {
    const res = await apiFetch("/api/overview/recent_events?limit=15");
    const events = await res.json();
    const container = document.getElementById("event-stream-container");
    if (events && events.length > 0) {
      container.innerHTML = events.map(e => {
        const text = e.payload?.raw_text || e.payload?.content || JSON.stringify(e.payload);
        const timeStr = new Date(e.timestamp * 1000).toLocaleTimeString();
        return `
          <div class="event-row">
            <div>
              <span class="event-type">${e.event_type}</span>
              <span style="font-size:0.75rem; color:var(--text-muted); margin-left:6px;">${e.scene_id} (${e.actor_id})</span>
              <div style="font-size:0.85rem; color:var(--text-primary); margin-top:3px; word-break:break-all;">
                ${escapeHtml(text.slice(0, 100))}
              </div>
            </div>
            <div style="font-size:0.75rem; color:var(--text-muted); white-space:nowrap; margin-left:12px;">
              ${timeStr}
            </div>
          </div>
        `;
      }).join("");
    } else {
      container.innerHTML = `<div style="color:var(--text-muted); font-size:0.9rem; text-align:center; padding:20px;">暂无历史事件</div>`;
    }
  } catch (e) {
    console.error("Error loading events:", e);
  }
}

// WebSocket Tab
async function loadWsStatus() {
  const res = await apiFetch("/api/websocket/status");
  const data = await res.json();
  document.getElementById("ws-host-val").value = data.host;
  document.getElementById("ws-port-val").value = data.port;
  
  const box = document.getElementById("ws-client-status-box");
  if (data.connected) {
    box.style.color = "var(--accent-emerald)";
    box.innerText = "● 已连接 OneBot 客户端";
  } else {
    box.style.color = "var(--accent-amber)";
    box.innerText = "○ 等待客户端连接 (ws://" + data.host + ":" + data.port + ")";
  }

  document.getElementById("ws-remote-address-box").innerText = data.remote_address || "None";
}

async function disconnectWs() {
  const res = await apiFetch("/api/websocket/disconnect", { method: "POST" });
  const data = await res.json();
  showToast(data.message);
  loadWsStatus();
}

// Models Tab
async function loadModelConfig() {
  const res = await apiFetch("/api/models/config");
  const data = await res.json();
  document.getElementById("model-base-url").value = data.openai_base_url;
  document.getElementById("model-default-name").value = data.default_model;
  document.getElementById("model-deliberate-name").value = data.deliberate_model;
  
  if (data.has_api_key) {
    document.getElementById("masked-key-hint").innerText = `当前已配置 API 密钥: ${data.openai_api_key_masked}`;
  } else {
    document.getElementById("masked-key-hint").innerText = "尚未配置 API Key";
  }
}

async function saveModelConfig() {
  const baseUrl = document.getElementById("model-base-url").value;
  const apiKey = document.getElementById("model-api-key").value;
  const defaultModel = document.getElementById("model-default-name").value;
  const deliberateModel = document.getElementById("model-deliberate-name").value;

  const res = await apiFetch("/api/models/config", {
    method: "POST",
    body: JSON.stringify({
      openai_base_url: baseUrl,
      openai_api_key: apiKey || null,
      default_model: defaultModel,
      deliberate_model: deliberateModel
    })
  });
  if (res.ok) {
    showToast("模型配置已生效！");
    document.getElementById("model-api-key").value = "";
    loadModelConfig();
  }
}

async function testModelCall() {
  const box = document.getElementById("model-test-result");
  box.style.display = "block";
  box.style.background = "rgba(99,102,241,0.15)";
  box.style.color = "white";
  box.innerText = "正在向模型提供商发送测试请求...";

  const baseUrl = document.getElementById("model-base-url").value;
  const apiKey = document.getElementById("model-api-key").value;
  const model = document.getElementById("model-default-name").value;

  try {
    const res = await apiFetch("/api/models/test", {
      method: "POST",
      body: JSON.stringify({
        openai_base_url: baseUrl,
        openai_api_key: apiKey || null,
        model: model
      })
    });
    const data = await res.json();
    if (data.success) {
      box.style.background = "rgba(16,185,129,0.15)";
      box.style.color = "var(--accent-emerald)";
      box.innerHTML = `✅ <strong>测试连接成功！</strong> 延迟: <strong>${data.latency_ms}ms</strong> | 响应: "${escapeHtml(data.response)}"`;
    } else {
      box.style.background = "rgba(244,63,94,0.15)";
      box.style.color = "var(--accent-rose)";
      box.innerHTML = `❌ <strong>连接失败 (${data.latency_ms}ms)</strong>: ${escapeHtml(data.error)}`;
    }
  } catch (e) {
    box.style.background = "rgba(244,63,94,0.15)";
    box.style.color = "var(--accent-rose)";
    box.innerText = "网络调用异常: " + e.message;
  }
}

// Persona & Social Tab
async function loadPersonaSettings() {
  const res = await apiFetch("/api/settings/persona");
  const data = await res.json();
  document.getElementById("persona-name").value = data.identity_name;
  document.getElementById("persona-qq").value = data.bot_qq;
  document.getElementById("persona-prompt").value = data.identity_persona;
}

async function savePersonaSettings() {
  const name = document.getElementById("persona-name").value;
  const qq = parseInt(document.getElementById("persona-qq").value);
  const prompt = document.getElementById("persona-prompt").value;

  const res = await apiFetch("/api/settings/persona", {
    method: "POST",
    body: JSON.stringify({
      identity_name: name,
      bot_qq: qq,
      identity_persona: prompt
    })
  });
  if (res.ok) {
    showToast("身份设定已成功保存！");
  }
}

async function loadSocialSettings() {
  const res = await apiFetch("/api/settings/social");
  const data = await res.json();
  document.getElementById("social-keywords").value = data.monitored_keywords.join(", ");
  document.getElementById("social-cooldown").value = data.bot_cooldown_seconds;
  
  const slider = document.getElementById("social-budget-slider");
  slider.value = data.speaking_budget_base_threshold;
  document.getElementById("budget-threshold-val").innerText = slider.value;

  // Render Interest topics
  const container = document.getElementById("interest-sliders-container");
  container.innerHTML = Object.entries(data.interest_topics).map(([topic, weight]) => `
    <div style="display:flex; align-items:center; justify-content:space-between; background:rgba(255,255,255,0.02); padding:10px 14px; border-radius:8px; border:1px solid var(--border-color);">
      <span style="font-weight:600; text-transform:uppercase; font-size:0.85rem; color:var(--accent-cyan);">${topic}</span>
      <div class="slider-container" style="width:60%;">
        <input type="range" min="0" max="1" step="0.05" value="${weight}" class="slider interest-topic-slider" data-topic="${topic}" oninput="this.nextElementSibling.innerText = this.value">
        <span style="font-size:0.85rem; font-weight:700; width:36px;">${weight}</span>
      </div>
    </div>
  `).join("");
}

async function saveSocialSettings() {
  const keywords = document.getElementById("social-keywords").value.split(",").map(s => s.trim()).filter(Boolean);
  const cooldown = parseInt(document.getElementById("social-cooldown").value);
  const budget = parseFloat(document.getElementById("social-budget-slider").value);

  const interestTopics = {};
  document.querySelectorAll(".interest-topic-slider").forEach(el => {
    interestTopics[el.dataset.topic] = parseFloat(el.value);
  });

  const res = await apiFetch("/api/settings/social", {
    method: "POST",
    body: JSON.stringify({
      monitored_keywords: keywords,
      bot_cooldown_seconds: cooldown,
      speaking_budget_base_threshold: budget,
      interest_topics: interestTopics
    })
  });
  if (res.ok) {
    showToast("社交与注意力参数已成功保存！");
  }
}

// Plugins Tab
async function loadPlugins() {
  const res = await apiFetch("/api/plugins/list");
  const plugins = await res.json();
  const grid = document.getElementById("plugins-cards-grid");

  grid.innerHTML = plugins.map(p => `
    <div class="glass bento-card">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:12px;">
        <div>
          <h3 style="font-size:1.05rem; font-weight:700; margin-bottom:4px;">${p.name}</h3>
          <span style="font-size:0.75rem; color:var(--text-muted);">v${p.version} by ${p.author}</span>
        </div>
        <label class="switch">
          <input type="checkbox" ${p.enabled ? "checked" : ""} onchange="togglePlugin('${p.id}', this.checked)">
          <span class="slider-round"></span>
        </label>
      </div>
      <p style="font-size:0.85rem; color:var(--text-secondary); line-height:1.5; flex:1; margin-bottom:16px;">
        ${p.description}
      </p>
      <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid var(--border-color); padding-top:12px;">
        <span class="badge ${p.enabled ? 'badge-emerald' : 'badge-amber'}">${p.status.toUpperCase()}</span>
        <button class="btn btn-secondary" onclick="alert('参数配置抽屉（预留）：' + JSON.stringify(${escapeHtml(JSON.stringify(p.config))}))" style="padding:4px 10px; font-size:0.75rem;">参数配置</button>
      </div>
    </div>
  `).join("");
}

async function togglePlugin(pluginId, enabled) {
  const res = await apiFetch("/api/plugins/toggle", {
    method: "POST",
    body: JSON.stringify({ plugin_id: pluginId, enabled: enabled })
  });
  if (res.ok) {
    showToast(`插件 ${pluginId} 状态已更新为: ${enabled ? '启用' : '禁用'}`);
    loadPlugins();
  }
}

// Helpers
function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (currentTab === "overview") {
      loadOverviewStats();
    }
  }, 4000);
}

// Init
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".nav-tab").forEach(tab => {
    tab.addEventListener("click", () => switchTab(tab.dataset.target));
  });

  document.getElementById("btn-logout").addEventListener("click", handleLogout);
  checkAuth();
});
