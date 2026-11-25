// 项目切换器：列表、切换、新建、重命名、删除
// 挂在顶栏，点按钮展开菜单。切换项目 = 改 URL 参数并重新加载，避免残留状态。

import { api } from "./api.js";
import { state } from "./state.js";
import { el, clear } from "./util.js";

let wrap = null;
let btn = null;
let menu = null;
let projects = [];
let open = false;

function closeMenu() {
  if (!menu) return;
  menu.classList.remove("open");
  open = false;
}

async function loadProjects() {
  try {
    const list = await api.listProjects();
    projects = Array.isArray(list) ? list : [];
  } catch (e) {
    projects = [];
  }
  return projects;
}

function renderMenu() {
  if (!menu) return;
  clear(menu);

  for (const p of projects) {
    const cur = p.id === state.pid;
    const row = el("div", {
      class: "proj-item" + (cur ? " current" : ""),
      title: p.dir || p.id,
    }, [
      el("span", { class: "pname" }, p.name || "未命名项目"),
      el("span", { class: "pmeta" }, (p.clip_count || 0) + " 片段"),
    ]);

    if (!cur) {
      row.addEventListener("click", () => switchTo(p.id));
    }

    const del = el("button", { class: "pdel", title: "删除此项目" }, "\u00D7");
    del.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (!confirm("删除项目「" + (p.name || p.id) + "」？\n此操作不可撤销。")) return;
      try {
        await api.deleteProject(p.id);
        await loadProjects();
        renderMenu();
        if (p.id === state.pid) {
          const next = projects[0];
          if (next) switchTo(next.id);
          else location.href = "/";
        }
      } catch (e) {
        alert("删除失败：" + e.message);
      }
    });
    row.appendChild(del);
    menu.appendChild(row);
  }

  menu.appendChild(el("div", { class: "proj-menu-sep" }));

  const add = el("button", { class: "proj-menu-action" }, "+ 新建项目");
  add.addEventListener("click", createProject);
  menu.appendChild(add);
}

async function createProject() {
  const name = prompt("新项目名称", "未命名项目");
  if (name == null) return;
  try {
    const d = await api.createProject(name || "未命名项目", null);
    const pid = d.project.id;
    switchTo(pid);
  } catch (e) {
    alert("创建失败：" + e.message);
  }
}

function switchTo(pid) {
  if (!pid) return;
  localStorage.setItem("clip-editor:pid", pid);
  const url = new URL(location.href);
  url.searchParams.set("p", pid);
  location.href = url.toString();
}

async function renameCurrent() {
  const cur = (state.project && state.project.name) || "";
  const name = prompt("项目名称", cur);
  if (name == null) return;
  try {
    await api.patchProject(state.pid, { name });
    await loadProjects();
    if (wrap) {
      const nm = document.getElementById("proj-name");
      if (nm) nm.textContent = name;
    }
    renderMenu();
  } catch (e) {
    alert("重命名失败：" + e.message);
  }
}

export function init() {
  wrap = document.getElementById("proj-switch");
  btn = document.getElementById("proj-switch-btn");
  menu = document.getElementById("proj-menu");
  if (!wrap || !btn || !menu) {
    console.warn("[projects] 项目切换器容器缺失");
    return;
  }

  btn.addEventListener("click", async (ev) => {
    // 双击改名交给 dblclick
    if (ev.detail > 1) return;
    if (open) {
      closeMenu();
      return;
    }
    await loadProjects();
    renderMenu();
    menu.classList.add("open");
    open = true;
  });

  const nameEl = document.getElementById("proj-name");
  if (nameEl) {
    nameEl.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      closeMenu();
      renameCurrent();
    });
  }

  document.addEventListener("click", (ev) => {
    if (!open) return;
    if (wrap.contains(ev.target)) return;
    closeMenu();
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && open) closeMenu();
  });

  loadProjects();
}

// __EOF__