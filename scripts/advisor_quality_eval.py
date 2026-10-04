#!/usr/bin/env python3
"""Compare legacy and advisor with fixed public snapshots and manual score sheets."""
from __future__ import annotations
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.source_snapshot import capture_snapshot

DIMENSIONS = ['目标理解','事实依据','实现可行性','取舍说明','约束保留']


def fixed_source(spec, directory):
    if spec is None: return None
    project=HSFProject.create_new('PublicFixture', str(directory))
    project.guid=spec['guid']
    for name,content in spec['scripts'].items(): project.set_script(ScriptType(name),content)
    return project


def assess_scores(comparisons):
    scored=improved=total_score=0
    errors=[]
    for row in comparisons:
        scores=row.get('scores') or {}
        legacy=scores.get('legacy') or {}
        advisor=scores.get('advisor') or {}
        complete=all(type(values.get(key)) is int and values[key] in {0,1,2} for values in (legacy,advisor) for key in DIMENSIONS)
        errors.extend(row.get('critical_errors') or [])
        if complete:
            scored+=1
            a=sum(advisor[k] for k in DIMENSIONS);b=sum(legacy[k] for k in DIMENSIONS)
            total_score+=a;improved+=a>b
    total=len(comparisons)
    mean=total_score/total if total else 0
    ratio=improved/total if total else 0
    return {'status':'已验收' if total>=42 and sum(str(r.get('id','')).startswith('multi_') for r in comparisons)>=12 and scored==total and mean>=8 and ratio>=.8 and not errors else '未验收',
            'total':total,'scored':scored,'mean_score':mean,'improvement_ratio':ratio,'critical_errors':errors}


def compare_case(case,sources,session,*,mock=False):
    from openbrep.llm import MockLLM
    from openbrep.runtime.pipeline import TaskPipeline
    usage=[]
    class Counting:
        def __init__(self,llm):self.llm=llm
        def __getattr__(self,name):return getattr(self.llm,name)
        def generate(self,*args,**kwargs):
            item={'usage':None,'completed':False};usage.append(item)
            response=self.llm.generate(*args,**kwargs)
            item.update(usage=dict(response.usage or {}),completed=True)
            return response
    directory=Path(tempfile.mkdtemp(prefix='advisor-quality-source-'))
    outputs={}
    original_llm=session.conversation_service._llm
    for entry in ('legacy','unified'):
        project=fixed_source(sources[case['source']],directory)
        session.project=project;session.source_path=project.root if project else None
        session.config.llm.conversation_entry=entry
        session.conversation_service.clear()
        def make_llm():
            if mock:return Counting(MockLLM(responses=[json.dumps({'conclusion':'离线示例回答，尚未做真实质量验收。','suggestions':[],'tradeoffs':[]})]))
            return Counting(original_llm())
        session.conversation_service._llm=make_llm
        class ObservedPipeline(TaskPipeline):
            def __init__(self,**kwargs):
                kwargs['trace_dir']=str(directory/'traces');super().__init__(**kwargs)
            def _make_llm(self,request):return make_llm()
        session.pipeline_class=ObservedPipeline
        snapshot=capture_snapshot(project,session.project_epoch)
        history=[];turns=[]
        first_call=len(usage);started=time.monotonic()
        for message in case.get('messages') or [case['message']]:
            response=session.assistant_service.assistant_reply({'message':message,'history':history,'requested_mode':'consult'})
            reply=(response.get('assistant') or {}).get('reply') or response.get('error') or ''
            turns.append({'message':message,'ok':response.get('ok'), 'reply':reply})
            history.extend([{'role':'user','content':message},{'role':'assistant','content':reply}])
        outputs[entry]={'turns':turns,'context_version':snapshot.context_fingerprint,
            'duration_ms':round((time.monotonic()-started)*1000,2),'llm_calls':len(usage)-first_call,
            'usage':usage[first_call:],'source_unchanged':snapshot.matches(session.project,session.project_epoch)}
    session.conversation_service._llm=original_llm
    return {'id':case['id'],'source':case['source'],'legacy':outputs['legacy'],'advisor':outputs['unified'],
            'scores':{key:{dimension:None for dimension in DIMENSIONS} for key in ('legacy','advisor')},'critical_errors':[], 'status':'未评分'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',default='tests/fixtures/advisor_quality_cases.json')
    parser.add_argument('--mode',choices=['mock','real'],default='mock')
    parser.add_argument('--config');parser.add_argument('--allow-paid',action='store_true')
    parser.add_argument('--limit',type=int,default=1,help='Use 0 for all frozen cases; default is a small smoke')
    parser.add_argument('--output',default='/private/tmp/advisor-quality-eval.json')
    args=parser.parse_args()
    if args.mode=='real' and (not args.allow_paid or not args.config):parser.error('Real calls require --allow-paid and an explicit --config.')
    from openbrep.workbench_api import WorkbenchSession
    if args.mode=='mock':
        from openbrep.config import GDLAgentConfig
        temporary=Path(tempfile.mkdtemp(prefix='advisor-quality-config-'))/'config.toml'
        GDLAgentConfig().save(str(temporary));config_path=temporary
    else:config_path=args.config
    session=WorkbenchSession(config_path=config_path)
    data=json.loads(Path(args.cases).read_text());cases=data['questions']+data['multi_rounds']
    if args.limit:cases=cases[:args.limit]
    results=[compare_case(case,data['sources'],session,mock=args.mode=='mock') for case in cases]
    from openbrep.config import model_to_provider
    output={'status':'未验收','mode':args.mode,'model':session.llm_model,'provider':model_to_provider(session.llm_model),
        'source_notice':data['source_notice'],'comparisons':results,'manual_summary':assess_scores(results)}
    destination=Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'未验收','mode':args.mode,'cases':len(results),'output':str(destination)},ensure_ascii=False))

if __name__=='__main__':main()
