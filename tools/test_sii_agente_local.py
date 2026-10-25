import json
import ssl
from urllib.error import HTTPError, URLError
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import sii_agente_local as agent

class AgentTests(unittest.TestCase):
    def test_safe_connection_diagnostics(self):
        secret='PRIVATE_PASSWORD_AND_BODY'
        for code in (401,403,404,429,500,503):
            exc=HTTPError('https://example.invalid/'+secret,code,secret,{},None)
            message=agent.connection_error(exc)
            self.assertNotIn(secret,message)
            self.assertNotIn('example.invalid',message)
        self.assertIn('Usuario o contraseña',agent.connection_error(HTTPError('',401,secret,{},None)))
        self.assertIn('HTTPS',agent.connection_error(URLError(ssl.SSLError(secret))))
        self.assertIn('tiempo',agent.connection_error(URLError(TimeoutError(secret))))
        self.assertIn('red',agent.connection_error(URLError(secret)))
        self.assertNotIn(secret,agent.connection_error(RuntimeError(secret)))

    def test_never_accepts_issuance_job(self):
        with patch.object(agent.subprocess,'run') as run:
            self.assertEqual(agent.run_job({'mode':'issue'},Path('.'))['status'],'blocked')
            run.assert_not_called()
    def test_preview_command_and_temporary_data_cleanup(self):
        seen=[]
        def execute(command, **kwargs):
            self.assertNotIn('--autorizar-orden',command)
            data=Path(command[command.index('--datos')+1]);seen.append(data)
            self.assertTrue(data.exists())
            result=Path(command[command.index('--resultado')+1])
            result.write_text(json.dumps({'status':'prepared','emitted':False}))
            return SimpleNamespace(returncode=0)
        with patch.object(agent.subprocess,'run',side_effect=execute):
            result=agent.run_job({'mode':'preview','data':{'monto':30000}},Path('.'))
        self.assertEqual(result,{'status':'prepared','emitted':False})
        self.assertFalse(seen[0].exists())
    def test_child_failure_never_success(self):
        with patch.object(agent.subprocess,'run',return_value=SimpleNamespace(returncode=1)):
            result=agent.run_job({'mode':'preview','data':{'monto':30000}},Path('.'))
        self.assertEqual(result['status'],'failed')

if __name__=='__main__':unittest.main()
