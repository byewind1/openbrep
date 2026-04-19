from __future__ import annotations


def build_generation_reply(plain_text: str, result_prefix: str = "", code_blocks: list[str] | None = None) -> str:
    reply_parts = []
    if plain_text:
        reply_parts.append(plain_text)
    if result_prefix:
        joined_blocks = "\n\n".join(code_blocks or [])
        reply_parts.append(result_prefix + joined_blocks)
    if reply_parts:
        return "\n\n---\n\n".join(reply_parts)
    return "🤔 AI 未返回代码或分析，请换一种描述方式。"
