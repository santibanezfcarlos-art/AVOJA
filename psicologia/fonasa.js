'use strict';
(()=>{
 const $=id=>document.getElementById(id),API='https://pscsantibanez.cl';
 const KEY='psc_fonasa_intake_v1';let access=null,busy=false,timer=null,attempts=0;
 try{access=sessionStorage.getItem(KEY)}catch{}
 function message(text,warn=false){$('message').hidden=false;$('message').textContent=text;$('message').classList.toggle('warn',warn)}
 async function call(path,body){
  const headers={};if(access)headers['X-Fonasa-Token']=access;
  const response=await fetch(API+path,{method:'POST',headers,body,cache:'no-store',credentials:'omit'});
  const data=await response.json().catch(()=>({error:'No se pudo leer la respuesta del servidor.'}));
  if(!response.ok){const error=Error(data.error||'No se pudo completar la validación.');error.status=response.status;throw error}return data;
 }
 function steps(state){
  const n={pdf_read:0,checking:1,ready_to_activate:2,activating:2,reviewing:2,ready:4}[state]??1;
  [...$('progressSteps').children].forEach((el,i)=>{el.classList.toggle('done',i<n);el.classList.toggle('active',i===n)});
 }
 async function progress(){
  if(busy||!access)return;busy=true;clearTimeout(timer);$('resume').hidden=true;
  $('uploadCard').hidden=true;$('progressCard').hidden=false;
  try{
   const data=await call('/api/fonasa/intake/progress');steps(data.state);
   if(data.state==='ready'){
    // Only links on our own booking page are rendered.
    const url=new URL(data.link);if(url.origin!=='https://pscsantibanez.cl'||url.pathname!=='/fonasa-reserva.html')throw Error('El enlace no pudo verificarse. Contacta al profesional.');
    $('bookingLink').href=data.link;$('folio').textContent='Activación confirmada · Folio '+data.folio;
    $('ready').hidden=false;$('progressCard').hidden=true;message('Bono validado y activado. Ya puedes agendar.');
    try{await call('/api/fonasa/intake/link-received')}catch{}
    return;
   }
   if(data.state==='already_booked'){message(data.message);$('newVoucher').hidden=false;return}
   message(data.message|| (data.state==='activating'?'Fonasa está procesando la activación de tu bono…':data.state==='reviewing'?'Estamos comprobando el resultado de la activación en Fonasa…':'Estamos validando tu bono en Fonasa…'));
   if(++attempts>=24){message('La verificación está tardando más de lo habitual. Puedes revisar el resultado en unos minutos o contactar al profesional.');$('resume').hidden=false;return}
   timer=setTimeout(progress,(data.retry_after||17)*1000);
  }catch(err){message(err.message,true);$('resume').hidden=err.status===404;$('newVoucher').hidden=err.status!==404}
  finally{busy=false}
 }
 $('patientUpload').addEventListener('submit',async ev=>{
  ev.preventDefault();if(busy)return;
  const file=$('voucher').files[0];if(!file)return;
  if(file.size>2_000_000){message('Adjunta un PDF de hasta 2 MB.',true);return}
  busy=true;$('submit').disabled=true;message('Leyendo y comprobando los datos de tu bono…');
  try{
   const body=new FormData();body.append('bono',file);body.append('activation_consent',$('consent').checked?'yes':'no');
   const data=await call('/api/fonasa/intake',body);access=data.access_token;
   try{sessionStorage.setItem(KEY,access)}catch{}
   busy=false;attempts=0;await progress();
  }catch(err){message(err.message,true)}finally{busy=false;$('submit').disabled=false}
 });
 $('newVoucher').onclick=()=>{access=null;clearTimeout(timer);try{sessionStorage.removeItem(KEY)}catch{}$('uploadCard').hidden=false;$('progressCard').hidden=true;$('ready').hidden=true;$('resume').hidden=true;$('newVoucher').hidden=true;$('message').hidden=true;$('patientUpload').reset()};
 $('resume').onclick=()=>{attempts=0;progress()};
 if(access){message('Tienes una validación en curso en este navegador.');$('resume').hidden=false;$('uploadCard').hidden=true;$('progressCard').hidden=false;}
})();
