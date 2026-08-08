"""LLM 录制/回放（黄金语料）：让 benchmark 摆脱真实 LLM 的非确定性。

实测 deepseek-chat 在 temperature=0 下同 prompt 连调三次返回三种结果
（服务端批处理/数值非确定性），客户端 temperature=0 无法让 benchmark 确定。

黄金语料模式：录制一次真实生成（--llm-record），之后回放同一语料验证
代码改动（--llm-replay）——LLM 变量被冻结，分数变化只能来自代码变化。

语料格式（jsonl，每行一个 {key, content, ...}）：
- key = sha256({messages, kwargs[, tools]} JSON)；
- generate 行：{key, content}；
- generate_with_tools 行：{key, content, tool_calls: [{id, name, raw_arguments}],
  finish_reason}——tool_calls 的 id/name/raw_arguments 必须逐字保真：agent loop
  下一轮请求的语料 key 包含上一轮的 tool_call id（assistant tool_calls 消息与
  role=tool 结果都带 id），id 变一个字符，后续轮次全部 miss。

限制（如实）：replay 只覆盖文本 generate 与 generate_with_tools（agent_loop）；
generate_with_image（视觉）在 replay 模式抛 NotImplementedError。
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from openbrep.llm import LLMResponse, ToolCall, ToolDefinition


def _serialize_tools(tools) -> list[dict]:
    """ToolDefinition/dict 的稳定序列化（不依赖 repr 顺序）：统一转 OpenAI function dict。

    key 的稳定性要求工具定义里的字段顺序变化不改变 key——
    外层 _corpus_key 的 sort_keys=True 对嵌套 dict 同样生效。
    """
    out: list[dict] = []
    for t in tools or []:
        if isinstance(t, ToolDefinition):
            out.append(t.to_openai_dict())
        elif isinstance(t, dict):
            out.append(t)
    return out


def _corpus_key(messages, kwargs, tools=None) -> str:
    """prompt → sha256。tools 传入时纳入 payload（generate_with_tools 专用）。"""
    payload: dict = {"messages": messages, "kwargs": kwargs}
    if tools is not None:
        payload["tools"] = _serialize_tools(tools)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _replay_tool_call(tc: dict) -> ToolCall:
    """从语料里逐字重建 ToolCall：arguments 按 llm.py 同款容错 JSON 解析。"""
    raw = str(tc.get("raw_arguments") or "")
    try:
        arguments = json.loads(raw) if raw else {}
        if not isinstance(arguments, dict):
            arguments = {}
    except Exception:
        arguments = {}
    return ToolCall(
        id=str(tc.get("id") or ""),
        name=str(tc.get("name") or ""),
        arguments=arguments,
        raw_arguments=raw,
    )


class RecordingLLM:
    """包一层真实 LLM：透传调用，把 (prompt hash → 完整响应) 写入语料 jsonl。

    线程安全（benchmark --jobs 并发写同一文件，逐行加锁）。
    """

    def __init__(self, inner, corpus_path: str):
        self.inner = inner
        self.corpus_path = Path(corpus_path)
        self.corpus_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.corpus_path.open("w", encoding="utf-8")  # 录制 =  fresh 语料
        self._lock = threading.Lock()

    def _record(self, messages, kwargs, resp: LLMResponse, tools=None) -> None:
        line: dict = {
            "key": _corpus_key(messages, kwargs, tools=tools),
            "content": resp.content,
        }
        if resp.tool_calls:
            line["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "raw_arguments": tc.raw_arguments}
                for tc in resp.tool_calls
            ]
        if resp.finish_reason:
            line["finish_reason"] = resp.finish_reason
        with self._lock:
            self._fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            self._fh.flush()

    def generate(self, messages, **kwargs) -> LLMResponse:
        resp = self.inner.generate(messages, **kwargs)
        self._record(messages, kwargs, resp)
        return resp

    def generate_with_tools(self, messages, tools, **kwargs) -> LLMResponse:
        resp = self.inner.generate_with_tools(messages, tools, **kwargs)
        self._record(messages, kwargs, resp, tools=tools)
        return resp

    def generate_with_image(self, *args, **kwargs) -> LLMResponse:
        return self.inner.generate_with_image(*args, **kwargs)


class ReplayLLM:
    """从语料回放：同一 prompt 返回录制时的同一响应，离线、零 token、完全确定。

    语料未命中抛 KeyError——说明当前代码的 prompt 流与录制时不一致
    （知识库/提示词/工具定义变了），需要重新录制，而不是静默编造。
    回放 generate_with_tools 时逐字重建 ToolCall（id/name/raw_arguments 原样），
    保证 agent loop 后续轮次的语料 key 与录制时一致。
    """

    def __init__(self, corpus_path: str):
        self.corpus_path = str(corpus_path)
        self.corpus: dict[str, dict] = {}
        for line in Path(corpus_path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                self.corpus[item["key"]] = item

    def _miss(self, key: str) -> KeyError:
        return KeyError(
            f"replay 语料未命中（sha256:{key[:12]}…）："
            f"当前 prompt 流与录制不一致，请用 --llm-record 重新录制 {self.corpus_path}"
        )

    def generate(self, messages, **kwargs) -> LLMResponse:
        key = _corpus_key(messages, kwargs)
        if key not in self.corpus:
            raise self._miss(key)
        item = self.corpus[key]
        return LLMResponse(
            content=item["content"], model="replay", usage={}, finish_reason="stop"
        )

    def generate_with_tools(self, messages, tools, **kwargs) -> LLMResponse:
        key = _corpus_key(messages, kwargs, tools=tools)
        if key not in self.corpus:
            raise self._miss(key)
        item = self.corpus[key]
        tool_calls = [_replay_tool_call(tc) for tc in item.get("tool_calls") or []]
        return LLMResponse(
            content=item.get("content", ""),
            model="replay",
            usage={},
            finish_reason=(
                item.get("finish_reason")
                or ("tool_calls" if tool_calls else "stop")
            ),
            tool_calls=tool_calls,
        )

    def generate_with_image(self, *args, **kwargs) -> LLMResponse:
        raise NotImplementedError("replay 模式暂不支持图像输入")
