// WebAudio 播放（WSOLA 变调不变速 / 变速不变调）
// 核心思路：
//   Web Audio 的 detune 和 playbackRate 本质都是重采样倍率，无法独立控制。
//   所以把「音高 + 速度」一次性预处理进 AudioBuffer：
//     - 变调：重采样 + WSOLA 拉伸回原长（音高变、时长不变）
//     - 变速：WSOLA 拉伸（时长变、音高不变）
//   处理后的 buffer 精确代表片段的整个时间轴区间，播放时 rate 固定 1.0，
//   offset 就是墙钟时间，不会再有"音高越高放越快"的耦合。

import { state, bus, tracks, clipsOfTrack, clipStart, clipDuration, resolveClipAt } from "./state.js";
import { mediaUrl } from "./api.js";
import { dbToLinear, clamp } from "./util.js";
import { processChannels, buildBuffer } from "./audio_process.js";

const LONG_THRESHOLD = 600;

let actx = null;
let masterGain = null;
let analyser = null;

const bufferCache = new Map();     // cid -> AudioBuffer（完整解码）
const processedCache = new Map();  // procKey -> AudioBuffer（片段范围 + 音高速度已应用）
const decoding = new Map();
const processing = new Map();

let activeSources = [];
let playStartAbs = 0;
let playStartCtx = 0;
let playing = false;
let paramTimer = null;

// ---------------------------------------------------------------- 上下文

function ensureCtx() {
  if (actx) return actx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  actx = new AC({ sampleRate: (state.project && state.project.settings && state.project.settings.sample_rate) || 48000 });
  masterGain = actx.createGain();
  masterGain.gain.value = 1.0;
  analyser = actx.createAnalyser();
  analyser.fftSize = 256;
  masterGain.connect(analyser);
  analyser.connect(actx.destination);
  return actx;
}

async function fetchArrayBuffer(path) {
  const res = await fetch(mediaUrl(path, true));
  if (!res.ok) throw new Error("音频流获取失败 " + res.status);
  return await res.arrayBuffer();
}

async function decodeFull(cid) {
  if (bufferCache.has(cid)) return bufferCache.get(cid);
  if (decoding.has(cid)) return decoding.get(cid);

  const c = state.clips[cid];
  if (!c) return null;
  const path = c.media && c.media.path;
  const kind = (c.media && c.media.kind) || "video";
  const probe = (c.media && c.media.probe) || {};
  if (kind === "image") return null;
  if (!probe.has_audio && kind !== "audio") return null;

  const p = (async () => {
    try {
      const ac = ensureCtx();
      const raw = await fetchArrayBuffer(path);
      const buf = await ac.decodeAudioData(raw);
      bufferCache.set(cid, buf);
      return buf;
    } catch (e) {
      console.warn("[audio] 解码失败", cid, e);
      return null;
    } finally {
      decoding.delete(cid);
    }
  })();
  decoding.set(cid, p);
  return p;
}

// ---------------------------------------------------------------- 预处理

function procKey(cid, speed, semitones) {
  return `${cid}|${speed.toFixed(4)}|${semitones.toFixed(3)}`;
}

// 排除掉过期键（同 cid 但不同 speed/pitch）
function evictProcessed(cid, keepKey) {
  for (const k of processedCache.keys()) {
    if (k.startsWith(cid + "|") && k !== keepKey) processedCache.delete(k);
  }
}

