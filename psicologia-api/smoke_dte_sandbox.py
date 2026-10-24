"""Explicit opt-in: vendor development API, synthetic receiver, no email.
PSC_DTE_SANDBOX_API_KEY must be the public development key from vendor docs.
Never reads issuer/production credentials. No database or Calendar writes.
"""
import argparse
import uuid
import billing
from dte import SANDBOX_ISSUER
from dte_provider import OpenFactura, payload, pdf_bytes


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-sandbox',action='store_true')
    args=parser.parse_args()
    if not args.run_sandbox: parser.error('Requires --run-sandbox')
    provider=OpenFactura('integration')
    body=payload({'amount':1000,'rut':'66666666-6','name':'PRUEBA SIN VALIDEZ TRIBUTARIA',
      'address':'DIRECCION DE PRUEBA 123','phone':'000000000','detail':'PRUEBA DTE 41 SIN VALIDEZ TRIBUTARIA'},
      SANDBOX_ISSUER,billing.now()[:10])
    result=provider.issue(body,'psc-sandbox-'+uuid.uuid4().hex)
    token=result['TOKEN']
    pdf=pdf_bytes(result['PDF'])
    document=provider.get(token,'json')['json']
    assert str(document['Encabezado']['IdDoc']['TipoDTE'])=='41'
    print('Sandbox DTE 41 OK. PDF bytes:',len(pdf),'Folio de prueba:',result['FOLIO'])

if __name__=='__main__':main()
