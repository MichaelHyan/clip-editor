#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clear_pycache.py —— 自动清除项目内的 Python 缓存

判断规则（按名字自动识别，无需手工维护清单）：
  1) 目录名 == "__pycache__"        -> 整个目录删除
  2) 文件后缀为 .pyc / .pyo         -> 删除该文件
  3) 加 -a/--all-caches 时，额外清理
     .pytest_cache / .mypy_cache / .ruff_cache / .pytype / .ipynb_checkpoints

安全约定：
  * 只删缓存，任何 .py / .pyi 源码都不会被修改或删除
  * test.py 始终忽略（不读、不改、不删）
  * 默认跳过 .git 与虚拟环境、node_modules 等目录（-i 可取消除此限制）
  * 不跟随符号链接
  * 默认先预览再询问；--dry-run 仅预览；-y 直接执行

用法：
  python clear_pycache.py                  # 预览 + 交互确认后删除
  python clear_pycache.py --dry-run        # 只预览，什么都不删
  python clear_pycache.py -y               # 直接删除
  python clear_pycache.py -a -y            # 连同 pytest/mypy 等缓存一起删
  python clear_pycache.py D:\\other\\path -y # 指定其它根目录
  python clear_pycache.py --exclude build  # 额外排除名为 build 的目录
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# ---------- 按名字识别的缓存 ----------
CACHE_DIR_NAMES = {"__pycache__"}
EXTRA_CACHE_DIR_NAMES = {
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".pytype",
    ".ipynb_checkpoints",
}
CACHE_FILE_SUFFIXES = {".pyc", ".pyo"}

# ---------- 默认跳过（安全考虑）----------
SKIP_DIR_NAMES = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules"}
SKIP_FILE_NAMES = {"test.py"}  # 用户指定：忽略这个文件


def find_targets(
    root: Path,
    *,
    include_extra: bool,
    include_all: bool,
    excludes: set[str],
) -> tuple[list[Path], list[Path]]:
    """遍历目录，返回 (待删缓存目录, 待删缓存文件)。"""
    cache_dirs = set(CACHE_DIR_NAMES)
    if include_extra:
        cache_dirs |= EXTRA_CACHE_DIR_NAMES

    skip_dirs = set() if include_all else set(SKIP_DIR_NAMES)
    skip_dirs |= set(excludes)

    dirs: list[Path] = []
    files: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        cur = Path(dirpath)
        kept: list[str] = []

        for name in dirnames:
            full = cur / name
            if name in skip_dirs or full.is_symlink():
                continue
            if name in cache_dirs:
                dirs.append(full)
                continue  # 命中缓存目录，不再深入其内部
            kept.append(name)

        dirnames[:] = kept  # 就地剪枝，控制遍历范围

        for name in filenames:
            if name in SKIP_FILE_NAMES:
                continue
            full = cur / name
            if full.is_symlink():
                continue
            if full.suffix.lower() in CACHE_FILE_SUFFIXES:
                files.append(full)

    return dirs, files


def _on_rm_error(func, path, _exc_info):
    """只读文件兜底：改权限后重试一次。"""
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        pass


def delete_all(dirs: list[Path], files: list[Path]) -> tuple[int, int]:
    removed = failed = 0

    for path in dirs:
        try:
            shutil.rmtree(path, onerror=_on_rm_error)
            removed += 1
        except OSError as exc:
            failed += 1
            print(f"  [失败] 目录 {path}  ({exc})")

    for path in files:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            failed += 1
            print(f"  [失败] 文件 {path}  ({exc})")

    return removed, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="自动清除 Python 缓存（__pycache__ 目录与 *.pyc/*.pyo 文件）"
    )
    parser.add_argument("root", nargs="?", default=".", help="根目录，默认当前目录")
    parser.add_argument("-n", "--dry-run", action="store_true", help="只预览，不删除")
    parser.add_argument("-y", "--yes", action="store_true", help="不再询问，直接删除")
    parser.add_argument(
        "-a", "--all-caches", action="store_true",
        help="额外清理 .pytest_cache / .mypy_cache / .ruff_cache 等目录",
    )
    parser.add_argument(
        "-i", "--include-all", action="store_true",
        help="不跳过 .git / 虚拟环境 / node_modules 等目录",
    )
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="NAME",
        help="额外排除的目录名，可多次指定",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"目录不存在或不是目录：{root}")
        return 2
    root = root.resolve()

    print(f"扫描根目录：{root}")
    dirs, files = find_targets(
        root,
        include_extra=args.all_caches,
        include_all=args.include_all,
        excludes=set(args.exclude),
    )

    total = len(dirs) + len(files)
    if total == 0:
        print("没有发现可清理的 Python 缓存。")
        return 0

    if dirs:
        print(f"\n待删除缓存目录（{len(dirs)} 个）:")
        for p in dirs:
            print(f"  {p}")
    if files:
        print(f"\n待删除缓存文件（{len(files)} 个）:")
        for p in files:
            print(f"  {p}")
    print(f"\n合计 {total} 项")

    if args.dry_run:
        print("（预览模式：未做任何删除）")
        return 0

    if not args.yes:
        try:
            answer = input("确认删除以上内容？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return 1
        if answer not in {"y", "yes", "是"}:
            print("已取消。")
            return 1

    removed, failed = delete_all(dirs, files)
    tail = f"，失败 {failed} 项" if failed else ""
    print(f"\n完成：成功删除 {removed} 项{tail}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())