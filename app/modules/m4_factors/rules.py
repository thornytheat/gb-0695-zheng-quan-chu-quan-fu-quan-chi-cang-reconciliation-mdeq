from app.domain import action_math
from app.numeric import fmt

def _actions(ctx):
    """Factors price off the latest valid action version.

    For a derived revision batch ``current_actions`` holds the freshly merged
    latest version, while ``actions`` stays on the registered version used for
    entitlement and cash.  A plain batch has only ``actions``.
    """
    return ctx.get('current_actions') or ctx['actions']

def apply(ctx):
    rows=[];factors={}
    for a in _actions(ctx):
        reference,factor=action_math(a);factors[(a['canonical'],a['ex_date'])]={'reference':reference,'factor':factor}
        rows.append({'证券':a['canonical'],'除权日':a['ex_date'],'前收盘_CNY':a['price'],'参考价_CNY':fmt(reference),'复权因子':fmt(factor,'0.00000001'),'行动版本':a['version']})
    ctx['factors']={str(k):v for k,v in factors.items()}
    derivation=ctx.get('derivation') or {}
    result={'rows':rows,'metrics':[['参考价',len(rows),'项'],['因子',len(factors),'项'],['冲突隔离',sum(r['原因码']=='CONFLICT' for r in ctx['quarantine']),'条']],
            'version_source':'当前有效版本' if ctx.get('current_actions') else '行动版本',
            'late_records':derivation.get('late_records',[])}
    if derivation:
        result['linked']={'base_run_id':derivation.get('base_run_id'),
                          'base_revision':derivation.get('base_revision'),
                          'base_decision':derivation.get('base_decision'),
                          'factor_source':'当前有效版本',
                          'current_versions':derivation.get('current_versions',{})}
    return result
