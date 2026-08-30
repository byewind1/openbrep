"""对话窗口统一口径（HF5，2026-08-30）。

"最近 N 轮"到底是多少，历史上在前后端各写各的，导致口径漂移：HF4 后端
core.py `history[-6:]` 截的是 6 条**消息**（= 3 轮），前端又预截 12 条，
实际生效只有 3 轮——维护者长任务对话第 3 次"继续"即退化为空转检查。

本模块是对话窗口的唯一实现，所有消费方必须走它，禁止各自实现
"最近 N 条消息"切片：

- core.py::GDLAgent._build_messages（chat 历史注入，含 code 折叠护栏）
- pipeline.py CHAT/EXPLAIN/wiki 路径（原 _trim_history）
- 前端 frontend/src/state/actions/assistantActions.ts::buildAssistantHistory
  （前端只是预截，最终生效以后端为准）

口径（轮/条写死在这里，避免再漂移）：

- **按轮**：保留最近 ``RECENT_TURNS`` 轮 = 12 轮 = 24 条消息。
  1 轮 = 1 条 user + 1 条 assistant = 2 条消息。轮次优先于条数——history
  列表按到达顺序排列（末尾最新），尾部切片保证整轮保留，不会因条数把
  半轮塞进窗口。
- **字符预算兜底**：总内容字符数超过 ``max_chars``（默认 8000）时，从
  最早的消息开始整条丢弃，保证当前轮与最近轮次优先；极端情况下至少
  保留最后一条消息（最新轮），防止空窗口。
"""

from __future__ import annotations

from typing import Optional

RECENT_TURNS = 12          # 最近 12 轮
MESSAGES_PER_TURN = 2      # 1 轮 = 1 条 user + 1 条 assistant = 2 条消息
MAX_HISTORY_CHARS = 8000   # 总字符预算兜底（防爆 token）


def _content_len(msg: dict) -> int:
    content = msg.get("content")
    if content is None:
        return 0
    return len(content) if isinstance(content, str) else len(str(content))


def trim_history_messages(
    history: Optional[list[dict]],
    *,
    turns: int = RECENT_TURNS,
    max_chars: int = MAX_HISTORY_CHARS,
) -> list[dict]:
    """统一对话窗口截断：先按轮取最近 N 轮，再按字符预算从最早丢弃。

    返回 history 的一个尾部切片副本，不修改入参。history 元素形如
    ``{"role": "user"/"assistant", "content": str}``。
    """
    if not history:
        return []
    messages = history[-(turns * MESSAGES_PER_TURN):]
    total = sum(_content_len(m) for m in messages)
    drop = 0
    while total > max_chars and drop < len(messages) - 1:
        total -= _content_len(messages[drop])
        drop += 1
    return messages[drop:] if drop else messages
