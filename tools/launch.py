# -*- coding: utf-8 -*-
"""以分离进程方式启动服务（终端关闭后仍存活）。

关键：Windows 允许多个进程绑定同一端口，后绑的会"半隐"在监听队列里，
请求被随机分给其中任意一个。所以启动前必须清理端口上的所有占用进程，
只靠 server.pid 记录是不够的（早期手动启动的进程没被记录）。
"""
import os
import signal
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(BASE, "data", "server.log")
PIDFILE = os.path.join(BASE, "data", "server.pid")

DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000


def _port_from_config():
    try:
        import json
        with open(os.path.join(BASE, "config.json"), encoding="utf-8") as f:
            return int(json.load(f).get("port", 5173))
    except Exception:
        return 5173


def _pids_listening(port):
    """返回占用该端口的 PID 列表（含 LISTENING 与 ESTABLISHED 的对端）。"""
    pids = set()
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             creationflags=CREATE_NO_WINDOW).stdout
    except OSError:
        return pids
    needle = ":%d" % port
    for line in out.splitlines():
        if needle not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        pid = parts[-1]
        if not pid.isdigit():
            continue
        if "LISTENING" in line:
            pids.add(int(pid))
    return pids


def _kill(pid):
    if pid <= 0:
        return False
    try:
        r = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0
    except OSError:
        return False


def cleanup_port(port):
    """清掉端口上的所有监听进程 + pid 文件记录的进程。"""
    killed = []

    # 1) pid 文件
    if os.path.isfile(PIDFILE):
        try:
            pid = int(open(PIDFILE).read().strip())
        except (OSError, ValueError):
            pid = 0
        if pid and _kill(pid):
            killed.append(pid)
            print("已停止 pidfile 记录的进程", pid)

    # 2) 端口监听者（可能包含别人的进程，逐个确认）
    for pid in sorted(_pids_listening(port)):
        if pid in killed:
            continue
        if _kill(pid):
            killed.append(pid)
            print("已停止占用端口 %d 的进程 %d" % (port, pid))

    if killed:
        time.sleep(0.8)
    try:
        if os.path.isfile(PIDFILE):
            os.remove(PIDFILE)
    except OSError:
        pass
    return killed


def main():
    os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
    port = _port_from_config()

    if "--restart" in sys.argv or "--clean" in sys.argv:
        cleanup_port(port)
    else:
        # 即使不显式重启，若端口已被占用也先清，避免双实例
        pids = _pids_listening(port)
        if pids:
            print("端口 %d 已被占用，自动清理: %s" % (port, sorted(pids)))
            cleanup_port(port)

    log = open(LOG, "ab", buffering=0)
    log.write(("\n===== launch %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S")).encode("utf-8"))
    flags = DETACHED_PROCESS | CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.Popen(
        [sys.executable, "-u", os.path.join(BASE, "run.py")],
        cwd=BASE, stdout=log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
    )
    with open(PIDFILE, "w") as f:
        f.write(str(p.pid))
    print("started pid %d on port %d, log -> %s" % (p.pid, port, LOG))


if __name__ == "__main__":
    main()

# __EOF__