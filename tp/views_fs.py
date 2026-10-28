"""
原生文件对话框视图（表结构 · 连接 SQLite 时的「路径」）
------------------------------------------------------------
「浏览…」/「创建…」需要的是**系统文件资源管理器**的原生对话框，浏览器自身
无法做到（`<input type=file>` 拿不到真实绝对路径，也没有「新建文件」对话框）。
本模块因此由**后端**在服务器所在机器上弹窗，再把选中的绝对路径回传前端。

  POST /api/fs/pick/   {mode: "open"|"save", create?: bool, title?, initialfile?, initialdir?}
      → {ok:true, path:"D:/data/x.db", created:false}
      → {ok:false, canceled:true}          （用户点取消）
      → {ok:false, error:"..."}            （找不到 tkinter / 无桌面会话 / 超时 …）

重要限制（务必知悉）：
  · 对话框弹在**运行 Django 的这台机器**上。仅适用于本机调试；
    若部署到远程服务器，弹窗会出现在服务器上而不是用户屏幕上，需要改为
    「浏览器端 File System Access API」或「上传文件」等方案。
  · 浏览器与服务器需在同一台机器（当前即如此）。
"""

import json
import os
import subprocess
import sys
from functools import lru_cache

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fsdialog.py")

# 用户选定文件前一直等待；超时给出明确提示，避免请求永久挂起
DIALOG_TIMEOUT = int(os.environ.get("TP_FSDIALOG_TIMEOUT", "300"))


def _candidate_pythons():
    """可能带 tkinter 的解释器（按优先级）。venv 通常不带 tkinter。"""
    out = []
    env = os.environ.get("TP_TK_PYTHON")
    if env:
        out.append([env])
    out.append([sys.executable])
    if os.name == "nt":
        out.append(["py", "-3"])          # Python Launcher：取已安装的最新 3.x
        out.append(["py"])
    out.append(["python3"])
    out.append(["python"])
    return out


@lru_cache(maxsize=1)
def _find_tk_python():
    """返回第一个能 `import tkinter` 的解释器 argv 前缀；都没有则 None。"""
    for argv in _candidate_pythons():
        try:
            p = subprocess.run(argv + ["-c", "import tkinter"],
                               capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if p.returncode == 0:
            return argv
    return None


@csrf_exempt
def fs_pick(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "仅支持 POST"}, status=405)

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "请求体不是合法 JSON"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"ok": False, "error": "请求体必须是 JSON 对象"}, status=400)

    mode = "save" if data.get("mode") == "save" else "open"
    py = _find_tk_python()
    if not py:
        return JsonResponse({
            "ok": False,
            "error": ("未找到带 tkinter 的 Python 解释器，无法弹出系统文件对话框。"
                      "云端部署（如魔搭创空间）本身不支持弹出本机文件对话框，"
                      "请直接在「路径」输入框里填写服务器上的绝对路径（默认库为 "
                      "/mnt/workspace/Prompt_Test_DB.db）；本机运行时可改用环境变量 "
                      "TP_TK_PYTHON 指定一个带 tkinter 的 python。"),
        })

    cmd = list(py) + [HELPER, mode, "--title", str(data.get("title") or "")]
    if data.get("initialfile"):
        cmd += ["--initialfile", str(data["initialfile"])]
    if data.get("initialdir"):
        cmd += ["--initialdir", str(data["initialdir"])]
    if data.get("filetypes"):
        cmd += ["--filetypes", str(data["filetypes"])]
    if mode == "save" and data.get("create"):
        cmd.append("--create")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=DIALOG_TIMEOUT)
    except subprocess.TimeoutExpired:
        return JsonResponse(
            {"ok": False, "error": "文件对话框等待超时（%d 秒），请重试" % DIALOG_TIMEOUT})
    except OSError as exc:
        return JsonResponse({"ok": False, "error": "无法启动文件对话框：%s" % exc})

    payload = None
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
    if not isinstance(payload, dict):
        detail = (proc.stderr or "").strip().splitlines()
        detail = detail[-1] if detail else "文件对话框调用失败"
        return JsonResponse({"ok": False, "error": detail[:400]})
    return JsonResponse(payload)
