'use strict';
document.addEventListener('DOMContentLoaded',()=>{
  const button=document.getElementById('siiLoginTest');
  const notice=document.getElementById('notice');
  if(!button||!notice)return;
  button.addEventListener('click',async()=>{
    button.disabled=true;
    notice.textContent='Probando acceso al portal SII desde Render, sin emitir boletas…';
    try{
      const response=await fetch('/admin/boletas/sii-login-test',{
        method:'POST',credentials:'same-origin',
        headers:{'X-Billing-Action':'manual-invoice'},
      });
      const result=await response.json();
      if(!response.ok)throw new Error(result.error||'No se pudo conectar.');
      notice.textContent=(result.message||'Prueba ejecutada.')+' Estado: '+result.status;
    }catch(error){notice.textContent=error.message||'Error de conexión.'}
    finally{button.disabled=false}
  });
});