#!/usr/bin/env python3
"""Real React/browser paths with offline models, mock compiler and fault injection."""
from __future__ import annotations
import argparse
import json
import re
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from openbrep.config import GDLAgentConfig
from openbrep.compiler import MockHSFCompiler
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.llm import MockLLM
from openbrep.source_fingerprint import compute_source_fingerprint
from openbrep.workbench_api import WorkbenchSession
import openbrep.workbench.http_server as http


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='/private/tmp/assistant-turn-e2e/')
    args=parser.parse_args()
    from playwright.sync_api import sync_playwright
    root=Path(__file__).resolve().parents[1]
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='assistant-turn-e2e-'))
    config=GDLAgentConfig();config.compiler.mode='mock';config.llm.model='glm-4-flash';config.llm.api_key='fake-offline-test'
    config.save(str(temp/'config.toml'))
    session=WorkbenchSession(config_path=temp/'config.toml');session.compiler_mode='mock'
    flags={'plan_failure':False,'source_changed':False,'legacy':False}
    phases=[];generation=[];errors=[];results=[]
    plan={'intent_summary':'调整宽度','user_visible_changes':['调整A'],'affected_files':['paramlist.xml'],'risk':'尺寸变化',
        'constraints':[],'assumptions':[],'acceptance_criteria':['编译通过'],'finding_refs':[],'optional_suggestions':[]}
    class OfflineLLM(MockLLM):
        def generate(self,messages,**kwargs):
            text=str(messages[-1].get('content',''))
            if flags['plan_failure']:content='not valid json'
            else:
                data={'conclusion':'只读顾问：A是宽度；本轮未修改项目。','suggestions':[],'tradeoffs':[], 'plan':plan,**plan}
                if '给我两种方案' in text:
                    data['proposals']=[{'title':'轻量方案','target_intent':'MODIFY','goal':'把B改成0.7','scope':[], 'constraints':[], 'assumptions':[], 'tradeoffs':['深度减少'],'evidence_refs':[]},
                        {'title':'高柜方案','target_intent':'MODIFY','goal':'把ZZYZX改成2.4','scope':[], 'constraints':[], 'assumptions':[], 'tradeoffs':['高度增加'],'evidence_refs':[]}]
                content=json.dumps(data,ensure_ascii=False)
            self.responses=[content];self.call_count=0
            return super().generate(messages,**kwargs)
    session.settings_service.llm_adapter_factory=lambda cfg:OfflineLLM()
    build=session.assistant_service._build_generate_pipeline
    def offline_pipeline(*args,**kwargs):
        pipeline,request=build(*args,**kwargs)
        pipeline._make_llm=lambda request:OfflineLLM()
        pipeline._make_compiler=lambda:MockHSFCompiler()
        return pipeline,request
    session.assistant_service._build_generate_pipeline=offline_pipeline
    session.assistant_service._safe_harvest=lambda *args:None
    original_generate=session.assistant_service.generate_with_assistant
    def counted_generate(body):
        generation.append(body['message']);return original_generate(body)
    session.assistant_service.generate_with_assistant=counted_generate
    def rpc(method,path,body=None):
        if path=='/api/assistant/turn':
            phases.append({'phase':body.get('phase'),'turn_id':body.get('turn_id')})
            if flags['source_changed'] and body.get('phase')=='execute':
                session.project.parameters[0].value=str(float(session.project.parameters[0].value)+.1)
                session.project.save_to_disk()
        return session.route(method,path,body)
    http.route_rpc=rpc;http._STATIC_DIR=root/'frontend/dist'
    server=ThreadingHTTPServer(('127.0.0.1',0),http._WorkbenchRequestHandler);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}'
    def source():return compute_source_fingerprint(session.project.root) if session.project else None
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1600,'height':1100})
            page.on('pageerror',lambda error:errors.append(str(error)))
            def fixture(name,has_project=True,legacy=False):
                flags.update(plan_failure=False,source_changed=False,legacy=legacy)
                if has_project:
                    project=HSFProject.create_new('Public_'+name,str(temp));project.guid='00000000-0000-0000-0000-000000000001'
                    project.set_script(ScriptType.SCRIPT_3D,'BLOCK A, B, ZZYZX\n');project.save_to_disk()
                else:project=None
                session.project=project;session.source_path=project.root if project else None;session.source='hsf' if project else 'empty'
                session.config.llm.conversation_entry='legacy' if legacy else 'unified';session.conversation_service.clear()
                generation.clear();phases.clear()
                page.goto(url);page.get_by_label('Ask or generate').wait_for(timeout=30000)
                return source()
            def send(message):
                page.get_by_label('Ask or generate').fill(message)
                page.get_by_role('button',name='发送',exact=True).click()
                page.get_by_role('button',name='发送',exact=True).wait_for(timeout=30000)
            def record(name):
                page.screenshot(path=str(out/(name+'.png')))
                results.append({'case':name,'ok':True,'generation_calls':len(generation),'phases':list(phases)})
                print(name,flush=True)
            def parameter(name):return float(session.project.get_parameter(name).value)
            before=fixture('consult');send('只解释A参数');assert source()==before and not generation
            assert not page.locator('.delivery-card').count();record('01-project-consult')
            fixture('empty',False);send('讲讲PRISM_');assert session.project is None and not generation;record('02-empty-consult')
            before=fixture('plan_cancel');page.get_by_role('button',name='先出计划（不改项目）',exact=True).click();send('把A改成2')
            page.get_by_role('group',name='修改计划').wait_for();assert source()==before and not generation
            page.get_by_role('button',name='取消',exact=True).click();page.get_by_role('group',name='修改计划').wait_for(state='hidden');assert source()==before;record('03-plan-cancel')
            before=fixture('plan_approve');page.get_by_role('button',name='先出计划（不改项目）',exact=True).click();send('把A改成2')
            page.get_by_role('group',name='修改计划').wait_for();assert source()==before
            page.get_by_role('button',name='确认修改',exact=True).click();page.get_by_role('button',name='发送',exact=True).wait_for(timeout=30000)
            assert parameter('A')==2 and len(generation)==1;record('04-plan-approve')
            fixture('ordinary');send('把A改成1.8');assert parameter('A')==1.8 and len(generation)==1
            assert not page.get_by_role('group',name='修改计划').count();record('05-ordinary-execute')
            before=fixture('negative');send('不要把A改成2，只解释参数');assert source()==before and not generation;record('06-negation-readonly')
            before=fixture('proposal_select');send('只讨论，给我两种方案');page.get_by_role('button',name='选择方案',exact=True).first.click()
            page.get_by_role('button',name='发送',exact=True).wait_for(timeout=30000);assert source()==before and not generation;record('07-proposal-selection')
            fixture('proposal_execute');send('只讨论，给我两种方案');send('按方案一做');assert len(generation)==1 and parameter('B')==.7;record('08-proposal-reference-execute')
            before=fixture('flush_failure')
            page.get_by_role('tab',name='脚本',exact=True).click();page.locator('.monaco-editor').first.wait_for(timeout=30000)
            page.locator('.monaco-editor').first.click();page.keyboard.press('Control+A');page.keyboard.insert_text('BLOCK 2,2,2\n! offline draft\n')
            def fail_save(route):route.fulfill(status=200,content_type='application/json',body=json.dumps({'success':False,'error':'Injected save failure'}))
            page.route('**/api/project/script/*',fail_save);send('把A改成2');assert not generation and source()==before
            assert 'Injected save failure' in page.inner_text('body');record('09-flush-failure');page.unroute('**/api/project/script/*',fail_save)
            fixture('source_changed');flags['source_changed']=True;send('把A改成2');assert not generation
            assert [p['phase'] for p in phases].count('prepare')==2
            assert 'SOURCE_CHANGED' in page.inner_text('body');record('10-source-changed-once')
            before=fixture('switch');page.get_by_role('button',name='先出计划（不改项目）',exact=True).click();send('把A改成2');page.get_by_role('group',name='修改计划').wait_for()
            old_root=session.project.root
            page.get_by_role('button',name='Project',exact=True).click();page.get_by_role('button',name='New',exact=True).click()
            page.get_by_role('group',name='修改计划').wait_for(state='hidden');send('继续')
            assert not generation and session.project.name!='Public_switch' and compute_source_fingerprint(old_root)==before
            record('11-project-switch-invalidates')
            fixture('idempotency');duplicates=[]
            def duplicate_execute(route):
                body=route.request.post_data_json
                if body.get('phase')!='execute':return route.continue_()
                body['stream']=False
                first=route.fetch(post_data=json.dumps(body)).json();second=route.fetch(post_data=json.dumps(body)).json()
                duplicates.append(first==second)
                route.fulfill(status=200,content_type='text/event-stream',body='event: done\ndata: '+json.dumps(first,ensure_ascii=False)+'\n\n')
            page.route('**/api/assistant/turn',duplicate_execute);send('把A改成2');assert duplicates==[True] and len(generation)==1 and parameter('A')==2
            record('12-idempotent-http-retry');page.unroute('**/api/assistant/turn',duplicate_execute)
            fixture('legacy',legacy=True);send('把A改成2');page.get_by_role('group',name='修改计划').wait_for()
            assert not phases and parameter('A')==1;record('13-legacy-fallback')
            before=fixture('failed_plan');flags['plan_failure']=True;page.get_by_role('button',name='先出计划（不改项目）',exact=True).click();send('把A改成2')
            assert source()==before and not generation and not page.get_by_role('group',name='修改计划').count()
            assert '计划生成失败' in page.inner_text('body');record('14-plan-failure')
            browser.close()
    except Exception as exc:
        results.append({'case':'failure','ok':False,'error':str(exc)})
        if 'page' in locals():
            try:page.screenshot(path=str(out/'failure.png'))
            except Exception:pass
        raise
    finally:
        server.shutdown();server.server_close()
        (out/'result.json').write_text(json.dumps({'ok':len(results)>=14 and all(r['ok'] for r in results) and not errors,
            'mode':'offline real browser; model and compiler doubles','results':results,'page_errors':errors,
            'real_llm_calls':0,'not_covered':['真实模型质量','真实Archicad编译/图库适配']},ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
