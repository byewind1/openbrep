#!/usr/bin/env python3
"""卡06：任务反馈与连续对话修复——真实浏览器集成验收（离线模型替身）。

真实 React + HTTP + Playwright；模型与编译器为离线替身，事件流为真实后端。
真实模型质量、Archicad 适配与 Codex 实机链路不在本验收范围。覆盖：

01 连续两轮修改：同项目第二轮 prepare 不被 PROJECT_CHANGED 拒绝（卡02）
02 执行过程时间线：任务完成后时间线显示过程步骤（工具 running→succeeded）（卡04/05）
03 重开复盘：刷新页面后时间线从聊天 meta.task_ref/thinking_steps 恢复（卡04/05）
04 超过12步查看全部：历史/事件接口替身返回 18 步，"还有 N 步"真正展开（卡05）
05 PROJECT_CHANGED 文案：代次失效后提示重新确认项目，不盲重发（卡02/05）
06 部分修改不伪称成功：交付卡 partial headline + 复盘时间线"任务部分完成"（卡04/05）
07 存储与界面对照：后端任务事件 JSONL 与契约一致、无重复终止（卡04）
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openbrep.config import GDLAgentConfig
from openbrep.compiler import MockHSFCompiler
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import MockLLM
from openbrep.workbench_api import WorkbenchSession
import openbrep.workbench.http_server as http


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='/private/tmp/task-feedback-e2e/')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    root = Path(__file__).resolve().parents[1]
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='task-feedback-e2e-'))
    config = GDLAgentConfig()
    config.compiler.mode = 'mock'
    config.llm.model = 'glm-4-flash'
    config.llm.api_key = 'fake-offline-test'
    config.save(str(temp / 'config.toml'))
    session = WorkbenchSession(config_path=temp / 'config.toml')
    session.compiler_mode = 'mock'

    flags = {'partial': False, 'bump_epoch': False}
    results = []
    errors = []

    plan = {'intent_summary': '调整宽度', 'user_visible_changes': ['调整A'], 'affected_files': ['paramlist.xml'],
            'risk': '尺寸变化', 'constraints': [], 'assumptions': [],
            'acceptance_criteria': ['编译通过'], 'finding_refs': [], 'optional_suggestions': []}

    class OfflineLLM(MockLLM):
        def generate(self, messages, **kwargs):
            data = {'conclusion': '只读顾问：A是宽度；本轮未修改项目。', 'suggestions': [], 'tradeoffs': [],
                    'plan': plan, **plan}
            self.responses = [json.dumps(data, ensure_ascii=False)]
            self.call_count = 0
            return super().generate(messages, **kwargs)

    session.settings_service.llm_adapter_factory = lambda cfg: OfflineLLM()
    real_generate = session.assistant_service.generate_with_assistant

    def offline_generate(body):
        if not flags['partial']:
            return real_generate(body)
        # 部分修改替身：有产出、验证未过、delivery partial_change
        return {
            'ok': True,
            'assistant': {
                'kind': 'generate',
                'reply': '中断前已完成部分修改。',
                'changed_files': ['scripts/3d.gdl'],
                'delivery': {'state': 'partial_change', 'status': 'incomplete',
                             'headline': '未完成，存在部分修改', 'reason': '任务中断',
                             'show_success_badge': False, 'show_before_after': False,
                             'show_changed_files': True, 'can_recover': True, 'can_continue': True,
                             'can_view_diff': True, 'diff_target': 'working',
                             'changed_files': ['scripts/3d.gdl']},
                'delivery_source': {'state': 'partial_change', 'status': 'incomplete'},
            },
            'warnings': [],
        }

    session.assistant_service.generate_with_assistant = offline_generate
    session.assistant_service._safe_harvest = lambda *args: None

    build = session.assistant_service._build_generate_pipeline

    def offline_pipeline(*args, **kwargs):
        pipeline, request = build(*args, **kwargs)
        pipeline._make_llm = lambda request: OfflineLLM()
        pipeline._make_compiler = lambda: MockHSFCompiler()
        original_execute = pipeline.execute

        def execute_with_events(req):
            # 保证时间线有真实事件驱动的步骤（工具 running→succeeded 对）
            req.on_event("status", {"stage": "understand", "message": "🤔 正在理解你的修改意图…"})
            req.on_event("tool_started", {"tool": "update_script", "tool_call_id": "t1", "stage": "think"})
            req.on_event("tool_call", {"tool": "update_script", "ok": True, "summary": "已更新 scripts/3d.gdl"})
            return original_execute(req)

        pipeline.execute = execute_with_events
        return pipeline, request

    session.assistant_service._build_generate_pipeline = offline_pipeline

    def rpc(method, path, body=None):
        if path == '/api/assistant/turn' and flags['bump_epoch'] and body.get('phase') == 'prepare':
            # 卡05 场景替身：客户端代次与服务端漂移（模拟旧版收尾赋值事故）
            session.project_epoch += 1
            flags['bump_epoch'] = False
        return session.route(method, path, body)

    http.route_rpc = rpc
    http._STATIC_DIR = root / 'frontend/dist'
    server = ThreadingHTTPServer(('127.0.0.1', 0), http._WorkbenchRequestHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'

    def store_turn_paths():
        return sorted((Path(session.source_path) / '.openbrep' / 'memory' / 'chats' / 'tasks').glob('*.jsonl'))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1600, 'height': 1100})
            page.on('pageerror', lambda error: errors.append(str(error)))

            def fixture(name, has_project=True):
                flags.update(partial=False, bump_epoch=False)
                if has_project:
                    project = HSFProject.create_new('TF_' + name, str(temp))
                    project.guid = '00000000-0000-0000-0000-000000000001'
                    project.set_script(ScriptType.SCRIPT_3D, 'BLOCK A, B, ZZYZX\n')
                    project.save_to_disk()
                else:
                    project = None
                session.project = project
                session.source_path = project.root if project else None
                session.source = 'hsf' if project else 'empty'
                session.conversation_service.clear()
                page.goto(url)
                page.get_by_label('Ask or generate').wait_for(timeout=30000)

            def send(message):
                page.get_by_label('Ask or generate').fill(message)
                page.get_by_role('button', name='发送', exact=True).click()
                page.get_by_role('button', name='发送', exact=True).wait_for(timeout=30000)

            def record(name, ok=True, detail=None):
                page.screenshot(path=str(out / (name + '.png')))
                results.append({'case': name, 'ok': ok, 'detail': detail})
                print(name, 'OK' if ok else 'FAIL', flush=True)

            # ── 01 连续两轮修改（卡02 核心）──────────────────────
            fixture('continuous')
            send('把A改成2.2')
            assert float(session.project.get_parameter('A').value) == 2.2
            send('把A改成2.4')
            assert float(session.project.get_parameter('A').value) == 2.4
            record('01-continuous-modify-two-rounds')

            # ── 02+07 执行过程时间线 + 存储契约对照 ──────────────
            assert page.locator('.assistant-thinking-timeline').count() > 0, '任务完成后时间线必须可见'
            assert page.locator('.timeline-step').count() >= 2
            paths = store_turn_paths()
            assert paths, '任务事件记录必须落盘'
            for path in paths:
                events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
                kinds = [e['kind'] for e in events]
                assert kinds[0] == 'accepted'
                assert kinds[-1] in {'completed', 'failed', 'cancelled'}
                assert kinds.count('completed') <= 1 and kinds.count('failed') <= 1
                assert any(k == 'tool_started' for k in kinds), '工具开始事件必须落盘'
            record('02-task-timeline-and-07-store-match')

            # ── 03 重开复盘：刷新后时间线恢复 ────────────────────
            page.reload()
            page.get_by_label('Ask or generate').wait_for(timeout=30000)
            page.wait_for_timeout(1200)
            assert page.locator('.assistant-thinking-timeline').count() > 0, \
                '重开项目后时间线必须从 task_ref/事件记录恢复'
            record('03-reopen-replay')

            # ── 04 超过12步查看全部（历史/事件接口协议替身）──────
            eighteen_events = [{'seq': i + 1, 'event_id': f'e{i}', 'timestamp': '', 'kind': 'tool_started',
                                'tool_name': f'tool_{i}', 'state': 'running',
                                'session_id': 's', 'project_epoch': 1, 'turn_id': 'turn-fake'} for i in range(18)]

            def fake_history(route):
                route.fulfill(status=200, content_type='application/json', body=json.dumps({
                    'ok': True,
                    'messages': [{'role': 'assistant', 'content': '已完成一批修改。',
                                  'meta': {'task_ref': {'turn_id': 'turn-fake', 'schema_version': 1}}}],
                }))

            def fake_events(route):
                route.fulfill(status=200, content_type='application/json', body=json.dumps(
                    {'ok': True, 'turn_id': 'turn-fake', 'events': eighteen_events}))

            page.route('**/api/assistant/history', fake_history)
            page.route('**/api/assistant/turn/events/turn-fake', fake_events)
            page.reload()
            page.get_by_label('Ask or generate').wait_for(timeout=30000)
            more = page.locator('.timeline-more').last
            more.wait_for(timeout=10000)
            assert '还有 6 步' in more.inner_text()
            more.click()
            assert page.get_by_text('tool_0', exact=True).count() > 0, '查看全部必须展开最早步骤'
            assert page.get_by_text('tool_17', exact=True).count() > 0
            page.unroute('**/api/assistant/history')
            page.unroute('**/api/assistant/turn/events/turn-fake')
            record('04-view-all-18-steps')

            # ── 05 PROJECT_CHANGED 文案 ──────────────────────────
            fixture('epoch_drift')
            flags['bump_epoch'] = True
            send('把A改成2.6')
            assert '项目状态已变化' in page.inner_text('body')
            assert float(session.project.get_parameter('A').value) != 2.6
            record('05-project-changed-message')

            # ── 06 部分修改不伪称成功 ────────────────────────────
            fixture('partial')
            flags['partial'] = True
            send('把A改成2.8')
            body_text = page.inner_text('body')
            assert '未完成，存在部分修改' in body_text, body_text
            # 重开复盘：partial 完成态如实恢复（不显示任务成功）
            page.reload()
            page.get_by_label('Ask or generate').wait_for(timeout=30000)
            page.wait_for_timeout(1200)
            restored_text = page.inner_text('body')
            assert '任务部分完成' in restored_text or '未完成，存在部分修改' in restored_text, restored_text
            assert '✅ 任务完成' not in restored_text
            record('06-partial-not-success')

            browser.close()
    except Exception as exc:
        results.append({'case': 'failure', 'ok': False, 'error': str(exc)})
        if 'page' in locals():
            try:
                page.screenshot(path=str(out / 'failure.png'))
            except Exception:
                pass
        raise
    finally:
        server.shutdown()
        server.server_close()
        (out / 'result.json').write_text(json.dumps({
            'ok': len(results) >= 6 and all(r['ok'] for r in results) and not errors,
            'mode': 'offline real browser; model/compiler doubles; event stream real',
            'results': results, 'page_errors': errors, 'real_llm_calls': 0,
            'not_covered': ['真实模型质量', '真实Archicad编译/图库适配', 'Codex 实机链路',
                            '真实 180s idle / 1800s task 时长（虚拟时钟单测覆盖）'],
        }, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
