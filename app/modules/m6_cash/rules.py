from app.numeric import number,fmt
from decimal import Decimal


def apply(ctx):
    positions={(p['record'],p['canonical'],p['event_date']):p for p in ctx['positions']};rows=[]
    for r in ctx['holdings']:
        actual=number(positions[(r['持仓记录'],r['证券'],r['快照日'])]['reported_cash']);expected=number(r['分红_CNY']);difference=actual-expected
        rows.append({'持仓记录':r['持仓记录'],'证券':r['证券'],'应付_CNY':fmt(expected,'0.01'),'渠道报送_CNY':fmt(actual,'0.01'),'差异_CNY':fmt(difference,'0.01'),'结论':'相符' if abs(difference)<Decimal('0.005') else '差异'})
    ctx['cash_reconciliation']=rows
    # A derived revision reconciles the revision file on its own; the reviewed
    # batch keeps and reports its own conservation.
    conservation=ctx.get('revision_conservation')
    if conservation is not None:
        ctx['balanced']=conservation['balanced']
    else:
        ctx['balanced']=ctx['input_count']==len(ctx['actions'])+len(ctx['action_history'])+len(ctx['positions'])+ctx['duplicates']+len(ctx['quarantine'])
    result={'rows':rows,'metrics':[['相符',sum(r['结论']=='相符' for r in rows),'条'],['差异',sum(r['结论']=='差异' for r in rows),'条'],['记录守恒','相符' if ctx['balanced'] else '差异','']]}
    derivation=ctx.get('derivation') or {}
    if derivation:
        original=derivation.get('original') or {}
        result['linked']={'base_run_id':derivation.get('base_run_id'),
                          'base_revision':derivation.get('base_revision'),
                          'base_decision':derivation.get('base_decision'),
                          'voucher_source':'登记时版本',
                          'original_cash_rows':original.get('cash_reconciliation'),
                          'original_balanced':original.get('balanced')}
        result['revision_conservation']=conservation
        result['base_input_count']=derivation.get('base_input_count',original.get('input'))
    return result
