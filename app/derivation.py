"""Revision derivation.

A corporate-action revision never rewrites a reviewed batch.  Instead it
derives a *new* batch linked to the original one:

* the original batch keeps its positions, cash vouchers and review basis
  untouched, and remains the batch on screen while a derivation is unfinished;
* the derived batch publishes adjustment factors from the latest valid action
  version (M4/M7), while entitlement share counts and cash reconciliation
  (M3/M5/M6) stay on the version that was registered for the reviewed batch;
* once complete the same revision submission reuses the same derived batch;
  an interrupted derivation resumes from its last confirmed stage;
* a late-arriving lower action version cannot roll the current version back
  (selection is by version number, never by file order), and two different
  definitions claiming the same version are isolated -- for an already
  reviewed version the reviewed definition is kept and the incoming row is
  quarantined with its original row number.
"""
import hashlib
from time import perf_counter
from app.config import RULE_VERSION
from app.errors import DomainError
from app.manifest import make
from app.numeric import serial
from app.reasons import isolate
from app import store
from app.modules.m1_receiving.service import execute as stage1
from app.modules.m2_mapping.service import execute as stage2
from app.modules.m3_registry.service import execute as stage3
from app.modules.m4_factors.service import execute as stage4
from app.modules.m5_holdings.service import execute as stage5
from app.modules.m6_cash.service import execute as stage6
from app.modules.m7_revisions.service import execute as stage7

STAGES=[stage1,stage2,stage3,stage4,stage5,stage6,stage7]

# Fields that define an action; the evidence row number and the mapped alias are
# provenance, not definition, and are excluded.
SIGNATURE=('price','cash','bonus','rights','rights_price','record_date','ex_date')


def identity(base_id,text):
    digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
    key=hashlib.sha256((RULE_VERSION+':derive:'+base_id+':'+digest).encode('utf-8')).hexdigest()
    return key,key[:16],digest


def _version(row):
    try: return int(row.get('version_n',row.get('version',0)))
    except (TypeError,ValueError): return 0


def _signature(row):
    return tuple(str(row.get(name,'')) for name in SIGNATURE)


def _group(row):
    return (row['canonical'],row['ex_date'])


def _marker(row):
    return (_group(row),_version(row),_signature(row))


def _same_position(left,right):
    return all(str(left.get(name,''))==str(right.get(name,''))
               for name in ('shares','reported_cash','security','alias'))


def _dedupe(rows):
    seen=set();out=[]
    for row in rows:
        marker=_marker(row)
        if marker in seen: continue
        seen.add(marker);out.append(row)
    return out


def _source(ctx):
    """Untouched revision-file state, captured once so a resumed merge is
    deterministic and never accumulates duplicates."""
    if 'merge_source' not in ctx:
        ctx['merge_source']={
            'quarantine':[dict(row) for row in ctx.get('quarantine') or []],
            'positions':list(ctx.get('positions') or []),
            'actions':list(ctx.get('actions') or []),
            'action_history':list(ctx.get('action_history') or []),
            'duplicates':ctx.get('duplicates',0),
            'input_count':ctx.get('input_count',0),
            'base_quarantine':[],          # filled by _merge
        }
    return ctx['merge_source']


def _base_actions(base):
    return _dedupe(list(base.get('actions') or []))


def _base_current(base):
    return _dedupe(list(base.get('current_actions') or base.get('actions') or []))


def _quarantine_key(row):
    return (row.get('行号'),row.get('record'),row.get('原因码'),row.get('原因'),str(sorted(row.items())))


