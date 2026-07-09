(() => {
  "use strict";

  const STORAGE_KEY = "shushunyaWikiState:v1";
  const VISIBILITY_LABELS = {
    public: "Public",
    private: "Private",
    team: "Team",
    unlisted: "Unlisted"
  };

  const ACCESS_COPY = {
    public: "видно всем",
    private: "только тебе",
    team: "для команды",
    unlisted: "по ссылке"
  };

  const $ = (selector) => document.querySelector(selector);

  const els = {
    accountName: $("#account-name"),
    accountHandle: $("#account-handle"),
    avatar: $("#avatar"),
    authModal: $("#auth-modal"),
    authForm: $("#auth-form"),
    authName: $("#auth-name"),
    authHandle: $("#auth-handle"),
    authEmail: $("#auth-email"),
    openAuth: $("#open-auth"),
    themeToggle: $("#theme-toggle"),
    globalSearch: $("#global-search"),
    metricProjects: $("#metric-projects"),
    metricPublic: $("#metric-public"),
    metricPrivate: $("#metric-private"),
    projectList: $("#project-list"),
    docList: $("#doc-list"),
    publicLinks: $("#public-links"),
    publicCount: $("#public-count"),
    projectTitle: $("#project-title"),
    projectSummary: $("#project-summary"),
    projectVisibility: $("#project-visibility"),
    newProject: $("#new-project"),
    newProjectTop: $("#new-project-top"),
    projectModal: $("#project-modal"),
    projectForm: $("#project-form"),
    projectName: $("#project-name"),
    projectDescription: $("#project-description"),
    projectAccess: $("#project-access"),
    newDoc: $("#new-doc"),
    docModal: $("#doc-modal"),
    docForm: $("#doc-form"),
    docName: $("#doc-name"),
    docTags: $("#doc-tags"),
    docAccess: $("#doc-access"),
    breadcrumb: $("#breadcrumb"),
    readerPanel: $(".reader-panel"),
    titleInput: $("#doc-title-input"),
    docVisibility: $("#doc-visibility"),
    tagRow: $("#tag-row"),
    previewSurface: $("#preview-surface"),
    editorSurface: $("#editor-surface"),
    commitMessage: $("#commit-message"),
    saveDoc: $("#save-doc"),
    accessBadge: $("#access-badge"),
    versionCount: $("#version-count"),
    versionList: $("#version-list"),
    shareUrl: $("#share-url"),
    copyShare: $("#copy-share"),
    docCount: $("#doc-count"),
    toast: $("#toast"),
    repoMap: $("#repo-map"),
    archiveCanvas: $("#archive-canvas")
  };

  const seedState = () => ({
    theme: "night",
    user: {
      name: "Шушуня",
      handle: "shushunya",
      email: "owner@shushunya.wiki",
      registered: false
    },
    projects: [
      {
        id: "project-archive",
        title: "Archive of Heresy",
        slug: "archive-of-heresy",
        visibility: "team",
        summary: "Внутренняя база знаний: архитектура, роли, протоколы и живые решения по проекту.",
        accent: "#d7b660",
        updatedAt: "2026-07-09T09:20:00.000Z",
        docs: [
          {
            id: "doc-brief",
            title: "README",
            slug: "readme",
            visibility: "public",
            tags: ["overview", "public", "start"],
            updatedAt: "2026-07-09T09:20:00.000Z",
            content: [
              "# Archive of Heresy",
              "",
              "Публичная стартовая страница проекта. Здесь лежит краткая карта: что проект делает, где искать рабочие документы и какие части можно показать людям.",
              "",
              "## Быстрый контекст",
              "- `Public` страницы формируют витрину проекта.",
              "- `Team` страницы доступны рабочей группе.",
              "- `Private` страницы остаются в личном хранилище владельца.",
              "",
              "> Витрина может выглядеть как документация, а внутри оставаться полноценным рабочим архивом."
            ].join("\n"),
            versions: [
              version("a17c9f", "Открыта публичная стартовая страница", "2026-07-09T09:20:00.000Z"),
              version("91bc3a", "Добавлена структура доступа", "2026-07-08T21:15:00.000Z")
            ]
          },
          {
            id: "doc-access",
            title: "Access Matrix",
            slug: "access-matrix",
            visibility: "private",
            tags: ["security", "roles"],
            updatedAt: "2026-07-09T08:42:00.000Z",
            content: [
              "# Access Matrix",
              "",
              "Рабочая матрица прав для страниц и проектных пространств.",
              "",
              "## Роли",
              "- Owner: полный доступ и публикация.",
              "- Maintainer: правит командные страницы.",
              "- Reader: читает назначенные разделы.",
              "",
              "## Решение",
              "По умолчанию новая страница создаётся как `Private`. Публичность должна быть явным действием."
            ].join("\n"),
            versions: [
              version("d40e92", "Зафиксирована приватность по умолчанию", "2026-07-09T08:42:00.000Z")
            ]
          },
          {
            id: "doc-roadmap",
            title: "Roadmap",
            slug: "roadmap",
            visibility: "team",
            tags: ["plan", "delivery"],
            updatedAt: "2026-07-08T17:02:00.000Z",
            content: [
              "# Roadmap",
              "",
              "План развития проектной документации.",
              "",
              "## Сейчас",
              "- Единый редактор страниц.",
              "- Публичная витрина проекта.",
              "- История версий по каждой странице.",
              "",
              "## Дальше",
              "- Комментарии к абзацам.",
              "- Импорт Markdown-репозиториев.",
              "- Экспорт публичного сайта."
            ].join("\n"),
            versions: [
              version("b0a711", "Собран план MVP", "2026-07-08T17:02:00.000Z")
            ]
          }
        ]
      },
      {
        id: "project-vox",
        title: "Vox Relay",
        slug: "vox-relay",
        visibility: "private",
        summary: "Личные заметки по сервису сообщений, наблюдениям и будущему публичному API.",
        accent: "#4dd6c9",
        updatedAt: "2026-07-07T22:40:00.000Z",
        docs: [
          {
            id: "doc-api",
            title: "API Notes",
            slug: "api-notes",
            visibility: "private",
            tags: ["api", "draft"],
            updatedAt: "2026-07-07T22:40:00.000Z",
            content: [
              "# API Notes",
              "",
              "Черновик интерфейса для будущей интеграции.",
              "",
              "## События",
              "- `message.created`",
              "- `thread.updated`",
              "- `wiki.page.published`",
              "",
              "## Риск",
              "Нужно не смешивать приватные документы и публичный экспорт."
            ].join("\n"),
            versions: [
              version("7ce102", "Добавлен список событий", "2026-07-07T22:40:00.000Z")
            ]
          },
          {
            id: "doc-public-api",
            title: "Public API Sketch",
            slug: "public-api-sketch",
            visibility: "unlisted",
            tags: ["api", "share"],
            updatedAt: "2026-07-07T13:12:00.000Z",
            content: [
              "# Public API Sketch",
              "",
              "Страница для точечной отправки партнёрам по ссылке.",
              "",
              "- Стабильные URL страниц.",
              "- Версии документа как коммиты.",
              "- Отдельные права на каждую страницу."
            ].join("\n"),
            versions: [
              version("4fb891", "Создан unlisted-черновик", "2026-07-07T13:12:00.000Z")
            ]
          }
        ]
      },
      {
        id: "project-site",
        title: "shushunya.wiki",
        slug: "shushunya-wiki",
        visibility: "public",
        summary: "Конкурсный прототип вики для любых проектов: публичные страницы, скрытые документы и версия каждой правки.",
        accent: "#d64d9c",
        updatedAt: "2026-07-09T11:33:00.000Z",
        docs: [
          {
            id: "doc-product",
            title: "Product Spec",
            slug: "product-spec",
            visibility: "public",
            tags: ["spec", "public", "mvp"],
            updatedAt: "2026-07-09T11:33:00.000Z",
            content: [
              "# Product Spec",
              "",
              "**shushunya.wiki** соединяет вики-подход и репозиторную дисциплину: проекты, страницы, доступы, история правок и публичная витрина.",
              "",
              "## Основные сценарии",
              "- Владелец ведёт личные закрытые документы.",
              "- Команда видит рабочие страницы.",
              "- Публичные страницы можно показывать людям как аккуратный сайт проекта.",
              "",
              "## MVP",
              "- Регистрация профиля.",
              "- Создание проектов и страниц.",
              "- `Public`, `Private`, `Team`, `Unlisted` для каждой страницы.",
              "- Версионная история с сообщением изменения."
            ].join("\n"),
            versions: [
              version("c9f2a0", "Сформирован продуктовый каркас", "2026-07-09T11:33:00.000Z"),
              version("9af0dd", "Добавлены режимы доступа", "2026-07-09T10:28:00.000Z")
            ]
          },
          {
            id: "doc-brand",
            title: "Visual Direction",
            slug: "visual-direction",
            visibility: "team",
            tags: ["brand", "ui"],
            updatedAt: "2026-07-09T11:05:00.000Z",
            content: [
              "# Visual Direction",
              "",
              "Темная база, пурпур, золото, слоновая кость и холодные технические акценты. Интерфейс должен ощущаться как рабочий архив, а не лендинг.",
              "",
              "## Принципы",
              "- Много плотной информации, но без визуального шума.",
              "- Проект всегда в центре, документы рядом.",
              "- Состояние доступа видно до открытия страницы."
            ].join("\n"),
            versions: [
              version("f1a344", "Зафиксирован визуальный курс", "2026-07-09T11:05:00.000Z")
            ]
          }
        ]
      }
    ]
  });

  let state = loadState();
  let activeFilter = "all";
  let activeMode = "preview";
  let selectedProjectId = state.ui?.selectedProjectId || state.projects[0]?.id || null;
  let selectedDocId = state.ui?.selectedDocId || state.projects[0]?.docs[0]?.id || null;

  function version(hash, message, at) {
    return {
      hash,
      message,
      at,
      by: "shushunya",
      delta: Math.max(4, Math.floor(hash.charCodeAt(0) % 19) + 3)
    };
  }

  function loadState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return seedState();
      const parsed = JSON.parse(raw);
      if (!parsed.projects || !Array.isArray(parsed.projects)) return seedState();
      return parsed;
    } catch (error) {
      console.warn("State reset:", error);
      return seedState();
    }
  }

  function persist() {
    state.ui = { selectedProjectId, selectedDocId };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function currentProject() {
    return state.projects.find((project) => project.id === selectedProjectId) || state.projects[0] || null;
  }

  function currentDoc() {
    const project = currentProject();
    if (!project) return null;
    return project.docs.find((doc) => doc.id === selectedDocId) || project.docs[0] || null;
  }

  function ensureSelection() {
    if (!state.projects.length) {
      selectedProjectId = null;
      selectedDocId = null;
      return;
    }
    const project = currentProject();
    selectedProjectId = project.id;
    if (!project.docs.length) {
      selectedDocId = null;
      return;
    }
    const doc = currentDoc();
    selectedDocId = doc.id;
  }

  function render() {
    ensureSelection();
    renderAccount();
    renderMetrics();
    renderProjects();
    renderPublicLinks();
    renderProjectHero();
    renderDocs();
    renderReader();
    renderActivity();
    drawRepoMap();
    persist();
  }

  function renderAccount() {
    const initials = state.user.name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase() || "SW";
    els.accountName.textContent = state.user.name;
    els.accountHandle.textContent = `@${state.user.handle}`;
    els.avatar.textContent = initials;
    els.openAuth.textContent = state.user.registered ? "Профиль" : "Войти";
    els.authName.value = state.user.name;
    els.authHandle.value = state.user.handle;
    els.authEmail.value = state.user.email;
    document.title = `shushunya.wiki / @${state.user.handle}`;
  }

  function renderMetrics() {
    const docs = state.projects.flatMap((project) => project.docs);
    els.metricProjects.textContent = String(state.projects.length);
    els.metricPublic.textContent = String(docs.filter((doc) => doc.visibility === "public").length);
    els.metricPrivate.textContent = String(docs.filter((doc) => doc.visibility === "private").length);
  }

  function renderProjects() {
    const query = normalizedQuery();
    const projects = state.projects.filter((project) => {
      const matchesFilter = activeFilter === "all" || project.visibility === activeFilter;
      const haystack = `${project.title} ${project.summary} ${project.docs.map((doc) => `${doc.title} ${doc.tags.join(" ")}`).join(" ")}`.toLowerCase();
      return matchesFilter && (!query || haystack.includes(query));
    });

    els.projectList.innerHTML = "";
    if (!projects.length) {
      els.projectList.append(empty("Нет проектов под этот фильтр."));
      return;
    }

    for (const project of projects) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `project-card${project.id === selectedProjectId ? " active" : ""}`;
      button.dataset.projectId = project.id;
      button.innerHTML = `
        <strong></strong>
        <span></span>
        <i class="status-pill ${project.visibility}">${VISIBILITY_LABELS[project.visibility]}</i>
      `;
      button.querySelector("strong").textContent = project.title;
      button.querySelector("span").textContent = `${project.docs.length} pages · ${formatDate(project.updatedAt)}`;
      els.projectList.append(button);
    }
  }

  function renderPublicLinks() {
    const publicDocs = state.projects.flatMap((project) =>
      project.docs
        .filter((doc) => doc.visibility === "public" || doc.visibility === "unlisted")
        .map((doc) => ({ project, doc }))
    );

    els.publicCount.textContent = String(publicDocs.length);
    els.publicLinks.innerHTML = "";

    if (!publicDocs.length) {
      els.publicLinks.append(empty("Публичных страниц пока нет."));
      return;
    }

    for (const item of publicDocs.slice(0, 5)) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "public-link";
      button.dataset.projectId = item.project.id;
      button.dataset.docId = item.doc.id;
      button.innerHTML = "<strong></strong><span></span>";
      button.querySelector("strong").textContent = item.doc.title;
      button.querySelector("span").textContent = `/${state.user.handle}/${item.project.slug}/${item.doc.slug}`;
      els.publicLinks.append(button);
    }
  }

  function renderProjectHero() {
    const project = currentProject();
    if (!project) {
      els.projectTitle.textContent = "Пустой архив";
      els.projectSummary.textContent = "Создай первый проект и начни собирать документацию.";
      els.projectVisibility.textContent = "empty vault";
      return;
    }

    els.projectTitle.textContent = project.title;
    els.projectSummary.textContent = project.summary;
    els.projectVisibility.textContent = `${VISIBILITY_LABELS[project.visibility]} project · ${project.docs.length} pages · ${formatDate(project.updatedAt)}`;
  }

  function renderDocs() {
    const project = currentProject();
    const query = normalizedQuery();
    els.docList.innerHTML = "";

    if (!project) {
      els.docCount.textContent = "0";
      els.docList.append(empty("Нет выбранного проекта."));
      return;
    }

    const docs = project.docs.filter((doc) => {
      const haystack = `${doc.title} ${doc.visibility} ${doc.tags.join(" ")} ${doc.content}`.toLowerCase();
      return !query || haystack.includes(query);
    });
    els.docCount.textContent = String(docs.length);

    if (!docs.length) {
      els.docList.append(empty("Страницы не найдены."));
      return;
    }

    for (const doc of docs) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `doc-card${doc.id === selectedDocId ? " active" : ""}`;
      button.dataset.docId = doc.id;
      button.innerHTML = `
        <strong></strong>
        <span></span>
        <i class="status-pill ${doc.visibility}">${VISIBILITY_LABELS[doc.visibility]}</i>
      `;
      button.querySelector("strong").textContent = doc.title;
      button.querySelector("span").textContent = `${doc.tags.join(", ") || "без тегов"} · ${formatDate(doc.updatedAt)}`;
      els.docList.append(button);
    }
  }

  function renderReader() {
    const project = currentProject();
    const doc = currentDoc();
    const hasDoc = Boolean(project && doc);

    els.readerPanel.classList.toggle("editing", activeMode === "edit");
    document.querySelectorAll(".mode-button").forEach((button) => {
      button.classList.toggle("active", button.dataset.mode === activeMode);
    });

    els.titleInput.disabled = !hasDoc;
    els.docVisibility.disabled = !hasDoc;
    els.editorSurface.disabled = !hasDoc;
    els.commitMessage.disabled = !hasDoc;
    els.saveDoc.disabled = !hasDoc;

    if (!hasDoc) {
      els.breadcrumb.textContent = "wiki / empty";
      els.titleInput.value = "";
      els.docVisibility.value = "private";
      els.tagRow.innerHTML = "";
      els.previewSurface.innerHTML = "<p>Создай страницу, чтобы открыть редактор.</p>";
      els.editorSurface.value = "";
      return;
    }

    els.breadcrumb.textContent = `${state.user.handle} / ${project.slug} / ${doc.slug}`;
    els.titleInput.value = doc.title;
    els.docVisibility.value = doc.visibility;
    els.previewSurface.innerHTML = renderMarkdown(doc.content);
    els.editorSurface.value = doc.content;

    els.tagRow.innerHTML = "";
    for (const tag of doc.tags) {
      const pill = document.createElement("span");
      pill.className = "tag";
      pill.textContent = `#${tag}`;
      els.tagRow.append(pill);
    }
  }

  function renderActivity() {
    const project = currentProject();
    const doc = currentDoc();
    const hasDoc = Boolean(project && doc);

    if (!hasDoc) {
      els.accessBadge.textContent = "Empty";
      els.versionCount.textContent = "0";
      els.versionList.innerHTML = "";
      els.versionList.append(empty("История появится после сохранения."));
      els.shareUrl.textContent = "https://shushunya.wiki/";
      return;
    }

    els.accessBadge.textContent = VISIBILITY_LABELS[doc.visibility];
    els.versionCount.textContent = String(doc.versions.length);
    els.shareUrl.textContent = publicUrl(project, doc);

    document.querySelectorAll(".access-tile").forEach((tile) => {
      const access = tile.dataset.access;
      tile.classList.toggle("active", access === doc.visibility);
      tile.querySelector("span").textContent = ACCESS_COPY[access];
    });

    els.versionList.innerHTML = "";
    if (!doc.versions.length) {
      els.versionList.append(empty("История появится после сохранения."));
      return;
    }

    for (const entry of doc.versions.slice(0, 6)) {
      const item = document.createElement("div");
      item.className = "version-item";
      item.innerHTML = "<strong><code></code><time></time></strong><span></span><span></span>";
      item.querySelector("code").textContent = entry.hash;
      item.querySelector("time").textContent = shortTime(entry.at);
      item.querySelectorAll("span")[0].textContent = entry.message;
      item.querySelectorAll("span")[1].textContent = `+${entry.delta} lines by @${entry.by}`;
      els.versionList.append(item);
    }
  }

  function empty(message) {
    const div = document.createElement("div");
    div.className = "empty-state";
    div.textContent = message;
    return div;
  }

  function normalizedQuery() {
    return els.globalSearch.value.trim().toLowerCase();
  }

  function publicUrl(project, doc) {
    return `${publicBaseUrl()}/@${state.user.handle}/${project.slug}/${doc.slug}`;
  }

  function publicBaseUrl() {
    const host = window.location.host;
    if (host && !host.startsWith("127.0.0.1") && !host.startsWith("localhost")) {
      return window.location.origin;
    }
    return "https://shushunya.wiki";
  }

  function formatDate(value) {
    const date = new Date(value);
    return new Intl.DateTimeFormat("ru", { day: "2-digit", month: "short" }).format(date);
  }

  function shortTime(value) {
    const date = new Date(value);
    return new Intl.DateTimeFormat("ru", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function renderMarkdown(markdown) {
    const lines = markdown.split(/\r?\n/);
    const html = [];
    let inList = false;
    let inQuote = false;

    const closeList = () => {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
    };
    const closeQuote = () => {
      if (inQuote) {
        html.push("</blockquote>");
        inQuote = false;
      }
    };

    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (!line.trim()) {
        closeList();
        closeQuote();
        continue;
      }

      if (line.startsWith("- ")) {
        closeQuote();
        if (!inList) {
          html.push("<ul>");
          inList = true;
        }
        html.push(`<li>${inline(line.slice(2))}</li>`);
        continue;
      }

      closeList();

      if (line.startsWith("> ")) {
        if (!inQuote) {
          html.push("<blockquote>");
          inQuote = true;
        }
        html.push(`<p>${inline(line.slice(2))}</p>`);
        continue;
      }

      closeQuote();

      if (line.startsWith("### ")) {
        html.push(`<h3>${inline(line.slice(4))}</h3>`);
      } else if (line.startsWith("## ")) {
        html.push(`<h2>${inline(line.slice(3))}</h2>`);
      } else if (line.startsWith("# ")) {
        html.push(`<h1>${inline(line.slice(2))}</h1>`);
      } else {
        html.push(`<p>${inline(line)}</p>`);
      }
    }

    closeList();
    closeQuote();
    return html.join("");
  }

  function inline(value) {
    let safe = escapeHtml(value);
    const code = [];
    safe = safe.replace(/`([^`]+)`/g, (_match, content) => {
      code.push(`<code>${content}</code>`);
      return `@@CODE${code.length - 1}@@`;
    });
    safe = safe.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    safe = safe.replace(/@@CODE(\d+)@@/g, (_match, index) => code[Number(index)] || "");
    return safe;
  }

  function escapeHtml(value) {
    return value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function slugify(value) {
    const map = {
      а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i",
      й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t",
      у: "u", ф: "f", х: "h", ц: "c", ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "",
      э: "e", ю: "yu", я: "ya"
    };
    return value
      .trim()
      .toLowerCase()
      .split("")
      .map((char) => map[char] ?? char)
      .join("")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 70) || "page";
  }

  function uniqueSlug(base, existing) {
    let slug = base;
    let index = 2;
    while (existing.has(slug)) {
      slug = `${base}-${index}`;
      index += 1;
    }
    return slug;
  }

  function uid(prefix) {
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  }

  function simpleHash(value) {
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16).slice(0, 6).padStart(6, "0");
  }

  function showToast(message) {
    els.toast.textContent = message;
    els.toast.classList.add("visible");
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => els.toast.classList.remove("visible"), 2300);
  }

  function openDialog(dialog) {
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
      return;
    }
    dialog.setAttribute("open", "");
  }

  function closeDialog(dialog) {
    if (typeof dialog.close === "function") {
      dialog.close();
      return;
    }
    dialog.removeAttribute("open");
  }

  function wireEvents() {
    els.openAuth.addEventListener("click", () => openDialog(els.authModal));
    els.themeToggle.addEventListener("click", () => {
      state.theme = state.theme === "dawn" ? "night" : "dawn";
      document.body.classList.toggle("dawn", state.theme === "dawn");
      persist();
    });

    els.authForm.addEventListener("submit", (event) => {
      if (event.submitter?.value === "cancel") return;
      event.preventDefault();
      state.user = {
        name: els.authName.value.trim() || "Шушуня",
        handle: slugify(els.authHandle.value.trim() || "shushunya"),
        email: els.authEmail.value.trim() || "owner@shushunya.wiki",
        registered: true
      };
      closeDialog(els.authModal);
      showToast("Профиль сохранён локально.");
      render();
    });

    els.globalSearch.addEventListener("input", render);

    document.querySelectorAll(".filter-chip").forEach((button) => {
      button.addEventListener("click", () => {
        activeFilter = button.dataset.filter;
        document.querySelectorAll(".filter-chip").forEach((chip) => {
          chip.classList.toggle("active", chip === button);
        });
        render();
      });
    });

    els.projectList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-project-id]");
      if (!button) return;
      syncDraft();
      selectedProjectId = button.dataset.projectId;
      const project = currentProject();
      selectedDocId = project?.docs[0]?.id || null;
      activeMode = "preview";
      render();
    });

    els.publicLinks.addEventListener("click", (event) => {
      const button = event.target.closest("[data-project-id][data-doc-id]");
      if (!button) return;
      syncDraft();
      selectedProjectId = button.dataset.projectId;
      selectedDocId = button.dataset.docId;
      activeMode = "preview";
      render();
    });

    els.docList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-doc-id]");
      if (!button) return;
      syncDraft();
      selectedDocId = button.dataset.docId;
      activeMode = "preview";
      render();
    });

    document.querySelectorAll(".mode-button").forEach((button) => {
      button.addEventListener("click", () => {
        const doc = currentDoc();
        if (!doc) return;
        if (button.dataset.mode === "preview") {
          doc.content = els.editorSurface.value;
          doc.title = els.titleInput.value.trim() || "Untitled";
          doc.visibility = els.docVisibility.value;
        }
        activeMode = button.dataset.mode;
        render();
      });
    });

    els.docVisibility.addEventListener("change", () => {
      const doc = currentDoc();
      if (!doc) return;
      syncDraft();
      doc.visibility = els.docVisibility.value;
      doc.updatedAt = new Date().toISOString();
      updateProjectTimestamp();
      render();
    });

    document.querySelectorAll(".access-tile").forEach((tile) => {
      tile.addEventListener("click", () => {
        const doc = currentDoc();
        if (!doc) return;
        syncDraft();
        doc.visibility = tile.dataset.access;
        doc.updatedAt = new Date().toISOString();
        updateProjectTimestamp();
        showToast(`${VISIBILITY_LABELS[doc.visibility]} доступ применён.`);
        render();
      });
    });

    els.saveDoc.addEventListener("click", () => {
      const project = currentProject();
      const doc = currentDoc();
      if (!project || !doc) return;

      const title = els.titleInput.value.trim() || "Untitled";
      const existingSlugs = new Set(project.docs.filter((item) => item.id !== doc.id).map((item) => item.slug));
      doc.title = title;
      doc.slug = uniqueSlug(slugify(title), existingSlugs);
      doc.visibility = els.docVisibility.value;
      doc.content = els.editorSurface.value.trim() || `# ${title}\n\nНовая страница проекта.`;
      doc.updatedAt = new Date().toISOString();
      doc.versions.unshift({
        hash: simpleHash(`${doc.title}:${doc.content}:${doc.updatedAt}`),
        message: els.commitMessage.value.trim() || "Сохранена новая версия",
        at: doc.updatedAt,
        by: state.user.handle,
        delta: Math.max(1, doc.content.split(/\r?\n/).length)
      });
      els.commitMessage.value = "";
      updateProjectTimestamp();
      activeMode = "preview";
      showToast("Версия сохранена.");
      render();
    });

    els.copyShare.addEventListener("click", async () => {
      const project = currentProject();
      const doc = currentDoc();
      if (!project || !doc) return;
      const url = publicUrl(project, doc);
      try {
        await navigator.clipboard.writeText(url);
      } catch (_error) {
        const helper = document.createElement("textarea");
        helper.value = url;
        helper.style.position = "fixed";
        helper.style.left = "-9999px";
        document.body.append(helper);
        helper.select();
        document.execCommand("copy");
        helper.remove();
      }
      showToast("Ссылка скопирована.");
    });

    els.newProject.addEventListener("click", () => openProjectModal());
    els.newProjectTop.addEventListener("click", () => openProjectModal());
    els.projectForm.addEventListener("submit", (event) => {
      if (event.submitter?.value === "cancel") return;
      event.preventDefault();
      const title = els.projectName.value.trim() || "Новый проект";
      const existing = new Set(state.projects.map((project) => project.slug));
      const now = new Date().toISOString();
      const project = {
        id: uid("project"),
        title,
        slug: uniqueSlug(slugify(title), existing),
        visibility: els.projectAccess.value,
        summary: els.projectDescription.value.trim() || "Проектная документация.",
        accent: randomAccent(),
        updatedAt: now,
        docs: [
          {
            id: uid("doc"),
            title: "README",
            slug: "readme",
            visibility: els.projectAccess.value === "public" ? "public" : "private",
            tags: ["start"],
            updatedAt: now,
            content: `# ${title}\n\n${els.projectDescription.value.trim() || "Проектная документация."}\n\n## Первые страницы\n- README\n- Архитектура\n- Решения`,
            versions: [
              {
                hash: simpleHash(`${title}:${now}`),
                message: "Создан проект",
                at: now,
                by: state.user.handle,
                delta: 7
              }
            ]
          }
        ]
      };
      state.projects.unshift(project);
      selectedProjectId = project.id;
      selectedDocId = project.docs[0].id;
      closeDialog(els.projectModal);
      els.projectForm.reset();
      showToast("Проект создан.");
      render();
    });

    els.newDoc.addEventListener("click", () => {
      syncDraft();
      if (!currentProject()) {
        openProjectModal();
        return;
      }
      els.docAccess.value = "private";
      openDialog(els.docModal);
      window.setTimeout(() => els.docName.focus(), 50);
    });

    els.docForm.addEventListener("submit", (event) => {
      if (event.submitter?.value === "cancel") return;
      event.preventDefault();
      const project = currentProject();
      if (!project) return;
      const title = els.docName.value.trim() || "Новая страница";
      const existing = new Set(project.docs.map((doc) => doc.slug));
      const now = new Date().toISOString();
      const doc = {
        id: uid("doc"),
        title,
        slug: uniqueSlug(slugify(title), existing),
        visibility: els.docAccess.value,
        tags: els.docTags.value.split(",").map((tag) => slugify(tag)).filter(Boolean).slice(0, 5),
        updatedAt: now,
        content: `# ${title}\n\nНовая страница проекта **${project.title}**.\n\n## Заметки\n- Решение\n- Контекст\n- Следующий шаг`,
        versions: [
          {
            hash: simpleHash(`${title}:${now}`),
            message: "Создана страница",
            at: now,
            by: state.user.handle,
            delta: 7
          }
        ]
      };
      project.docs.unshift(doc);
      selectedDocId = doc.id;
      updateProjectTimestamp();
      closeDialog(els.docModal);
      els.docForm.reset();
      activeMode = "edit";
      showToast("Страница создана.");
      render();
    });

    window.addEventListener("resize", () => {
      resizeArchiveCanvas();
      drawRepoMap();
    });
  }

  function openProjectModal() {
    syncDraft();
    els.projectAccess.value = "private";
    openDialog(els.projectModal);
    window.setTimeout(() => els.projectName.focus(), 50);
  }

  function syncDraft() {
    const doc = currentDoc();
    if (!doc || activeMode !== "edit") return;
    doc.title = els.titleInput.value.trim() || doc.title;
    doc.content = els.editorSurface.value;
    doc.visibility = els.docVisibility.value;
  }

  function updateProjectTimestamp() {
    const project = currentProject();
    if (project) project.updatedAt = new Date().toISOString();
  }

  function randomAccent() {
    const accents = ["#d7b660", "#d64d9c", "#4dd6c9", "#8fe388", "#a982ff"];
    return accents[Math.floor(Math.random() * accents.length)];
  }

  function startCanvases() {
    resizeArchiveCanvas();
    const context = els.archiveCanvas.getContext("2d");
    let frame = 0;

    const draw = () => {
      const { width, height } = els.archiveCanvas;
      context.clearRect(0, 0, width, height);
      context.save();
      context.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
      const w = els.archiveCanvas.clientWidth;
      const h = els.archiveCanvas.clientHeight;
      context.globalAlpha = 0.72;

      drawBackgroundGrid(context, w, h, frame);
      drawFloatingPages(context, w, h, frame);
      drawConstellation(context, w, h, frame);

      context.restore();
      frame += 1;
      window.requestAnimationFrame(draw);
    };

    window.requestAnimationFrame(draw);
  }

  function resizeArchiveCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.floor(window.innerWidth * dpr));
    const height = Math.max(1, Math.floor(window.innerHeight * dpr));
    els.archiveCanvas.width = width;
    els.archiveCanvas.height = height;
  }

  function drawBackgroundGrid(context, width, height, frame) {
    context.strokeStyle = "rgba(215, 182, 96, 0.045)";
    context.lineWidth = 1;
    const gap = 52;
    const offset = (frame * 0.18) % gap;
    for (let x = -gap; x < width + gap; x += gap) {
      context.beginPath();
      context.moveTo(x + offset, 0);
      context.lineTo(x - width * 0.1 + offset, height);
      context.stroke();
    }
    for (let y = -gap; y < height + gap; y += gap) {
      context.beginPath();
      context.moveTo(0, y + offset);
      context.lineTo(width, y - height * 0.08 + offset);
      context.stroke();
    }
  }

  function drawFloatingPages(context, width, height, frame) {
    const pages = [
      [0.12, 0.22, 118, 82, 0.012],
      [0.78, 0.2, 150, 96, -0.009],
      [0.2, 0.78, 138, 92, -0.014],
      [0.72, 0.72, 120, 86, 0.011],
      [0.46, 0.14, 94, 68, 0.016]
    ];
    for (const [px, py, pw, ph, speed] of pages) {
      const x = width * px + Math.sin(frame * speed) * 12;
      const y = height * py + Math.cos(frame * speed * 1.2) * 10;
      context.save();
      context.translate(x, y);
      context.rotate(Math.sin(frame * speed) * 0.05);
      context.fillStyle = "rgba(255, 243, 220, 0.055)";
      context.strokeStyle = "rgba(215, 182, 96, 0.16)";
      roundedRect(context, -pw / 2, -ph / 2, pw, ph, 8);
      context.fill();
      context.stroke();
      context.fillStyle = "rgba(215, 182, 96, 0.22)";
      context.fillRect(-pw / 2 + 14, -ph / 2 + 16, pw * 0.44, 4);
      context.fillStyle = "rgba(77, 214, 201, 0.16)";
      context.fillRect(-pw / 2 + 14, -ph / 2 + 32, pw * 0.66, 3);
      context.fillRect(-pw / 2 + 14, -ph / 2 + 46, pw * 0.52, 3);
      context.restore();
    }
  }

  function drawConstellation(context, width, height, frame) {
    const nodes = [
      [0.34, 0.36],
      [0.44, 0.44],
      [0.55, 0.36],
      [0.64, 0.52],
      [0.5, 0.62],
      [0.38, 0.58]
    ].map(([x, y]) => [
      x * width + Math.sin(frame * 0.012 + x) * 10,
      y * height + Math.cos(frame * 0.013 + y) * 10
    ]);

    context.strokeStyle = "rgba(214, 77, 156, 0.18)";
    context.lineWidth = 1.5;
    for (let index = 0; index < nodes.length; index += 1) {
      const next = nodes[(index + 1) % nodes.length];
      context.beginPath();
      context.moveTo(nodes[index][0], nodes[index][1]);
      context.lineTo(next[0], next[1]);
      context.stroke();
    }

    for (const [x, y] of nodes) {
      const pulse = 4 + Math.sin(frame * 0.045 + x) * 1.5;
      const gradient = context.createRadialGradient(x, y, 1, x, y, 22);
      gradient.addColorStop(0, "rgba(215, 182, 96, 0.68)");
      gradient.addColorStop(1, "rgba(215, 182, 96, 0)");
      context.fillStyle = gradient;
      context.beginPath();
      context.arc(x, y, 22, 0, Math.PI * 2);
      context.fill();
      context.fillStyle = "rgba(255, 243, 220, 0.82)";
      context.beginPath();
      context.arc(x, y, pulse, 0, Math.PI * 2);
      context.fill();
    }
  }

  function drawRepoMap() {
    const canvas = els.repoMap;
    const context = canvas.getContext("2d");
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.floor(rect.width * dpr));
    const height = Math.max(1, Math.floor(rect.height * dpr));
    canvas.width = width;
    canvas.height = height;
    context.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const project = currentProject();
    const docs = project?.docs || [];

    context.clearRect(0, 0, w, h);
    const bg = context.createLinearGradient(0, 0, w, h);
    bg.addColorStop(0, "rgba(14, 8, 22, 0.2)");
    bg.addColorStop(1, "rgba(215, 182, 96, 0.12)");
    context.fillStyle = bg;
    context.fillRect(0, 0, w, h);

    const lanes = Math.max(3, Math.min(5, docs.length + 1));
    for (let lane = 0; lane < lanes; lane += 1) {
      const y = 46 + lane * ((h - 92) / Math.max(1, lanes - 1));
      context.strokeStyle = lane % 2 === 0 ? "rgba(215, 182, 96, 0.17)" : "rgba(77, 214, 201, 0.13)";
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(32, y);
      context.bezierCurveTo(w * 0.34, y - 22, w * 0.62, y + 24, w - 34, y);
      context.stroke();
    }

    docs.slice(0, 8).forEach((doc, index) => {
      const progress = docs.length <= 1 ? 0.5 : index / Math.max(1, docs.length - 1);
      const x = 58 + progress * (w - 116);
      const y = 54 + (index % lanes) * ((h - 108) / Math.max(1, lanes - 1));
      const selected = doc.id === selectedDocId;
      const colors = {
        public: "#8fe388",
        private: "#ff6b7c",
        team: "#4dd6c9",
        unlisted: "#d64d9c"
      };
      context.fillStyle = selected ? "rgba(255, 243, 220, 0.92)" : colors[doc.visibility] || "#d7b660";
      context.strokeStyle = "rgba(7, 5, 11, 0.8)";
      context.lineWidth = 4;
      context.beginPath();
      context.arc(x, y, selected ? 10 : 7, 0, Math.PI * 2);
      context.fill();
      context.stroke();

      if (selected) {
        const glow = context.createRadialGradient(x, y, 2, x, y, 36);
        glow.addColorStop(0, "rgba(215, 182, 96, 0.42)");
        glow.addColorStop(1, "rgba(215, 182, 96, 0)");
        context.fillStyle = glow;
        context.beginPath();
        context.arc(x, y, 36, 0, Math.PI * 2);
        context.fill();
      }

      context.fillStyle = "rgba(255, 243, 220, 0.78)";
      context.font = "12px SFMono-Regular, Consolas, monospace";
      context.fillText(doc.slug.slice(0, 18), Math.min(x + 13, w - 150), Math.max(20, y - 12));
    });

    context.fillStyle = "rgba(7, 5, 11, 0.58)";
    roundedRect(context, 22, h - 58, Math.min(270, w - 44), 36, 8);
    context.fill();
    context.fillStyle = "#fff3dc";
    context.font = "13px Inter, sans-serif";
    context.fillText(project ? `${project.docs.length} pages · ${VISIBILITY_LABELS[project.visibility]} project` : "empty vault", 38, h - 35);
  }

  function roundedRect(context, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    context.beginPath();
    context.moveTo(x + r, y);
    context.lineTo(x + width - r, y);
    context.quadraticCurveTo(x + width, y, x + width, y + r);
    context.lineTo(x + width, y + height - r);
    context.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
    context.lineTo(x + r, y + height);
    context.quadraticCurveTo(x, y + height, x, y + height - r);
    context.lineTo(x, y + r);
    context.quadraticCurveTo(x, y, x + r, y);
    context.closePath();
  }

  document.body.classList.toggle("dawn", state.theme === "dawn");
  wireEvents();
  render();
  startCanvases();
})();
