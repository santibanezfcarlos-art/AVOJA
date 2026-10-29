'use strict';
const $=id=>document.getElementById(id);
let generation=0;
let siiJobs=[];
let receiptMap={};
let firstCanaryReady=false;
let firstCanaryOrderId=null;
const issueProgress=Object.create(null);
const issueTimers=Object.create(null);
const previewProgress=Object.create(null);
const previewRunning=Object.create(null);
const previewTimers=Object.create(null);
function previewMessage(order,message){
 previewProgress[order]=message;
 const status=Array.from(document.querySelectorAll('[data-preview-order]')).find(e=>e.dataset.previewOrder===order);
 if(status)status.textContent=message;
}
const siiLabels={queued:"Esperando agente local",running:"Preparando en tu computador",prepared:"Formulario verificado · sin emitir",blocked:"Detenido · revisar formulario",session_required:"Renovar sesión SII",failed:"Preparación fallida",reconciliation:"Pendiente de conciliación"};
const labels={initiated:'Pago sin confirmar',approved:'Pago aprobado',rejected:'Pago rechazado',cancelled:'Pago cancelado',pending:'Boleta pendiente',issued:'Boleta emitida',test:'Prueba · no emitir',not_applicable:'No corresponde emitir'};
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
   section.append(node('p','Emisión SII con resultado por conciliar. NO volver a emitir. Primero comprueba si existe folio real en SII.','warning'));
  }else if(firstCanaryReady&&firstCanaryOrderId===r.order_id){
   const emit=node('button','Emitir','primary');emit.type='button';
   emit.disabled=!!issueProgress[r.order_id]||!!previewRunning[r.order_id];
   emit.title='Un clic inicia Chromium en Render y la emisión real del SII en segundo plano.';
   emit.onclick=async()=>{
    emit.disabled=true;
    issueProgress[r.order_id]='Conectando Chromium en Render…';
    $('notice').textContent='Ejecutando la emisión de esta compra directamente en Render…';
    try{
     const res=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/emit',{
       method:'POST',headers:{'X-Billing-Action':'manual-invoice'}});
     const data=await res.json();
     if(!res.ok)throw Error(data.error||'Render no confirmó el inicio.');
     issueProgress[r.order_id]='Chromium iniciado; SII preparando boleta…';
     await refresh();
     pollIssue(r.order_id);
    }catch(err){
     issueProgress[r.order_id]='Revisar estado antes de intentar nuevamente: '+err.message;
     $('notice').textContent=issueProgress[r.order_id];
     await refresh();
    }
   };actions.append(emit);
   const issueState=node('p',issueProgress[r.order_id]||'','sii-progress');
   issueState.dataset.issueOrder=r.order_id;issueState.setAttribute('role','status');
   issueState.setAttribute('aria-live','polite');actions.append(issueState);
  }else{
   const emit=node('button','Emitir','primary');emit.type='button';
   emit.title='Abrir e-Boleta SII oficial. Completa y confirma la emisión directamente en el portal.';
   emit.onclick=()=>{
    window.open('https://eboleta.sii.cl/emitir/','_blank','noopener,noreferrer');
    $('notice').textContent='SII oficial abierto. Completa la emisión real con esta ficha y registra el folio y PDF aquí. No hay autoemisión habilitada para esta compra.';
   };actions.append(emit);
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
if(r.invoice_status==='issued'){
 section.append(node('p','Folio '+r.folio+' · Emisión '+r.issued_date));
 if(r.environment==='production'){
  const receipt=receiptMap[r.order_id];
  const hasPdf=!!(receipt&&receipt.has_pdf);
  section.append(node('p',hasPdf?'Boleta emitida · PDF guardado · Correo PSC: '+(receipt.psc_state==='accepted'?'Aceptado':receipt.psc_state==='unknown'?'Resultado incierto, revisar':receipt.psc_state==='sending'?'En proceso, no reenviar':'Pendiente'):'Boleta emitida · PDF pendiente de guardar','meta'));
  if(hasPdf){
   const download=node('a','Descargar boleta PDF');download.href='/admin/boletas/'+encodeURIComponent(r.order_id)+'/official-pdf';
   actions.append(download);
   const send=node('button','Enviar boleta desde PSC');send.type='button';
   send.disabled=receipt.psc_state!=='pending';
   send.onclick=async()=>{
    send.disabled=true;$('notice').textContent='Solicitando un único envío al proveedor de correo…';
    try{
     const response=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/send-psc',
       {method:'POST',headers:{'X-Billing-Action':'manual-invoice'}});
     const result=await response.json();if(!response.ok)throw Error(result.error||'No se pudo procesar');
     await refresh();$('notice').textContent=result.state==='accepted'?'Proveedor aceptó el correo con PDF.':'Estado '+result.state+'; revisar antes de reintentar.';
    }catch(e){$('notice').textContent=e.message}
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
 const form=node('form',undefined,'actions');const fl=node('label','Folio emitido en SII'),folio=node('input');folio.required=true;folio.pattern='[1-9][0-9]{0,9}';folio.inputMode='numeric';fl.append(folio);
 const dl=node('label','Fecha de emisión'),date=node('input');date.type='date';date.required=true;date.min=r.created_at.slice(0,10);dl.append(date);
 const button=node('button','Marcar como emitida','primary');button.type='submit';form.append(fl,dl,button);
 form.onsubmit=async e=>{e.preventDefault();if(!confirm('¿Ya emitiste en el SII la boleta con folio '+folio.value+'?'))return;button.disabled=true;try{const res=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/issued',{method:'POST',headers:{'Content-Type':'application/json','X-Billing-Action':'manual-invoice'},body:JSON.stringify({folio:folio.value,issued_date:date.value})});const result=await res.json();if(!res.ok)throw Error(result.error||'No se pudo registrar');await refresh();$('notice').textContent='Folio registrado correctamente.'}catch(err){$('notice').textContent=err.message}finally{button.disabled=false}};
 actions.append(form);
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
 const terminal=new Set(['issued','reconciliation','failed','expired','unavailable','not_running']);
 const step=async()=>{
  try{
   const res=await fetch('/admin/boletas/'+encodeURIComponent(order)+'/issue-status',{cache:'no-store'});
   const status=await res.json();
   if(!res.ok)throw Error(status.error||'Estado no disponible');
   const stage=status.stage||status.state;
   const label={
     starting:'Iniciando Chromium en Render…',opening_sii:'Abriendo SII…',
     sii_authentication:'Ingresando al SII…',waiting_before_login:'Validando sesión SII…',
     waiting_calculator:'Cargando e-Boleta…',preparing_order:'Completando importe y receptor…',
     waiting_receiver_form:'Abriendo datos del receptor…',filling_receiver:'Completando datos de la compra…',
     issue_approved_pending_claim:'Preparación completa. Emitiendo…',
     claiming_canary:'Registrando intento y emitiendo…',issued:'Boleta emitida y folio guardado',
     reconciliation:'Resultado SII incierto: verificar antes de otra emisión.',
     not_running:'Chromium no informa un proceso activo. Revisar antes de reintentar.'
   };
   issueProgress[order]=label[stage]||stage;
   if(status.state==='issued'||status.state==='reconciliation'
      ||terminal.has(status.state)||stage.startsWith('canary_')||stage.startsWith('blocked_')){
    delete issueTimers[order];
    await refresh();return;
   }
   await refresh();
   issueTimers[order]=setTimeout(step,3500);
  }catch(err){
   issueProgress[order]='No se pudo consultar Render. Revisar antes de repetir la emisión.';
   delete issueTimers[order];
   await refresh();
  }
 };
 issueTimers[order]=true;await step();
}

async function refresh(){const seq=++generation;$('notice').textContent='Cargando historial…';$('orders').replaceChildren();$('count').textContent='';try{const res=await fetch('/admin/boletas/data?'+query(),{cache:'no-store'});if(!res.ok)throw Error('No se pudo cargar el historial. Revisa el acceso y vuelve a intentar.');const data=await res.json();const jr=await fetch('/admin/boletas/sii-jobs',{cache:'no-store'});if(!jr.ok)throw Error('No se pudo verificar el estado del agente SII.');const jd=await jr.json();const rr=await fetch('/admin/boletas/receipts',{cache:'no-store'});if(!rr.ok)throw Error('No se pudo comprobar el historial de PDFs.');const rd=await rr.json();if(seq!==generation)return;siiJobs=jd.jobs;receiptMap=Object.fromEntries(rd.receipts.map(x=>[x.order_id,x]));firstCanaryReady=!!rd.first_canary_ready;firstCanaryOrderId=rd.first_canary_order_id||null;for(const r of data.orders)$('orders').append(card(r));$('count').textContent=data.orders.length+' compras';$('notice').textContent=data.orders.length?'':'No hay compras para estos filtros.'}catch(err){if(seq===generation)$('notice').textContent=err.message}}
$('environment').onchange=()=>{$('status').replaceChildren();for(const [value,label] of ($('environment').value==='integration'?[['test','Pruebas aprobadas'],['all','Todos los pagos de prueba']]:[['pending','Pendientes de emisión'],['issued','Emitidas'],['all','Todos los pagos']])){const o=node('option',label);o.value=value;$('status').append(o)}refresh()};
$('refresh').onclick=refresh;$('status').onchange=refresh;$('export').onclick=()=>{location.href='/admin/boletas/export.csv?'+query()};refresh();
