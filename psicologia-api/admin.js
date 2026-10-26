'use strict';
const $=id=>document.getElementById(id),el=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e},money=n=>'$'+Number(n).toLocaleString('es-CL');
let config,version,rows=[],loadId=0,paused=false,locked=false;
const states={approved:'Aprobado',rejected:'Rechazado',cancelled:'Cancelado',initiated:'Sin confirmar',pending:'Pendiente',issued:'Emitida',test:'Prueba · no emitir',not_applicable:'No corresponde'};
function notice(s){$('notice').textContent=s}
async function api(path,body){const r=await fetch(path,{cache:'no-store',...(body?{method:'POST',headers:{'Content-Type':'application/json','X-Billing-Action':'manual-invoice'},body:JSON.stringify(body)}:{})});const d=await r.json().catch(()=>({error:'No se pudo acceder. Revisa tu sesión.'}));if(!r.ok)throw Error(d.error||'Error al consultar el servidor');return d}
function show(tab){document.querySelectorAll('.view').forEach(e=>e.hidden=e.id!==tab);document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);if(b.dataset.tab===tab)$('heading').textContent=b.textContent});$('dataTools').hidden=['agenda','services','transbank','communications','fonasa'].includes(tab);if($('mobileSection'))$('mobileSection').value=tab;history.replaceState(null,'','#'+tab)}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>show(b.dataset.tab));$('mobileSection').onchange=e=>show(e.target.value);
function table(target,items){target.replaceChildren();if(!items.length){target.append(el('p','No hay registros para esta selección.'));return}const wrap=el('div');wrap.className='table-wrap';const t=el('table'),head=el('tr');['Cliente / orden','Horario','Monto','Pago','Boleta'].forEach(s=>head.append(el('th',s)));t.append(head);for(const r of items){const tr=el('tr');[r.name+' · '+r.order_id,(r.appointment_date||'Sin fecha')+' '+r.appointment_time,money(r.amount),states[r.payment_status],states[r.invoice_status]].forEach(s=>tr.append(el('td',s)));t.append(tr)}wrap.append(t);target.append(wrap)}
function renderData(){const q=$('search').value.toLowerCase(),list=rows.filter(r=>[r.name,r.rut,r.order_id].some(s=>s.toLowerCase().includes(q)));const approved=list.filter(r=>r.payment_status==='approved');$('metrics').replaceChildren();for(const [name,value] of [['Pagos aprobados',approved.length],['Monto aprobado',money(approved.reduce((a,r)=>a+r.amount,0))],['Boletas pendientes',list.filter(r=>r.invoice_status==='pending').length]]){const box=el('div');box.className='metric';box.append(el('span',name),el('strong',value),el('small',$('environment').value==='integration'?'Solo simulaciones':'Compras reales'));$('metrics').append(box)}const today=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Santiago',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());table($('upcoming'),approved.filter(r=>r.appointment_date>=today).sort((a,b)=>(a.appointment_date+a.appointment_time).localeCompare(b.appointment_date+b.appointment_time)).slice(0,10));table($('purchaseRows'),list.filter(r=>$('paymentFilter').value==='all'||r.payment_status===$('paymentFilter').value));table($('invoiceRows'),approved);$('clientRows').replaceChildren();const clients=new Map();for(const r of list){if(!clients.has(r.rut))clients.set(r.rut,[]);clients.get(r.rut).push(r)}for(const records of [...clients.values()].sort((a,b)=>a[0].name.localeCompare(b[0].name,'es'))){const r=records[0],d=el('details'),summary=el('summary',r.name+' · '+r.rut+' · '+records.length+' compras');d.append(summary,el('p',r.email+' · '+r.phone),el('p',r.address));const body=el('div');table(body,records);d.append(body);$('clientRows').append(d)}if(!clients.size)$('clientRows').append(el('p','Todavía no hay clientes en este ambiente.'))}
async function reload(){const seq=++loadId;notice('Consultando historial…');try{const d=await api('/admin/boletas/data?status=all&environment='+$('environment').value);if(seq!==loadId)return;rows=d.orders;renderData();notice('Historial actualizado.')}catch(e){if(seq===loadId){rows=[];renderData();notice(e.message)}}}
$('reload').onclick=reload;$('environment').onchange=reload;$('search').oninput=renderData;$('paymentFilter').onchange=renderData;$('export').onclick=()=>location.href='/admin/boletas/export.csv?status=all&environment='+$('environment').value+'&q='+encodeURIComponent($('search').value);
function input(label,value,type='text'){const l=el('label',label),i=el('input');i.type=type;i.value=value;l.append(i);return [l,i]}
function parseHours(s){return s.split(',').map(v=>v.trim()).filter(Boolean)}
function renderExceptions(){$('exceptions').replaceChildren();for(const [day,v] of Object.entries(config.exceptions).sort()){const d=el('div');d.className='entry';const b=el('button','Restaurar horario semanal');b.type='button';b.onclick=()=>{delete config.exceptions[day];renderExceptions();notice('Cambio pendiente. Pulsa Guardar disponibilidad.')};d.append(el('span',day+' · '+(v.closed?'Cerrado':v.slots.join(', '))+' '),b);$('exceptions').append(d)}}
function renderConfig(){$('weekdays').replaceChildren();['Domingo','Lunes','Martes','Miércoles','Jueves','Viernes','Sábado'].forEach((name,d)=>{const row=el('div');row.className='day';const label=el('label','Abierto'),check=el('input');check.type='checkbox';check.id='open-'+d;check.checked=!config.closedWeekdays.includes(d);label.className='check';label.prepend(check);const hours=el('input');hours.id='hours-'+d;hours.value=config.weekly[d].join(', ');hours.setAttribute('aria-label','Horas del '+name);row.append(el('strong',name),label,hours);$('weekdays').append(row)});$('capacity').value=config.maxPerDay;$('capacity').disabled=locked;renderExceptions();$('serviceFields').replaceChildren();for(const [key,s] of Object.entries(config.services)){const box=el('div');box.className='card';box.append(el('h2',s.title));const grid=el('div');grid.className='service-grid';for(const [field,label,type] of [['title','Nombre','text'],['price','Valor CLP','number'],['minutes','Duración en minutos','number'],['detail','Detalle para boleta','text']]){const [l,i]=input(label,s[field],type);i.id=key+'-'+field;i.required=true;if(field==='detail')l.className='wide';if(field==='price'){i.min=100;i.max=10000000;i.disabled=false}if(field==='minutes'){i.min=45;i.max=90;i.disabled=false}grid.append(l)}const l=el('label','Disponible para agendar'),c=el('input');c.type='checkbox';c.id=key+'-active';c.checked=s.active;l.prepend(c);grid.append(l);box.append(grid);$('serviceFields').append(box)}renderServicePreview()}

function renderServicePreview(){
 const target=$('servicePreview');if(!target||!config)return;target.replaceChildren();
 for(const [key,base] of Object.entries(config.services)){
  const title=$(key+'-title')?.value||base.title;
  const price=Number($(key+'-price')?.value||0);
  const minutes=Number($(key+'-minutes')?.value||0);
  const active=$(key+'-active')?.checked;
  const row=el('div');row.className='service-preview-row'+(active?'':' is-off');
  const info=el('div');info.append(el('strong',title),el('small',minutes+' minutos · '+(active?'Disponible para reservar':'Desactivado')));
  row.append(info,el('span',money(price)));row.lastChild.className='service-preview-price';target.append(row);
 }
}
$('serviceFields').addEventListener('input',renderServicePreview);
$('serviceFields').addEventListener('change',renderServicePreview);
function renderEmailPreview(){
 const kind=$('emailPreviewType').value,box=$('emailPreview');box.replaceChildren();
 const heading=$('comm-'+kind+'_heading').value;
 const message=$('comm-'+kind+'_message').value;
 const notes=$('comm-'+kind+'_notes').value;
 box.append(el('strong',heading),el('p',message),el('p','Ejemplo de reserva: servicio, fecha y horario se insertan automáticamente.'),el('p',notes),el('p',$('comm-signature').value));
}
$('emailPreviewType').onchange=renderEmailPreview;
$('communicationsForm').addEventListener('input',renderEmailPreview);

async function save(e,section){e.preventDefault();if(!config)return;const next=structuredClone(config);if(section==='agenda'){next.closedWeekdays=[];for(let d=0;d<7;d++){next.weekly[d]=parseHours($('hours-'+d).value);if(!$('open-'+d).checked)next.closedWeekdays.push(d)}next.maxPerDay=Number($('capacity').value)}else{for(const [key,s] of Object.entries(next.services)){for(const field of ['title','detail'])s[field]=$(key+'-'+field).value;s.price=Number($(key+'-price').value);s.minutes=Number($(key+'-minutes').value);s.active=$(key+'-active').checked}}if(section==='services'){const summary=Object.values(next.services).map(s=>s.title+' · '+money(s.price)+' · '+s.minutes+' min · '+(s.active?'Activo':'Inactivo')).join(String.fromCharCode(10));if(!window.confirm('¿Publicar estos cambios para nuevas compras?'+String.fromCharCode(10,10)+summary))return;}const button=e.submitter;button.disabled=true;try{const result=await api('/admin/settings',{settings:next,version});config=result.settings;version=result.version;renderConfig();notice('Cambios guardados en el servidor. Se aplicarán al abrir o actualizar la agenda pública.')}catch(err){notice(err.message)}finally{button.disabled=false}}
$('agendaForm').onsubmit=e=>save(e,'agenda');$('servicesForm').onsubmit=e=>save(e,'services');$('closeRange').onclick=()=>{if(!config)return;const a=$('exceptionFrom').value,b=$('exceptionTo').value||a;if(!a||b<a)return notice('Revisa el rango de fechas.');const first=new Date(a+'T12:00:00Z'),last=new Date(b+'T12:00:00Z');if((last-first)/86400000>365)return notice('Elige como máximo un año.');for(let d=first;d<=last;d.setUTCDate(d.getUTCDate()+1))config.exceptions[d.toISOString().slice(0,10)]={closed:true,slots:[]};renderExceptions();notice('Cierre pendiente. Pulsa Guardar disponibilidad.')};$('addException').onclick=()=>{if(!config)return;const day=$('exceptionDate').value;if(!day)return notice('Selecciona una fecha.');config.exceptions[day]={closed:false,slots:parseHours($('exceptionHours').value)};renderExceptions();notice('Horario pendiente. Pulsa Guardar disponibilidad.')};
async function transbank(){try{const d=await api('/admin/transbank');paused=d.paused;$('transbankInfo').replaceChildren();for(const [label,v] of [['Ambiente',d.mode],['Código de comercio',d.commerce_code||'No configurado'],['Clave API',d.api_key_configured?'Configurada':'Falta configurar'],['Firma de resultados',d.signing_configured?'Configurada':'Falta configurar'],['Estado',d.enabled?(paused?'Pausado':'Pruebas habilitadas'):'No habilitado'],['URL de retorno',d.return_url]]){const p=el('p');p.append(el('strong',label+': '),document.createTextNode(v));$('transbankInfo').append(p)}$('pausePayments').textContent=paused?'Reanudar intentos de prueba':'Pausar nuevos intentos de prueba';$('pausePayments').disabled=false}catch(e){notice(e.message)}}$('refreshPayments').onclick=transbank;$('pausePayments').onclick=async()=>{$('pausePayments').disabled=true;try{await api('/admin/transbank',{paused:!paused});await transbank();notice('Estado de Transbank actualizado.')}catch(e){notice(e.message);$('pausePayments').disabled=false}};
let communicationsVersion=0;
async function loadCommunications(){
 try{
  const d=await api('/admin/communications');communicationsVersion=d.version;
  for(const [key,value] of Object.entries(d.communications)){
   const input=$('comm-'+key);if(input)input.value=value;
  }
  $('commStatus').textContent='Textos cargados. Los cambios solo afectan correos posteriores.';renderEmailPreview();
 }catch(e){$('commStatus').textContent=e.message}
}
$('communicationsForm').onsubmit=async e=>{
 e.preventDefault();const btn=e.submitter;btn.disabled=true;
 const keys=['patient_heading','patient_message','patient_notes','professional_heading','professional_message','professional_notes','signature'];
 const data={};for(const key of keys)data[key]=$('comm-'+key).value.trim();
 if(!window.confirm('¿Guardar los nuevos textos para los próximos correos?')){btn.disabled=false;return;}
 try{
  const result=await api('/admin/communications',{version:communicationsVersion,communications:data});
  communicationsVersion=result.version;$('commStatus').textContent='Textos guardados correctamente.';
 }catch(err){$('commStatus').textContent=err.message}
 finally{btn.disabled=false}
};
loadCommunications();
(async()=>{show(document.querySelector('[data-tab="'+location.hash.slice(1).replace(/[^a-z]/g,'')+'"]')?location.hash.slice(1):'summary');try{const d=await api('/admin/settings');config=d.settings;version=d.version;locked=d.booking_bridge_active;renderConfig()}catch(e){notice(e.message);document.querySelectorAll('#agenda button,#services button').forEach(b=>b.disabled=true)}await reload();await transbank()})();
