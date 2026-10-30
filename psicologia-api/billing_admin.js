'use strict';
const $=id=>document.getElementById(id);
let generation=0;
let siiJobs=[];
let receiptMap={};
let issuanceEnabled=false;
const issueProgress=Object.create(null);
const issueTimers=Object.create(null);
const issueRunning=Object.create(null);
function issueMessage(order,message){
 issueProgress[order]=message;
 const box=Array.from(document.querySelectorAll('[data-issue-order]'))
                .find(e=>e.dataset.issueOrder===order);
 if(box)box.textContent=message;
}
const previewProgress=Object.create(null);
const previewRunning=Object.create(null);
const previewTimers=Object.create(null);
function previewMessage(order,message){
 previewProgress[order]=message;
 const status=Array.from(document.querySelectorAll('[data-preview-order]')).find(e=>e.dataset.previewOrder===order);
 if(status)status.textContent=message;
}
const siiLabels={queued:"Esperando agente local",running:"Preparando en tu computador",prepared:"Formulario verificado · sin emitir",blocked:"Detenido · revisar formulario",session_required:"Renovar sesión SII",failed:"Preparación fallida",reconciliation:"Pendiente de conciliación"};
const labels={initiated:'Pago sin confirmar',approved:'Pago aprobado',rejected:'Pago rechazado',cancelled:'Pago cancelado',pending:'Boleta pendiente',issued:'Boleta emitida',sent_manually:'Boleta enviada manualmente',test:'Prueba · no emitir',not_applicable:'No corresponde emitir'};
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function query(){const p=new URLSearchParams();for(const id of ['environment','status','from','to','q'])p.set(id,$(id).value);return p.toString()}
async function copy(value){try{await navigator.clipboard.writeText(String(value));$('notice').textContent='Copiado. Puedes pegarlo en el SII.'}catch{$('notice').textContent='No se pudo copiar automáticamente. Selecciona el texto y usa Ctrl+C.'}}
function field(container,label,value,wide=false){const box=node('div',undefined,'field'+(wide?' wide':''));const text=node('div');text.append(node('span',label),node('strong',value));const b=node('button','Copiar');b.type='button';b.setAttribute('aria-label','Copiar '+label);b.onclick=()=>copy(value);box.append(text,b);container.append(box)}
function card(r){const section=node('section',undefined,'order');section.append(node('h2',r.name),node('p',r.order_id+' · Compra: '+r.created_at+' · Atención: '+r.appointment_date+' '+r.appointment_time,'meta'),node('span',(labels[r.payment_status]||r.payment_status)+' · '+(labels[r.invoice_status]||r.invoice_status),'badge'));
if(r.environment==='integration')section.append(node('p','Simulación de Webpay. Puedes previsualizar el formulario SII; la emisión real está bloqueada.','warning'));
const fields=node('div',undefined,'fields');const pairs=[['Monto',r.amount],['Tipo de documento','Boleta exenta'],['Método de pago',r.payment_method],['RUT receptor',r.rut],['Nombre receptor',r.name],['Dirección receptor',r.address],['E-mail receptor',r.email],['Teléfono receptor',r.phone],['Detalle',r.detail]];
for(const [label,value] of pairs)field(fields,label,value,['Dirección receptor','Detalle'].includes(label));section.append(fields);
const actions=node('div',undefined,'actions'),all=node('button','Copiar ficha completa');all.onclick=()=>copy(pairs.map(([k,v])=>k+': '+v).join('\n'));actions.append(all);

if(r.payment_status==='approved'&&!r.folio&&((r.environment==='production'&&r.invoice_status==='pending')||(r.environment==='integration'&&r.invoice_status==='test'))){
 const jobs=siiJobs.filter(j=>j.order_id===r.order_id);
 const active=jobs.find(j=>['queued','running','reconciliation'].includes(j.status));
 const latest=jobs[0];
 if(latest&&r.environment==='integration')section.append(node('p','Agente SII: '+(siiLabels[latest.status]||latest.status)));
 if(r.environment==='production'){
  const prepare=node('button','Preparar en SII (sin emitir)','primary');
  prepare.type='button';
  prepare.disabled=!!previewRunning[r.order_id]||r.issuance_state==='reconciliation'||r.issuance_state==='processing';
  prepare.title='Ejecuta Chromium en Render sin abrir otra pestaña y sin emitir un documento.';
  prepare.onclick=async()=>{
   if(previewRunning[r.order_id])return;
   previewRunning[r.order_id]=true;prepare.disabled=true;
   previewMessage(r.order_id,'Iniciando Chromium en Render…');
   try{
    const response=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/prepare-render',{
       method:'POST',headers:{'X-Billing-Action':'manual-invoice'}});
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'No se pudo preparar el formulario.');
    previewMessage(r.order_id,'Chromium iniciado. Ingresando al SII…');
    pollPreview(r.order_id);
   }catch(err){
    previewRunning[r.order_id]=false;
    previewMessage(r.order_id,'Preparación no iniciada: '+err.message);
    prepare.disabled=false;
   }
  };
  actions.append(prepare);
  section.append(node('p',r.issuance_state==='awaiting_issuer'?'Compra aprobada · boleta pendiente.':'Compra aprobada · revisión SII pendiente.','meta'));
 }
 if(r.environment==='production'){
  if(r.issuance_state==='reconciliation'||r.issuance_state==='processing'){
   const message=r.issuance_state==='processing'
     ?'Emisión solicitada en Render. No vuelvas a pulsar Emitir hasta conocer el resultado.'
     :'El SII recibió un intento de emisión, pero PSC no ha confirmado su folio. Comprueba en eBoleta si existe el documento antes de hacer cualquier otra operación.';
   const warning=node('p',message,'warning');
   warning.setAttribute('role','status');
   section.append(warning);
   if(issueProgress[r.order_id]){
    const live=node('p',issueProgress[r.order_id],'sii-progress');
    live.dataset.issueOrder=r.order_id;
    live.setAttribute('role','status');live.setAttribute('aria-live','polite');
    actions.append(live);
   }
  }else{
   // Never navigate to SII: ALL real issuance starts only through the
   // authenticated PSC backend -> Render Chromium. If this purchase is not
   // authorized for issuance, the backend returns a visible inline error.
   const emit=node('button','Emitir','primary');emit.type='button';
   emit.disabled=!issuanceEnabled||!!issueRunning[r.order_id]||!!previewRunning[r.order_id];
   emit.title='Inicia la emisión real en Chromium de Render, sin abrir pestañas.';
   emit.onclick=async()=>{
    if(issueRunning[r.order_id]||previewRunning[r.order_id])return;
    issueRunning[r.order_id]=true;emit.disabled=true;
    issueProgress[r.order_id]='Solicitando emisión al servicio interno de Render…';
    issueMessage(r.order_id,issueProgress[r.order_id]);
    try{
     const res=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/emit',{
       method:'POST',headers:{'X-Billing-Action':'manual-invoice'}});
     const data=await res.json();
     if(!res.ok)throw Error(data.error||'Render no confirmó el inicio de emisión.');
     issueMessage(r.order_id,'Chromium iniciado. Preparando la boleta real en SII…');
     pollIssue(r.order_id);
    }catch(err){
     issueRunning[r.order_id]=false;
     issueMessage(r.order_id,'No se inició una nueva emisión: '+err.message);
     await refresh();
    }
   };actions.append(emit);
   const issueState=node('p',issueProgress[r.order_id]||'','sii-progress');
   issueState.dataset.issueOrder=r.order_id;
   issueState.setAttribute('role','status');
   issueState.setAttribute('aria-live','polite');
   actions.append(issueState);

  }
 }
 if(r.environment==='integration'&&active&&active.mode==='preview'){
  const close=node('button','Cerrar preparación interrumpida');close.type='button';
  close.onclick=async()=>{
   if(!confirm('Primero detén el agente con Ctrl+C y cierra su Chromium. ¿Ya está detenido?'))return;
   try{const res=await fetch('/admin/boletas/sii-jobs/'+active.id+'/close',{method:'POST',headers:{'Content-Type':'application/json','X-Billing-Action':'manual-invoice'},body:JSON.stringify({agent_stopped:true})});if(!res.ok)throw Error('No fue posible cerrar el trabajo');await refresh()}catch(e){$('notice').textContent=e.message}
  };actions.append(close);
 }
 if(r.environment==='integration'){
 const prep=node('button','Previsualizar prueba SII (sin emitir)');prep.type='button';prep.disabled=!!active;
 prep.onclick=async()=>{
  prep.disabled=true;$('notice').textContent='Validando orden en el servidor…';
  try{
   const res=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/sii-jobs',{
    method:'POST',headers:{'Content-Type':'application/json','X-Billing-Action':'manual-invoice'},body:JSON.stringify({mode:'preview'})});
   const data=await res.json();if(!res.ok)throw Error(data.error||'No fue posible preparar la orden');
   await refresh();$('notice').textContent='Compra enviada al agente local. Revisa el navegador que se abrirá en tu computador. Cierra esa ventana al terminar y pulsa Actualizar. No se emitirá una boleta.';
  }catch(error){$('notice').textContent=error.message;prep.disabled=false}
 };actions.append(prep);
 }
 if(r.environment==='production'){
  const state=node('p',previewProgress[r.order_id]||'','sii-progress');
  state.dataset.previewOrder=r.order_id;
  state.setAttribute('role','status');state.setAttribute('aria-live','polite');
  actions.append(state);
 }
}
if(r.invoice_status==='sent_manually'){section.append(node('p','Envío manual registrado · gestión finalizada.','meta'));}
if(r.invoice_status==='issued'){
 section.append(node('p','Folio '+r.folio+' · Emisión '+r.issued_date));
 // A purchase is shown as fully delivered only when the official PDF is
 // persisted and the transactional provider accepted the patient's email.
 // Never equate "folio issued" with "email delivered" without this evidence.
 const completedReceipt=receiptMap[r.order_id];
 if(r.environment==='production'&&completedReceipt&&completedReceipt.has_pdf&&completedReceipt.psc_state==='accepted'){
  section.append(node('p','Boleta OK · PDF oficial guardado y envío al paciente aceptado.','meta'));
 }
 if(r.environment==='production'){
  const receipt=receiptMap[r.order_id];
  const hasPdf=!!(receipt&&receipt.has_pdf);
  const describe=s=>s==='accepted'?'aceptado por el servicio':s==='pending'?'pendiente':s==='sending'?'en curso; no reintentar':s==='unknown'?'resultado incierto; revisar antes de reenviar':'sin confirmar';
  section.append(node('p',hasPdf?'PDF oficial guardado como respaldo de la compra.':'Boleta emitida · PDF oficial pendiente de guardar.','meta'));
  if(receipt){
   section.append(node('p','Correo SII'+(receipt.sii_recipient?' a '+receipt.sii_recipient:'')+': '+describe(receipt.sii_state)+' · Correo PSC al paciente: '+describe(receipt.psc_state),'meta'));
   if(receipt.resend_count) section.append(node('p','Reenvíos PSC: '+receipt.resend_count+' · Último: '+describe(receipt.resend_state),'meta'));
  }
  if(hasPdf){
   const download=node('a','Descargar PDF oficial');download.href='/admin/boletas/'+encodeURIComponent(r.order_id)+'/official-pdf';
   actions.append(download);
   const delivered=receipt.psc_state==='accepted';
   const send=node('button',delivered?'Reenviar al paciente':'Enviar al paciente');send.type='button';
   send.disabled=!delivered&&receipt.psc_state!=='pending'
      ||delivered&&['sending','unknown'].includes(receipt.resend_state);
   send.title=delivered?'Reenvío nuevo y explícito del PDF guardado desde PSC.':'Envía el PDF oficial de esta compra desde PSC.';
   send.onclick=async()=>{
    if(delivered&&!confirm('¿Reenviar una nueva copia del PDF oficial al correo registrado para esta compra?'))return;
    send.disabled=true;$('notice').textContent='Consultando el envío de la boleta…';
    try{
     const route=delivered?'resend-psc':'send-psc';
     const response=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/'+route,
       {method:'POST',headers:{'X-Billing-Action':'manual-invoice'}});
     const result=await response.json();if(!response.ok)throw Error(result.error||'No se pudo procesar');
     await refresh();
     $('notice').textContent=result.state==='accepted'?'El proveedor de correo aceptó '+(delivered?'el reenvío.':'la boleta con PDF.'):'Resultado incierto. Revisa antes de otro envío.';
    }catch(e){$('notice').textContent=e.message;await refresh()}
   };
   actions.append(send);
  }else{
   const form=node('form',undefined,'actions'),label=node('label','PDF oficial SII'),file=node('input');
   file.type='file';file.accept='application/pdf,.pdf';file.required=true;label.append(file);
   const save=node('button','Guardar PDF oficial','primary');save.type='submit';form.append(label,save);
   form.onsubmit=async e=>{
    e.preventDefault();save.disabled=true;
    try{
     const body=new FormData();body.append('file',file.files[0]);
     const response=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/official-pdf',
       {method:'POST',headers:{'X-Billing-Action':'manual-invoice'},body});
     const result=await response.json();if(!response.ok)throw Error(result.error||'No se pudo guardar el PDF');
     await refresh();$('notice').textContent='PDF oficial guardado en la compra.';
    }catch(err){$('notice').textContent=err.message;save.disabled=false}
   };actions.append(form);
  }
 }
}
if(r.environment==='production'&&r.payment_status==='approved'&&r.invoice_status==='pending'){
 const manual=node('details');manual.className='manual-folio';
 manual.append(node('summary','Registrar manualmente una boleta ya emitida fuera de PSC'));
 const form=node('form',undefined,'actions');const fl=node('label','Folio emitido en SII'),folio=node('input');folio.required=true;folio.pattern='[1-9][0-9]{0,9}';folio.inputMode='numeric';fl.append(folio);
 const dl=node('label','Fecha de emisión'),date=node('input');date.type='date';date.required=true;date.min=r.created_at.slice(0,10);dl.append(date);
 const button=node('button','Marcar como emitida','primary');button.type='submit';form.append(fl,dl,button);
 form.onsubmit=async e=>{e.preventDefault();if(!confirm('¿Ya emitiste en el SII la boleta con folio '+folio.value+'?'))return;button.disabled=true;try{const res=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/issued',{method:'POST',headers:{'Content-Type':'application/json','X-Billing-Action':'manual-invoice'},body:JSON.stringify({folio:folio.value,issued_date:date.value})});const result=await res.json();if(!res.ok)throw Error(result.error||'No se pudo registrar');await refresh();$('notice').textContent='Folio registrado correctamente.'}catch(err){$('notice').textContent=err.message}finally{button.disabled=false}};
 manual.append(form);actions.append(manual);
}section.append(actions);return section}
async function pollPreview(order){
 if(previewTimers[order])return;
 let checks=0;previewTimers[order]=true;
 const finish=async (message)=>{
   previewRunning[order]=false;delete previewTimers[order];
   previewMessage(order,message);
   await refresh();
 };
 const step=async()=>{
  try{
   const res=await fetch('/admin/boletas/'+encodeURIComponent(order)+'/prepare-status',{cache:'no-store'});
   if(!res.ok)throw Error('No se pudo consultar Render');
   const value=await res.json();
   const stage=value.stage||value.state;
   const labels={
    starting:'Iniciando Chromium en Render…',opening_sii:'Abriendo portal SII…',
    sii_authentication:'Ingresando al SII…',waiting_before_login:'Verificando credenciales…',
    waiting_calculator:'Cargando e-Boleta…',calculator_ready:'Calculadora SII lista…',
    preparing_order:'Preparando monto y receptor…',
    waiting_receiver_form:'Abriendo formulario del receptor…',
    filling_receiver:'Completando datos del receptor…',
    prepared_not_issued:'Formulario completo y verificado. NO se emitió una boleta.',
    filled_document_type_unverified:'Campos completados, pero falta verificar boleta exenta. NO se emitió.',
    blocked_receiver_verification:'El SII no validó un campo. NO se emitió.',
    calculator_not_ready:'No se pudo abrir la calculadora SII.',
    not_running:'Chromium no informa un proceso activo. Comprueba el estado antes de reintentar.',
    issued:'La boleta ya figura como emitida en el registro.'
   };
   const text=labels[stage]||((value.state==='blocked'||value.state==='failed')
                       ?'Preparación detenida: '+stage+'. NO se emitió desde esta preparación.'
                       :'Procesando en Render: '+stage+'…');
   previewMessage(order,text);
   if(value.state==='prepared'||value.state==='blocked'||value.state==='failed'
      ||value.state==='expired'||value.state==='not_running'||value.state==='issued'
      ||stage==='prepared_not_issued'||stage.startsWith('blocked_')){
     await finish(text);return;
   }
   if(++checks>120){await finish('La preparación supera el tiempo previsto; comprueba Render antes de reintentar.');return}
   previewTimers[order]=setTimeout(step,3000);
  }catch(err){await finish('No se pudo consultar el estado de Chromium. Revisa antes de repetir.')}
 };
 await step();
}

async function pollIssue(order){
 if(issueTimers[order])return;
 let checks=0;
 const terminal=new Set(['issued','reconciliation','failed','expired','unavailable','not_running','not_started']);
 const finish=async (message)=>{
   issueRunning[order]=false;
   delete issueTimers[order];
   issueMessage(order,message);
   // Rebuild only once at completion, not on every status poll.
   await refresh({quiet:true});
 };
 const step=async()=>{
  try{
   const response=await fetch('/admin/boletas/'+encodeURIComponent(order)+'/issue-status',{cache:'no-store'});
   if(!response.ok)throw Error('No se pudo consultar el estado de Render.');
   const result=await response.json();
   const stage=result.stage||result.state||'unknown';
   const labels={
      starting:'Iniciando Chromium en Render…',
      render_waking:'Iniciando el navegador SII en Render. La primera conexión puede tardar hasta 90 segundos…',
      start_failed_rearmed:'El navegador no llegó a iniciar la emisión. No se emitió una boleta; el botón Emitir está disponible nuevamente.',
      credential_fetch:'Recuperando datos de la compra…',
      opening_sii:'Abriendo SII…',
      sii_authentication:'Ingresando al SII…',
      waiting_before_login:'Validando la sesión SII…',
      waiting_calculator:'Cargando e-Boleta…',
      preparing_order:'Completando datos de la boleta…',
      waiting_receiver_form:'Abriendo los datos del receptor…',
      filling_receiver:'Completando receptor…',
      prepared_canary_awaiting_approval:'Formulario validado; preparando emisión real…',
      issue_approved_pending_claim:'Formulario validado. Iniciando emisión real…',
      claiming_canary:'Registrando intento único y enviando al SII…',
      waiting_sii_receipt:'Esperando comprobante oficial del SII (hasta 90 segundos)…',
      downloading_sii_pdf:'Boleta confirmada. Descargando el PDF original del SII para guardar el respaldo…',
      sending_sii_company_email:'PDF guardado. Enviando copia desde el SII al correo de PSC…',
      canary_clicked_reconciliation:'Se pulsó Emitir en SII, pero no se confirmó el folio. Verifica el documento oficial antes de repetir.',
      canary_attempt_reconciliation:'Intento SII incierto: verifica si se generó folio antes de repetir.',
      canary_issued_pdf_saved:'Boleta confirmada: folio y PDF guardados.',
      canary_issued_pdf_saved_sii_accepted:'Boleta confirmada: PDF guardado y envío del SII al correo de PSC aceptado.',
      canary_issued_pdf_saved_sii_unknown:'Boleta y PDF guardados. El envío del SII quedó sin confirmar; revisar antes de reenviar.',
      canary_issued_pdf_saved_sii_blocked_mail_controls:'Boleta y PDF guardados. No se pudo identificar el botón de correo del SII.',
      canary_issued_pdf_saved_sii_blocked_claim:'Boleta y PDF guardados. No se autorizó otro envío de correo desde el SII.',
      canary_issued_pdf_saved_sii_blocked_document_unverified:'Boleta y PDF guardados. El documento cambió en el portal; envío SII detenido.',
      canary_issued_pdf_pending:'Boleta confirmada: falta guardar el PDF oficial.',
      issued:'Boleta emitida y folio registrado.',
      reconciliation:'Emisión con resultado incierto; revisar folio en SII. No volver a emitir.',
      not_running:'No hay una sesión Chromium activa. Verifica primero el estado de esta compra.'
   };
   const message=labels[stage]||
       (result.state==='reconciliation'?'Resultado SII por conciliar: verifica el folio antes de volver a emitir.'
        :'Estado de emisión: '+stage);
   issueMessage(order,message);
   if(terminal.has(result.state)||stage.startsWith('canary_')||stage.startsWith('blocked_')){
     await finish(message);
     return;
   }
   if(++checks>240){
     await finish('El proceso no confirmó su resultado. Verifica la boleta en SII; no repitas la emisión.');
     return;
   }
   issueTimers[order]=setTimeout(step,3500);
  }catch(err){
   await finish('No fue posible comprobar el resultado en Render. Revisa el SII antes de otro intento.');
  }
 };
 issueTimers[order]=true;
 await step();
}

async function refresh({quiet=false}={}){
 const seq=++generation;
 if(!quiet)$('notice').textContent='Actualizando historial…';
 try{
  const response=await fetch('/admin/boletas/data?'+query(),{cache:'no-store'});
  if(!response.ok)throw Error('No se pudo cargar el historial. Revisa el acceso y vuelve a intentar.');
  const data=await response.json();
  const jobsResponse=await fetch('/admin/boletas/sii-jobs',{cache:'no-store'});
  if(!jobsResponse.ok)throw Error('No se pudo verificar el estado del agente SII.');
  const jobs=await jobsResponse.json();
  const receiptsResponse=await fetch('/admin/boletas/receipts',{cache:'no-store'});
  if(!receiptsResponse.ok)throw Error('No se pudo comprobar el historial de PDFs.');
  const receipts=await receiptsResponse.json();
  if(seq!==generation)return;
  siiJobs=jobs.jobs;
  receiptMap=Object.fromEntries(receipts.receipts.map(x=>[x.order_id,x]));
  issuanceEnabled=!!receipts.issuance_enabled;
  const rendered=document.createDocumentFragment();
  for(const order of data.orders)rendered.append(card(order));
  // Keep the previous list visible during requests. Replace it once.
  $('orders').replaceChildren(rendered);
  $('count').textContent=data.orders.length+' compras';
  for(const order of data.orders){
   if(!quiet&&order.issuance_state==='processing'&&!issueTimers[order.order_id]){
    issueRunning[order.order_id]=true;pollIssue(order.order_id);
   }
  }
  if(!quiet)$('notice').textContent=data.orders.length?'':'No hay compras para estos filtros.';
 }catch(err){
  if(seq===generation)$('notice').textContent=err.message;
 }
}

$('environment').onchange=()=>{$('status').replaceChildren();for(const [value,label] of ($('environment').value==='integration'?[['test','Pruebas aprobadas'],['all','Todos los pagos de prueba']]:[['pending','Pendientes de emisión'],['issued','Emitidas'],['all','Todos los pagos']])){const o=node('option',label);o.value=value;$('status').append(o)}refresh()};
$('refresh').onclick=refresh;$('status').onchange=refresh;$('export').onclick=()=>{location.href='/admin/boletas/export.csv?'+query()};refresh();

