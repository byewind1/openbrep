from __future__ import annotations


def render_user_bubble(st, text: str, *, image_bytes: bytes | None = None, image_width: int = 240) -> None:
    content_html = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    st.markdown(
        f"""
<div style="text-align:right;background:#23324a;border:1px solid #334155;border-radius:10px;padding:10px 12px;margin:6px 0;">{content_html}</div>
        """,
        unsafe_allow_html=True,
    )
    if image_bytes:
        st.image(image_bytes, width=image_width)


def render_assistant_block(st, text: str, *, image_bytes: bytes | None = None, image_width: int = 240) -> None:
    st.markdown(text or "")
    if image_bytes:
        st.image(image_bytes, width=image_width)
