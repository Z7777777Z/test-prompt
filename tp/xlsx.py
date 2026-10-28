"""
极简 .xlsx 解析（标准库 zipfile + xml）
------------------------------------------------------------
为什么不用 openpyxl / pandas：
  这个项目要打包成「魔搭创空间」的 Docker 镜像，多一个依赖就多一份构建与
  体积风险；而「导入用例」只需要读第一个工作表的二维文本，标准库足够。

口径：
  · 只读第一个工作表（按 sheetN.xml 的编号取最小者）。
  · 表头识别：含「问题 / 用户提示词」的列做问题列，含「标准答案 / 答案」的列做答案列；
    识别不到表头时，退化为「第 1 列 = 问题、第 2 列 = 标准答案」，且不再跳过首行。
  · 只取文本，公式按缓存值（<v>）读取。
"""
import io
import re
import zipfile
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

HEAD_QUESTION = ("问题", "用户提示词")
HEAD_ANSWER = ("标准答案", "答案")


def _col_index(ref: str) -> int:
    """单元格引用 'BC12' → 列下标 54（0 起）。"""
    col = 0
    for ch in ref:
        if ch.isalpha():
            col = col * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return max(col - 1, 0)


def _shared_strings(zf: zipfile.ZipFile):
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    out = []
    root = ET.fromstring(data)
    for si in root.findall(NS + "si"):
        # 富文本会被拆成多个 <t>，拼起来
        out.append("".join(t.text or "" for t in si.iter(NS + "t")))
    return out


def _first_sheet(zf: zipfile.ZipFile):
    names = [n for n in zf.namelist()
             if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
    if not names:
        return None
    names.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    return names[0]


def read_rows(blob: bytes):
    """xlsx 字节流 → 二维列表 [[单元格, ...], ...]"""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        shared = _shared_strings(zf)
        path = _first_sheet(zf)
        if not path:
            return []

        root = ET.fromstring(zf.read(path))
        rows = []
        for r in root.iter(NS + "row"):
            cells = {}
            for c in r.findall(NS + "c"):
                idx = _col_index(c.get("r") or "")
                ctype = c.get("t")
                v = c.find(NS + "v")
                if ctype == "s" and v is not None:
                    try:
                        val = shared[int(v.text)]
                    except (TypeError, ValueError, IndexError):
                        val = ""
                elif ctype == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(NS + "t"))
                else:
                    val = (v.text if v is not None else "") or ""
                cells[idx] = val

            if cells:
                width = max(cells) + 1
                rows.append([cells.get(i, "") for i in range(width)])
        return rows


def parse_cases(blob: bytes):
    """→ [{'question': ..., 'answer': ...}, ...]"""
    rows = read_rows(blob)
    if not rows:
        return []

    head = [str(x).strip() for x in rows[0]]
    qi = ai = -1
    for i, h in enumerate(head):
        if qi == -1 and h in HEAD_QUESTION:
            qi = i
        if ai == -1 and h in HEAD_ANSWER:
            ai = i

    if qi == -1 or ai == -1:
        qi, ai, start = 0, 1, 0          # 没表头：前两列，首行也算数据
    else:
        start = 1                        # 有表头：跳过首行

    cases = []
    for r in rows[start:]:
        q = str(r[qi]).strip() if qi < len(r) else ""
        a = str(r[ai]).strip() if ai < len(r) else ""
        if q:
            cases.append({"question": q, "answer": a})
    return cases
