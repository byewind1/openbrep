"""WorkbenchSession 测试替身共享助手（SimpleNamespace 假会话与真实接口对齐）。"""

from __future__ import annotations


def attach_refresh_same_project(session) -> None:
    """给假会话补上 refresh_same_project，镜像 WorkbenchSession 同名方法：

    同一活动项目（root 一致且两侧非 None）→ 换入内存对象并保持 project_epoch；
    其余情况回落普通 setter 语义（project_epoch + 1）。
    """
    def refresh_same_project(project) -> None:
        current = session.project
        if project is None or current is None:
            session.project = project
            session.project_epoch += 1
            return
        same = project.root.resolve() == current.root.resolve()
        session.project = project
        if not same:
            session.project_epoch += 1

    session.refresh_same_project = refresh_same_project
