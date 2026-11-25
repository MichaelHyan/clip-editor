# -*- coding: utf-8 -*-
"""媒体探测 / 代理转码 / 缩略图 / 波形。"""
import hashlib
import json
import os
import time
import shutil
import subprocess
import threading
from typing import Any, Dict, List, Optional, Tuple

from . import config as cfg

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv",
             ".mpg", ".mpeg", ".ts", ".m2ts"}
AUDIO_EXT = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".opus", ".wma",
             ".aiff", ".aif"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}

_LOCK = threading.RLock()
_PROBE_MEM: Dict[str, Dict[str, Any]] = {}
_PROXY_JOBS: Dict[str, str] = {}

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def kind_of(path: str) -> str:
    ext = os.path.splitext(str(path).lower())[1]
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return "unknown"


# 浏览器（Chromium/Edge/Firefox）可解码的编码
BROWSER_VIDEO_CODECS = {"h264", "vp8", "vp9", "av1", "theora"}
BROWSER_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis", "flac"}


def browser_compat(info: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """判断浏览器能否直接解码该媒体。返回 (是否兼容, 原因列表)。"""
    issues: List[str] = []
    if not info or not info.get("ok"):
        return False, ["媒体探测失败"]

    kind = info.get("kind")
    if kind == "image":
        return True, []

    if info.get("has_video"):
        vc = str(info.get("video_codec") or "").lower()
        if vc and vc not in BROWSER_VIDEO_CODECS:
            issues.append("视频编码 %s 浏览器不支持" % vc)
        pix = str(info.get("pix_fmt") or "").lower()
        if pix and pix not in ("yuv420p", "yuvj420p", "nv12"):
            issues.append("像素格式 %s 浏览器不支持" % pix)

    if info.get("has_audio"):
        ac = str(info.get("audio_codec") or "").lower()
        if ac and ac not in BROWSER_AUDIO_CODECS:
            issues.append("音频编码 %s 浏览器不支持" % ac)

    return (len(issues) == 0), issues


def decorate(info: Dict[str, Any]) -> Dict[str, Any]:
    """补齐 browser_ok / browser_issues（兼容旧缓存，不落盘也能算）。"""
    if not isinstance(info, dict) or not info.get("ok"):
        return info
    ok, issues = browser_compat(info)
    info["browser_ok"] = ok
    info["browser_issues"] = issues
    return info


def _run(cmd: List[str], timeout: int = 300) -> Dict[str, Any]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=_NO_WINDOW)
        return {"rc": p.returncode,
                "out": p.stdout.decode("utf-8", "replace"),
                "err": p.stderr.decode("utf-8", "replace")}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "out": "", "err": "timeout"}
    except FileNotFoundError:
        return {"rc": -2, "out": "", "err": f"未找到可执行文件: {cmd[0]}"}
    except OSError as e:
        return {"rc": -3, "out": "", "err": str(e)}


