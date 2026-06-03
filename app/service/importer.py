"""上传文件解析 —— txt / csv / xlsx → token 列表（去重保序）。

用途：批量导入 商品ID / 关键词 / 卖家ID，避免在输入框里手贴。
- txt / csv / 其它文本：按行读，每行取第一列（逗号分隔时取第一段）。
- xlsx：读首个工作表第一列的非空单元格。
- 自动跳过空行、# 注释行、疑似表头行；按出现顺序去重。
"""
import io
import logging
from typing import List

logger = logging.getLogger(__name__)

# 疑似表头词（首行命中则丢弃，避免把列名当成一条数据）
_HEADER_WORDS = {
    "id", "ids", "usitemid", "us_item_id", "itemid", "item_id", "商品id", "商品编号",
    "keyword", "keywords", "kw", "关键词", "搜索词",
    "seller", "sellerid", "seller_id", "catalogsellerid", "卖家", "卖家id", "卖家编号",
}


def parse_upload(filename: str, content: bytes) -> List[str]:
    """解析上传文件内容为 token 列表。

    Args:
        filename: 原始文件名（用于判断扩展名）。
        content:  文件字节内容。

    Returns:
        去重保序后的字符串 token 列表（已 strip、已去空/注释/表头）。

    Raises:
        RuntimeError: xlsx 缺少 openpyxl 依赖时。
    """
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        tokens = _parse_xlsx(content)
    elif name.endswith(".xls"):
        raise RuntimeError("暂不支持 .xls，请另存为 .xlsx 或 .csv 后再导入")
    else:
        tokens = _parse_text(content)

    # 丢弃疑似表头（首条命中表头词）
    if tokens and tokens[0].strip().lower() in _HEADER_WORDS:
        tokens = tokens[1:]
    return _dedupe(tokens)


def _parse_text(content: bytes) -> List[str]:
    text = content.decode("utf-8-sig", errors="ignore")
    out: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "," in line:  # csv：取第一列
            line = line.split(",", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _parse_xlsx(content: bytes) -> List[str]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("解析 xlsx 需要 openpyxl，请先 pip install openpyxl") from exc
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    out: List[str] = []
    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        cell = row[0]
        if cell is None:
            continue
        s = str(cell).strip()
        # 数字ID 可能被 Excel 读成浮点（123.0），去掉无意义的 .0
        if s.endswith(".0") and s[:-2].isdigit():
            s = s[:-2]
        if s:
            out.append(s)
    wb.close()
    return out


def _dedupe(tokens: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for t in tokens:
        t = t.strip()
        if not t or t.startswith("#"):
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out
