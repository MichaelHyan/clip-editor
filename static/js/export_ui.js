// 导出对话框：预设选择 / 进度轮询 / 告警提示 / 下载
import { api } from "./api.js";
import { state, bus } from "./state.js";
import { el, clear, fmtBytes } from "./util.js";

let modal = null;
let jobId = null;
let timer = null;
let lastJob = null;

function ensure() {
  if (modal) return modal;
  modal = el("div", { class: "modal", id: "export-modal" }, [
    el("div", { class: "modal-box export-box" }, [
      el("div", { class: "modal-head" }, [
        el("span", { class: "modal-title" }, "导出视频"),
        el("button", { class: "modal-close", onclick: close }, "\u00D7"),
      ]),
      el("div", { class: "export-body", id: "export-body" }),
      el("div", { class: "modal-foot" }, [
        el("span", { class: "modal-info", id: "export-info" }, ""),
        el("span", { class: "modal-spacer" }),
        el("button", { class: "btn", id: "export-cancel", onclick: cancel }, "关闭"),
        el("button", { class: "btn primary", id: "export-go" }, "开始导出"),
      ]),
    ]),
  ]);
  document.body.appendChild(modal);
  modal.addEventListener("click", (ev) => {
    if (ev.target === modal) close();
  });
  return modal;
}

function open() {
  ensure().classList.add("open");
  jobId = null;
  lastJob = null;
  renderForm();
}

function close() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  const m = document.getElementById("export-modal");
  if (m) m.classList.remove("open");
}

async function renderForm() {
  const body = document.getElementById("export-body");
  clear(body);
  document.getElementById("export-go").textContent = "开始导出";
  document.getElementById("export-go").onclick = start;
  document.getElementById("export-cancel").textContent = "关闭";

  let presets = {};
  try {
    const d = await api.exportPresets();
    presets = d.presets || {};
  } catch (e) {
    presets = { h264_standard: { label: "H.264 标准" } };
  }
  const st = (state.project && state.project.settings) || {};

  const sel = el("select", { class: "insp-select", id: "export-preset" });
  for (const [k, v] of Object.entries(presets)) {
    sel.appendChild(el("option", { value: k }, v.label || k));
  }
  sel.value = presets.h264_standard ? "h264_standard" : Object.keys(presets)[0];

  body.appendChild(row("编码预设", sel));
  body.appendChild(row("文件名", el("input", { class: "insp-num wide", id: "export-name", value: "output" })));
  body.appendChild(row("分辨率", el("span", { class: "export-static" },
    `${st.width || 1920} × ${st.height || 1080}`)));
  body.appendChild(row("帧率", el("span", { class: "export-static" }, `${st.fps || 60} fps`)));
  body.appendChild(row("时长", el("span", { class: "export-static" },
    `${(state.totalDuration || 0).toFixed(2)} s`)));
  body.appendChild(row("关键帧策略", el("select", { class: "insp-select", id: "export-dynamic" }, [
    el("option", { value: "expr" }, "表达式拟合（位置/色相/响度等动态生效）"),
    el("option", { value: "static" }, "静态（全部取起点值，最快）"),
  ])));
  body.appendChild(el("div", { class: "export-hint" },
    "缩放/裁剪/透明度/模糊等滤镜本身不支持随时间变化，导出时取片段起点值，会在告警里注明。"));
}

function row(label, ctrl) {
  return el("div", { class: "export-row" }, [
    el("label", { class: "insp-label" }, label),
    ctrl,
  ]);
}

async function start() {
  const preset = document.getElementById("export-preset").value;
  const name = (document.getElementById("export-name").value || "output").trim();
  const dyn = document.getElementById("export-dynamic").value;
  const btn = document.getElementById("export-go");
  btn.disabled = true;
  btn.textContent = "准备中…";
  try {
    const job = await api.exportStart(state.pid, {
      filename: name,
      dynamic: dyn,
      ...presetToEnc(preset),
    });
    jobId = job.id;
    lastJob = job;
    renderProgress(job);
    btn.textContent = "导出中…";
    timer = setInterval(poll, 700);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "开始导出";
    document.getElementById("export-info").textContent = "启动失败：" + e.message;
  }
}

