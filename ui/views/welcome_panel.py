from __future__ import annotations

from typing import Callable


def render_welcome(
    st,
    *,
    browse_and_open_project_source_fn: Callable[[], tuple[bool, str]] | None = None,
) -> None:
    st.markdown(
        """
<div class="welcome-card">
<h2 style="color:#22d3ee; margin-top:0; font-family:'JetBrains Mono';">欢迎使用 OpenBrep 🏗️</h2>
<p style="color:#94a3b8;">用自然语言驱动 ArchiCAD GDL 对象的创建与编译。无需了解 GDL 语法，直接描述需求即可。</p>
</div>
""",
        unsafe_allow_html=True,
    )

    st.markdown("#### 三步快速开始")
    st.info("**① 配置 API Key**  \n在左侧边栏选择 AI 模型，填入对应 API Key。免费的智谱 GLM 可直接使用。")
    st.info("**② 开始对话**  \n在底部输入框描述你想创建的 GDL 对象，例如：  \n「创建一个宽 600mm、深 400mm 的书架，带 iShelves 参数控制层数」")
    st.info("**③ 编译输出**  \nAI 生成代码后自动触发编译。真实编译需在侧边栏配置 LP_XMLConverter 路径。Mock 模式可验证结构，无需安装 ArchiCAD。")

    st.divider()
    st.markdown("#### 或者：打开已有项目/文件")
    if browse_and_open_project_source_fn is not None:
        if st.button(
            "📂 打开文件或 HSF 文件夹",
            key="welcome_open_project_source",
            width="stretch",
            help="支持 .gdl / .txt / .gsm 文件；选择文件夹时会判断是否为 HSF 项目目录",
        ):
            ok, msg = browse_and_open_project_source_fn()
            if ok:
                st.rerun()
            elif msg.startswith("❌"):
                st.error(msg)
            else:
                st.info(msg)

    st.divider()
    st.caption("💡 提示：第一条消息无需创建项目，直接描述需求，AI 会自动初始化。")
