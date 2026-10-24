'use strict';
const $=id=>document.getElementById(id);
let generation=0;
const labels={initiated:'Pago sin confirmar',approved:'Pago aprobado',rejected:'Pago rechazado',cancelled:'Pago cancelado',pending:'Boleta pendiente',issued:'Boleta emitida',test:'Prueba · no emitir',not_applicable:'No corresponde emitir'};
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function query(){const p=new URLSearchParams();for(const id of ['environment','status','from','to','q'])p.set(id,$(id).value);return p.toString()}
async function copy(value){try{await navigator.clipboard.writeText(String(value));$('notice').textContent='Copiado. Puedes pegarlo en el SII.'}catch{$('notice').textContent='No se pudo copiar automáticamente. Selecciona el texto y usa Ctrl+C.'}}
function field(container,label,value,wide=false){const box=node('div',undefined,'field'+(wide?' wide':''));const text=node('div');text.append(node('span',label),node('strong',value));const b=node('button','Copiar');b.type='button';b.setAttribute('aria-label','Copiar '+label);b.onclick=()=>copy(value);box.append(text,b);container.append(box)}
function card(r){const section=node('section',undefined,'order');section.append(node('h2',r.name),node('p',r.order_id+' · Compra: '+r.created_at+' · Atención: '+r.appointment_date+' '+r.appointment_time,'meta'),node('span',(labels[r.payment_status]||r.payment_status)+' · '+(labels[r.invoice_status]||r.invoice_status),'badge'));
if(r.environment==='integration')section.append(node('p','Simulación de Webpay. No emitir una boleta real por esta operación.','warning'));
if(r.dte){
 const d=r.dte, names={queued:'En cola',processing:'Procesando',retry:'Reintento programado',submitted:'Esperando al SII',accepted:'Aceptada',accepted_with_observations:'Aceptada con reparos',rejected:'Rechazada por el SII',review:'Revisión requerida',reconciliation:'Conciliación requerida'};
 section.append(node('p','Emisión automática: '+(names[d.state]||d.state)+' · Intentos: '+d.attempts));
 if(d.folio){section.append(node('p',(r.environment==='integration'?'Folio de simulación: ':'Folio tributario: ')+d.folio));const pdf=node('a','Descargar PDF');pdf.href='/admin/boletas/'+encodeURIComponent(r.order_id)+'/pdf';section.append(pdf)}
 section.append(node('p','SII: '+(d.sii_status||'Pendiente')+' · Correo: '+d.email_state));
 if(d.error)section.append(node('p',d.error,'warning'));
 const act=(suffix,label,question)=>{const b=node('button',label);b.onclick=async()=>{if(!confirm(question))return;b.disabled=true;try{const res=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/'+suffix,{method:'POST',headers:{'Content-Type':'application/json','X-Billing-Action':'manual-invoice'},body:JSON.stringify({confirm:true})});const data=await res.json();if(!res.ok)throw Error(data.error);await refresh()}catch(e){$('notice').textContent=e.message;b.disabled=false}};section.append(b)};
 if(['retry','review','submitted','reconciliation'].includes(d.state))act('retry','Reintentar consulta o emisión','¿Solicitar un reintento seguro de esta operación?');
 if(r.environment==='production'&&['sent','review'].includes(d.email_state))act('resend','Reenviar PDF','¿Reenviar el PDF? El paciente podría haber recibido el correo anterior. No se emitirá otra boleta.');
 const history=node('button','Ver historial de emisión');history.onclick=async()=>{try{const res=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/audit');if(!res.ok)throw Error();const data=await res.json();section.append(node('pre',data.events.map(e=>e.created_at+' · '+e.action).join('\n')));history.disabled=true}catch{$('notice').textContent='No se pudo cargar el historial.'}};section.append(history);
}
const fields=node('div',undefined,'fields');const pairs=[['Monto',r.amount],['Tipo de documento','Boleta exenta'],['Método de pago',r.payment_method],['RUT receptor',r.rut],['Nombre receptor',r.name],['Dirección receptor',r.address],['E-mail receptor',r.email],['Teléfono receptor',r.phone],['Detalle',r.detail]];
for(const [label,value] of pairs)field(fields,label,value,['Dirección receptor','Detalle'].includes(label));section.append(fields);
const actions=node('div',undefined,'actions'),all=node('button','Copiar ficha completa');all.onclick=()=>copy(pairs.map(([k,v])=>k+': '+v).join('\n'));actions.append(all);
const sii=node('a','Abrir e-Boleta SII');sii.href='https://eboleta.sii.cl/emitir/';sii.target='_blank';sii.rel='noopener noreferrer';actions.append(sii);
if(r.invoice_status==='issued')section.append(node('p','Folio '+r.folio+' · Emisión '+r.issued_date));
if(r.environment==='production'&&r.payment_status==='approved'&&r.invoice_status==='pending'&&!r.dte){
 const form=node('form',undefined,'actions');const fl=node('label','Folio emitido en SII'),folio=node('input');folio.required=true;folio.pattern='[1-9][0-9]{0,9}';folio.inputMode='numeric';fl.append(folio);
 const dl=node('label','Fecha de emisión'),date=node('input');date.type='date';date.required=true;date.min=r.created_at.slice(0,10);dl.append(date);
 const button=node('button','Marcar como emitida','primary');button.type='submit';form.append(fl,dl,button);
 form.onsubmit=async e=>{e.preventDefault();if(!confirm('¿Ya emitiste en el SII la boleta con folio '+folio.value+'?'))return;button.disabled=true;try{const res=await fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/issued',{method:'POST',headers:{'Content-Type':'application/json','X-Billing-Action':'manual-invoice'},body:JSON.stringify({folio:folio.value,issued_date:date.value})});const result=await res.json();if(!res.ok)throw Error(result.error||'No se pudo registrar');await refresh();$('notice').textContent='Folio registrado correctamente.'}catch(err){$('notice').textContent=err.message}finally{button.disabled=false}};
 actions.append(form);
}section.append(actions);return section}
async function refresh(){const seq=++generation;$('notice').textContent='Cargando historial…';$('orders').replaceChildren();$('count').textContent='';try{const res=await fetch('/admin/boletas/data?'+query(),{cache:'no-store'});if(!res.ok)throw Error('No se pudo cargar el historial. Revisa el acceso y vuelve a intentar.');const data=await res.json();if(seq!==generation)return;for(const r of data.orders)$('orders').append(card(r));$('count').textContent=data.orders.length+' compras';$('notice').textContent=data.orders.length?'':'No hay compras para estos filtros.'}catch(err){if(seq===generation)$('notice').textContent=err.message}}
$('environment').onchange=()=>{$('status').replaceChildren();for(const [value,label] of ($('environment').value==='integration'?[['test','Pruebas aprobadas'],['all','Todos los pagos de prueba']]:[['pending','Pendientes de emisión'],['issued','Emitidas'],['all','Todos los pagos']])){const o=node('option',label);o.value=value;$('status').append(o)}refresh()};
$('refresh').onclick=refresh;$('status').onchange=refresh;$('export').onclick=()=>{location.href='/admin/boletas/export.csv?'+query()};refresh();