def _fingerprint(path: str) -> str:
    try:
        st = os.stat(path)
        raw = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        raw = f"{path}|0|0"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cache_file(*parts: str) -> str:
    p = os.path.join(cfg.cache_dir(), *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p.replace("\\", "/")


def _parse_rate(s: Optional[str]) -> float:
    if not s or s in ("0/0", "N/A"):
        return 0.0
    if "/" in s:
        try:
            a, b = s.split("/")
            b = float(b)
            return float(a) / b if b else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def probe(path: str, use_cache: bool = True) -> Dict[str, Any]:
    path = str(path).replace("\\", "/")
    if not os.path.isfile(path):
        return {"ok": False, "error": "文件不存在", "path": path}
    fp = _fingerprint(path)
    with _LOCK:
        if use_cache and fp in _PROBE_MEM:
            return decorate(_PROBE_MEM[fp])
    cache_path = _cache_file("probe", fp + ".json")
    if use_cache and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data = decorate(data)
            with _LOCK:
                _PROBE_MEM[fp] = data
            return data
        except (OSError, ValueError):
            pass

    r = _run([FFPROBE, "-v", "error", "-print_format", "json",
              "-show_format", "-show_streams", path], timeout=60)
    info: Dict[str, Any] = {
        "ok": False, "path": path, "name": os.path.basename(path),
        "kind": kind_of(path), "fingerprint": fp,
    }
    try:
        info["size"] = os.path.getsize(path)
    except OSError:
        info["size"] = 0

    if r["rc"] != 0:
        info["error"] = (r["err"] or "ffprobe 失败").strip()[:500]
        if info["kind"] == "image":
            info.update({"ok": True, "duration": None, "has_video": True,
                         "has_audio": False, "width": 0, "height": 0, "fps": 0.0})
        return info

    try:
        raw = json.loads(r["out"] or "{}")
    except ValueError:
        info["error"] = "ffprobe 输出解析失败"
        return info

    fmt = raw.get("format") or {}
    streams = raw.get("streams") or []
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    as_ = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = _parse_float(fmt.get("duration"), 0.0)
    if duration <= 0.0:
        for s in streams:
            d = _parse_float(s.get("duration"), 0.0)
            if d > 0:
                duration = max(duration, d)
    if duration <= 0.0 and info["kind"] == "image":
        duration = None

    info.update({
        "ok": True,
        "duration": duration if duration and duration > 0 else None,
        "bitrate": int(_parse_float(fmt.get("bit_rate"), 0.0)),
        "format": fmt.get("format_name", ""),
        "has_video": vs is not None,
        "has_audio": as_ is not None,
        "width": int(vs.get("width", 0)) if vs else 0,
        "height": int(vs.get("height", 0)) if vs else 0,
        "fps": _parse_rate((vs or {}).get("r_frame_rate") or (vs or {}).get("avg_frame_rate")),
        "video_codec": (vs or {}).get("codec_name"),
        "pix_fmt": (vs or {}).get("pix_fmt"),
        "sample_rate": int(_parse_float((as_ or {}).get("sample_rate"), 0)),
        "channels": int((as_ or {}).get("channels", 0) or 0),
        "audio_codec": (as_ or {}).get("codec_name"),
    })

    rotation = 0
    for s in streams:
        for sd in (s.get("side_data_list") or []):
            if "rotation" in sd:
                rotation = int(_parse_float(sd.get("rotation"), 0))
    if rotation:
        info["rotation"] = rotation
        if abs(rotation) % 180 == 90 and info["width"] and info["height"]:
            info["width"], info["height"] = info["height"], info["width"]

    if info["kind"] == "unknown":
        info["kind"] = "video" if info["has_video"] else ("audio" if info["has_audio"] else "unknown")

    info = decorate(info)
    with _LOCK:
        _PROBE_MEM[fp] = info
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return info


def thumbnail(path: str, t: float = 0.0, width: int = 320) -> Optional[str]:
    path = str(path).replace("\\", "/")
    if not os.path.isfile(path):
        return None
    fp = _fingerprint(path)
    tw = max(64, min(int(width), 1920))
    out = _cache_file("thumb", f"{fp}_{int(t * 1000)}_{tw}.jpg")
    if os.path.isfile(out):
        return out
    kind = kind_of(path)
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    if kind != "image" and t > 0:
        cmd += ["-ss", f"{max(0.0, float(t)):.3f}"]
    cmd += ["-i", path, "-frames:v", "1",
            "-vf", f"scale={tw}:-2:force_original_aspect_ratio=decrease",
            "-q:v", "4", out]
    r = _run(cmd, timeout=60)
    if r["rc"] != 0 or not os.path.isfile(out):
        return None
    return out


def thumbnail_bytes(path: str, t: float = 0.0, width: int = 320) -> Optional[bytes]:
    p = thumbnail(path, t, width)
    if not p:
        return None
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


def waveform(path: str, buckets: int = 1200, sample_rate: int = 4000) -> List[float]:
    path = str(path).replace("\\", "/")
    if not os.path.isfile(path):
        return []
    fp = _fingerprint(path)
    n = max(16, min(int(buckets), 20000))
    out = _cache_file("wave", f"{fp}_{n}.json")
    if os.path.isfile(out):
        try:
            with open(out, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    info = probe(path)
    if not info.get("has_audio"):
        return []
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", path,
           "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=120,
                           creationflags=_NO_WINDOW)
        raw = p.stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    if not raw:
        return []
    total = len(raw) // 2
    if total <= 0:
        return []
    step = max(1, total // n)
    peaks: List[float] = []
    for i in range(0, total, step):
        chunk = raw[i * 2:(i + step) * 2]
        peak = 0
        for j in range(0, len(chunk) - 1, 2):
            v = chunk[j] | (chunk[j + 1] << 8)
            if v >= 32768:
                v -= 65536
            av = -v if v < 0 else v
            if av > peak:
                peak = av
        peaks.append(round(peak / 32768.0, 4))
        if len(peaks) >= n:
            break
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(peaks, f)
    except OSError:
        pass
    return peaks


def _meta_path(out: str) -> str:
    return out + ".meta"


def _mtime(path: str) -> float:
    try:
        return round(os.path.getmtime(path), 3)
    except OSError:
        return 0.0


def proxy_meta(out: str) -> Dict[str, Any]:
    """读代理的完成标记。没有有效标记 = 代理不可用（可能是半成品）。"""
    try:
        with open(_meta_path(out), "r", encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def _meta_ok(out: str, src: str = "") -> bool:
    """代理是否真正可用：必须有完成标记，且源文件没变。"""
    m = proxy_meta(out)
    if not (m.get("short_gop") or m.get("audio_only")):
        return False
    if src and m.get("source_size") is not None:
        try:
            if int(m["source_size"]) != os.path.getsize(src):
                return False
        except (OSError, TypeError, ValueError):
            return False
    return True


def _write_meta(out: str, payload: Dict[str, Any]) -> None:
    tmp = _meta_path(out) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, _meta_path(out))
    except OSError:
        pass


def _lock_path(out: str) -> str:
    return out + ".lock"


def _acquire_lock(out: str) -> bool:
    """跨进程转码锁（O_EXCL 创建，2 小时视为过期）。"""
    lp = _lock_path(out)
    try:
        if os.path.isfile(lp):
            if time.time() - os.path.getmtime(lp) < 7200:
                return False
            os.remove(lp)
    except OSError:
        pass
    try:
        fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, ("%d %s" % (os.getpid(),
                                 time.strftime("%Y-%m-%d %H:%M:%S"))).encode())
        os.close(fd)
        return True
    except OSError:
        return False


def _release_lock(out: str) -> None:
    try:
        os.remove(_lock_path(out))
    except OSError:
        pass


def proxy_path(path: str) -> str:
    fp = _fingerprint(path)
    return _cache_file("proxy", f"{fp}.mp4")


def proxy_audio_path(path: str) -> str:
    fp = _fingerprint(path)
    return _cache_file("proxy", f"{fp}.m4a")


def proxy_state(path: str, audio: Optional[bool] = None) -> Dict[str, Any]:
    """代理就绪状态。

    只认「主文件 + .meta 完成标记」同时存在。早期版本只看文件存在，
    导致转码中的半成品被当成可用代理 —— `-movflags +faststart` 要等
    转码结束才回填 moov atom，半成品不是有效 mp4，前端必然解码失败。
    """
    info = probe(path)
    kind = info.get("kind")
    use_audio = bool(audio) if audio is not None else (kind == "audio")
    p = proxy_audio_path(path) if use_audio else proxy_path(path)
    exists = os.path.isfile(p)
    ready = exists and _meta_ok(p, path)
    return {"ready": ready, "path": p,
            "size": os.path.getsize(p) if exists else 0,
            "meta": proxy_meta(p) if ready else {},
            "audio_only": use_audio,
            "browser_ok": bool(info.get("browser_ok", True)),
            "issues": info.get("browser_issues") or [],
            "kind": kind}




def ensure_audio_proxy(path: str, force: bool = False) -> Dict[str, Any]:
    """只抽音轨成 .m4a 音频代理。

    视频代理要整段 4GB 转完才能用（几十分钟），而音频只需 `-vn` 抽一条
    aac 轨（源本身是 aac 时直接 `-c:a copy`，十几秒）。前端拿这个小文件
    才能完整下载并 decodeAudioData。
    """
    path = str(path).replace("\\", "/")
    if not os.path.isfile(path):
        return {"ready": False, "path": None, "status": "missing-source"}
    info = probe(path)
    if not info.get("has_audio"):
        return {"ready": False, "path": None, "status": "no-audio",
                "original": path}
    out = proxy_audio_path(path)
    if not force and os.path.isfile(out) and _meta_ok(out, path):
        return {"ready": True, "path": out, "status": "cached",
                "size": os.path.getsize(out), "original": path}
    if not _acquire_lock(out):
        return {"ready": False, "path": out, "status": "running",
                "original": path}
    tmp = out + ".part.m4a"
    try:
        acodec = str(info.get("audio_codec") or "").lower()
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-vn"]
        if acodec in ("aac", "mp3"):
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        cmd += ["-movflags", "+faststart", tmp]
        r = _run(cmd, timeout=3600)
        if r["rc"] != 0 or not os.path.isfile(tmp):
            return {"ready": False, "path": None, "status": "failed",
                    "error": (r["err"] or "")[:400], "original": path}
        os.replace(tmp, out)
        _write_meta(out, {"audio_only": True,
                          "source_size": info.get("size"),
                          "source_mtime": _mtime(path),
                          "created": _fingerprint(path)})
        return {"ready": True, "path": out, "status": "created",
                "size": os.path.getsize(out), "original": path}
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        _release_lock(out)


def ensure_proxy(path: str, force: bool = False) -> Dict[str, Any]:
    path = str(path).replace("\\", "/")
    c = cfg.get("proxy", {}) or {}
    if not c.get("enabled", True):
        return {"ready": False, "path": None, "status": "disabled"}
    if not os.path.isfile(path):
        return {"ready": False, "path": None, "status": "missing-source"}
    info = probe(path)
    kind = info.get("kind")
    if kind == "image":
        return {"ready": False, "path": None, "status": "image-no-proxy", "original": path}

    browser_ok = bool(info.get("browser_ok", True))
    need_by_codec = not browser_ok

    if kind == "audio":
        return ensure_audio_proxy(path, force)

    size_mb = (info.get("size") or 0) / (1024 * 1024)
    if not force and not need_by_codec and size_mb < float(c.get("threshold_mb", 40)):
        return {"ready": False, "path": None, "status": "below-threshold", "original": path}

    out = proxy_path(path)
    if not force and os.path.isfile(out) and _meta_ok(out, path):
        return {"ready": True, "path": out, "status": "cached",
                "size": os.path.getsize(out), "original": path}

    with _LOCK:
        if out in _PROXY_JOBS.values():
            return {"ready": False, "path": out, "status": "running", "original": path}
        _PROXY_JOBS[out] = out
    if not _acquire_lock(out):
        with _LOCK:
            _PROXY_JOBS.pop(out, None)
        return {"ready": False, "path": out, "status": "running", "original": path}

    # 写 .part 临时文件，成功后原子改名：半成品永远不会被 proxy_state 看到
    tmp = out + ".part.mp4"
    try:
        max_h = int(c.get("max_height", 720))
        if info.get("has_video"):
            vf = f"scale=-2:'min({max_h},ih)'"
        else:
            vf = None
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", path]
        if vf:
            cmd += ["-vf", vf]
        # 关键帧间隔必须短：浏览器 seek 到非关键帧位置时解码器给不出帧，
        # 默认 250 帧（≈10s@25fps）会导致拖动播放头时大面积黑屏。
        # 这里强制每秒 1 个关键帧，并禁用场景切换自适应。
        fps_hint = max(12, min(120, int(round(float(info.get("fps") or 25)))))
        cmd += ["-c:v", "libx264", "-preset", str(c.get("preset", "veryfast")),
                "-crf", str(c.get("crf", 28)),
                "-pix_fmt", "yuv420p",
                "-g", str(fps_hint), "-keyint_min", str(fps_hint),
                "-sc_threshold", "0"]
        if info.get("has_audio"):
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart", tmp]
        r = _run(cmd, timeout=3600)
        if r["rc"] != 0 or not os.path.isfile(tmp):
            return {"ready": False, "path": None, "status": "failed",
                    "error": (r["err"] or "")[:400], "original": path}
        os.replace(tmp, out)
        _write_meta(out, {"short_gop": True, "fps": fps_hint,
                          "source_size": info.get("size"),
                          "source_mtime": _mtime(path),
                          "created": _fingerprint(path)})
        return {"ready": True, "path": out, "status": "created",
                "size": os.path.getsize(out), "original": path}
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        _release_lock(out)
        with _LOCK:
            _PROXY_JOBS.pop(out, None)


def playback_source(path: str) -> Dict[str, Any]:
    st = proxy_state(path)
    if st["ready"]:
        return {"url_path": st["path"], "proxy": True}
    return {"url_path": path, "proxy": False}


def scan_dir(path: str, limit: int = 500) -> Dict[str, Any]:
    path = os.path.normpath(str(path).replace("\\", "/")).replace("\\", "/")
    if not os.path.isdir(path):
        return {"ok": False, "error": "目录不存在", "path": path, "items": []}
    items = []
    try:
        with os.scandir(path) as it:
            for e in it:
                if len(items) >= limit:
                    break
                try:
                    is_dir = e.is_dir()
                except OSError:
                    continue
                name = e.name
                if name.startswith("."):
                    continue
                ext = os.path.splitext(name.lower())[1]
                if is_dir:
                    items.append({"name": name, "path": e.path.replace("\\", "/"),
                                  "type": "dir", "kind": "dir"})
                elif ext in VIDEO_EXT or ext in AUDIO_EXT or ext in IMAGE_EXT:
                    try:
                        size = e.stat().st_size
                    except OSError:
                        size = 0
                    items.append({"name": name, "path": e.path.replace("\\", "/"),
                                  "type": "file", "kind": kind_of(name), "size": size})
    except OSError as e:
        return {"ok": False, "error": str(e), "path": path, "items": []}
    items.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    parent = os.path.dirname(path).replace("\\", "/")
    return {"ok": True, "path": path, "parent": parent if parent != path else None,
            "items": items}


# __EOF__