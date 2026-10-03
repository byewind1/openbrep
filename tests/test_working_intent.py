from openbrep.workbench.working_intent import initial_intent, reduce_intent, intent_context, gui_instruction


def turn(state, message='添加背板，不改宽度', **extra):
    return reduce_intent(state, dict(kind='turn', message_id='t1', message=message, constraints=['不改宽度'], execute=True, **extra))


def test_constraints_survive_assumptions_and_uncertain_summary_without_promotion():
    state = turn(initial_intent('s', 1))
    assumed = reduce_intent(state, {'kind':'assumptions','message_id':'t1','values':['可能需要改宽度']})
    assert state['assumptions'] == []  # pure reducer
    assert intent_context(assumed)['constraints'][0]['value'] == '不改宽度'
    discussed = reduce_intent(assumed, {'kind':'proposals','proposals':[{'proposal_id':'p1','constraints':['改宽度']}]})
    assert len(discussed['constraints']) == 1
    next_turn = reduce_intent(discussed, {'kind':'turn','message_id':'t2','message':'还是笨重','constraints':[]})
    assert next_turn['assumptions'] == []
    assert intent_context(next_turn)['constraints'][0]['value'] == '不改宽度'
    assert next_turn['message_refs'][-1]['text'] == '还是笨重'


def test_this_turn_constraint_expires_and_exact_withdrawal_is_explicit():
    state = turn(initial_intent('s',1), message='这次不改宽度')
    assert state['constraints'][0]['scope'] == 'turn'
    assert reduce_intent(state, {'kind':'turn','message_id':'t2','message':'添加背板'})['constraints'] == []
    task = turn(initial_intent('s',1))
    withdrawn = reduce_intent(task, {'kind':'withdraw','constraint_id':'t1:0'})
    assert intent_context(withdrawn)['constraints'] == []
    assert task['constraints'][0]['status'] == 'active'


def test_completion_requires_run_change_delivery_and_passing_verification():
    state = turn(initial_intent('s',1))
    result = {'ok':True,'assistant':{'reply':'完成了'}}
    observed = reduce_intent(state, {'kind':'result','task_id':'t1','result':result})
    assert observed['tasks'][0]['state'] == 'incomplete'
    result['assistant'].update(run_id='r1',changed_files=['paramlist.xml'],delivery_source={'run_id':'r1','after_revision_id':'after'},verification={'passed':False})
    failed = reduce_intent(state, {'kind':'result','task_id':'t1','result':result})
    assert failed['tasks'][0]['state'] == 'failed'
    result['assistant']['verification']['passed'] = True
    completed = reduce_intent(state, {'kind':'result','task_id':'t1','result':result})
    assert completed['tasks'][0]['state'] == 'completed'
    assert intent_context(completed)['active_task'] is None
    rolled = reduce_intent(completed, {'kind':'rollback','revision_id':'after'})
    assert rolled['tasks'][0]['state'] == 'invalidated'
    assert rolled['goals'] == completed['goals']


def test_context_absence_is_byte_identical_and_selection_does_not_adopt_constraints():
    base = ' existing\n  instruction\n'
    assert gui_instruction(base,None).encode() == base.encode()
    assert gui_instruction(base,{}).encode() == base.encode()
    state = initial_intent('s',1)
    state = reduce_intent(state, {'kind':'proposals','proposals':[{'proposal_id':'p1','constraints':['不改宽度'],'assumptions':['默认木材']}]})
    selected = reduce_intent(state, {'kind':'select','proposal_id':'p1','message_id':'t1'})
    assert not selected['constraints']
    adopted = reduce_intent(selected, {'kind':'select','proposal_id':'p1','message_id':'t1','execute':True})
    assert adopted['constraints'][0]['value'] == '不改宽度'
    assert adopted['assumptions'][0]['scope'] == 'turn'
    assert '当前工作计划' in gui_instruction(base,intent_context(adopted))
