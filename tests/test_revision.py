import json
import sqlite3

import pytest

from app.config import HEADERS, SAMPLE
from app.derivation import derive
from app.pipeline import calculate
from app.store import get_run, history, latest


def _csv(rows):
    return ','.join(HEADERS) + '\n' + ''.join(','.join(row) + '\n' for row in rows)


def _action(record, security, version, price='11', cash='0.13', bonus='0.1',
            rights='0.05', rights_price='5', event='2026-11-15',
            record_date='2026-11-17', ex_date='2026-11-18'):
    return [record, 'action', security, '', event, record_date, ex_date, price,
            cash, bonus, rights, rights_price, '', version, '']


@pytest.fixture
def base(client):
    raw = SAMPLE.read_text(encoding='utf-8-sig')
    run = client.post('/api/run', json={'csv': raw}).get_json()
    client.post('/api/review', json={'id': run['manifest']['run_id'], 'revision': 1, 'decision': '通过'})
    return run


def _derive(client, base, rows, stop=None):
    body = {'base': base['manifest']['run_id'], 'csv': _csv(rows)}
    if stop is not None:
        body['stop'] = stop
    response = client.post('/api/derive', json=body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_derivation_links_base_and_base_stays_reviewed(client, base):
    ident = base['manifest']['run_id']
    derived = _derive(client, base, [_action('A0001', '000001', '3', bonus='0.3')])
    assert derived['parent'] == ident
    assert derived['derivation']['base_decision'] == '通过'
    assert derived['status'] == 'complete'
    original = get_run(ident)
    assert original['decision'] == '通过' and original['revision'] == 2
    assert original['stages']['5'] == base['stages']['5']
    assert original['stages']['6'] == base['stages']['6']


def test_factor_uses_new_version_entitlement_keeps_registered_version(client, base):
    derived = _derive(client, base, [_action('A0001', '000001', '3', bonus='0.3')])
    factor = [r for r in derived['stages']['4']['rows'] if r['证券'] == '000001'][0]
    holding = [r for r in derived['stages']['5']['rows'] if r['证券'] == '000001'][0]
    assert factor['行动版本'] == '3'
    assert holding['行动版本'] == '2'            # registered version
    assert holding['新股份'] == '127'            # registered bonus 0.2, unchanged
    assert holding['分红_CNY'] == '13.26'
    assert factor['复权因子'] == '0.74882155'   # (11-0.13+0.25)/(1+0.3+0.05), v3
    assert derived['derivation']['current_versions']['000001|2026-11-18'] == 3
    assert derived['derivation']['registered_versions']['000001|2026-11-18'] == 2


def test_cash_reconciliation_uses_registered_voucher(client, base):
    derived = _derive(client, base, [_action('A0001', '000001', '3', cash='0.2', bonus='0.3')])
    cash = [r for r in derived['stages']['6']['rows'] if r['证券'] == '000001'][0]
    original = [r for r in base['stages']['6']['rows'] if r['证券'] == '000001'][0]
    assert cash == original
    assert cash['应付_CNY'] == '13.26' and cash['结论'] == '相符'
    assert derived['stages']['6']['linked']['voucher_source'] == '登记时版本'
    assert derived['stages']['6']['linked']['original_cash_rows'] == 19


def test_current_entitlement_derived_on_m7(client, base):
    derived = _derive(client, base, [_action('A0001', '000001', '3', bonus='0.3')])
    current = [r for r in derived['stages']['7']['current_entitlement'] if r['证券'] == '000001'][0]
    assert current['登记版本'] == '2' and current['当前版本'] == '3'
    assert current['登记新股份'] == '127'
    assert current['当前新股份'] == '137'
    assert current['当前分红_CNY'] == '13.26'
    page = client.get('/work/7?run_id=' + derived['manifest']['run_id']).data.decode()
    assert '当前权益结果（派生）' in page and '关联原批次' in page


def test_interrupted_derivation_resumes_and_original_stays_visible(client, base):
    ident = base['manifest']['run_id']
    rows = [_action('A0001', '000001', '3', bonus='0.4')]
    partial = _derive(client, base, rows, stop=4)
    assert partial['status'] == 'partial' and partial['progress'] == 4
    assert '5' not in partial['stages']          # unconfirmed stages are not published
    # A stage endpoint continues the same derivation from its confirmed stage.
    advanced = client.post('/api/m5', json={'base': base['manifest']['run_id'], 'csv': _csv(rows)}).get_json()
    assert advanced['stage'] == 5 and advanced['progress'] == 5
    assert advanced['run_id'] == partial['manifest']['run_id']
    # The original batch is still the one shown while the revision is unfinished.
    assert latest()['manifest']['run_id'] == ident
    for stage in range(1, 8):
        assert client.get('/work/%d?run_id=%s' % (stage, partial['manifest']['run_id'])).status_code == 200
    finished = _derive(client, base, rows)
    assert finished['status'] == 'complete'
    assert finished['manifest']['resumed_from_stage'] == 5
    assert finished['manifest']['run_id'] == partial['manifest']['run_id']
    assert latest()['manifest']['run_id'] == finished['manifest']['run_id']


def test_same_revision_resubmission_reuses_derived_result(client, base):
    rows = [_action('A0001', '000001', '3', bonus='0.3')]
    first = _derive(client, base, rows)
    stored = get_run(first['manifest']['run_id'])['stages']['5']
    # A later review of the derived batch must not be overwritten by a resubmit.
    client.post('/api/review', json={'id': first['manifest']['run_id'], 'revision': 1, 'decision': '通过'})
    again = _derive(client, base, rows)
    assert again['manifest']['run_id'] == first['manifest']['run_id']
    assert again['reused'] is True
    assert again['decision'] == '通过'
    assert get_run(first['manifest']['run_id'])['stages']['5'] == stored
    assert len([r for r in history() if r['parent'] == base['manifest']['run_id']]) == 1


def test_late_lower_version_never_rolls_back_current(client, base):
    # Base 000001 is already on version 2; a late version 1 row must be ignored.
    derived = _derive(client, base, [_action('A0001', '000001', '1', price='10', bonus='0.1')])
    factor = [r for r in derived['stages']['4']['rows'] if r['证券'] == '000001'][0]
    assert factor['行动版本'] == '2'
    assert derived['derivation']['current_versions']['000001|2026-11-18'] == 2
    late = derived['derivation']['late_records']
    assert late and late[0]['晚到版本'] == 1 and late[0]['当前版本'] == 2
    assert derived['stages']['4']['late_records'][0]['记录'] == 'A0001'
    page = client.get('/work/4?run_id=' + derived['manifest']['run_id']).data.decode()
    assert '行动晚到记录' in page


def test_same_version_different_definition_is_isolated(client, base):
    derived = _derive(client, base, [_action('A0001', '000001', '2', bonus='0.25')])
    factor = [r for r in derived['stages']['4']['rows'] if r['证券'] == '000001'][0]
    assert factor['行动版本'] == '2'
    assert factor['复权因子'] == '0.80872727'      # reviewed definition kept (bonus 0.2)
    conflicts = [q for q in derived['quarantine'] if q.get('record') == 'A0001']
    assert conflicts and all(q['原因码'] == 'CONFLICT' for q in conflicts)
    assert any('已复核批次冲突' in q['原因'] for q in conflicts)
    # The isolated row is not applied to the registered result either.
    holding = [r for r in derived['stages']['5']['rows'] if r['证券'] == '000001'][0]
    assert holding['新股份'] == '127'


def test_revision_file_conservation_and_counts(client, base):
    derived = _derive(client, base, [_action('A0001', '000001', '3', bonus='0.3')])
    conservation = derived['revision_conservation']
    assert conservation['input'] == 1 and conservation['balanced'] is True
    assert derived['stages']['6']['metrics'][-1][1] == '相符'
    assert derived['stages']['6']['linked']['original_balanced'] is True


def test_derivation_validation(client, base):
    ident = base['manifest']['run_id']
    assert client.post('/api/derive', json={'csv': _csv([_action('A0001', '000001', '3')])}).status_code == 422
    assert client.post('/api/derive', json={'base': 'absent', 'csv': 'x'}).status_code == 404
    assert client.post('/api/derive', json={'base': ident, 'csv': 'x', 'stop': 0}).status_code == 422
    assert client.post('/api/derive', json={'base': ident, 'csv': 'x', 'stop': 9}).status_code == 422
    assert client.post('/api/derive', json={'base': ident, 'csv': 7}).status_code == 422


def test_incomplete_derivation_cannot_be_reviewed(client, base):
    partial = _derive(client, base, [_action('A0001', '000001', '3', bonus='0.3')], stop=3)
    response = client.post('/api/review', json={'id': partial['manifest']['run_id'],
                                                'revision': 1, 'decision': '通过'})
    assert response.status_code == 409
    assert response.get_json()['code'] == 'INCOMPLETE'


def test_stage_endpoint_for_derivation(client, base):
    body = {'base': base['manifest']['run_id'], 'csv': _csv([_action('A0001', '000001', '3', bonus='0.4')])}
    for number in (3, 4, 5, 6, 7):
        payload = client.post('/api/m%d' % number, json=body).get_json()
        assert payload['stage'] == number
        assert payload['parent'] == base['manifest']['run_id']
    assert payload['progress'] == 7


def test_legacy_database_is_migrated(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB', str(tmp_path / 'old.sqlite3'))
    raw = SAMPLE.read_text(encoding='utf-8-sig')
    result = calculate(raw)
    ident = result['manifest']['run_id']
    db = sqlite3.connect(tmp_path / 'old.sqlite3')
    db.execute('CREATE TABLE runs(id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('INSERT INTO runs(id,payload,revision,decision) VALUES (?,?,?,?)',
               (ident, json.dumps(result, ensure_ascii=False), 3, '通过'))
    db.commit()
    db.close()
    migrated = get_run(ident)
    assert migrated['decision'] == '通过' and migrated['revision'] == 3
    assert migrated['status'] == 'complete' and migrated['parent'] is None
    assert len(migrated['stages']['5']['rows']) == 19
    derived = derive(ident, _csv([_action('A0001', '000001', '3', bonus='0.4')]))
    assert derived['status'] == 'complete' and derived['parent'] == ident
