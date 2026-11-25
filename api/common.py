# -*- coding: utf-8 -*-
"""API 公共工具：统一响应、异常包装、路径校验。"""
import functools
import json
import os
import traceback
from typing import Any, Callable, Dict, Optional, Tuple

from flask import jsonify, request

from core import config as cfg
from core import workspace


def ok(data: Any = None, **extra: Any) -> Any:
    payload: Dict[str, Any] = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return jsonify(payload)


def err(message: str, code: int = 400, **extra: Any) -> Any:
    payload: Dict[str, Any] = {"ok": False, "error": str(message)}
    payload.update(extra)
    resp = jsonify(payload)
    resp.status_code = code
    return resp


def body() -> Dict[str, Any]:
    if request.is_json:
        d = request.get_json(silent=True)
        return d if isinstance(d, dict) else {}
    raw = request.form.get("payload")
    if raw:
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}
    return {}


def arg(name: str, default: Any = None) -> Any:
    v = request.args.get(name, None)
    return default if v is None else v


def argf(name: str, default: float = 0.0) -> float:
    try:
        return float(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


def argi(name: str, default: int = 0) -> int:
    try:
        return int(float(request.args.get(name, default)))
    except (TypeError, ValueError):
        return default


def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def inner(*a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except KeyError as e:
            return err("未找到: " + str(e), 404)
        except PermissionError as e:
            return err("路径不在白名单: " + str(e), 403)
        except ValueError as e:
            return err(str(e), 400)
        except Exception as e:
            traceback.print_exc()
            return err("服务端异常: " + str(e), 500)
    return inner


def ws_or_404(pid: str) -> Tuple[Optional[Any], Any]:
    ws = workspace.get(pid)
    if ws is None:
        return None, err("项目不存在: " + pid, 404)
    return ws, None


def _in_data_dir(path: str) -> bool:
    data = os.path.join(cfg.ROOT, "data")
    try:
        real = os.path.realpath(path).replace("\\", "/").lower()
        base = os.path.realpath(data).replace("\\", "/").lower().rstrip("/")
    except OSError:
        return False
    return real == base or real.startswith(base + "/")


def check_path(path: Optional[str]) -> str:
    if not path:
        raise PermissionError("空路径")
    p = os.path.normpath(str(path).replace("\\", "/")).replace("\\", "/")
    if _in_data_dir(p):
        return p
    if cfg.is_media_allowed(p):
        return p
    raise PermissionError(p)


def client_id() -> Optional[str]:
    b = body()
    cid = b.get("client_id") or request.headers.get("X-Client-Id") or request.args.get("client_id")
    return str(cid) if cid else None


# __EOF__