async function getProcessedBuffer(cid) {
  const c = state.clips[cid];
  if (!c) return null;
  const rp = resolveClipAt(cid, 0);
  if (!rp) return null;
  const speed = clamp(rp.audio.speed || 1, 0.1, 8);
  const semitones = clamp(rp.audio.pitch || 0, -24, 24);
  const key = procKey(cid, speed, semitones);

  if (processedCache.has(key)) return processedCache.get(key);
  if (processing.has(key)) return processing.get(key);

  const p = (async () => {
    const full = await decodeFull(cid);
    if (!full) return null;

    const probe = (c.media && c.media.probe) || {};
    const srcIn = rp.audio.srcIn != null ? rp.audio.srcIn : 0;
    let srcOut = rp.audio.srcOut;
    if (srcOut == null) srcOut = probe.duration || full.duration;

    const sr = full.sampleRate;
    const s0 = Math.max(0, Math.min(full.length - 1, Math.floor(srcIn * sr)));
    const s1 = Math.max(s0 + 1, Math.min(full.length, Math.floor(srcOut * sr)));

    // 抽取片段范围的声道数据（slice 复制，避免影响原 buffer）
    const chans = [];
    for (let ch = 0; ch < full.numberOfChannels; ch++) {
      chans.push(full.getChannelData(ch).slice(s0, s1));
    }

    // WSOLA 处理：变调 + 变速
    const { channels } = processChannels(chans, speed, semitones);
    const buf = buildBuffer(actx, channels, sr);

    evictProcessed(cid, key);
    processedCache.set(key, buf);
    return buf;
  })();

  processing.set(key, p);
  try {
    return await p;
  } finally {
    processing.delete(key);
  }
}

// ---------------------------------------------------------------- 播放控制

function stopAllSources() {
  for (const a of activeSources) {
    try {
      a.src.stop();
    } catch (e) {
      /* 忽略 */
    }
    for (const n of [a.src, a.gain, a.pan]) {
      if (!n) continue;
      try {
        n.disconnect();
      } catch (e) {
        /* 忽略 */
      }
    }
  }
  activeSources = [];
  if (paramTimer) {
    clearInterval(paramTimer);
    paramTimer = null;
  }
}

// 播放中只更新音量/声像（音高速度变了需要重排，不在 60ms 周期内处理）
function updateActiveParams() {
  if (!playing || !actx) return;
  for (const a of activeSources) {
    const rp = resolveClipAt(a.cid, a.whenAbs);
    if (!rp) continue;
    const db = rp.audio.gain || 0;
    const target = dbToLinear(db) * (rp.audio.enabled ? 1 : 0);
    try {
      a.gain.gain.setTargetAtTime(target, actx.currentTime, 0.02);
    } catch (e) {
      a.gain.gain.value = target;
    }
    if (a.pan) {
      const pv = clamp(rp.audio.pan || 0, -1, 1);
      try {
        a.pan.pan.setTargetAtTime(pv, actx.currentTime, 0.02);
      } catch (e) {
        a.pan.pan.value = pv;
      }
    }
  }
}

async function scheduleAll(fromAbsT) {
  const ac = ensureCtx();
  if (!ac) return;
  stopAllSources();
  if (!state.project) return;

  const audioTracks = tracks().filter(
    (t) => (t.type === "audio" || t.type === "video") && !t.muted);
  const baseCtxTime = ac.currentTime + 0.05;
  playStartAbs = fromAbsT;
  playStartCtx = baseCtxTime;

  for (const tr of audioTracks) {
    for (const c of clipsOfTrack(tr.id)) {
      const s = clipStart(c.id);
      const dur = clipDuration(c.id);
      if (s + dur <= fromAbsT) continue;

      const cid = c.id;
      const clipPlayFrom = Math.max(0, fromAbsT - s);
      const whenAbs = s + clipPlayFrom;
      const delay = whenAbs - fromAbsT;
      const when = baseCtxTime + delay;

      const rp = resolveClipAt(cid, whenAbs);
      if (!rp || !rp.audio.enabled) continue;

      const buf = await getProcessedBuffer(cid);
      if (!buf) continue;

      const gainDb = rp.audio.gain || 0;
      const pan = clamp(rp.audio.pan || 0, -1, 1);
      const semitones = rp.audio.pitch || 0;
      const speed = rp.audio.speed || 1;

      const src = ac.createBufferSource();
      src.buffer = buf;
      src.playbackRate.value = 1.0;   // 处理时已应用音高与速度
      src.detune.value = 0;

      const g = ac.createGain();
      g.gain.value = dbToLinear(gainDb);

      let panner = null;
      if (ac.createStereoPanner) {
        panner = ac.createStereoPanner();
        panner.pan.value = pan;
        src.connect(g).connect(panner).connect(masterGain);
      } else {
        src.connect(g).connect(masterGain);
      }

      // 处理后的 buffer 精确代表 [srcIn, srcOut]，时长 = 片段时长
      // 所以 offset 直接用墙钟时间 clipPlayFrom
      const offset = Math.max(0, Math.min(clipPlayFrom, buf.duration - 0.01));
      const remain = Math.max(0.01, Math.min(dur - clipPlayFrom, buf.duration - offset));

      try {
        src.start(when, offset, remain);
      } catch (e) {
        continue;
      }
      activeSources.push({
        src, gain: g, pan: panner, cid, whenAbs, speed, semitones,
      });
    }
  }
}