def _merge(ctx,base,base_id):
    """Merge the revision file (ctx) with the reviewed batch (base)."""
    source=_source(ctx)
    base_actions=_base_actions(base)
    base_current=_base_current(base)
    base_history=_dedupe(list(base.get('action_history') or []))
    base_positions=list(base.get('positions') or [])
    base_quarantine=list(base.get('quarantine') or [])
    revision_actions=list(source['actions'])
    revision_history=list(source['action_history'])
    revision_positions=list(source['positions'])
    revision_quarantine=source['quarantine']

    # Evidence of the revision file itself, preserved before the merge.
    ctx['revision_counts']={'input':source['input_count'],'actions':len(revision_actions),
                            'history':len(revision_history),'positions':len(revision_positions),
                            'duplicates':source['duplicates'],'quarantine':len(revision_quarantine)}
    # The reviewed batch's quarantine is part of its review basis and stays
    # visible; rows the revision file merely repeats are not listed twice.
    quarantine=[];seen=set()
    for row in base_quarantine+revision_quarantine:
        key=_quarantine_key(row)
        if key in seen: continue
        seen.add(key);quarantine.append(row)
    ctx['quarantine']=quarantine

    # -- positions -------------------------------------------------------
    # Reviewed snapshots are the registered basis; an identical revision row is
    # a duplicate, a different row for the same investor/stock/date is isolated.
    positions=[];index={};position_overlap=0;position_conflicts=0
    for row in base_positions:
        positions.append(row);index[(row['record'],row['canonical'],row['event_date'])]=row
    for row in revision_positions:
        key=(row['record'],row['canonical'],row['event_date'])
        seen=index.get(key)
        if seen is None:
            positions.append(row);index[key]=row
        elif _same_position(seen,row):
            position_overlap+=1
        else:
            isolate(ctx,row,'POSITION_CONFLICT','修订持仓与已复核批次冲突')
            position_conflicts+=1

    # -- actions ---------------------------------------------------------
    groups={}
    for row in base_actions+base_current+base_history:
        groups.setdefault(_group(row),{'base':[],'revision':[]})['base'].append(row)
    known={_marker(row) for row in base_actions+base_current+base_history}
    action_overlap=0;action_conflicts=0
    for row in revision_actions+revision_history:
        if _marker(row) in known:
            action_overlap+=1;continue          # same reviewed definition
        groups.setdefault(_group(row),{'base':[],'revision':[]})['revision'].append(row)

    current=[];registered=[];history=[];late=[]
    current_versions={};registered_versions={}
    definition_conflicts=0
    for key,parts in sorted(groups.items()):
        base_rows=parts['base'];revision_rows=parts['revision']
        rows=base_rows+revision_rows
        effective=max([_version(row) for row in rows] or [0])   # latest, never file order
        base_at=[row for row in base_rows if _version(row)==effective]
        revision_at=[row for row in revision_rows if _version(row)==effective]
        signatures={_signature(row) for row in base_at+revision_at}
        winner=None
        if len(signatures)>1:
            definition_conflicts+=1
            if base_at:   # the reviewed definition wins a same-version clash
                winner=min(base_at,key=lambda r:r.get('record',''))
                for row in revision_at:
                    isolate(ctx,row,'CONFLICT','同版本公司行动定义与已复核批次冲突');action_conflicts+=1
            else:
                for row in revision_at:
                    isolate(ctx,row,'CONFLICT','同版本公司行动定义冲突');action_conflicts+=1
        else:
            pool=base_at or revision_at
            winner=min(pool,key=lambda r:r.get('record','')) if pool else None
        if winner is None: continue
        current.append(winner);current_versions[key[0]+'|'+key[1]]=effective

        reg_pool=[row for row in base_actions if _group(row)==key]
        registered_row=min(reg_pool,key=lambda r:r.get('record','')) if reg_pool else winner
        registered.append(registered_row)
        registered_versions[key[0]+'|'+key[1]]=_version(registered_row)

        for row in rows:
            if row is winner or _version(row)==effective: continue
            history.append(row)                 # old versions stay queryable
        # A late row claiming an old version must not redefine that version
        # either: a different definition under an existing version is isolated.
        by_version={}
        for row in history:
            if _group(row)!=key: continue
            by_version.setdefault(_version(row),[]).append(row)
        for row in revision_rows:
            if _version(row)>=effective: continue
            same=[other for other in by_version.get(_version(row),[]) if other is not row]
            if any(_signature(other)!=_signature(row) for other in same):
                isolate(ctx,row,'CONFLICT','晚到版本与既有同版本定义冲突');action_conflicts+=1
                history=[other for other in history if other is not row]
        for row in revision_rows:               # late arrivals are recorded, not applied
            if _version(row)<effective:
                late.append({'记录':row['record'],'证券':row['canonical'],'除权日':row['ex_date'],
                             '晚到版本':_version(row),'当前版本':effective,'行号':row.get('行号')})

    # -- accounting ------------------------------------------------------
    quarantine=len(revision_quarantine)+position_conflicts+action_conflicts
    duplicates=source['duplicates']+position_overlap+action_overlap
    accepted_actions=len(revision_actions)+len(revision_history)-action_overlap-action_conflicts
    accepted_positions=len(revision_positions)-position_overlap-position_conflicts
    input_count=source['input_count']
    ctx['revision_conservation']={
        'input':input_count,'actions':accepted_actions,'positions':accepted_positions,
        'duplicates':duplicates,'quarantine':quarantine,
        'balanced':input_count==accepted_actions+accepted_positions+duplicates+quarantine}
    # Restore the revision file's own duplicate counter for later stages.
    ctx['duplicates']=source['duplicates']

    ctx['positions']=positions
    ctx['actions']=registered            # M3/M5/M6 use the registered version
    ctx['current_actions']=current       # M4/M7 use the latest valid version
    ctx['action_history']=_dedupe(history)
    ctx['derivation']={
        'base_run_id':base_id,'base_revision':base.get('revision'),'base_decision':base.get('decision'),
        'base_sha256':base.get('manifest',{}).get('sha256'),
        'factor_source':'current','entitlement_source':'registered',
        'current_versions':current_versions,'registered_versions':registered_versions,
        'late_records':late,'definition_conflicts':definition_conflicts,'conflicts':action_conflicts,
        'original':{'actions':len(base.get('actions') or []),'positions':len(base_positions),
                    'holdings':len(base.get('holdings') or []),
                    'cash_reconciliation':len(base.get('cash_reconciliation') or []),
                    'quarantine':len(base.get('quarantine') or []),
                    'input':base.get('input_count',0),'balanced':base.get('balanced')},
        'revision':{'input':input_count,'duplicates':source['duplicates']},
    }
    ctx.pop('merge_source',None)
    return ctx


