// 音频处理：WSOLA 时间拉伸 + 变调不变速
// 背景：
//   Web Audio 的 detune 和 playbackRate 本质都是「重采样倍率」，
//   设了其中一个再设另一个会相乘，无法独立控制音高和速度。
//   要实现「变调不变速」和「变速不变调」，必须做时间拉伸。
//
// 实现：
//   - WSOLA（Waveform Similarity Overlap-Add）：帧重叠相加 + 相似度搜索，
//     质量优于朴素 OLA，没有明显相位不连续
//   - 变调 = 重采样到 r 倍密度（音高 r 倍、时长 1/r）+ WSOLA 拉伸回原长
//   - 变速 = WSOLA 拉伸/压缩（音高不变）

const FRAME = 2048;
const HANN = (() => {
  const w = new Float32Array(FRAME);
  for (let i = 0; i < FRAME; i++) {
    w[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / FRAME);
  }
  return w;
})();

// WSOLA 时间拉伸：把 channels 拉成 targetLen 帧，音高不变
// channels: Float32Array[]，返回同结构
function wsolaStretch(channels, targetLen) {
  const numCh = channels.length;
  const inLen = channels[0].length;
  if (inLen < FRAME * 2 || targetLen < FRAME * 2) {
    return channels.map((s) => resampleLinear(s, targetLen));
  }

  const ratio = targetLen / inLen;
  const hopIn = FRAME >> 1;
  const hopOut = Math.max(1, Math.round(hopIn * ratio));
  // 搜索半径：hopIn 的 25%，保证能找到高相似度帧
  const search = Math.max(1, Math.min(hopIn - 1, Math.round(hopIn * 0.25)));

  const out = [];
  for (let c = 0; c < numCh; c++) out.push(new Float32Array(targetLen));

  let inPos = 0;
  let outPos = 0;
  let prevBest = 0;

  while (outPos + FRAME <= targetLen && inPos + FRAME <= inLen) {
    // 在 [inPos-search, inPos+search] 内找与「上次最佳位置 + hopIn」最相似的帧
    let bestPos = inPos;
    const refPos = prevBest + hopIn;
    if (refPos + FRAME <= inLen) {
      let bestCorr = -Infinity;
      const ref = channels[0];
      const cur = channels[0];
      for (let off = -search; off <= search; off++) {
        const p = inPos + off;
        if (p < 0 || p + FRAME > inLen) continue;
        let corr = 0;
        // 步长 8 采样足够判断相似度，速度提升 8 倍
        for (let i = 0; i < FRAME; i += 8) {
          corr += ref[refPos + i] * cur[p + i];
        }
        if (corr > bestCorr) {
          bestCorr = corr;
          bestPos = p;
        }
      }
    }

    for (let c = 0; c < numCh; c++) {
      const src = channels[c];
      const dst = out[c];
      for (let i = 0; i < FRAME; i++) {
        const di = outPos + i;
        if (di >= targetLen) break;
        dst[di] += src[bestPos + i] * HANN[i];
      }
    }

    prevBest = bestPos;
    inPos += hopIn;
    outPos += hopOut;
  }

  // 边界处理：末尾剩余直接用线性重采样补齐，避免静音尾巴
  if (outPos < targetLen) {
    const tailSrc = channels.map((s) => s.subarray(Math.min(inLen - 1, inPos)));
    const tailLen = targetLen - outPos;
    for (let c = 0; c < numCh; c++) {
      const r = resampleLinear(tailSrc[c], tailLen);
      out[c].set(r.subarray(0, Math.min(tailLen, r.length)), outPos);
    }
  }
  return out;
}

// 线性插值重采样到指定长度（会同时改变音高和时长，仅用于变调的第二步之前）
function resampleLinear(src, targetLen) {
  const out = new Float32Array(targetLen);
  const srcLen = src.length;
  if (srcLen === 0) return out;
  const step = srcLen / targetLen;
  for (let i = 0; i < targetLen; i++) {
    const pos = i * step;
    const i0 = Math.floor(pos);
    const i1 = Math.min(srcLen - 1, i0 + 1);
    const f = pos - i0;
    out[i] = src[i0] * (1 - f) + src[i1] * f;
  }
  return out;
}

// 从 AudioBuffer 提取声道数据
export function extractChannels(buffer) {
  const out = [];
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    out.push(new Float32Array(buffer.getChannelData(c)));
  }
  return out;
}

// 核心：按 speed 与 semitones 处理声道数据
//   返回 { channels, changed }，changed 表示是否需要重建 buffer
export function processChannels(channels, speed, semitones) {
  let ch = channels;
  let changed = false;

  // ---- 第一步：变调（音高 semitones 半音，时长不变）
  if (Math.abs(semitones) > 0.01) {
    const r = Math.pow(2, semitones / 12);
    const origLen = ch[0].length;
    // 重采样到 1/r 长度：音高变 r 倍，时长变 1/r
    const resampled = ch.map((s) => resampleLinear(s, Math.max(FRAME * 2, Math.floor(origLen / r))));
    // 拉伸回原长：时长恢复，音高保持 r 倍
    ch = wsolaStretch(resampled, origLen);
    changed = true;
  }

  // ---- 第二步：变速（speed 倍，音高不变）
  if (Math.abs(speed - 1) > 0.01) {
    const origLen = ch[0].length;
    const targetLen = Math.max(FRAME * 2, Math.floor(origLen / speed));
    ch = wsolaStretch(ch, targetLen);
    changed = true;
  }

  return { channels: ch, changed };
}

// 把处理后的声道数据写进新的 AudioBuffer
export function buildBuffer(ctx, channels, sampleRate) {
  const numCh = channels.length;
  const len = channels[0].length;
  const buf = ctx.createBuffer(numCh, len, sampleRate);
  for (let c = 0; c < numCh; c++) {
    buf.copyToChannel(channels[c], c);
  }
  return buf;
}

// 处理结果缓存键
export function cacheKey(cid, speed, semitones) {
  return `${cid}|s${speed.toFixed(3)}|p${semitones.toFixed(2)}`;
}

// __EOF__