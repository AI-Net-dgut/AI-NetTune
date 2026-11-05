"""轻量检索增强（RAG）：从项目知识库中检索与问题相关的段落。

设计取舍（为什么不用向量库）：
- 知识库就是仓库里的 7 份 Markdown（故障定义、数据字典、验收口径、架构、部署），总量 <150 KB；
- 关键词 + 标题加权检索已足够召回，且**完全可复现**（同一问题永远返回同样段落），
  便于在验收与演示中"翻回去核对引用"；向量检索会引入 embedding 依赖与随机性。
- 检索结果只作为**证据的一部分**交给模型，模型必须能指出出处（文档名 + 章节号），
  由 `answer_llm.verify` 核对引用是否真实存在，防止编造章节。

对外只暴露 `retrieve(question, top_k)`，返回
    [{"doc": "fault_definition.md", "section": "2.2", "title": "...", "text": "...", "score": 3.5}]
"""
from __future__ import annotations

import math
import re
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent / "docs"

# 参与检索的文档（顺序即同等得分时的优先序）
DOC_FILES = (
    "fault_definition.md",
    "data_dict.md",
    "acceptance.md",
    "architecture.md",
    "real_data.md",
)

# 中文关键词同义词扩展：把用户口语/术语映射到文档里实际出现的词
SYNONYMS = {
    "弱覆盖": ["弱覆盖", "功率", "dBm", "覆盖"],
    "拥塞": ["拥塞", "占用率", "PRB", "负载"],
    "边缘": ["边缘", "SINR", "弱信号"],
    "阈值": ["阈值", "判据", "门槛", "40/50", "连续"],
    "误报": ["误报", "false", "抽样"],
    "预测": ["预测", "外推", "Prophet", "移动平均", "提前量"],
    "场景": ["场景", "校园", "商场", "体育馆"],
    "字段": ["字段", "列", "数据字典", "单位"],
    "仿真": ["仿真", "方案", "参数", "RSRP", "PRB"],
    "数据范围": ["数据范围", "覆盖", "01-01", "01-07", "insufficient"],
}

_SECTION_RE = re.compile(r"^(#{2,3})\s+(.+)$")
_INDEX: list[dict] | None = None

# 文档别名：问题里提到"数据字典""阈值怎么定"时，优先给对应文档加权
# 固定置顶规则：(触发词, 文档, 章节号)
PIN_RULES = (
    (("弱覆盖", "拥塞", "阈值", "判据", "40/50"), "fault_definition.md", "2.2"),
)

DOC_ALIASES = {
    "fault_definition.md": ("故障定义", "判据", "阈值", "门槛", "弱覆盖", "拥塞", "严重程度", "时间语义"),
    "data_dict.md": ("数据字典", "字段", "列", "单位", "18 列", "列含义"),
    "acceptance.md": ("验收", "口径", "误报", "命中", "提前量", "复核"),
    "architecture.md": ("架构", "分层", "模块", "数据流", "设计"),
    "real_data.md": ("真实数据", "接入", "数据契约", "缺列"),
}


def _build_index() -> list[dict]:
    """把文档切成「章节块」：标题 + 正文，保留文档名与章节号。"""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    blocks: list[dict] = []
    for name in DOC_FILES:
        path = DOCS_DIR / name
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        current = {"doc": name, "section": "", "title": "", "lines": []}
        for line in lines:
            match = _SECTION_RE.match(line)
            if match:
                if current["lines"]:
                    blocks.append(current)
                title = match.group(2).strip()
                number = title.split(" ")[0] if re.match(r"^[\d.]+", title) else ""
                current = {"doc": name, "section": number, "title": title, "lines": [line]}
            else:
                current["lines"].append(line)
        if current["lines"]:
            blocks.append(current)
    _INDEX = [b for b in blocks if len("\n".join(b["lines"]).strip()) > 80]
    return _INDEX


def _keywords(question: str) -> list[str]:
    words: list[str] = []
    for key, synonyms in SYNONYMS.items():
        if key in question or any(s in question for s in synonyms):
            words.extend(synonyms)
    # 问题里出现的技术词也直接算关键词
    for token in re.findall(r"[A-Za-z]{2,}|\d+(?:/\d+)?", question or ""):
        words.append(token)
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def retrieve(question: str, top_k: int = 3, max_chars: int = 260) -> list[dict]:
    """按问题检索最相关的 top_k 个章节块。

    打分用 TF-IDF：词频饱和 + 逆文档频率（"弱覆盖/预测"这类到处都有的词自动降权，
    "40/50""Prophet"这类只在少数块出现的词加权），标题命中再乘 2。
    """
    if not question or not question.strip():
        return []
    words = _keywords(question)
    if not words:
        return []
    blocks = _build_index()
    total = len(blocks) or 1
    # 文档频率：多少块包含该词
    df: dict[str, int] = {}
    bodies = ["\n".join(b["lines"]) for b in blocks]
    for w in words:
        df[w] = sum(1 for body in bodies if w in body)

    scored = []
    for block, body in zip(blocks, bodies):
        score = 0.0
        for w in words:
            tf = body.count(w)
            if not tf:
                continue
            idf = 1.0 + math.log(total / max(1, df[w]))
            title_bonus = 2.0 if w in block["title"] else 1.0
            score += min(tf, 5) * idf * title_bonus
    # 问题里点了文档主题（如"阈值怎么定的"）→ 该文档整体加权
        if any(alias in question for alias in DOC_ALIASES.get(block["doc"], ())):
            score *= 1.8
        if score > 0:
            scored.append((score, block))
    # 关键判据必须能被引用：问到弱覆盖/拥塞/阈值时，固定把"故障定义 §2.2"排到最前，
    # 避免因词频波动导致模型拿不到判据出处（演示与验收都要求"能翻回去核对"）。
    for triggers, doc_name, section in PIN_RULES:
        if any(t in question for t in triggers):
            for block in blocks:
                if block["doc"] == doc_name and block["section"].rstrip(".") == section:
                    scored = [(999.0, block)] + [item for item in scored if item[1] is not block]
                    break
    scored.sort(key=lambda item: (-item[0], DOC_FILES.index(item[1]["doc"])))
    results = []
    for score, block in scored[:top_k]:
        body = "\n".join(block["lines"]).strip()
        body = re.sub(r"^#{2,3}\s+.+$", "", body).strip()
        results.append({
            "doc": block["doc"],
            "section": block["section"],
            "title": block["title"],
            "text": body[:max_chars],
            "score": round(score, 1),
        })
    return results


def citation_label(snippet: dict) -> str:
    """回答里应使用的引用写法，例如「依据：故障定义 §2.2」。"""
    doc = snippet["doc"].replace(".md", "")
    cn = {
        "fault_definition": "故障定义",
        "data_dict": "数据字典",
        "acceptance": "验收口径",
        "architecture": "架构说明",
        "real_data": "真实数据接入说明",
    }.get(doc, doc)
    return f"{cn} §{snippet['section']}" if snippet["section"] else cn


if __name__ == "__main__":
    for q in ("为什么 AP-03 弱覆盖", "40/50 频段阈值怎么定的", "数据字典里 RSRP 是什么单位"):
        print(f"\n问题：{q}")
        for item in retrieve(q):
            print(f"  [{item['score']}] {citation_label(item)} | {item['title'][:40]}")
