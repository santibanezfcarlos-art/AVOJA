"""Isolated intake tests; never contact Fonasa, email, or Google Calendar."""
import hashlib
import io
import sys
from pathlib import Path
import pytest
from flask import Flask
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import billing
import fonasa_internal as f
import fonasa_public as public

ORIGIN={'Origin':'https://pscsantibanez.cl'}

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('PSICOLOGIA_BILLING_DB',str(tmp_path/'test.sqlite'))
    monkeypatch.setenv('PSICOLOGIA_BILLING_ADMIN_PASSWORD','test-only-password-12345')
    monkeypatch.delenv('RENDER',raising=False)
    monkeypatch.setattr(f,'parse_tcpdf',lambda raw:{'folio':'TEST001','rut':'17000344-6','patient_name':'PERSONA DE PRUEBA','emitted':'26/10/2026'})
    calls=[]
    def worker(path,body=None,timeout=12):
        calls.append(path)
        if path.endswith('/activate'):return {'state':'running','job_id':'A'*24}
        if path.endswith('/start'):return {'state':'running','job_id':'B'*24}
        if path.endswith('B'*24):return {'state':'ready_to_activate','activate_available':True,'activated':False}
        return {'state':'activated','activated':True}
    monkeypatch.setattr(f,'_call_worker',worker)
    app=Flask(__name__);app.config['TESTING']=True
    app.register_blueprint(f.bp);app.register_blueprint(public.bp)
    return app.test_client(),calls


def upload(client,consent='yes',headers=ORIGIN):
    return client.post('/api/fonasa/intake',headers=headers,data={'bono':(io.BytesIO(b'%PDF-test'),'bono.pdf'),'activation_consent':consent})


def advance(client,token):
    with billing.db() as con:
        con.execute('UPDATE fonasa_intake SET public_polled_at=NULL')
    return client.post('/api/fonasa/intake/progress',headers={**ORIGIN,'X-Fonasa-Token':token})


def test_patient_full_flow_activates_once_and_tracks_delivery(env):
    client,calls=env
    response=upload(client);assert response.status_code==201
    token=response.json['access_token']
    assert 'bono' not in response.json and 'rut' not in response.json
    assert advance(client,token).json['state']=='checking'
    assert advance(client,token).json['state']=='ready_to_activate'
    assert advance(client,token).json['state']=='activating'
    assert advance(client,token).json['state']=='processing'
    ready=advance(client,token).json
    assert ready['state']=='ready'
    assert advance(client,token).json['link']==ready['link']
    assert calls.count('/bridge/fonasa/activate')==1
    with billing.db() as con:
        row=con.execute('SELECT * FROM fonasa_intake').fetchone()
        assert f._admin_item(row)['link_generated'] is True
        assert row['link_delivered_at'] is None
        assert row['calendar_event_id'] is None
    ack=client.post('/api/fonasa/intake/link-received',headers={**ORIGIN,'X-Fonasa-Token':token})
    assert ack.status_code==200
    with billing.db() as con:
        row=con.execute('SELECT * FROM fonasa_intake').fetchone()
        assert row['link_delivery_method']=='patient_screen'
        assert row['activation_status']=='activated'
        assert f._by_token(con,ready['link'].split('#')[1]) is not None


def test_origin_consent_and_admin_auth(env):
    client,calls=env
    assert upload(client,headers={'Origin':'https://other.example'}).status_code==403
    assert upload(client,consent='no').status_code==400
    assert client.post('/admin/fonasa/upload').status_code==401
    assert calls==[]


def test_duplicate_does_not_disclose_or_reset_record(env):
    client,calls=env
    first=upload(client).json
    second=upload(client)
    assert second.status_code==409
    assert set(second.json)=={'error'}
    assert advance(client,first['access_token']).status_code==202
    with billing.db() as con:
        assert con.execute('SELECT count(*) FROM fonasa_intake').fetchone()[0]==1


def test_link_requires_activation_and_ack_requires_link(env):
    client,calls=env;token=upload(client).json['access_token']
    with billing.db() as con:
        row=con.execute('SELECT * FROM fonasa_intake').fetchone();id=row['id']
        con.execute("UPDATE fonasa_intake SET state='ready_to_activate' WHERE id=?",(id,))
    with client.application.test_request_context():
        assert client.application.make_response(f._issue_booking_link(id)).status_code==409
    assert client.post('/api/fonasa/intake/link-received',headers={**ORIGIN,'X-Fonasa-Token':token}).status_code==409


def test_uncertain_activation_is_only_looked_up(env):
    client,calls=env;token=upload(client).json['access_token']
    with billing.db() as con:
        con.execute("UPDATE fonasa_intake SET state='ready_to_activate',activation_status='needs_review'")
    assert advance(client,token).json['state']=='reviewing'
    assert calls==['/bridge/fonasa/start']


def test_throttle_invalid_token_and_private_history(env):
    client,calls=env;token=upload(client).json['access_token']
    assert client.post('/api/fonasa/intake/progress',headers={**ORIGIN,'X-Fonasa-Token':'x'*43}).status_code==404
    assert advance(client,token).json['state']=='checking'
    r=client.post('/api/fonasa/intake/progress',headers={**ORIGIN,'X-Fonasa-Token':token})
    assert r.status_code==202 and r.json['state']=='processing'
    assert len(calls)==1
    assert client.get('/admin/fonasa/items').status_code==401


def test_existing_schema_migrates_without_touching_confirmed_reservations(env):
    client,calls=env
    with billing.db() as con:
        con.executescript(f.SCHEMA)
        con.execute("INSERT INTO fonasa_intake(id,folio,rut,patient_name,emitted,state,created_at,updated_at,requested_date,requested_time) VALUES('old','OLD','17000344-6','PRUEBA','26/10/2026','calendar_confirmed',?,?,'2026-11-01','09:00')",(f._utc(),f._utc()))
    upload(client)
    with billing.db() as con:
        row=con.execute("SELECT * FROM fonasa_intake WHERE id='old'").fetchone()
        assert row['state']=='calendar_confirmed'
        assert row['requested_date']=='2026-11-01'
        assert row['requested_time']=='09:00'
    assert calls==[]
