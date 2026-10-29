'use strict';
const $=id=>document.getElementById(id);
let generation=0;
let siiJobs=[];
let receiptMap={};
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
 if(latest)section.append(node('p','Agente SII: '+(siiLabels[latest.status]||latest.status)));
 if(r.environment==='production'){
  const formSii=node('form',undefined,'actions');formSii.action='/admin/sii-visual/session';formSii.method='post';formSii.target='_blank';formSii.rel='noopener';
  const id=node('input');id.type='hidden';id.name='order_id';id.value=r.order_id;
  const open=node('button','Abrir esta compra en SII (sin emitir)','primary');open.type='submit';
  formSii.append(id,open);actions.append(formSii);
  section.append(node('p',r.issuance_state==='awaiting_issuer'?'Compra aprobada vinculada a solicitud de boleta. Emisor automático aún no habilitado.':'Compra aprobada pendiente de gestión en SII.','meta'));
 }
 if(r.environment==='production'){
  const emit=node('button','Emitir','primary');emit.type='button';
  emit.title='Abrir el portal oficial SII para efectuar una emisión real. El registro requiere folio verdadero.';
  emit.onclick=async()=>{
   const portal=window.open('https://eboleta.sii.cl/emitir/','_blank','noopener,noreferrer');
   if(!portal){$('notice').textContent='Autoriza ventanas emergentes para abrir e-Boleta SII.';return}
   $('notice').textContent='SII abierto. Utiliza la ficha de esta compra para completar la emisión real y después registra el folio y guarda su PDF aquí. La preparación automática continúa sin permiso de emisión.';
  };actions.append(emit);
 }
 if(active&&active.mode==='preview'){
  const close=node('button','Cerrar preparación interrumpida');close.type='button';
  close.onclick=async()=>{
   if(!confirm('Primero detén el agente con Ctrl+C y cierra su Chromium. ¿Ya está detenido?'))return;
   try{const res=await fetch('/admin/boletas/sii-jobs/'+active.id+'/close',{method:'POST',headers:{'Content-Type':'application/json','X-Billing-Action':'manual-invoice'},body:JSON.stringify({agent_stopped:true})});if(!res.ok)throw Error('No fue posible cerrar el trabajo');await refresh()}catch(e){$('notice').textContent=e.message}
  };actions.append(close);
 }
 const prep=node('button',r.environment==='integration'?'Previsualizar prueba en SII (sin emitir)':'Preparar en mi computador (sin emitir)');prep.type='button';prep.disabled=!!active;
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
async function refresh(){const seq=++generation;$('notice').textContent='Cargando historial…';$('orders').replaceChildren();$('count').textContent='';try{const res=await fetch('/admin/boletas/data?'+query(),{cache:'no-store'});if(!res.ok)throw Error('No se pudo cargar el historial. Revisa el acceso y vuelve a intentar.');const data=await res.json();const jr=await fetch('/admin/boletas/sii-jobs',{cache:'no-store'});if(!jr.ok)throw Error('No se pudo verificar el estado del agente SII.');const jd=await jr.json();const rr=await fetch('/admin/boletas/receipts',{cache:'no-store'});if(!rr.ok)throw Error('No se pudo comprobar el historial de PDFs.');const rd=await rr.json();if(seq!==generation)return;siiJobs=jd.jobs;receiptMap=Object.fromEntries(rd.receipts.map(x=>[x.order_id,x]));for(const r of data.orders)$('orders').append(card(r));$('count').textContent=data.orders.length+' compras';$('notice').textContent=data.orders.length?'':'No hay compras para estos filtros.'}catch(err){if(seq===generation)$('notice').textContent=err.message}}
$('environment').onchange=()=>{$('status').replaceChildren();for(const [value,label] of ($('environment').value==='integration'?[['test','Pruebas aprobadas'],['all','Todos los pagos de prueba']]:[['pending','Pendientes de emisión'],['issued','Emitidas'],['all','Todos los pagos']])){const o=node('option',label);o.value=value;$('status').append(o)}refresh()};
$('refresh').onclick=refresh;$('status').onchange=refresh;$('export').onclick=()=>{location.href='/admin/boletas/export.csv?'+query()};refresh();
