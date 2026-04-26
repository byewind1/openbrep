from __future__ import annotations

from typing import Callable


VISION_SYSTEM_PROMPT = """\
你是专业 GDL 建筑师，精通 ArchiCAD GDL scripting（GDL Reference v26 标准）。
用户上传了一张建筑构件/家具/设施图片，请按以下结构输出：

## 构件识别
- 类型：（书架 / 桌椅 / 门窗 / 楼梯 / 柱 / 墙面板 / 灯具 / ...）
- 几何形态：（主体形状、结构层次、细部特征，2-4句）
- 材料/表面：（可见材质，用于 Material 参数默认值）

## 参数化分析
以 GDL paramlist 格式列出所有可参数化维度，给出合理默认值（长度单位 mm，转为 m 除以 1000）：

```
Length w  = 0.9     ! 总宽度（m）
Length h  = 2.1     ! 总高度（m）
Length d  = 0.3     ! 总深度（m）
Integer n = 4       ! 重复单元数量
Material mat = "Wood"  ! 主体材质
```

## GDL 3D Script

```gdl
! [构件名称] — AI 从图片生成
! 参数：w h d n mat

MATERIAL mat

! 主体
BLOCK w, d, h

END
```

规则：
- paramlist 代码块内必须有 ≥2 行 `Type Name = value  ! 注释` 格式
- 3D Script 最后一行必须是 `END`（单独一行）
- 所有尺寸由参数驱动，禁止硬编码数字
- GDL 命令必须全大写（BLOCK / CYLIND / LINE3 / ADD / DEL / FOR / NEXT 等）
- 如有重复元素（层板/格栅/百叶）用 FOR/NEXT 循环
"""


def run_vision_generate(
    *,
    image_b64: str,
    image_mime: str,
    extra_text: str,
    proj,
    status_col,
    auto_apply: bool,
    session_state,
    logger,
    get_llm_fn: Callable[[], object],
    begin_generation_state_fn: Callable[[object], str],
    guarded_event_update_fn: Callable[[object, str, str, str], None],
    consume_generation_result_fn: Callable[[str], bool],
    finalize_generation_fn: Callable[[str, str], bool],
    generation_cancelled_message_fn: Callable[[], str],
    classify_code_blocks_fn: Callable[[str], dict],
    apply_generation_result_fn: Callable[[dict, object, str | None, bool], tuple[str, list[str]]],
    classify_vision_error_fn: Callable[[Exception], str],
    error_fn: Callable[[str], None],
) -> str:
    generation_id = begin_generation_state_fn(session_state)
    status_ph = status_col.empty()
    try:
        llm = get_llm_fn()
        logger.info(
            "vision generate start route=generate image_mime=%s has_project=%s prompt_len=%d",
            image_mime,
            bool(proj),
            len(extra_text or ""),
        )
        guarded_event_update_fn(status_ph, generation_id, "info", "🖼️ AI 正在解析图片...")

        user_text = extra_text.strip() if extra_text else "请分析这张图片，生成对应的 GDL 脚本。"
        resp = llm.generate_with_image(
            text_prompt=user_text,
            image_b64=image_b64,
            image_mime=image_mime,
            system_prompt=VISION_SYSTEM_PROMPT,
        )
        if not consume_generation_result_fn(generation_id):
            status_ph.empty()
            finalize_generation_fn(generation_id, "cancelled")
            return generation_cancelled_message_fn()

        status_ph.empty()
        raw_text = resp.content
        extracted = classify_code_blocks_fn(raw_text)

        if extracted:
            result_prefix, _ = apply_generation_result_fn(extracted, proj, None, auto_apply)
            finalize_generation_fn(generation_id, "completed")
            return result_prefix + raw_text

        finalize_generation_fn(generation_id, "completed")
        return f"🖼️ **图片分析完成**（未检测到 GDL 代码块，AI 可能只给了文字分析）\n\n{raw_text}"

    except Exception as exc:
        status_ph.empty()
        finalize_generation_fn(generation_id, "failed")
        logger.warning("vision generate failed error=%s", exc.__class__.__name__)
        err_msg = classify_vision_error_fn(exc)
        error_fn(err_msg)
        return f"❌ {err_msg}"
