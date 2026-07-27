"""LLM 录制/回放（黄金语料）：让 benchmark 摆脱真实 LLM 的非确定性。

实测 deepseek-chat 在 temperature=0 下同 prompt 连调三次返回三种结果
（服务端批处理/数值非确定性），客户端 temperature=0 无法让 benchmark 确定。

黄金语料模式：录制一次真实生成（--llm-record），之后回放同一语料验证
代码改动（--llm-replay）——LLM 变量被冻结，分数变化只能来自代码变化。

限制（如实）：replay 只覆盖文本 generate；generate_with_tools（agent_loop）
与 generate_with_image（视觉）在 replay 模式抛 NotImplementedError。
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from openbrep.llm import LLMResponse


def _corpus_key(messages, kwargs) -> str:
    payload = json.dumps(
        {"messages": messages, "kwargs": kwargs},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RecordingLLM:
    """包一层真实 LLM：透传调用，把 (prompt hash → content) 写入语料 jsonl。

    线程安全（benchmark --jobs 并发写同一文件，逐行加锁）。
    """

    def __init__(self, inner, corpus_path: str):
        self.inner = inner
        self.corpus_path = Path(corpus_path)
        self.corpus_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.corpus_path.open("w", encoding="utf-8")  # 录制 =  fresh 语料
        self._lock = threading.Lock()

    def _record(self, messages, kwargs, resp: LLMResponse) -> None:
        line = json.dumps(
            {"key": _corpus_key(messages, kwargs), "content": resp.content},
            ensure_ascii=False,
        )
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def generate(self, messages, **kwargs) -> LLMResponse:
        resp = self.inner.generate(messages, **kwargs)
        self._record(messages, kwargs, resp)
        return resp

    def generate_with_tools(self, messages, tools, **kwargs) -> LLMResponse:
        return self.inner.generate_with_tools(messages, tools, **kwargs)

    def generate_with_image(self, *args, **kwargs) -> LLMResponse:
        return self.inner.generate_with_image(*args, **kwargs)


class ReplayLLM:
    """从语料回放：同一 prompt 返回录制时的同一响应，离线、零 token、完全确定。

    语料未命中抛 KeyError——说明当前代码的 prompt 流与录制时不一致
    （知识库/提示词变了），需要重新录制，而不是静默编造。
    """

    def __init__(self, corpus_path: str):
        self.corpus_path = str(corpus_path)
        self.corpus: dict[str, str] = {}
        for line in Path(corpus_path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                self.corpus[item["key"]] = item["content"]

    def generate(self, messages, **kwargs) -> LLMResponse:
        key = _corpus_key(messages, kwargs)
        if key not in self.corpus:
            raise KeyError(
                f"replay 语料未命中（sha256:{key[:12]}…）："
                f"当前 prompt 流与录制不一致，请用 --llm-record 重新录制 {self.corpus_path}"
            )
        return LLMResponse(
            content=self.corpus[key], model="replay", usage={}, finish_reason="stop"
        )

    def generate_with_tools(self, messages, tools, **kwargs) -> LLMResponse:
        raise NotImplementedError("replay 模式暂不支持 tool_calls（agent_loop 路径）")

    def generate_with_image(self, *args, **kwargs) -> LLMResponse:
        raise NotImplementedError("replay 模式暂不支持图像输入")
