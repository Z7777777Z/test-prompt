"""原生文件对话框助手（仅用于「本机运行」的调试工具）。

用途：被 tp/views_fs.py 以子进程方式调用，弹出**系统文件资源管理器**的
「打开 / 另存为」对话框，把用户选中的**绝对路径**以 JSON 输出到 stdout。

用法::

    python fsdialog.py open [--title T] [--initialdir D] [--initialfile F] [--filetypes "..."]
    python fsdialog.py save [--title T] [--initialfile F] [--create] [--filetypes "..."]

输出（stdout 最后一行 JSON）::

    {"ok": true,  "path": "D:/data/x.db", "created": false}
    {"ok": false, "canceled": true}
    {"ok": false, "error": "..."}

说明：
- 本文件**不在模块顶层导入 tkinter**，因此任何 Python 都能启动它（找不到 tkinter
  时返回结构化错误，而不是抛栈）。
- `--create`：选定路径不存在时创建一个空文件（SQLite 的空文件即为合法空库）。
- `TP_FSDIALOG_MOCK`：仅用于无头/自动化测试。设置后**不弹窗**，直接返回该路径
  （配合 `--create` 仍会创建文件）。默认不设置，即正常弹窗。
"""

import argparse
import json
import os
import sys

DEFAULT_FILETYPES = "SQLite 数据库:*.db,所有文件:*.*"


def _parse_filetypes(spec):
    """'标签:*.db,所有文件:*.*' -> [('标签', '*.db'), ('所有文件', '*.*')]"""
    out = []
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            label, pattern = part.split(":", 1)
        else:
            label, pattern = part, "*.*"
        label = label.strip() or pattern.strip()
        pattern = pattern.strip() or "*.*"
        out.append((label, pattern))
    return out or [("所有文件", "*.*")]


def _emit(payload):
    """把结果作为最后一行 JSON 输出（ensure_ascii=False 保留中文路径）。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(description="原生文件对话框（打开/另存为）")
    ap.add_argument("mode", choices=["open", "save"])
    ap.add_argument("--title", default="")
    ap.add_argument("--initialdir", default="")
    ap.add_argument("--initialfile", default="")
    ap.add_argument("--filetypes", default=DEFAULT_FILETYPES)
    ap.add_argument("--create", action="store_true",
                    help="save 模式下若文件不存在则创建空文件")
    args = ap.parse_args(argv)

    mock = os.environ.get("TP_FSDIALOG_MOCK")
    if mock == "__CANCEL__":          # 测试用：模拟用户点「取消」
        _emit({"ok": False, "canceled": True})
        return 0
    if mock:
        # 无头/自动化测试：不弹窗，直接返回指定路径
        path = os.path.abspath(mock)
    else:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception as exc:  # noqa: BLE001
            _emit({"ok": False, "error": "当前 Python 缺少 tkinter，无法弹出系统文件对话框：%s" % exc})
            return 2

        try:
            root = tk.Tk()
        except Exception as exc:  # noqa: BLE001
            _emit({"ok": False, "error": "无法初始化图形界面（可能没有可用的桌面会话）：%s" % exc})
            return 2

        root.withdraw()
        try:
            root.update()
            root.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass

        kw = {"parent": root, "filetypes": _parse_filetypes(args.filetypes)}
        if args.initialdir:
            kw["initialdir"] = args.initialdir
        if args.initialfile:
            kw["initialfile"] = args.initialfile

        try:
            if args.mode == "open":
                picked = filedialog.askopenfilename(
                    title=args.title or "选择数据库文件", **kw)
            else:
                picked = filedialog.asksaveasfilename(
                    title=args.title or "创建数据库文件",
                    defaultextension=".db", confirmoverwrite=False, **kw)
        except Exception as exc:  # noqa: BLE001
            _emit({"ok": False, "error": "文件对话框调用失败：%s" % exc})
            return 2
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass

        if not picked:
            _emit({"ok": False, "canceled": True})
            return 0
        path = os.path.abspath(picked)

    created = False
    if args.create and args.mode == "save" and not os.path.exists(path):
        try:
            with open(path, "ab"):
                pass
            created = True
        except OSError as exc:
            _emit({"ok": False, "error": "无法创建文件：%s" % exc})
            return 3

    # 统一为 POSIX 分隔符：Windows API 与 jdbc:sqlite: 均接受，界面更一致
    _emit({"ok": True, "path": path.replace("\\", "/"), "created": created})
    return 0


if __name__ == "__main__":
    sys.exit(main())