function presetToEnc(key) {
  const map = {
    h264_high: { preset: "slow", crf: 16, abitrate: "256k" },
    h264_standard: { preset: "medium", crf: 20, abitrate: "192k" },
    h264_web: { preset: "veryfast", crf: 28, abitrate: "128k" },
    h265_high: { preset: "medium", crf: 22, abitrate: "192k", pix_fmt: "yuv420p" },
  };
  return map[key] || map.h264_standard;
}

async function poll() {
  if (!jobId) return;
  try {
    const job = await api.exportJob(jobId);
    lastJob = job;
    renderProgress(job);
    if (["done", "failed", "cancelled"].includes(job.status)) {
      clearInterval(timer);
      timer = null;
      const btn = document.getElementById("export-go");
      btn.disabled = false;
      if (job.status === "done") {
        btn.textContent = "下载";
        btn.onclick = download;
        document.getElementById("export-cancel").textContent = "关闭";
      } else {
        btn.textContent = "重试";
        btn.onclick = start;
      }
    }
  } catch (e) {
    clearInterval(timer);
    timer = null;
  }
}

function renderProgress(job) {
  const body = document.getElementById("export-body");
  clear(body);
  const pct = Math.round((job.progress || 0) * 100);
  body.appendChild(el("div", { class: "export-progress" }, [
    el("div", { class: "export-bar" }, [
      el("div", { class: "export-fill " + job.status, style: { width: pct + "%" } }),
    ]),
    el("div", { class: "export-stat" },
      `${job.status} · ${pct}%` +
      (job.size ? ` · ${fmtBytes(job.size)}` : "") +
      (job.speed ? ` · ${job.speed}x` : "") +
      (job.eta ? ` · 剩余 ${job.eta}s` : "")),
  ]));
  if (job.error) {
    body.appendChild(el("pre", { class: "export-error" }, job.error));
  }
  if (job.warnings && job.warnings.length) {
    body.appendChild(el("div", { class: "export-warn-title" }, "告警"));
    body.appendChild(el("ul", { class: "export-warns" },
      job.warnings.map((w) => el("li", {}, w))));
  }
  const info = document.getElementById("export-info");
  if (info) {
    info.textContent =
      job.status === "done" ? "已完成：" + (job.output || "") :
      job.status === "failed" ? "失败" :
      job.status === "cancelled" ? "已取消" : "渲染中…";
  }
  syncButtons(job);
}

// 统一同步按钮 / 定时器状态：无论来自 poll 还是 socket，终态都保持一致
function syncButtons(job) {
  const btn = document.getElementById("export-go");
  const cancelBtn = document.getElementById("export-cancel");
  const terminal = ["done", "failed", "cancelled"].includes(job.status);
  if (terminal) {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    if (btn) {
      btn.disabled = false;
      btn.textContent = job.status === "done" ? "下载" : "重试";
      btn.onclick = job.status === "done" ? download : start;
    }
    if (cancelBtn) {
      cancelBtn.textContent = "关闭";
      cancelBtn.onclick = close;
    }
  } else {
    if (btn) {
      btn.disabled = true;
      btn.textContent = "导出中…";
    }
    if (cancelBtn) {
      cancelBtn.textContent = "取消导出";
      cancelBtn.onclick = cancel;
    }
  }
}

async function cancel() {
  if (lastJob && ["running", "queued"].includes(lastJob.status)) {
    try {
      await api.exportCancel(lastJob.id);
    } catch (e) {
      /* 忽略 */
    }
  }
  close();
}

function download() {
  if (!lastJob) return;
  window.open(api.exportDownloadUrl(lastJob.id), "_blank");
}

export function init() {
  const btn = document.getElementById("btn-export");
  if (btn) {
    btn.onclick = open;
    btn.title = "导出视频";
  }
  bus.on("agent:event", (p) => {
    if (p.kind === "export.progress" && jobId && p.id === jobId) {
      lastJob = p;
      renderProgress(p);
    }
  });
}


// __EOF__