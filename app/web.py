from flask import Blueprint, render_template, request, abort
from app.config import TITLE, MODULES, SHORT, SAMPLE
from app.pipeline import calculate
from app.store import latest, save, get_run, derivations, source_of
bp=Blueprint('web',__name__)

def _view(run,stage):
    """A partial derivation has not confirmed every stage yet; the page shows
    what is confirmed and labels the rest instead of failing to render."""
    stages=run.get('stages') or {}
    current=stages.get(str(stage)) or {'stage':stage,'name':MODULES[stage-1],'rows':[],
                                       'metrics':[['阶段','未确认','']],'issues':{},'new_quarantine':0}
    return {**run,'stages':{str(n):stages.get(str(n)) or {'stage':n,'name':MODULES[n-1],'rows':[],
                                                          'metrics':[['阶段','未确认','']]} for n in range(1,8)},
            'actions':run.get('actions') or [],'quarantine':run.get('quarantine') or [],
            'cash_reconciliation':run.get('cash_reconciliation') or []},current

@bp.route('/')
@bp.route('/work/<int:stage>')
def work(stage=1):
    if stage<1 or stage>7:
        abort(404)
    run=get_run(request.args['run_id']) if request.args.get('run_id') else latest()
    if run is None: run=save(calculate(),source=SAMPLE.read_text(encoding='utf-8-sig'))
    view,current=_view(run,stage)
    base=get_run(run['parent']) if run.get('parent') else None
    linked=derivations(run['manifest']['run_id'])
    source=source_of(run['manifest']['run_id']) or SAMPLE.read_text(encoding='utf-8-sig')
    # The derived current entitlement is the accounting view of the linked base
    # batch; show it on every module page of a derived batch once M7 confirmed.
    derived_entitlement=(view['stages'].get('7') or {}).get('current_entitlement')
    return render_template('m'+str(stage)+'.html', title=TITLE, modules=MODULES, short=SHORT, run=view,
                           current=current, stage=stage, sample=source, base=base, linked=linked,
                           derived_entitlement=derived_entitlement)
