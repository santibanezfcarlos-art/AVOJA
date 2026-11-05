"""No-network protocol test for the signed main PSC state bridge."""
import hashlib,hmac,json,os,unittest
from unittest.mock import patch
import psc_sqlite_fonasa_client as client

class Fake:
    def __init__(self,request):
        self.request=request
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,limit=None):return b'{"state":"REGISTERED"}'

class Protocol(unittest.TestCase):
    def test_hmac_roundtrip(self):
        captured=[]
        def fake(request,timeout=None):
            captured.append(request)
            return Fake(request)
        secret="x"*48
        with patch.dict(os.environ,{"PSICOLOGIA_DEMO_BRIDGE_KEY":secret}):
            with patch.object(client,"urlopen",side_effect=fake):
                self.assertEqual(client.call("register",{"source_id":"test-00000000001"}),"REGISTERED")
        self.assertEqual(len(captured),1)
        req=captured[0]
        doc=json.loads(req.data)
        sig=next(v for k,v in req.headers.items() if k.lower()=="x-psc-state-signature")
        ts=next(v for k,v in req.headers.items() if k.lower()=="x-psc-state-timestamp")
        expected=hmac.new(secret.encode(),ts.encode()+b".register."+req.data,hashlib.sha256).hexdigest()
        self.assertEqual(sig,expected)
        self.assertEqual(doc["source_id"],"test-00000000001")
if __name__=="__main__":unittest.main()
