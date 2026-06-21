const state = {
  services: [],
  groups: {},
  activeTab: "overview",
};

const commandOutput = () => document.querySelector("#commandOutput");

function byKey(key) {
  return state.services.find((service) => service.key === key);
}

function setCommandOutput(payload) {
  const node = commandOutput();
  if (!node) return;
  if (payload.results) {
    node.textContent = payload.results
      .map((item) => `[${item.service}] ${item.ok ? "OK" : "ERROR"}\n${item.output || ""}`)
      .join("\n\n");
  } else {
    node.textContent = payload.output || JSON.stringify(payload, null, 2);
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw data;
  }
  return data;
}

async function refresh() {
  const data = await api("/api/status");
  state.services = data.services;
  state.groups = data.groups;
  render();
}

function renderHealth() {
  const core = ["llm_host", "archive", "telegram_bot"];
  const strip = document.querySelector("#healthStrip");
  strip.innerHTML = "";
  core.forEach((key) => {
    const service = byKey(key);
    const chip = document.createElement("span");
    chip.className = `chip ${service?.running ? "ok" : ""}`;
    chip.textContent = `${service?.name || key}: ${service?.running ? "online" : "off"}`;
    strip.appendChild(chip);
  });
}

function serviceCard(service) {
  const card = document.createElement("article");
  card.className = "card";
  const url = service.url ? `<a href="${service.url}" target="_blank" rel="noreferrer">${service.url}</a>` : "";
  card.innerHTML = `
    <div class="card-top">
      <div>
        <h2>${service.name}</h2>
        <p>${service.description}</p>
      </div>
      <span class="status ${service.running ? "running" : "stopped"}">${service.running ? "Запущен" : "Остановлен"}</span>
    </div>
    <div class="meta">
      ${service.pid ? `<span>PID ${service.pid}</span>` : ""}
      ${service.port ? `<span>port ${service.port}</span>` : ""}
      ${url}
    </div>
    <div class="card-actions">
      <button data-service="${service.key}" data-action="start">Старт</button>
      <button data-service="${service.key}" data-action="stop">Стоп</button>
      <button data-service="${service.key}" data-action="check">Проверить</button>
      <button data-log="${service.key}">Лог</button>
    </div>
  `;
  return card;
}

function renderGroup(group) {
  document.querySelectorAll(`.service-grid[data-group="${group}"]`).forEach((grid) => {
    grid.innerHTML = "";
    state.services.filter((service) => service.group === group).forEach((service) => {
      grid.appendChild(serviceCard(service));
    });
  });
}

function renderOverview() {
  const grid = document.querySelector("#overviewServices");
  const keys = ["llm_host", "archive", "telegram_bot", "translator", "stt", "site", "named_tunnel", "roxdub"];
  grid.innerHTML = "";
  keys.map(byKey).filter(Boolean).forEach((service) => grid.appendChild(serviceCard(service)));
}

function renderLogSelect() {
  const select = document.querySelector("#logSelect");
  const current = select.value;
  select.innerHTML = `<option value="console">Palatine Console</option>`;
  state.services.forEach((service) => {
    const option = document.createElement("option");
    option.value = service.key;
    option.textContent = `${service.groupName} / ${service.name}`;
    select.appendChild(option);
  });
  if ([...select.options].some((option) => option.value === current)) {
    select.value = current;
  }
}

function render() {
  renderHealth();
  renderOverview();
  ["core", "public", "translation", "image", "mechanicum"].forEach(renderGroup);
  renderLogSelect();
}

async function runService(service, action) {
  setCommandOutput({ output: `${action}: ${service}...` });
  try {
    const result = await api("/api/service", {
      method: "POST",
      body: JSON.stringify({ service, action }),
    });
    setCommandOutput(result);
  } catch (error) {
    setCommandOutput(error);
  }
  await refresh();
}

async function runBundle(bundle, action) {
  setCommandOutput({ output: `${action}: ${bundle}...` });
  try {
    const result = await api("/api/bundle", {
      method: "POST",
      body: JSON.stringify({ bundle, action }),
    });
    setCommandOutput(result);
  } catch (error) {
    setCommandOutput(error);
  }
  await refresh();
}

async function loadLog(service) {
  const output = document.querySelector("#logOutput");
  output.textContent = "Читаю лог...";
  try {
    const data = await api(`/api/logs?service=${encodeURIComponent(service)}`);
    output.textContent = data.log || "Лог пуст.";
  } catch (error) {
    output.textContent = error.output || error.error || JSON.stringify(error, null, 2);
  }
}

function activateTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll(".nav").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".tab").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tab}`);
  });
}

document.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-tab]");
  if (nav) {
    activateTab(nav.dataset.tab);
    return;
  }

  const serviceButton = event.target.closest("[data-service]");
  if (serviceButton) {
    runService(serviceButton.dataset.service, serviceButton.dataset.action);
    return;
  }

  const bundleButton = event.target.closest("[data-bundle]");
  if (bundleButton) {
    runBundle(bundleButton.dataset.bundle, bundleButton.dataset.action);
    return;
  }

  const logButton = event.target.closest("[data-log]");
  if (logButton) {
    activateTab("logs");
    document.querySelector("#logSelect").value = logButton.dataset.log;
    loadLog(logButton.dataset.log);
  }
});

document.querySelector("#refreshOverview").addEventListener("click", refresh);
document.querySelector("#refreshLogs").addEventListener("click", () => loadLog(document.querySelector("#logSelect").value));
document.querySelector("#logSelect").addEventListener("change", (event) => loadLog(event.target.value));

refresh().then(() => loadLog("console"));
setInterval(refresh, 5000);
