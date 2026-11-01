/* Read-only weekly administration calendar. Data comes exclusively from /api/admin. */
let adminCalWeek = '';
let adminCalBox = 'all';
let adminCalProfessional = 'all';
let adminCalStatus = 'confirmed';

function adminCalDate(iso) {
  return new Intl.DateTimeFormat('sv-SE', {timeZone:'America/Santiago', year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date(iso));
}
function adminCalUTC(dateKey) {
  return new Date(dateKey + 'T12:00:00Z');
}
function adminCalShift(dateKey, days) {
  const value = adminCalUTC(dateKey);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0,10);
}
function adminCalMonday(dateKey) {
  const d = adminCalUTC(dateKey);
  return adminCalShift(dateKey, -((d.getUTCDay()+6)%7));
}
function adminCalClock(iso) {
  const parts = new Intl.DateTimeFormat('en-GB',{timeZone:'America/Santiago',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(iso));
  return Number(parts.find(part=>part.type==='hour')?.value||0)*60 + Number(parts.find(part=>part.type==='minute')?.value||0);
}
function adminCalReadable(dateKey, opts) {
  return new Intl.DateTimeFormat('es-CL',Object.assign({timeZone:'UTC'},opts)).format(adminCalUTC(dateKey));
}
function adminCalBookingCard(b, startHour, rowHeight, filtered) {
  const startMinutes=adminCalClock(b.start);
  const duration=Number(b.duration_minutes)||55;
  const top=Math.max(0,(startMinutes-startHour*60)*rowHeight/60)+2;
  const height=Math.max(30,duration*rowHeight/60-4);
  const box=Math.min(3,Math.max(1,Number(b.box)||1));
  const name=escapeHTML(b.name||'Profesional');
  const title=escapeHTML((b.name||'Profesional')+' · '+rooms[box-1]+' · '+sessionRange(b.start)+' · '+(labels[b.status]||b.status));
  const lane=filtered?'left:3px;width:calc(100% - 6px);':'left:calc('+(box-1)+' * 100% / 3 + 2px);width:calc(100% / 3 - 4px);';
  return '<button type="button" class="admin-cal-event box-'+box+(b.status==='cancelled'?' is-cancelled':'')+'" style="top:'+top+'px;height:'+height+'px;'+lane+'" data-admin-cal-open="'+Number(b.id)+'" title="'+title+'" aria-label="'+title+'">'+
    '<strong>'+name+'</strong><small>'+escapeHTML(rooms[box-1])+'</small><span>'+escapeHTML(sessionRange(b.start))+'</span></button>';
}
function adminWeeklyCalendar() {
  const today=state.today||adminCalDate(new Date().toISOString());
  if(!adminCalWeek)adminCalWeek=adminCalMonday(today);
  const monday=adminCalMonday(adminCalWeek);
  const days=Array.from({length:7},(_,index)=>adminCalShift(monday,index));
  const bookings=adminData.bookings.filter(b=>
    (adminCalStatus==='all'||b.status===adminCalStatus)&&
    (adminCalBox==='all'||String(b.box)===adminCalBox)&&
    (adminCalProfessional==='all'||String(b.user_id)===adminCalProfessional)&&
    days.includes(adminCalDate(b.start))
  );
  const professionals=[...new Map(adminData.bookings.map(b=>[String(b.user_id),{id:String(b.user_id),name:b.name||'Profesional'}])).values()]
    .sort((a,b)=>a.name.localeCompare(b.name,'es'));
  const filtered=adminCalBox!=='all';
  const dayMinutes=bookings.map(b=>adminCalClock(b.start));
  const endMinutes=bookings.map(b=>adminCalClock(b.start)+(Number(b.duration_minutes)||55));
  const startHour=Math.max(0,Math.min(8,...dayMinutes.map(min=>Math.floor(min/60))));
  const endHour=Math.min(24,Math.max(22,...endMinutes.map(min=>Math.ceil(min/60))));
  const rowHeight=66;
  const height=(endHour-startHour)*rowHeight;
  const weekEnd=days[6];
  const label=adminCalReadable(monday,{day:'numeric',month:'short'})+' – '+adminCalReadable(weekEnd,{day:'numeric',month:'short',year:'numeric'});
  const hours=(bookings.reduce((total,b)=>total+(Number(b.duration_minutes)||55),0)/60).toLocaleString('es-CL',{maximumFractionDigits:1});
  const boxOptions=rooms.map((room,i)=>'<option value="'+(i+1)+'"'+(adminCalBox===String(i+1)?' selected':'')+'>'+escapeHTML(room)+'</option>').join('');
  const professionalOptions=professionals.map(p=>'<option value="'+escapeHTML(p.id)+'"'+(adminCalProfessional===p.id?' selected':'')+'>'+escapeHTML(p.name)+'</option>').join('');
  const statusOptions=[['confirmed','Confirmadas'],['cancelled','Canceladas'],['all','Todas']].map(item=>'<option value="'+item[0]+'"'+(adminCalStatus===item[0]?' selected':'')+'>'+item[1]+'</option>').join('');
  const timeAxis=Array.from({length:endHour-startHour+1},(_,i)=>'<span class="admin-cal-time" style="top:'+(i*rowHeight)+'px">'+String(startHour+i).padStart(2,'0')+':00</span>').join('');
  const columns=days.map((date,index)=>{
    const todayClass=date===today?' is-today':'';
    const dayEvents=bookings.filter(b=>adminCalDate(b.start)===date);
    const dayTitle=adminCalReadable(date,{weekday:'short',day:'numeric',month:'short'});
    const block=dayEvents.map(b=>adminCalBookingCard(b,startHour,rowHeight,filtered)).join('');
    return '<div class="admin-cal-day-col"><div class="admin-cal-day-header'+todayClass+'"><strong>'+escapeHTML(dayTitle)+'</strong><small>'+dayEvents.length+' reserva'+(dayEvents.length===1?'':'s')+'</small></div>'+
      '<div class="admin-cal-day-body" style="height:'+height+'px">'+block+'</div></div>';
  }).join('');
  return '<div class="admin-cal-wrap"><div class="admin-cal-heading"><div><h2>Calendario semanal</h2><p class="section-intro">Ocupación real de los tres box. Solo consulta: no mueve ni altera reservas.</p></div>'+
    '<div class="admin-cal-navigation"><button type="button" class="btn ghost small" data-admin-cal-today>Hoy</button>'+
    '<button type="button" class="btn ghost small" data-admin-cal-shift="-7" aria-label="Semana anterior">‹</button>'+
    '<strong aria-live="polite">'+escapeHTML(label)+'</strong>'+
    '<button type="button" class="btn ghost small" data-admin-cal-shift="7" aria-label="Semana siguiente">›</button></div></div>'+
    '<div class="admin-cal-filters"><label>Box<select id="admin-cal-box"><option value="all">Todos los box</option>'+boxOptions+'</select></label>'+
    '<label>Profesional<select id="admin-cal-professional"><option value="all">Todos los profesionales</option>'+professionalOptions+'</select></label>'+
    '<label>Estado<select id="admin-cal-status">'+statusOptions+'</select></label>'+
    '<span class="admin-cal-summary">'+bookings.length+' reservas · '+hours+' horas</span></div>'+
    '<div class="admin-cal-legend">'+rooms.map((room,index)=>'<span class="admin-cal-legend-box box-'+(index+1)+'">'+escapeHTML(room)+'</span>').join('')+'</div>'+
    '<p class="admin-cal-hint">Desplázate horizontalmente para ver toda la semana. Selecciona una reserva para consultar su detalle.</p>'+
    '<div class="admin-cal-scroll" tabindex="0" aria-label="Calendario de reservas de lunes a domingo"><div class="admin-cal-grid">'+
    '<div class="admin-cal-axis"><div class="admin-cal-axis-heading">GMT-3 / GMT-4*</div><div class="admin-cal-time-body" style="height:'+height+'px">'+timeAxis+'</div></div>'+
    columns+'</div></div>'+
    '<p class="admin-cal-footnote">*Horario local de Chile (America/Santiago), con ajuste automático de horario de verano. Las reservas canceladas no aparecen salvo que las selecciones en Estado.</p></div>';
}
function bindAdminWeeklyCalendar() {
  const box=document.querySelector('#admin-cal-box');
  if(!box)return;
  const redraw=()=>render().catch(err=>toast(err.message));
  box.onchange=()=>{adminCalBox=box.value;redraw();};
  document.querySelector('#admin-cal-professional').onchange=event=>{adminCalProfessional=event.target.value;redraw();};
  document.querySelector('#admin-cal-status').onchange=event=>{adminCalStatus=event.target.value;redraw();};
  document.querySelector('[data-admin-cal-today]').onclick=()=>{adminCalWeek=adminCalMonday(state.today||adminCalDate(new Date().toISOString()));redraw();};
  document.querySelectorAll('[data-admin-cal-shift]').forEach(button=>button.onclick=()=>{adminCalWeek=adminCalShift(adminCalWeek,Number(button.dataset.adminCalShift));redraw();});
  document.querySelectorAll('[data-admin-cal-open]').forEach(button=>button.onclick=()=>{
    const b=adminData.bookings.find(item=>Number(item.id)===Number(button.dataset.adminCalOpen));
    if(!b)return;
    const room=rooms[(Number(b.box)||1)-1]||'Box';
    modal.classList.remove('user-detail-modal');
    modal.innerHTML='<h2 id="modal-title">Detalle de reserva #'+Number(b.id)+'</h2>'+
      '<div class="modal-detail"><strong>'+escapeHTML(b.name||'Profesional')+'</strong><p>'+escapeHTML(room)+'</p>'+
      '<p>'+escapeHTML(dateText(b.start))+' · '+escapeHTML(sessionRange(b.start))+'</p>'+
      '<p>Estado: '+escapeHTML(labels[b.status]||b.status)+'</p>'+
      '<p>Modificaciones: '+Number(b.modifications||0)+'</p></div>'+
      '<div class="modal-actions"><button type="button" class="btn ghost" id="admin-cal-close">Cerrar</button>'+
      '<button type="button" class="btn" id="admin-cal-list">Ver en Reservas</button></div>';
    modal.showModal();
    document.querySelector('#admin-cal-close').onclick=()=>modal.close();
    document.querySelector('#admin-cal-list').onclick=()=>{modal.close();adminTab='bookings';redraw();};
  });
}