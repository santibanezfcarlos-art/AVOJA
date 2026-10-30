from test_fonasa_patient import env, upload, advance
import billing
import fonasa_internal as f


def test_sleeping_lookup_recovers_without_reupload_or_activation(env, monkeypatch):
    client, calls = env
    access = upload(client).json['access_token']
    worker = f._call_worker
    attempts = []
    def cold_once(path, body=None, timeout=12):
        attempts.append(path)
        if len(attempts) == 1:
            raise RuntimeError('No se pudo contactar el servicio de consulta Fonasa.')
        return worker(path, body, timeout)
    monkeypatch.setattr(f, '_call_worker', cold_once)
    first = advance(client, access)
    assert first.status_code == 202
    assert first.json['state'] == 'warming'
    assert first.json['retry_after'] == 17
    second = advance(client, access)
    assert second.status_code == 202
    assert second.json['state'] == 'checking'
    with billing.db() as con:
        row = con.execute('SELECT * FROM fonasa_intake').fetchone()
        assert row['worker_job'] == 'B'*24
        assert row['activation_status'] == 'not_requested'
        assert con.execute('SELECT count(*) FROM fonasa_intake').fetchone()[0] == 1
    assert all(path.endswith('/start') for path in attempts)


def test_actual_validation_failure_is_not_retried(env):
    client, calls = env
    access = upload(client).json['access_token']
    with billing.db() as con:
        con.execute("UPDATE fonasa_intake SET state='check_failed',worker_job='existing-job',check_detail='Bono no encontrado'")
    response = advance(client, access)
    assert response.status_code == 409
    assert calls == []


def test_uncertain_activation_is_not_restarted_as_lookup(env, monkeypatch):
    client, calls = env
    access = upload(client).json['access_token']
    with billing.db() as con:
        con.execute("UPDATE fonasa_intake SET state='check_failed',activation_status='needs_review',check_detail='No se pudo contactar el servicio de consulta Fonasa.'")
    response = advance(client, access)
    assert response.status_code == 409
    assert calls == []
