"""Separate delivery outbox: email failures never reissue a DTE."""
import os
import smtplib
import ssl
from email.message import EmailMessage
import billing


def deliver_one():
    if os.getenv('PSC_DTE_EMAIL_ENABLED') != 'true' or os.getenv('PSC_DTE_MODE') != 'production':
        return False
    with billing.db() as con:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('''SELECT j.order_id,j.folio,j.pdf,b.email FROM dte_jobs j
          JOIN billing_orders b USING(order_id) WHERE j.environment='production'
          AND j.verified_payment=1 AND j.state IN ('accepted','accepted_with_observations')
          AND j.email_state='pending' AND j.pdf IS NOT NULL LIMIT 1''').fetchone()
        if not row: return False
        # A crash or SMTP timeout is ambiguous. Do not blindly resend.
        con.execute("UPDATE dte_jobs SET email_state='sending' WHERE order_id=?",(row['order_id'],))
    message=EmailMessage()
    message['Subject']='Tu boleta electrónica · PSC Santibáñez'
    message['From']=os.environ['PSC_DTE_SMTP_FROM']
    message['To']=row['email']
    message['Message-ID']='<dte-'+row['order_id']+'@pscsantibanez.cl>'
    message.set_content('Adjuntamos tu boleta electrónica no afecta o exenta de IVA, folio '+row['folio']+'.\n\nPSC Santibáñez')
    message.add_attachment(bytes(row['pdf']),maintype='application',subtype='pdf',filename='boleta-'+row['folio']+'.pdf')
    try:
        with smtplib.SMTP_SSL(os.environ['PSC_DTE_SMTP_HOST'],int(os.getenv('PSC_DTE_SMTP_PORT','465')),
                              context=ssl.create_default_context(),timeout=25) as smtp:
            smtp.login(os.environ['PSC_DTE_SMTP_USER'],os.environ['PSC_DTE_SMTP_PASSWORD'])
            refused=smtp.send_message(message)
            if refused: raise smtplib.SMTPException('Recipient refused')
        state='sent'
    except (OSError,smtplib.SMTPException):
        state='review'
    with billing.db() as con:
        con.execute('UPDATE dte_jobs SET email_state=? WHERE order_id=?',(state,row['order_id']))
        con.execute('INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)',
                    (row['order_id'],'dte_email_'+state,billing.now(),'dte-worker'))
    return True
