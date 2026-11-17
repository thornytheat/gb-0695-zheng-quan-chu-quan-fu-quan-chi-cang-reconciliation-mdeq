from flask import request
from app.pipeline import calculate
from app.derivation import derive
from app.numeric import serial
from app.errors import DomainError


def stage(stage_number):
    """Shared handler for ``/api/m1``..``/api/m7``.

    ``base`` (or ``base_run_id``) derives the stage against a reviewed batch;
    otherwise the stage runs on the submitted/sample file alone.
    """
    payload=request.get_json(silent=True) or {}
    base=(payload.get('base') or payload.get('base_run_id') or payload.get('parent'))
    if base:
        result=derive(base,payload.get('csv') or payload.get('revision_csv'),stage_number)
        stage_result=result['stages'].get(str(stage_number))
        if stage_result is None: raise DomainError('STAGE','该阶段尚未确认',409)
        return {**stage_result,'run_id':result['manifest']['run_id'],'parent':result.get('parent'),
                'status':result.get('status'),'progress':result.get('progress'),
                'derivation':result.get('derivation')}
    result=calculate(payload.get('csv'), stop=stage_number)
    return serial(result['stages'][str(stage_number)])
