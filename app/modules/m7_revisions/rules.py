from app.domain import entitlement,allocate_cents,action_math,day
from app.numeric import fmt
from decimal import Decimal


def _eligible_positions(ctx,action):
    """Registration scope of ``action`` applied to the positions on file."""
    matching=[p for p in ctx['positions']
              if p['canonical']==action['canonical'] and day(p['event_date'])<=day(action['record_date'])]
    latest={}
    for position in matching:
        prior=latest.get(position['record'])
        if prior is None or (position['event_date'],position['行号'])>(prior['event_date'],prior['行号']):
            latest[position['record']]=position
    return sorted(latest.values(),key=lambda p:p['record'])


def _current_entitlement(ctx):
    """The entitlement that today's valid action versions would produce.

    Published alongside (never instead of) the registered share counts and
    cash vouchers, so the page can show the current result without rewriting
    what was reviewed.  Registration scope also follows the current version.
    """
    current=[a for a in ctx.get('current_actions') or []]
    registered={(a['canonical'],a['ex_date']):a for a in ctx['actions']}
    registered_rows={(item['position']['record'],item['action']['canonical']):item for item in ctx['eligible']}
    rows=[]
    for action in sorted(current,key=lambda a:(a['canonical'],a['ex_date'])):
        positions=_eligible_positions(ctx,action)
        calculations={p['record']:entitlement(p,action) for p in positions}
        cash=allocate_cents({k:v['cash'] for k,v in calculations.items()}) if calculations else {}
        old=registered.get((action['canonical'],action['ex_date']))
        for p in positions:
            e=calculations[p['record']]
            prior=registered_rows.get((p['record'],action['canonical']))
            before=entitlement(prior['position'],prior['action']) if prior is not None else None
            rows.append({'证券':p['canonical'],'持仓记录':p['record'],'快照日':p['event_date'],
                         '除权日':action['ex_date'],'原股份':p['shares'],
                         '登记版本':old['version'] if old else '—',
                         '当前版本':action['version'],
                         '登记新股份':str(before['new_shares']) if before else '—',
                         '当前新股份':str(e['new_shares']),'当前零碎股':str(e['fraction']),
                         '登记分红_CNY':fmt(before['cash'],'0.01') if before else '—',
                         '当前分红_CNY':fmt(cash[p['record']],'0.01')})
    return rows


def _derived_rows(ctx):
    """Impact of the revision, registered version -> current version."""
    current={(a['canonical'],a['ex_date']):a for a in ctx.get('current_actions') or []}
    registered={(a['canonical'],a['ex_date']):a for a in ctx['actions']}
    rows=[]
    for key in sorted(registered):
        old,new=registered[key],current.get(key)
        if new is None or old['version']==new['version']:
            continue
        for item in ctx['eligible']:
            if item['action']['record']!=old['record']:continue
            p=item['position'];before,after=entitlement(p,old),entitlement(p,new)
            rows.append({'证券':new['canonical'],'持仓记录':p['record'],'旧版本':old['version'],'新版本':new['version'],
                         '新增股份差':str(after['new_shares']-before['new_shares']),
                         '分红差_CNY':fmt(after['cash']-before['cash'],'0.01'),
                         '参考价差_CNY':fmt(action_math(new)[0]-action_math(old)[0])})
    return rows


def _history_rows(ctx):
    rows=[]
    for old in ctx['action_history']:
        new=next((a for a in ctx['actions'] if a['canonical']==old['canonical'] and a['ex_date']==old['ex_date']),None)
        if not new or old['version']==new['version']:continue
        for item in ctx['eligible']:
            if item['action']['record']!=new['record']:continue
            p=item['position'];before,after=entitlement(p,old),entitlement(p,new)
            rows.append({'证券':new['canonical'],'持仓记录':p['record'],'旧版本':old['version'],'新版本':new['version'],
                         '新增股份差':str(after['new_shares']-before['new_shares']),
                         '分红差_CNY':fmt(after['cash']-before['cash'],'0.01'),
                         '参考价差_CNY':fmt(action_math(new)[0]-action_math(old)[0])})
    return rows


def apply(ctx):
    derivation=ctx.get('derivation') or {}
    if ctx.get('current_actions') is not None:
        rows=_derived_rows(ctx)
        current_rows=_current_entitlement(ctx)
    else:
        rows=_history_rows(ctx);current_rows=[]
    result={'rows':rows,'metrics':[['修订影响',len(rows),'条'],['旧版留存',len(ctx['action_history']),'项'],
            ['当前版本',max((a['version_n'] for a in ctx.get('current_actions') or ctx['actions']),default=0),'']]}
    if ctx.get('current_actions') is not None:
        result['current_entitlement']=current_rows
        result['current_entitlement_metrics']=[['当前权益结果',len(current_rows),'条'],
                                               ['当前分红合计',str(sum((Decimal(r['当前分红_CNY']) for r in current_rows),Decimal(0))),'CNY']]
    if derivation:
        result['linked']={'base_run_id':derivation.get('base_run_id'),
                          'base_revision':derivation.get('base_revision'),
                          'base_decision':derivation.get('base_decision'),
                          'factor_source':'当前有效版本',
                          'entitlement_source':'登记时版本'}
        result['late_records']=derivation.get('late_records',[])
        result['conflicts']=derivation.get('definition_conflicts',0)
        result['original']=derivation.get('original',{})
    return result
