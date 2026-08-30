"""HF5 AC-1 合同测试：对话窗口统一口径（最近 12 轮 = 24 条消息 + 字符预算兜底）。

覆盖验收点：
- 按轮截断：12 轮全保留、13 轮丢最早（轮次优先，不因条数切半轮）
- 字符预算：从最早消息开始整条丢弃、至少保留最后一条（最新轮优先）
- 前后端口径一致：core._build_messages 与 pipeline 消费同一实现
  （openbrep.chat_history；前端 buildAssistantHistory 由 vitest 单独覆盖）；
  codex 桥接链（build_turn_prompt 折叠）消费 _build_messages 产物自动跟随。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from openbrep.chat_history import MAX_HISTORY_CHARS, RECENT_TURNS, trim_history_messages
from openbrep.core import GDLAgent


def _rounds(n: int) -> list[dict]:
    """n 轮交替历史：user/assistant 各一条，末尾最新。"""
    msgs: list[dict] = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    return msgs


class TrimHistoryByTurnsTest(unittest.TestCase):
    def test_keeps_all_24_messages_for_12_rounds(self):
        history = _rounds(12)
        self.assertEqual(trim_history_messages(history), history)
        self.assertEqual(len(trim_history_messages(history)), RECENT_TURNS * 2)

    def test_drops_earliest_round_for_13_rounds(self):
        history = _rounds(13)
        trimmed = trim_history_messages(history)
        self.assertEqual(trimmed, history[2:])  # u0/a0 最早轮被丢，剩 u1..a12

    def test_odd_message_count_keeps_most_recent_window(self):
        history = _rounds(12) + [{"role": "user", "content": "u12"}]
        trimmed = trim_history_messages(history)
        self.assertEqual(len(trimmed), 24)
        self.assertEqual(trimmed[-1], {"role": "user", "content": "u12"})

    def test_never_mutates_input(self):
        history = _rounds(13)
        snapshot = list(history)
        trim_history_messages(history)
        self.assertEqual(history, snapshot)

    def test_empty_and_none_history(self):
        self.assertEqual(trim_history_messages([]), [])
        self.assertEqual(trim_history_messages(None), [])


class CharBudgetTest(unittest.TestCase):
    def test_budget_drops_from_earliest_keeps_latest(self):
        # 24 条窗口内总字符远超预算 → 从最早开始整条丢弃，最近轮次优先
        history: list[dict] = []
        for i in range(25):
            history.append({"role": "user", "content": f"u{i}" + "x" * 1000})
            history.append({"role": "assistant", "content": f"a{i}"})
        trimmed = trim_history_messages(history)
        # 契约断言（不依赖精确切分点）：
        # - 总字符回到预算内；最早消息已被丢出；最新消息一定保留
        total = sum(len(m["content"]) for m in trimmed)
        self.assertLessEqual(total, MAX_HISTORY_CHARS)
        self.assertNotEqual(trimmed[0]["content"], "u13" + "x" * 1000)
        self.assertEqual(trimmed[-1], {"role": "assistant", "content": "a24"})
        # 窗口先截 24 条再按预算丢 → 剩余数量 < 24
        self.assertLess(len(trimmed), 24)

    def test_budget_keeps_at_least_last_message(self):
        history = [
            {"role": "user", "content": "y" * 9000},
            {"role": "assistant", "content": "保持"},
        ]
        trimmed = trim_history_messages(history, max_chars=8000)
        self.assertEqual(trimmed, [{"role": "assistant", "content": "保持"}])

    def test_under_budget_no_drop(self):
        history = _rounds(12)
        self.assertEqual(trim_history_messages(history), history)


class CoreBackendConsistencyTest(unittest.TestCase):
    """core._build_messages 与 chat_history 同源：13 轮历史只注入最近 12 轮。"""

    def _build_messages(self, history):
        agent = GDLAgent(llm=MagicMock())
        return agent._build_messages(
            instruction="当前指令",
            context="project-state",
            knowledge="knowledge",
            skills="skills",
            error=None,
            history=history,
        )

    def test_build_messages_injects_last_12_rounds_only(self):
        messages = self._build_messages(_rounds(13))
        history_msgs = messages[1:-1]  # system 之外、当前 user 之前
        self.assertEqual(len(history_msgs), 24)
        self.assertEqual(history_msgs[0], {"role": "user", "content": "u1"})
        self.assertEqual(history_msgs[-1], {"role": "assistant", "content": "a12"})
        self.assertEqual(messages[-1]["role"], "user")
        self.assertIn("当前指令", messages[-1]["content"])

    def test_build_messages_keeps_code_block_folding_guard(self):
        history = [
            {"role": "user", "content": "按图新增漏窗"},
            {"role": "assistant", "content": "已完成：\n```gdl\nBLOCK A, B, ZZYZX\nEND\n```"},
        ]
        messages = self._build_messages(history)
        folded = messages[2]["content"]  # system / user / assistant(折叠) / 当前 user
        self.assertNotIn("BLOCK", folded)
        self.assertIn("[code block omitted", folded)

    def test_build_messages_applies_char_budget(self):
        history = [
            {"role": "user", "content": "u0" + "x" * 9000},
            {"role": "assistant", "content": "a0"},
        ]
        messages = self._build_messages(history)
        history_msgs = messages[1:-1]
        self.assertEqual(history_msgs, [{"role": "assistant", "content": "a0"}])

    def test_codex_bridge_input_is_bounded_after_window(self):
        """codex 桥接链消费 _build_messages 产物：13 轮 → 桥接收到的 history 折叠 ≤24 条。"""
        from openbrep.codex.turn import build_turn_prompt

        messages = self._build_messages(_rounds(13))
        _, user_text = build_turn_prompt(messages)
        # 折叠后 user_text = "以下是此前对话记录：" + 24 条 + 当前指令
        self.assertLessEqual(user_text.count("user:"), 13)
        self.assertIn("当前指令", user_text)
        self.assertIn("user: u1", user_text)      # 最早保留轮次
        self.assertNotIn("user: u0", user_text)   # 最早轮被窗口丢出


if __name__ == "__main__":
    unittest.main()
