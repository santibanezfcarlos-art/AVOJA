const root = document.querySelector('#root');
const modal = document.querySelector('#modal');
let state = null, adminData = null, view = 'overview', adminTab = 'payments', selectedDate = '', modifying = null;
let activeProfile = 'admin';
let officeGallery = [];
const adminMode = () => state?.user.role==='admin' && activeProfile==='admin';
let toastTimer;
const money = n => new Intl.NumberFormat('es-CL', {style:'currency', currency:'CLP', maximumFractionDigits:0}).format(n);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const dateText = value => new Intl.DateTimeFormat('es-CL', {timeZone:'America/Santiago', day:'numeric',month:'short',year:'numeric'}).format(new Date(value));
const hourText = value => new Intl.DateTimeFormat('es-CL', {timeZone:'America/Santiago',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
const labels = {pending:'Pendiente',paid:'Confirmado',rejected:'Rechazado',approved:'Aprobado',confirmed:'Confirmada',cancelled:'Cancelada'};
const pill = status => `<span class="pill ${escapeHTML(status)}">${labels[status] || escapeHTML(status)}</span>`;
const brand = `<div class="brand"><span class="mark" aria-hidden="true">V</span><div>VALHALLA<small>OFFICE & WELLBEING</small></div></div>`;
const rooms = ['Box 1','Box 2','Box infantil'];

async function api(path, body) {
  const response = await fetch('/api/' + path, body === undefined ? {} : {
    method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state?.csrf || ''},body:JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && !['login','register','me'].includes(path)) { state = null; auth(); }
    throw new Error(data.error || 'No se pudo completar la solicitud.');
  }
  return data;
}
function toast(message) {
  clearTimeout(toastTimer); const box = document.querySelector('#toast');
  box.textContent = message; box.hidden = false; toastTimer = setTimeout(() => box.hidden = true, 6000);
}
function empty(message) { return `<div class="empty"><span class="empty-symbol" aria-hidden="true">◇</span>${escapeHTML(message)}</div>`; }
function pendingBalance() { return state.packages.filter(p=>p.status==='paid').reduce((sum,p)=>sum+p.remaining,0); }
function upcoming() { return state.bookings.filter(b=>b.status==='confirmed' && new Date(b.start)>new Date()); }
function canModify(b) { return b.status==='confirmed' && b.modifications===0 && new Date(b.start)-Date.now() >= 7200000; }

function auth(register = false, administrator = false) {
  root.innerHTML = `<div class="auth"><section class="auth-story">${brand}<div><div class="eyebrow">UN LUGAR PARA CRECER</div><h1>Tu vocación.<br>Tu tiempo.<br><em>Tu espacio.</em></h1><p>Tres boxes profesionales. Una comunidad. La libertad de atender a tu ritmo.</p><div class="story-art" aria-hidden="true"><div class="arch">01</div><div class="arch">02</div><div class="arch">03</div></div></div><footer>VALHALLA OFFICE · ESPACIOS QUE ACOMPAÑAN</footer></section>
    <section class="auth-form"><div class="auth-inner"><span class="demo-label">PROTOTIPO · SIN COBROS REALES</span><h2>${administrator ? 'Acceso administrador' : register ? 'Crea tu cuenta profesional' : 'Bienvenido a tu espacio'}</h2><p class="muted">${administrator ? 'Ingresa con tu correo autorizado y tu contraseña privada.' : register ? 'Regístrate y envía tu documentación para comenzar.' : 'Organiza tus atenciones, con todo en un solo lugar.'}</p>
    <form id="auth-form" class="form-stack">${register ? '<label>Nombre completo<input name="name" autocomplete="name" required minlength="2" maxlength="100"></label><label>Profesión o especialidad<input name="specialty" required minlength="2" maxlength="100" placeholder="Ej. Psicología"></label>' : ''}
    <label>Correo electrónico<input name="email" type="email" autocomplete="username" required maxlength="200" placeholder="tu@correo.cl"></label><label>Contraseña<input name="password" type="password" autocomplete="${register?'new-password':'current-password'}" required minlength="${register?10:1}" maxlength="200" placeholder="${register?'Mínimo 10 caracteres':'Tu contraseña'}"></label>
    <p id="auth-error" class="error" role="alert"></p><button class="btn full" type="submit">${register?'Crear cuenta':'Iniciar sesión'} <span aria-hidden="true">→</span></button></form>
    <div class="auth-switch">${administrator ? '¿Eres profesional?' : register?'¿Ya tienes una cuenta?':'¿Primera vez en Valhalla?'} <button class="text-btn" id="auth-switch">${administrator ? 'Volver al inicio' : register?'Inicia sesión':'Regístrate'}</button></div>
    <div class="demo-access"><strong>Elige tu acceso</strong><p class="muted">Cada persona utiliza su propia cuenta. No hay accesos automáticos.</p><div class="row"><button class="btn secondary small" id="professional-access">Acceso profesional</button><button class="btn ghost small" id="administrator-access">Acceso administrador</button></div></div></div></section></div>${gallerySection()}`;
  bindGallery();
  document.querySelector('#auth-switch').onclick = () => administrator ? auth() : auth(!register);
  document.querySelector('#professional-access').onclick = () => auth(true);
  document.querySelector('#administrator-access').onclick = () => auth(false, true);
  document.querySelector('#auth-form').onsubmit = async event => {
    event.preventDefault(); const form=event.currentTarget, button=form.querySelector('button[type=submit]');button.disabled=true;
    try { const credentials=Object.fromEntries(new FormData(form)); credentials.portal=administrator?'admin':'professional'; await api(register?'register':'login', credentials); activeProfile=administrator?'admin':'professional'; view=administrator?'admin':'overview'; await reload(); }
    catch(e) { document.querySelector('#auth-error').textContent=e.message; }
    finally { button.disabled=false; }
  };
}
async function reload() {
  state = await api('me');
  if (!selectedDate) selectedDate=state.today;
  if(adminMode()) { adminData=await api('admin');view='admin'; }
  else if(view==='admin') view='overview';
  await render();
}
function shell() {
  const u = state.user;
  const links = adminMode() ? [['admin','▦','Administración'],['availability','▤','Disponibilidad']] : [['overview','▦','Mi espacio'],['availability','▤','Disponibilidad'],['bookings','◷','Mis reservas'],['packages','◇','Paquetes'],['documents','▱','Documentación'],['profile','○','Mi perfil']];
  root.innerHTML=`<div class="shell"><aside class="sidebar">${brand}<nav aria-label="Navegación principal">${links.map(([id,icon,label])=>`<button class="nav-item ${view===id?'active':''}" data-view="${id}" ${view===id?'aria-current="page"':''}><span class="nav-icon" aria-hidden="true">${icon}</span>${label}</button>`).join('')}</nav><div class="sidebar-bottom"><div class="sidebar-note"><strong>Un saldo, tres espacios</strong><p>Tus horas te acompañan en cualquiera de nuestros boxes.</p></div><div class="profile"><span class="avatar">${escapeHTML(u.name.split(' ').map(s=>s[0]).slice(0,2).join(''))}</span><div><div class="profile-name">${escapeHTML(u.name)}</div><small>${adminMode()?'Administración':escapeHTML(u.specialty)||'Perfil profesional'}</small></div></div><button class="btn ghost small full" data-logout>Cerrar sesión</button></div></aside>
  <main class="main"><header class="topbar"><span class="topbar-date">Tu espacio profesional / ${links.find(l=>l[0]===view)?.[2]||'Reservas'}</span>${brand}<div class="topbar-right"><span class="demo-label">DEMO · SIN COBROS</span>${adminMode()?'<span class="pill">Administración</span>':`<span class="pill ${u.verified?'approved':'pending'}">${u.verified?'✓ Cuenta verificada':'Por verificar'}</span>`}<button class="btn ghost small mobile-logout" data-logout aria-label="Cerrar sesión">Salir</button></div></header>${u.role==='admin'?`<section class="profile-switcher row spread" aria-label="Cambiar perfil"><div><strong>Perfil activo</strong><p>${adminMode()?'Administración del centro':'Tu agenda y saldo personal'}</p></div><div class="row"><button class="btn ${adminMode()?'':'ghost'} small" data-mode="admin" aria-pressed="${adminMode()}">Administración</button><button class="btn ${adminMode()?'ghost':''} small" data-mode="professional" aria-pressed="${!adminMode()}">Perfil profesional</button></div></section>`:''}<div id="content"></div></main></div>`;
  document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=async()=>{activeProfile=b.dataset.mode;view=adminMode()?'admin':'overview';modifying=null;try{await reload();}catch(e){toast(e.message);}});
  document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;modifying=null;render().catch(e=>toast(e.message));});
  document.querySelectorAll('[data-logout]').forEach(b=>b.onclick=async()=>{try{await api('logout',{});state=null;selectedDate='';view='overview';auth();}catch(e){toast(e.message);}});
}
function head(title,subtitle,action='') {return `<div class="page-head"><div><h1>${title}</h1><p>${subtitle}</p></div>${action}</div>`;}
function gallerySection() {
  if(!officeGallery.length)return '';
  return `<section class="office-gallery" aria-labelledby="office-gallery-title"><div class="gallery-intro"><div class="eyebrow">CONOCE VALHALLA</div><h2 id="office-gallery-title">Un espacio para cada atención</h2><p>Recorre la entrada, la recepción y nuestros tres boxes. Pulsa una foto para verla completa.</p></div><div class="office-gallery-grid">${officeGallery.map(group=>`<article class="gallery-group"><div class="row spread"><h3>${escapeHTML(group.title)}</h3><span class="pill neutral">${group.photos.length} ${group.photos.length===1?'foto':'fotos'}</span></div><div class="gallery-photos">${group.photos.map((photo,index)=>`<button class="gallery-photo" data-gallery="${escapeHTML(group.id)}" data-photo-index="${index}" aria-label="Ver ${escapeHTML(photo.alt)}"><img src="${escapeHTML(photo.image_url)}" alt="${escapeHTML(photo.alt)}" loading="lazy" decoding="async"><span>Ampliar foto ${index+1}</span></button>`).join('')}</div></article>`).join('')}</div></section>`;
}
function bindGallery() {
  document.querySelectorAll('[data-gallery]').forEach(button=>button.onclick=()=>{
    const group=officeGallery.find(group=>group.id===button.dataset.gallery);if(!group)return;
    let index=Number(button.dataset.photoIndex||0);
    const show=()=>{
      const photo=group.photos[index];
      modal.innerHTML=`<h2 id="modal-title">${escapeHTML(group.title)}</h2><p class="muted">Foto ${index+1} de ${group.photos.length}</p><img class="room-photo-full" src="${escapeHTML(photo.image_url)}" alt="${escapeHTML(photo.alt)}"><div class="modal-actions gallery-controls">${group.photos.length>1?`<button class="btn ghost small" id="photo-previous" aria-label="Foto anterior">← Anterior</button><button class="btn ghost small" id="photo-next" aria-label="Foto siguiente">Siguiente →</button>`:''}<button class="btn small" id="photo-close">Cerrar foto</button></div>`;
      document.querySelector('#photo-close').onclick=()=>modal.close();
      const previous=document.querySelector('#photo-previous'),next=document.querySelector('#photo-next');
      if(previous)previous.onclick=()=>{index=(index+group.photos.length-1)%group.photos.length;show();};
      if(next)next.onclick=()=>{index=(index+1)%group.photos.length;show();};
    };
    show();modal.showModal();
  });
}
function roomPhoto(box, large=false) {
  const group=officeGallery.find(group=>group.box===box);
  const photo=group?.photos[0];
  return photo ? `<button class="room-photo ${large?'room-photo-large':''}" data-gallery="${escapeHTML(group.id)}" data-photo-index="0" aria-label="Ver fotos de ${escapeHTML(group.title)}"><img src="${escapeHTML(photo.image_url)}" alt="${escapeHTML(photo.alt)}" loading="lazy" decoding="async"><span>${escapeHTML(group.title)} · ${group.photos.length} ${group.photos.length===1?'foto':'fotos'}</span></button>` : '';
}
function bookingItem(b, actions=true) {
  return `<div class="booking-item"><div class="booking-info"><span class="box-avatar">0${b.box}</span><div><strong>${rooms[b.box-1]}</strong><p>${dateText(b.start)} · ${hourText(b.start)} · 1 hora</p>${pill(b.status)} ${b.modifications?'<span class="pill neutral">Cambio utilizado</span>':''}</div></div>${actions&&canModify(b)?`<button class="btn ghost small" data-modify="${b.id}">Modificar</button>`:''}</div>`;
}
function stats() {
  return `<div class="grid stats"><div class="stat"><div class="stat-top"><span>Horas disponibles</span><span>◇</span></div><div class="value">${pendingBalance()} <small>h</small></div><small>Compartidas entre los 3 boxes</small></div><div class="stat"><div class="stat-top"><span>Próximas reservas</span><span>◷</span></div><div class="value">${upcoming().length}</div><small>Tu agenda en Valhalla</small></div><div class="stat"><div class="stat-top"><span>Tu cuenta</span><span>✓</span></div><div class="value account-value">${state.user.verified?'Verificada':'En revisión'}</div><small>${state.user.verified?'Todo listo para reservar':'Envía o revisa tu documentación'}</small></div></div>`;
}
function overview() {
  const first = escapeHTML(state.user.name.split(' ')[0]);
  return head(`Hola, ${first} <span aria-hidden="true">✦</span>`, 'Un buen día empieza con un espacio para ti.', '<button class="btn" data-go="availability">Reservar un box →</button>')+stats()+
  `${!state.user.verified?'<div class="notice">Tu cuenta necesita documentación aprobada para reservar. <button class="text-btn" data-go="documents">Enviar documentación →</button></div>':''}
  <section class="hero"><div><div class="eyebrow">ESPACIO PARA LO QUE IMPORTA</div><h2>Tú cuidas de tus pacientes.<br>Nosotros, de tu espacio.</h2><p>Elige el box y el horario que mejor se adapten a ti. Tus horas funcionan en los tres espacios.</p><button class="btn" data-go="availability">Ver disponibilidad <span aria-hidden="true">→</span></button></div><div class="hero-art" aria-hidden="true"><div class="arch">01</div><div class="arch">02</div><div class="arch">03</div></div></section>
  <div class="grid columns"><section class="card"><div class="card-head"><h2>Tus próximas atenciones</h2><button class="text-btn" data-go="bookings">Ver todas →</button></div>${upcoming().slice(0,3).map(b=>bookingItem(b)).join('')||empty('Tu agenda está abierta. Reserva tu primera hora.')}</section><section class="card"><h2>Tres boxes. Tu elección.</h2><p class="section-intro">Un entorno privado para tu práctica profesional.</p>${rooms.map((r,i)=>`<div class="room">${roomPhoto(i+1)||`<span class="room-badge">0${i+1}</span>`}<div><p>${r}</p><small>Uso profesional · Reserva de 1 hora</small></div></div>`).join('')}<small>Horarios: 08:00 a 20:00 · Chile</small></section></div>${gallerySection()}`;
}
async function availability() {
  const data=await api('availability?date='+selectedDate);
  return head(modifying?'Modifica tu reserva':'Encuentra tu próximo espacio',modifying?'Un único cambio, sin consumir horas adicionales.':'Elige una hora y cualquiera de los tres boxes.')+
  `${modifying?`<div class="notice info">Reserva #${modifying.id}: ${rooms[modifying.box-1]} · ${dateText(modifying.start)} · ${hourText(modifying.start)}. <button class="text-btn" id="abort-change">Salir sin modificar</button></div>`:''}
  ${!state.user.verified&&state.user.role!=='admin'?'<div class="notice">Puedes consultar la agenda. Para reservar, primero verifica tu documentación.</div>':''}
  ${(state.rooms||[]).length?`<section class="room-gallery" aria-label="Fotos de los boxes">${state.rooms.map(room=>roomPhoto(room.box,true)).join('')}</section>`:''}<section class="card"><div class="calendar-toolbar"><label>Fecha de atención<input id="schedule-date" type="date" value="${selectedDate}" min="${state.today}" max="${new Date(new Date(state.today+'T12:00:00Z').getTime()+90*86400000).toISOString().slice(0,10)}"></label><div><div class="legend"><span><i class="dot"></i>Disponible</span><span><i class="dot busy"></i>No disponible</span></div><p class="data-note">Hora de Chile · Sesiones de 60 minutos</p></div></div><div class="table-wrap"><table class="availability"><thead><tr><th scope="col">Hora</th>${rooms.map((r,index)=>`<th scope="col"><span class="room-heading">${r}</span><span class="room-subtitle">${index===2?'Atención infantil':'Espacio profesional'}</span></th>`).join('')}</tr></thead><tbody>${data.slots.map(slot=>`<tr><td>${slot.label}</td>${slot.boxes.map(box=>`<td><button class="slot" data-slot="${slot.start}" data-box="${box.box}" ${box.available?'':'disabled'} aria-label="${rooms[box.box-1]}, ${slot.label}, ${box.available?'disponible':'no disponible'}">${box.available?'Disponible':'—'}</button></td>`).join('')}</tr>`).join('')}</tbody></table></div></section><p class="data-note">Las reservas se confirman con saldo y verificación vigentes. Un profesional no puede ocupar dos boxes a la misma hora.</p>`;
}
function bookings() {
  return head('Tu agenda, en orden','Una sola modificación por reserva, con al menos 2 horas de anticipación.','<button class="btn" data-go="availability">Nueva reserva →</button>')+
  `<section class="card"><div class="card-head"><h2>Próximas reservas</h2><span class="pill neutral">${upcoming().length} reservas</span></div>${upcoming().map(b=>bookingItem(b)).join('')||empty('No tienes reservas próximas.')}</section><section class="card spaced-card"><div class="card-head"><h2>Historial</h2></div>${state.bookings.filter(b=>!upcoming().some(u=>u.id===b.id)).map(b=>bookingItem(b,false)).join('')||empty('Aquí aparecerán tus reservas anteriores o canceladas.')}</section><p class="data-note">Para cancelar una reserva, contacta a administración. En este prototipo, el administrador puede cancelar reservas futuras y devolver la hora al paquete original.</p>`;
}
function packages() {
  return head('Más tiempo para tu vocación','Horas personales, flexibles y compartidas entre nuestros tres boxes.')+
  '<div class="notice info">Pagos simulados: solicitar un paquete no genera un cobro. Administración debe confirmar el pago de prueba para habilitar el saldo.</div>'+
  `<div class="grid package-grid">${state.catalog.map(p=>`<section class="package ${p.hours===10?'featured':''}">${p.hours===10?'<span class="recommend">Más equilibrio</span>':''}<div class="eyebrow">${p.hours===5?'COMIENZA A TU RITMO':p.hours===10?'DA ESPACIO A TU AGENDA':'CRECE CON VALHALLA'}</div><div class="hours">${p.hours} <span>horas</span></div><p class="muted small-text">Un paquete. Los tres boxes.</p><div class="price">${money(p.price)}</div><small>${money(p.price/p.hours)} por hora · precio de ejemplo</small><ul><li>Saldo compartido entre boxes</li><li>Reservas de una hora</li><li>Uso exclusivo de tu cuenta</li><li>Sin vencimiento en el prototipo</li></ul><button class="btn ${p.hours===10?'':'secondary'} full" data-buy="${p.id}">Solicitar paquete →</button></section>`).join('')}</div>
  <section class="card"><div class="card-head"><h2>Mis paquetes</h2><span class="pill neutral">${pendingBalance()} h disponibles</span></div>${state.packages.length?`<div class="table-wrap"><table><thead><tr><th>Paquete</th><th>Saldo</th><th>Total</th><th>Pago simulado</th></tr></thead><tbody>${state.packages.map(p=>`<tr><td>#${p.id} · ${p.hours} horas</td><td>${p.status==='paid'?p.remaining+' h':'No habilitado'}</td><td>${money(p.price)}</td><td>${pill(p.status)}</td></tr>`).join('')}</tbody></table></div>`:empty('Aún no has solicitado paquetes.')}</section><p class="data-note">Los paquetes están vinculados a su comprador y no tienen una opción de transferencia. El precio y la vigencia definitivos deben acordarse antes de producción.</p>`;
}
function profile() {
  return head('Mi perfil profesional','Tu cuenta conserva el mismo correo, saldo y reservas al cambiar de perfil.')+
    `<section class="card"><form id="profile-form" class="form-stack"><label>Nombre completo<input name="name" autocomplete="name" required minlength="2" maxlength="100" value="${escapeHTML(state.user.name)}"></label><label>Profesión o especialidad<input name="specialty" required minlength="2" maxlength="100" value="${escapeHTML(state.user.specialty)}" placeholder="Ej. Psicología"></label><label>Correo de la cuenta<input type="email" value="${escapeHTML(state.user.email)}" disabled></label><p id="profile-error" class="error" role="alert"></p><button class="btn" type="submit">Guardar perfil</button></form></section>`;
}
function documents() {
  return head('Tu respaldo profesional','Revisión manual y privada por el equipo de Valhalla.')+
  `<div class="notice ${state.user.verified?'info':''}">${state.user.verified?'✓ Tu cuenta está verificada. Puedes reservar con saldo disponible.':'Tu cuenta aún no está verificada. Adjunta un documento y espera la revisión de administración.'}</div><section class="card"><h2>Envía tu documentación</h2><p class="section-intro">Para la demostración, adjunta un PDF ficticio de título o acreditación profesional. No uses documentos personales reales.</p><form id="upload-form"><div class="upload-card"><strong>Acreditación profesional</strong><p>Formato PDF · Hasta 2 MB · Acceso exclusivo del titular y administración</p><label>Seleccionar documento<input id="document-file" type="file" accept="application/pdf,.pdf" required></label></div><p class="muted upload-note">Al adjuntar un nuevo documento, la cuenta vuelve a revisión y se pausa la creación y modificación de reservas. Las reservas existentes se conservan.</p><p id="upload-error" class="error" role="alert"></p><button class="btn" type="submit">Enviar a revisión →</button></form></section>
  <section class="card spaced-card"><div class="card-head"><h2>Estado de tus documentos</h2></div>${state.documents.length?state.documents.map(d=>`<div class="booking-item"><div><strong class="document-name">${escapeHTML(d.filename)}</strong><p class="muted caption-text">Enviado el ${dateText(d.created_at)}</p>${d.note?`<p class="small-text">Observación: ${escapeHTML(d.note)}</p>`:''}</div><div class="row">${pill(d.status)}<a class="btn ghost small" href="/api/documents/${d.id}">Descargar</a></div></div>`).join(''):empty('Todavía no has enviado documentos.')}</section>`;
}
function administration() {
  const pending=adminData.packages.filter(p=>p.status==='pending');
  let body='';
  if(adminTab==='payments') body=`<h2>Pagos y paquetes</h2><p class="section-intro">Confirmación manual de operaciones ficticias. Ninguna acción mueve dinero.</p><div class="table-wrap"><table class="admin-table"><thead><tr><th>Profesional</th><th>Paquete</th><th>Monto de ejemplo</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>${adminData.packages.map(p=>`<tr><td>${escapeHTML(p.name)}<small>Titular #${p.user_id}</small></td><td>#${p.id} · ${p.hours} h<small>${p.remaining} h restantes</small></td><td>${money(p.price)}</td><td>${pill(p.status)}</td><td>${p.status==='pending'?`<div class="row"><button class="btn small" data-admin="payment" data-id="${p.id}" data-decision="paid">Confirmar pago simulado</button><button class="btn ghost small" data-admin="payment" data-id="${p.id}" data-decision="rejected">Rechazar</button></div>`:'—'}</td></tr>`).join('')}</tbody></table></div>`;
  if(adminTab==='documents') body=`<h2>Revisión de documentación</h2><p class="section-intro">Descarga y revisa el PDF más reciente de cada profesional antes de aprobar. La revisión del prototipo no certifica autenticidad.</p>${adminData.documents.length?`<div class="table-wrap"><table class="admin-table"><thead><tr><th>Profesional</th><th>Documento</th><th>Estado</th><th>Revisión</th></tr></thead><tbody>${adminData.documents.map(d=>{const latest=!adminData.documents.some(x=>x.user_id===d.user_id&&x.id>d.id);return `<tr><td>${escapeHTML(d.name)}<small>Titular #${d.user_id}</small></td><td><a href="/api/documents/${d.id}">${escapeHTML(d.filename)}</a><small>${dateText(d.created_at)}</small></td><td>${pill(d.status)}${d.note?`<small>${escapeHTML(d.note)}</small>`:''}</td><td>${latest?`<div class="row"><button class="btn small" data-admin="document" data-id="${d.id}" data-decision="approved">Aprobar</button><button class="btn ghost small" data-admin="document" data-id="${d.id}" data-decision="rejected">Rechazar</button></div>`:'Documento anterior'}</td></tr>`;}).join('')}</tbody></table></div>`:empty('No hay documentos para revisar.')}`;
  if(adminTab==='users') body=`<h2>Profesionales y usuarios</h2><p class="section-intro">Desactivar una cuenta cierra sus sesiones e impide nuevas operaciones. Sus reservas existentes se conservan.</p><div class="table-wrap"><table class="admin-table"><thead><tr><th>Nombre</th><th>Correo</th><th>Especialidad</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>${adminData.users.map(u=>`<tr><td>${escapeHTML(u.name)}<small>${u.role==='admin'?'Administrador':'Profesional'}</small></td><td>${escapeHTML(u.email)}</td><td>${escapeHTML(u.specialty)||'—'}</td><td><span class="pill ${u.active?'approved':'rejected'}">${u.active?'Activa':'Inactiva'}</span><small>${u.verified?'Verificada':'Por verificar'}</small></td><td>${u.role==='professional'?`<button class="btn ghost small" data-admin="user" data-id="${u.id}" data-active="${!u.active}">${u.active?'Desactivar':'Activar'}</button>`:'—'}</td></tr>`).join('')}</tbody></table></div>`;
  if(adminTab==='bookings') body=`<h2>Agenda general</h2><p class="section-intro">Cancelar una reserva futura libera el box y devuelve 1 hora al paquete original, una sola vez.</p>${adminData.bookings.length?`<div class="table-wrap"><table class="admin-table"><thead><tr><th>Profesional</th><th>Box / fecha</th><th>Estado</th><th>Modificaciones</th><th>Acciones</th></tr></thead><tbody>${adminData.bookings.map(b=>`<tr><td>${escapeHTML(b.name)}<small>Reserva #${b.id}</small></td><td>${rooms[b.box-1]}<small>${dateText(b.start)} · ${hourText(b.start)}</small></td><td>${pill(b.status)}</td><td>${b.modifications} / 1</td><td>${b.status==='confirmed'&&new Date(b.start)>new Date()?`<button class="btn danger small" data-admin="cancel" data-id="${b.id}">Cancelar y devolver hora</button>`:'—'}</td></tr>`).join('')}</tbody></table></div>`:empty('No hay reservas registradas.')}`;
  if(adminTab==='audit') body=`<h2>Registro de operaciones</h2><p class="section-intro">Últimas 30 acciones realizadas en el prototipo.</p>${adminData.audit.map(a=>`<div class="audit-item"><strong>${escapeHTML(a.name||'Sistema')}</strong><p>${escapeHTML(a.action)} · registro #${escapeHTML(a.target)}</p><small>${dateText(a.created_at)} · ${hourText(a.created_at)}</small></div>`).join('')||empty('Aún no hay operaciones.')}`;
  return head('Administración','La operación de Valhalla, en un solo lugar.')+`<div class="grid stats"><div class="stat"><div class="stat-top">Profesionales</div><div class="value">${adminData.users.filter(u=>u.role==='professional').length}</div><small>Cuentas registradas</small></div><div class="stat"><div class="stat-top">Pagos pendientes</div><div class="value">${pending.length}</div><small>Operaciones simuladas</small></div><div class="stat"><div class="stat-top">Reservas próximas</div><div class="value">${adminData.bookings.filter(b=>b.status==='confirmed'&&new Date(b.start)>new Date()).length}</div><small>Entre los tres boxes</small></div></div><div class="tabs" role="group" aria-label="Secciones de administración">${[['payments','Pagos'],['documents','Documentos'],['users','Usuarios'],['bookings','Reservas'],['audit','Actividad']].map(([id,label])=>`<button class="tab ${adminTab===id?'active':''}" data-tab="${id}" aria-pressed="${adminTab===id}">${label}</button>`).join('')}</div><section class="card">${body}</section>`;
}

async function render() {
  shell(); const content = document.querySelector('#content');
  content.innerHTML = view==='availability'?await availability():view==='packages'?packages():view==='bookings'?bookings():view==='documents'?documents():view==='profile'?profile():view==='admin'?administration():overview();
  bind();
}
function showModal(title, content, confirmLabel, action) {
  modal.innerHTML=`<h2 id="modal-title">${title}</h2>${content}<p class="error modal-error" id="modal-error" role="alert"></p><div class="modal-actions"><button class="btn ghost" id="modal-close">Volver</button><button class="btn" id="modal-confirm">${confirmLabel}</button></div>`;
  modal.showModal();document.querySelector('#modal-close').onclick=()=>modal.close();
  document.querySelector('#modal-confirm').onclick=async event=>{
    const button=event.currentTarget;button.disabled=true;
    try {await action();modal.close();await reload();}
    catch(e){document.querySelector('#modal-error').textContent=e.message;}
    finally{button.disabled=false;}
  };
}
function bind() {
  bindGallery();
  const profileForm=document.querySelector('#profile-form');if(profileForm)profileForm.onsubmit=async event=>{event.preventDefault();const button=profileForm.querySelector('button');button.disabled=true;try{await api('profile',Object.fromEntries(new FormData(profileForm)));await reload();toast('Perfil profesional actualizado.');}catch(e){document.querySelector('#profile-error').textContent=e.message;}finally{button.disabled=false;}};
  document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>{view=b.dataset.go;modifying=null;render().catch(e=>toast(e.message));});
  document.querySelectorAll('[data-modify]').forEach(b=>b.onclick=()=>{modifying=state.bookings.find(x=>x.id===Number(b.dataset.modify));selectedDate=new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Santiago'}).format(new Date(modifying.start));view='availability';render().catch(e=>toast(e.message));});
  const abort=document.querySelector('#abort-change');if(abort)abort.onclick=()=>{modifying=null;view='bookings';render().catch(e=>toast(e.message));};
  const date=document.querySelector('#schedule-date');if(date)date.onchange=()=>{if(date.value){selectedDate=date.value;render().catch(e=>toast(e.message));}};
  document.querySelectorAll('[data-slot]').forEach(b=>b.onclick=()=>{
    if(adminMode()){toast('La agenda administrativa es de consulta. Las reservas se crean desde la cuenta profesional.');return;}
    if(!state.user.verified){toast('Primero envía tu documentación y espera su aprobación.');return;}
    if(!modifying && pendingBalance()===0){toast('Necesitas horas de un paquete con pago confirmado.');return;}
    const box=Number(b.dataset.box),start=b.dataset.slot, current=modifying;
    showModal(current?'Confirma tu único cambio':'Tu próxima atención',`<p>${current?'Se conservará el saldo y se utilizará tu única modificación.':'Se descontará 1 hora de tu paquete confirmado más antiguo con saldo.'}</p><div class="modal-detail"><strong>${rooms[box-1]}</strong><br>${dateText(start)} · ${hourText(start)}<br>Duración: 1 hora</div><p>${current?'El horario original se libera solo si el cambio se confirma.':'La reserva y su saldo pertenecen exclusivamente a tu cuenta.'}</p>`,current?'Confirmar cambio':'Confirmar reserva',async()=>{
      await api(current?'bookings/'+current.id:'bookings',{box,start});modifying=null;view='bookings';toast(current?'Reserva modificada. Has utilizado tu único cambio.':'Reserva confirmada. ¡Tu espacio te espera!');
    });
  });
  document.querySelectorAll('[data-buy]').forEach(b=>b.onclick=()=>{
    const p=state.catalog.find(p=>p.id===b.dataset.buy);
    showModal('Solicitar paquete',`<p>Esta operación es una simulación. No ingreses datos bancarios.</p><div class="modal-detail"><strong>${p.hours} horas · ${money(p.price)}</strong><br>Uso personal en cualquiera de los tres boxes.</div><p>El saldo se habilitará cuando administración confirme el pago de prueba.</p>`,'Solicitar sin cobro',async()=>{await api('packages',{catalog_id:p.id});toast('Solicitud registrada. Pago simulado pendiente de confirmación.');});
  });
  const upload=document.querySelector('#upload-form');if(upload)upload.onsubmit=async event=>{
    event.preventDefault();const file=document.querySelector('#document-file').files[0],button=upload.querySelector('button');
    if(!file || !file.name.toLowerCase().endsWith('.pdf') || file.size>2000000){document.querySelector('#upload-error').textContent='Selecciona un PDF de hasta 2 MB.';return;}
    button.disabled=true;
    try {const buffer=new Uint8Array(await file.arrayBuffer());let binary='';for(const byte of buffer)binary+=String.fromCharCode(byte);await api('documents',{filename:file.name,content:btoa(binary)});toast('Documento enviado. Tu cuenta está en revisión.');await reload();}
    catch(e){document.querySelector('#upload-error').textContent=e.message;}
    finally{button.disabled=false;}
  };
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{adminTab=b.dataset.tab;render().catch(e=>toast(e.message));});
  document.querySelectorAll('[data-admin]').forEach(b=>b.onclick=()=>{
    const action=b.dataset.admin,decision=b.dataset.decision,active=b.dataset.active==='true';
    const title=action==='payment'?(decision==='paid'?'Confirmar pago simulado':'Rechazar solicitud'):action==='document'?(decision==='approved'?'Aprobar documentación':'Rechazar documentación'):action==='user'?(active?'Activar profesional':'Desactivar profesional'):'Cancelar reserva';
    let copy=action==='payment'?'No se realizará ningún cobro ni devolución real.':action==='document'?'Confirma que revisaste el documento más reciente del profesional.':action==='user'?'Las reservas existentes se conservan. Desactivar cierra las sesiones del profesional.':'Se libera el horario y se devuelve 1 hora al paquete del titular. Esta acción no se puede deshacer.';
    showModal(title,`<p>${copy}</p>${action==='document'?`<label class="review-label">Observación ${decision==='rejected'?'(obligatoria)':'(opcional)'}<textarea id="review-note" rows="3" maxlength="500"></textarea></label>`:''}`,'Confirmar',async()=>{await api('admin',{action,id:Number(b.dataset.id),decision,active,note:document.querySelector('#review-note')?.value||''});toast('Operación registrada.');});
  });
}
async function startApp() {
  try {officeGallery=(await api('gallery')).groups||[];}catch{officeGallery=[];}
  try{const data=await api('me');state=data;selectedDate=data.today;if(data.user.role==='admin'){adminData=await api('admin');view='admin';}await render();}
  catch{auth();}
}
startApp();
