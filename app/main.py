from flask import Flask, request, jsonify, Response
import csv,io
from app.errors import DomainError
from app.reader import decode
from app.config import SAMPLE
from app.pipeline import calculate
from app.derivation import derive
from app.store import save, latest, history, review, get_run, derivations
from app.web import bp as web

from app.modules.m1_receiving.handler import bp as bp1
from app.modules.m2_mapping.handler import bp as bp2
from app.modules.m3_registry.handler import bp as bp3
from app.modules.m4_factors.handler import bp as bp4
from app.modules.m5_holdings.handler import bp as bp5
from app.modules.m6_cash.handler import bp as bp6
from app.modules.m7_revisions.handler import bp as bp7
app=Flask(__name__)
app.config['MAX_CONTENT_LENGTH']=5*1024*1024
app.json.ensure_ascii=False
@app.template_filter('display')
def display(value):
    import re
    from decimal import Decimal
    text=str(value)
    if re.fullmatch(r'-?[0-9]+\.[0-9]+',text):
        return format(Decimal(text),'.4f').rstrip('0').rstrip('.')
    return text
app.register_blueprint(web)
app.register_blueprint(bp1)
app.register_blueprint(bp2)
app.register_blueprint(bp3)
app.register_blueprint(bp4)
app.register_blueprint(bp5)
app.register_blueprint(bp6)
app.register_blueprint(bp7)
@app.before_request
def validate_request_object():
    if request.method!='POST': return None
    if request.files:
        if request.path not in ('/api/run','/api/derive') or 'file' not in request.files:
            raise DomainError('REQUEST','上传必须使用file字段',400)
        return None
    if not request.is_json:
        raise DomainError('REQUEST','请求须为JSON对象或CSV文件',400)
    if not isinstance(request.get_json(silent=True),dict):
        raise DomainError('REQUEST','请求内容必须为JSON对象',400)
@app.errorhandler(DomainError)
def domain_error(error): return jsonify({'code':error.code,'message':error.message}),error.status
@app.errorhandler(413)
def too_big(error): return jsonify({'code':'LIMIT','message':'文件超过5MB'}),413
@app.route('/health')
def health(): return {'status':'ok'}
@app.route('/api/run',methods=['POST'])
def run():
    supplied=False
    if request.files:
        raw=request.files['file'].read();text=decode(raw);supplied=True;payload=request.form
    else:
        payload=request.get_json(silent=True) or {};text=payload.get('csv')
        supplied=text is not None
    if payload.get('base') or payload.get('base_run_id'):
        return jsonify(_revision_request(payload,text))     # run-as-revision convenience
    if not supplied: text=SAMPLE.read_text(encoding='utf-8-sig')
    result=calculate(text)
    return jsonify(save(result,source=text))
@app.route('/api/runs/<ident>')
def run_detail(ident): return jsonify(get_run(ident,with_source=False))
@app.route('/api/history')
def runs(): return jsonify(history())
@app.route('/api/derivations')
def derived():
    base=request.args.get('base') or request.args.get('run_id') or None
    if base is not None: get_run(base)
    return jsonify(derivations(base))

def _revision_request(body,text=None):
    ident=(body.get('base') or body.get('base_run_id') or body.get('parent')
           or body.get('id') or body.get('run_id'))
    if not ident: raise DomainError('BASE','必须指定原批次',422)
    if text is None:
        text=body.get('csv')
        if text is None: text=body.get('revision_csv') or body.get('text')
    if text is not None and not isinstance(text,str): raise DomainError('TYPE','CSV必须是文本')
    return derive(ident,text,body.get('stop',7))

@app.route('/api/derive',methods=['POST'])
def revision():
    if request.files:
        body=request.form;raw=request.files['file'].read();text=decode(raw)
    else:
        body=request.get_json(silent=True) or {};text=None
    return jsonify(_revision_request(body,text))

@app.route('/api/runs/<ident>/derive',methods=['POST'])
def revision_of(ident):
    body=request.get_json(silent=True) or {}
    body={**body,'base':ident}
    return jsonify(_revision_request(body))

@app.route('/api/runs/<ident>/derivations')
def derivations_of(ident):
    get_run(ident)
    return jsonify(derivations(ident))
@app.route('/api/review',methods=['POST'])
def decide():
    body=request.get_json(silent=True) or {}
    try: version=int(body.get('revision',0))
    except (ValueError,TypeError): raise DomainError('VERSION','版本必须为整数')
    return jsonify(review(body.get('id'),version,body.get('decision')))
@app.route('/api/evidence.csv')
def evidence():
    result=get_run(request.args['run_id']) if request.args.get('run_id') else (latest() or save(calculate()));out=io.StringIO(newline='')
    rows=[]
    for index,stage in result['stages'].items():
        rows.extend({'阶段':index,**r} for r in stage['rows'])
    rows.extend({'阶段':'隔离',**r} for r in result['quarantine'])
    derivation=result.get('derivation') or {}
    if derivation:
        original=derivation.get('original') or {}
        rows.append({'阶段':'派生关联','关联原批次':derivation.get('base_run_id'),
                     '原批次复核':derivation.get('base_decision'),'原批次修订号':derivation.get('base_revision'),
                     '原批次摘要':derivation.get('base_sha256'),
                     '因子版本来源':derivation.get('factor_source'),'权益版本来源':derivation.get('entitlement_source'),
                     '原批次持仓':original.get('positions'),'原批次现金凭据':original.get('cash_reconciliation'),
                     '原批次隔离':original.get('quarantine'),'原批次守恒':original.get('balanced')})
        for r in result['stages'].get('7',{}).get('current_entitlement') or []:
            rows.append({'阶段':'派生权益',**r})
        for r in derivation.get('late_records') or []:
            rows.append({'阶段':'晚到记录',**r,'原因':'晚到版本未回退当前版本'})
    fields=list(dict.fromkeys(['阶段','run_id','规则版本']+[k for r in rows for k in r]))
    writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader()
    for row in rows: writer.writerow({'run_id':result['manifest']['run_id'],'规则版本':result['manifest']['rule_version'],**row})
    return Response('﻿'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=evidence.csv'})
if __name__=='__main__': app.run(host='0.0.0.0',port=8000)