def _fresh(base,base_id,text,digest):
    manifest=make(text)
    manifest['run_id']=None
    manifest['parameters']={'base_run_id':base_id}
    return {'rows':[],'quarantine':[],'stages':{},'manifest':manifest,'progress':0,
            'derivation':{'base_run_id':base_id,'base_revision':base.get('revision'),
                          'base_decision':base.get('decision'),
                          'base_sha256':base.get('manifest',{}).get('sha256'),
                          'revision_sha256':digest,'factor_source':'current',
                          'entitlement_source':'registered'}}


_RUN_KEYS=('revision','decision','parent','derived','reused','resumed','base_run_id',
           'created_at','source','derivation_key')


def _from_payload(payload):
    """Fall back to the last confirmed payload if its checkpoint is missing
    (e.g. the process stopped between the two writes)."""
    ctx={key:value for key,value in payload.items() if key not in _RUN_KEYS}
    ctx.setdefault('rows',[]);ctx.setdefault('quarantine',[]);ctx.setdefault('stages',{})
    return ctx


def derive(base_id,text=None,stop=7):
    if not base_id: raise DomainError('BASE','必须指定原批次',422)
    base=store.get_run(base_id)                  # 404 when the base batch is unknown
    if base.get('status','complete')!='complete':
        raise DomainError('INCOMPLETE','原批次修订尚未走完全部阶段，不能作为派生依据',409)
    if text is None: text=store.source_of(base_id)
    if text is None:
        raise DomainError('CSV','原批次未留存原文，必须提供修订CSV',422)
    if not isinstance(text,str): raise DomainError('TYPE','CSV必须是文本')
    try: stop=int(stop)
    except (TypeError,ValueError): raise DomainError('STAGE','续跑阶段必须为整数')
    if stop<1 or stop>7: raise DomainError('STAGE','续跑阶段必须在一到七之间')

    key,derived_id,digest=identity(base_id,text)
    existing=store.find_derivation(key)
    if existing and (existing['status']=='complete' or stop<=existing['progress']):
        existing['reused']=True;existing['resumed']=False
        return existing

    checkpoint=store.load_checkpoint(derived_id) if existing else None
    if existing and checkpoint and checkpoint.get('progress',0)>=existing['progress']:
        ctx=checkpoint;start=int(ctx.get('progress',0))
    elif existing:
        # The payload is at least as advanced as any checkpoint (or the
        # checkpoint row is gone): continue from the last confirmed payload
        # instead of replaying earlier evidence.
        ctx=_from_payload(existing);start=int(existing.get('progress',0))
    else:
        ctx=_fresh(base,base_id,text,digest);ctx['text']=text;start=0
    resumed=start>0

    ctx['manifest']['run_id']=derived_id
    started=perf_counter()
    if start: ctx['manifest']['resumed_from_stage']=start
    for index in range(start+1,stop+1):
        if index==3: _merge(ctx,base,base_id)
        STAGES[index-1](ctx)
        if index<3:
            # Earlier stages only see the revision file; link them to the base
            # batch so the page shows where their evidence came from.
            stage=ctx['stages'][str(index)]
            stage['linked']={'base_run_id':base_id,'base_revision':base.get('revision'),
                             'base_decision':base.get('decision'),
                             'factor_source':ctx['derivation'].get('factor_source'),
                             'entitlement_source':ctx['derivation'].get('entitlement_source')}
        ctx['progress']=index
        ctx.pop('text',None)
        ctx['manifest']['elapsed_ms']=round((perf_counter()-started)*1000,3)
        payload=serial(ctx)
        status='complete' if index==7 else 'partial'
        store.save_derived(derived_id,payload,base_id,key,text,index,status)
        if index==7:
            store.delete_checkpoint(derived_id)
            break
        store.save_checkpoint(derived_id,payload,index)

    result=store.find_derivation(key)
    if result is None:
        result={**serial(ctx),'revision':1,'decision':'待复核','parent':base_id,
                'progress':stop,'status':'partial'}
    result['reused']=bool(existing)
    result['resumed']=resumed
    return result