export async function play(fromAbsT) {
  const ac = ensureCtx();
  if (!ac) return;
  if (ac.state === "suspended") await ac.resume();
  playing = true;
  await scheduleAll(fromAbsT);
  if (paramTimer) clearInterval(paramTimer);
  paramTimer = setInterval(updateActiveParams, 60);
}

export function stop() {
  playing = false;
  stopAllSources();
}

export function isPlaying() {
  return playing;
}

export function currentTime() {
  if (!playing || !actx) return null;
  return playStartAbs + (actx.currentTime - playStartCtx);
}

export function setMasterVolume(v) {
  if (!masterGain) return;
  masterGain.gain.value = clamp(v, 0, 2);
}

export function getAnalyser() {
  return analyser;
}

export function getSpectrum() {
  if (!analyser) return null;
  const arr = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(arr);
  return arr;
}

// 参数（音高/速度）变化需要重排，音量/声像只需就地更新
function detectNeedsReschedule() {
  for (const a of activeSources) {
    const rp = resolveClipAt(a.cid, a.whenAbs);
    if (!rp) continue;
    const sp = clamp(rp.audio.speed || 1, 0.1, 8);
    const se = clamp(rp.audio.pitch || 0, -24, 24);
    if (Math.abs(sp - a.speed) > 0.005 || Math.abs(se - a.semitones) > 0.01) {
      return true;
    }
  }
  return false;
}

export function init() {
  bus.on("playing:changed", (v) => {
    if (v) {
      play(state.playhead);
    } else {
      stop();
    }
  });

  bus.on("playhead:changed", () => {
    if (playing && !state.playing) stop();
  });

  bus.on("clip:changed", () => {
    if (!playing) return;
    if (detectNeedsReschedule()) {
      // 音高/速度变了：重排（处理结果有缓存，很快）
      scheduleAll(currentTime() || state.playhead);
    } else {
      // 只是音量/声像：就地平滑更新
      updateActiveParams();
    }
  });

  if (typeof window !== "undefined") {
    window.__audioDebug = {
      state: () => ({
        playing,
        ctxState: actx ? actx.state : null,
        currentTime: currentTime(),
        masterGain: masterGain ? masterGain.gain.value : null,
        sources: activeSources.map((a) => ({
          cid: a.cid,
          whenAbs: +a.whenAbs.toFixed(3),
          gain: +a.gain.gain.value.toFixed(4),
          pan: a.pan ? +a.pan.pan.value.toFixed(3) : null,
          bufDur: a.src.buffer ? +a.src.buffer.duration.toFixed(3) : null,
          rate: +a.src.playbackRate.value.toFixed(3),
        })),
        processed: [...processedCache.keys()],
      }),
      updateParams: () => updateActiveParams(),
      clearCache: () => {
        processedCache.clear();
      },
    };
  }

  const unlock = () => {
    const ac = ensureCtx();
    if (ac && ac.state === "suspended") ac.resume();
    document.removeEventListener("pointerdown", unlock);
    document.removeEventListener("keydown", unlock);
  };
  document.addEventListener("pointerdown", unlock, { once: true });
  document.addEventListener("keydown", unlock, { once: true });
}

// __EOF__