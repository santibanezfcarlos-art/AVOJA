const root = document.querySelector('#root');
const modal = document.querySelector('#modal');
let state = null, adminData = null, view = 'overview', adminTab = 'payments', selectedDate = '', modifying = null;
let activeProfile = 'admin';
let contactsDate='',dailyContacts=null;
let officeGallery = [];
let googleEnabled = false;
const googleResult = new URLSearchParams(location.search).get('google_result');
if(googleResult){const clean=new URL(location.href);clean.searchParams.delete('google_result');history.replaceState(null,'',clean.pathname+clean.search+clean.hash);}
async function continueWithGoogle(link=false){
  const button=document.querySelector(link?'#link-google':'#google-access');if(button)button.disabled=true;
  try{const result=await api(link?'google/link':'google/start',{});const target=new URL(result.authorization_url);if(target.origin!=='https://accounts.google.com')throw new Error('No se pudo abrir Google.');location.assign(target.href);}
  catch(e){toast(e.message);if(button)button.disabled=false;}
}
const adminMode = () => state?.user.role==='admin' && activeProfile==='admin';
let disposeWelcomeCarousel = () => {};
let toastTimer;
let paymentReturn = new URLSearchParams(window.location.search).has('payment_result');
if(paymentReturn){const clean=new URL(window.location.href);clean.searchParams.delete('payment_result');history.replaceState(null,'',clean.pathname+clean.search+clean.hash);}
let passwordResetToken = new URLSearchParams(window.location.search).get('reset_password');
if(passwordResetToken){const clean=new URL(window.location.href);clean.searchParams.delete('reset_password');history.replaceState(null,'',clean.pathname+clean.search+clean.hash);}
let emailConfirmationToken = new URLSearchParams(window.location.search).get('verify_email');
if(emailConfirmationToken){const clean=new URL(window.location.href);clean.searchParams.delete('verify_email');history.replaceState(null,'',clean.pathname+clean.search+clean.hash);}
const money = n => new Intl.NumberFormat('es-CL', {style:'currency', currency:'CLP', maximumFractionDigits:0}).format(n);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const dateText = value => new Intl.DateTimeFormat('es-CL', {timeZone:'America/Santiago', day:'numeric',month:'short',year:'numeric'}).format(new Date(value));
const hourText = value => new Intl.DateTimeFormat('es-CL', {timeZone:'America/Santiago',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
const sessionRange = start => `${hourText(start)}–${hourText(new Date(new Date(start).getTime()+55*60000))}`;
const timingLabels={in_session:'En horario de atención',buffer:'Margen entre atenciones',upcoming:'Próxima reserva',finished:'Atención finalizada'};
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

function welcomeCarousel() {
  const order=['box-1','box-2','box-infantil','recepcion','entrada'];
  const groups=order.map(id=>officeGallery.find(group=>group.id===id)).filter(Boolean);
  const slides=groups.flatMap(group=>group.photos.map(photo=>({...photo,title:group.title})));
  if(!slides.length)return '';
  return `<section class="welcome-carousel" role="region" aria-roledescription="carrusel" aria-label="Fotos de Valhalla Office"><div class="welcome-photo-frame"><img id="welcome-photo" src="${escapeHTML(slides[0].image_url)}" alt="${escapeHTML(slides[0].alt)}" decoding="async" fetchpriority="high"></div><div class="welcome-photo-caption"><div><span class="eyebrow">CONOCE TU PRÓXIMO ESPACIO</span><strong id="welcome-photo-title">${escapeHTML(slides[0].title)}</strong></div><span id="welcome-photo-count">1 / ${slides.length}</span></div><div class="welcome-carousel-controls"><button type="button" id="welcome-previous" aria-label="Mostrar foto anterior">←</button><div class="welcome-dots" role="group" aria-label="Elegir foto">${slides.map((slide,index)=>`<button type="button" data-slide="${index}" aria-label="Foto ${index+1}: ${escapeHTML(slide.title)}" aria-pressed="${index===0}"></button>`).join('')}</div><button type="button" id="welcome-next" aria-label="Mostrar foto siguiente">→</button><button type="button" id="welcome-pause" aria-label="Pausar carrusel" aria-pressed="false">Pausar</button></div></section>`;
}
function bindWelcomeCarousel() {
  disposeWelcomeCarousel();
  const carousel=document.querySelector('.welcome-carousel');if(!carousel)return;
  const order=['box-1','box-2','box-infantil','recepcion','entrada'];
  const slides=order.flatMap(id=>{const group=officeGallery.find(group=>group.id===id);return group?group.photos.map(photo=>({...photo,title:group.title})):[];});
  const image=carousel.querySelector('#welcome-photo');
  const pause=carousel.querySelector('#welcome-pause');
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
  let index=0,timer=null,paused=reduced.matches,hovered=false,focused=false;
  const stop=()=>{clearInterval(timer);timer=null;};
  const paint=()=>{
    const slide=slides[index];image.src=slide.image_url;image.alt=slide.alt;
    image.classList.remove('slide-arrival');if(!reduced.matches)requestAnimationFrame(()=>image.isConnected&&image.classList.add('slide-arrival'));
    carousel.querySelector('#welcome-photo-title').textContent=slide.title;
    carousel.querySelector('#welcome-photo-count').textContent=`${index+1} / ${slides.length}`;
    carousel.querySelectorAll('[data-slide]').forEach(button=>button.setAttribute('aria-pressed',String(Number(button.dataset.slide)===index)));
  };
  const schedule=()=>{stop();if(!paused&&!hovered&&!focused&&!document.hidden&&slides.length>1)timer=setInterval(()=>{if(!carousel.isConnected){stop();return;}index=(index+1)%slides.length;paint();},5000);};
  const show=next=>{index=(next+slides.length)%slides.length;paint();schedule();};
  const updatePause=()=>{pause.textContent=paused?'Reanudar':'Pausar';pause.setAttribute('aria-label',paused?'Reanudar carrusel':'Pausar carrusel');pause.setAttribute('aria-pressed',String(paused));};
  carousel.querySelector('#welcome-previous').onclick=()=>show(index-1);
  carousel.querySelector('#welcome-next').onclick=()=>show(index+1);
  carousel.querySelectorAll('[data-slide]').forEach(button=>button.onclick=()=>show(Number(button.dataset.slide)));
  pause.onclick=()=>{paused=!paused;updatePause();schedule();};
  carousel.onmouseenter=()=>{hovered=true;schedule();};carousel.onmouseleave=()=>{hovered=false;schedule();};
  carousel.onfocusin=()=>{focused=true;schedule();};carousel.onfocusout=event=>{focused=carousel.contains(event.relatedTarget);schedule();};
  carousel.onkeydown=event=>{if(event.key==='ArrowRight'){event.preventDefault();show(index+1);}if(event.key==='ArrowLeft'){event.preventDefault();show(index-1);}};
  const visibility=()=>schedule();const preference=()=>{if(reduced.matches)paused=true;updatePause();schedule();};
  document.addEventListener('visibilitychange',visibility);reduced.addEventListener('change',preference);
  disposeWelcomeCarousel=()=>{stop();document.removeEventListener('visibilitychange',visibility);reduced.removeEventListener('change',preference);disposeWelcomeCarousel=()=>{};};
  updatePause();schedule();
}
function auth(register = false, administrator = false) {
  disposeWelcomeCarousel();
  root.innerHTML = `<div class="auth"><section class="auth-story">${brand}<div><div class="eyebrow">UN LUGAR PARA CRECER</div><h1>Tu vocación.<br><em>Su mejor espacio.</em></h1><p>Tres boxes para atender a tu ritmo, una recepción para tus pacientes y un espacio para hacer crecer tu práctica.</p><div class="welcome-benefits"><span>3 boxes profesionales</span><span>Paquetes de horas</span><span>Box infantil</span></div>${welcomeCarousel()}<a class="welcome-gallery-link" href="#office-gallery-title">Recorre todos nuestros espacios <span aria-hidden="true">↓</span></a></div><footer>COLO COLO 222 · OFICINA 710</footer></section>
    <section class="auth-form"><div class="auth-inner"><span class="demo-label">PROTOTIPO · SIN COBROS REALES</span><h2>${administrator ? 'Acceso administrador' : register ? 'Crea tu cuenta profesional' : 'Bienvenido a tu espacio'}</h2><p class="muted">${administrator ? 'Ingresa con tu correo autorizado y tu contraseña privada.' : register ? 'Regístrate y envía tu documentación para comenzar.' : 'Organiza tus atenciones, con todo en un solo lugar.'}</p>
    ${googleEnabled&&!administrator?'<button type="button" class="btn secondary full" id="google-access">Continuar con Google</button><p class="muted">O continúa con tu correo y contraseña.</p>':''}
    <form id="auth-form" class="form-stack">${register ? '<label>Nombre completo<input name="name" autocomplete="name" required minlength="2" maxlength="100"></label><label>Profesión o especialidad<input name="specialty" required minlength="2" maxlength="100" placeholder="Ej. Psicología"></label>' : ''}
    <label>Correo electrónico<input name="email" type="email" autocomplete="username" required maxlength="200" placeholder="tu@correo.cl"></label><label>Contraseña<input name="password" type="password" autocomplete="${register?'new-password':'current-password'}" required minlength="${register?10:1}" maxlength="200" placeholder="${register?'Mínimo 10 caracteres':'Tu contraseña'}"></label>
    ${register?'<label>Confirmar contraseña<input name="password_confirmation" type="password" autocomplete="new-password" required minlength="10" maxlength="200" placeholder="Repite tu contraseña"></label>':''}
    <p id="auth-error" class="error" role="alert"></p><button class="btn full" type="submit">${register?'Crear cuenta':'Iniciar sesión'} <span aria-hidden="true">→</span></button></form>
    ${!register?'<button class="text-btn" id="forgot-password">Olvidé mi contraseña</button>':''}
    <div class="auth-switch">${administrator ? '¿Eres profesional?' : register?'¿Ya tienes una cuenta?':'¿Primera vez en Valhalla?'} <button class="text-btn" id="auth-switch">${administrator ? 'Volver al inicio' : register?'Inicia sesión':'Regístrate'}</button></div>
    <div class="demo-access"><strong>Elige tu acceso</strong><p class="muted">Cada persona utiliza su propia cuenta. No hay accesos automáticos.</p><div class="row"><button class="btn secondary small" id="professional-access">Acceso profesional</button><button class="btn ghost small" id="administrator-access">Acceso administrador</button></div></div></div></section></div>${gallerySection()}`;
  bindGallery();
  bindWelcomeCarousel();
  const googleButton=document.querySelector('#google-access');if(googleButton)googleButton.onclick=()=>continueWithGoogle();
  const forgot=document.querySelector('#forgot-password');if(forgot)forgot.onclick=()=>passwordRecovery(false,administrator);
  document.querySelector('#auth-switch').onclick = () => administrator ? auth() : auth(!register);
  document.querySelector('#professional-access').onclick = () => auth(true);
  document.querySelector('#administrator-access').onclick = () => auth(false, true);
  document.querySelector('#auth-form').onsubmit = async event => {
    event.preventDefault(); const form=event.currentTarget, button=form.querySelector('button[type=submit]');button.disabled=true;
    try { const credentials=Object.fromEntries(new FormData(form)); if(register&&credentials.password!==credentials.password_confirmation)throw new Error('Las contraseñas no coinciden.'); credentials.portal=administrator?'admin':'professional'; const result=await api(register?'register':'login', credentials); activeProfile=administrator?'admin':'professional'; view=administrator?'admin':'overview'; await reload(); if(result.email_verification?.message)toast(result.email_verification.message); }
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
  disposeWelcomeCarousel();
  const u = state.user;
  const links = adminMode() ? [['admin','▦','Administración'],['availability','▤','Disponibilidad']] : [['overview','▦','Mi espacio'],['availability','▤','Disponibilidad'],['bookings','◷','Mis reservas'],['packages','◇','Paquetes'],['documents','▱','Documentación'],['profile','○','Mi perfil']];
  root.innerHTML=`<div class="shell"><aside class="sidebar">${brand}<nav aria-label="Navegación principal">${links.map(([id,icon,label])=>`<button class="nav-item ${view===id?'active':''}" data-view="${id}" ${view===id?'aria-current="page"':''}><span class="nav-icon" aria-hidden="true">${icon}</span>${label}</button>`).join('')}</nav><div class="sidebar-bottom"><div class="sidebar-note"><strong>Un saldo, tres espacios</strong><p>Tus horas te acompañan en cualquiera de nuestros boxes.</p></div><div class="profile"><span class="avatar">${escapeHTML(u.name.split(' ').map(s=>s[0]).slice(0,2).join(''))}</span><div><div class="profile-name">${escapeHTML(u.name)}</div><small>${adminMode()?'Administración':escapeHTML(u.specialty)||'Perfil profesional'}</small></div></div><button class="btn ghost small full" data-logout>Cerrar sesión</button></div></aside>
  <main class="main"><header class="topbar"><span class="topbar-date">Tu espacio profesional / ${links.find(l=>l[0]===view)?.[2]||'Reservas'}</span>${brand}<div class="topbar-right"><span class="demo-label">DEMO · SIN COBROS</span>${adminMode()?'<span class="pill">Administración</span>':`<span class="pill ${u.verified?'approved':'pending'}">${u.verified?(u.identity_source==='legacy'?'✓ Validación anterior':'✓ Carnet aprobado'):'Por verificar'}</span>`}<button class="btn ghost small mobile-logout" data-logout aria-label="Cerrar sesión">Salir</button></div></header>${u.role==='admin'?`<section class="profile-switcher row spread" aria-label="Cambiar perfil"><div><strong>Perfil activo</strong><p>${adminMode()?'Administración del centro':'Tu agenda y saldo personal'}</p></div><div class="row"><button class="btn ${adminMode()?'':'ghost'} small" data-mode="admin" aria-pressed="${adminMode()}">Administración</button><button class="btn ${adminMode()?'ghost':''} small" data-mode="professional" aria-pressed="${!adminMode()}">Perfil profesional</button></div></section>`:''}${!u.email_verified?`<div class="notice" role="status"><strong>Confirma tu correo electrónico</strong><p>${state.email_delivery_available?(state.email_verification_delivery==='accepted'?'El proveedor aceptó tu correo de confirmación. Revisa entrada y spam.':state.email_verification_delivery==='error'?'No se pudo enviar la confirmación. Administración debe revisar el diagnóstico de correo.':'Tu correo aún no está confirmado. Solicita un enlace con el botón de abajo.'):'El envío de confirmaciones todavía no está habilitado. Administración debe configurarlo antes de que puedas confirmar tu correo y reservar.'}</p><button class="btn ghost small" id="resend-email" ${state.email_delivery_available?'':'disabled'}>Reenviar confirmación</button></div>`:adminMode()&&!state.email_delivery_available?'<div class="notice">El envío de correos aún no está habilitado. Configura el servicio de correo para que los profesionales puedan confirmar sus cuentas y reservar.</div>':''}<div id="content"></div></main></div>`;
  const resend=document.querySelector('#resend-email');if(resend)resend.onclick=async()=>{resend.disabled=true;try{const result=await api('email-verification/resend',{});toast(result.message||'Tu correo ya está confirmado.');await reload();}catch(e){toast(e.message);}finally{resend.disabled=false;}};
  document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=async()=>{activeProfile=b.dataset.mode;view=adminMode()?'admin':'overview';modifying=null;try{await reload();}catch(e){toast(e.message);}});
  document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;modifying=null;render().catch(e=>toast(e.message));});
  document.querySelectorAll('[data-logout]').forEach(b=>b.onclick=async()=>{try{await api('logout',{});state=null;selectedDate='';contactsDate='';dailyContacts=null;view='overview';auth();}catch(e){toast(e.message);}});
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
  return `<div class="booking-item"><div class="booking-info"><span class="box-avatar">0${b.box}</span><div><strong>${rooms[b.box-1]}</strong><p>${dateText(b.start)} · ${sessionRange(b.start)} · 55 min</p>${pill(b.status)} ${b.modifications?'<span class="pill neutral">Cambio utilizado</span>':''}</div></div>${actions&&canModify(b)?`<button class="btn ghost small" data-modify="${b.id}">Modificar</button>`:''}</div>`;
}
function stats() {
  return `<div class="grid stats"><div class="stat"><div class="stat-top"><span>Sesiones disponibles</span><span>◇</span></div><div class="value">${pendingBalance()} <small>ses.</small></div><small>Compartidas entre los 3 boxes</small></div><div class="stat"><div class="stat-top"><span>Próximas reservas</span><span>◷</span></div><div class="value">${upcoming().length}</div><small>Tu agenda en Valhalla</small></div><div class="stat"><div class="stat-top"><span>Tu cuenta</span><span>✓</span></div><div class="value account-value">${state.user.verified?'Verificada':'En revisión'}</div><small>${state.user.verified?(state.user.email_verified?'Identidad validada':'Falta confirmar tu correo'):'Envía o revisa tu documentación'}</small></div></div>`;
}
function overview() {
  const first = escapeHTML(state.user.name.split(' ')[0]);
  return head(`Hola, ${first} <span aria-hidden="true">✦</span>`, 'Un buen día empieza con un espacio para ti.', '<button class="btn" data-go="availability">Reservar un box →</button>')+stats()+
  `${!state.user.verified?'<div class="notice">Tu cuenta necesita carnet de identidad aprobado para reservar. <button class="text-btn" data-go="documents">Enviar carnet →</button></div>':''}
  <section class="hero"><div><div class="eyebrow">ESPACIO PARA LO QUE IMPORTA</div><h2>Tú cuidas de tus pacientes.<br>Nosotros, de tu espacio.</h2><p>Elige el box y el horario que mejor se adapten a ti. Tus horas funcionan en los tres espacios.</p><button class="btn" data-go="availability">Ver disponibilidad <span aria-hidden="true">→</span></button></div><div class="hero-art" aria-hidden="true"><div class="arch">01</div><div class="arch">02</div><div class="arch">03</div></div></section>
  <div class="grid columns"><section class="card"><div class="card-head"><h2>Tus próximas atenciones</h2><button class="text-btn" data-go="bookings">Ver todas →</button></div>${upcoming().slice(0,3).map(b=>bookingItem(b)).join('')||empty('Tu agenda está abierta. Reserva tu primera hora.')}</section><section class="card"><h2>Tres boxes. Tu elección.</h2><p class="section-intro">Un entorno privado para tu práctica profesional.</p>${rooms.map((r,i)=>`<div class="room">${roomPhoto(i+1)||`<span class="room-badge">0${i+1}</span>`}<div><p>${r}</p><small>Uso profesional · Atención de 55 minutos</small></div></div>`).join('')}<small>Horarios: 08:00 a 20:00 · Chile</small></section></div>${gallerySection()}`;
}
async function availability() {
  const data=await api((adminMode()?'admin/availability':'availability')+'?date='+selectedDate);
  return head(adminMode()?'Agenda y ocupación de los boxes':modifying?'Modifica tu reserva':'Encuentra tu próximo espacio',adminMode()?'Consulta quién reservó cada box, con nombre y horario de atención.':modifying?'Un único cambio, sin consumir sesiones adicionales.':'Sesiones de 55 minutos, con inicios cada hora.')+
  `${modifying?`<div class="notice info">Reserva #${modifying.id}: ${rooms[modifying.box-1]} · ${dateText(modifying.start)} · ${hourText(modifying.start)}. <button class="text-btn" id="abort-change">Salir sin modificar</button></div>`:''}
  ${!state.user.verified&&!adminMode()?'<div class="notice">Puedes consultar la agenda. Para reservar necesitas el carnet aprobado y saldo de un paquete confirmado.</div>':''}
  ${!adminMode()&&(state.rooms||[]).length?`<section class="room-gallery" aria-label="Fotos de los boxes">${state.rooms.map(room=>roomPhoto(room.box,true)).join('')}</section>`:''}<section class="card"><div class="calendar-toolbar"><label>Fecha de atención<input id="schedule-date" type="date" value="${selectedDate}" min="${state.today}" max="${new Date(new Date(state.today+'T12:00:00Z').getTime()+90*86400000).toISOString().slice(0,10)}"></label><div><button class="btn ghost small" id="refresh-agenda">Actualizar agenda</button><div class="legend"><span><i class="dot"></i>Disponible</span><span><i class="dot busy"></i>No disponible</span></div><p class="data-note">Hora de Chile · 55 min de atención + 5 min de margen</p></div></div><div class="table-wrap"><table class="availability"><thead><tr><th scope="col">Hora</th>${rooms.map((r,index)=>`<th scope="col"><span class="room-heading">${r}</span><span class="room-subtitle">${index===2?'Atención infantil':'Espacio profesional'}</span></th>`).join('')}</tr></thead><tbody>${data.slots.map(slot=>`<tr><td>${slot.label}</td>${slot.boxes.map(box=>`<td>${adminMode()&&box.booking?`<div class="admin-occupied ${box.booking.timing==='in_session'?'current':''}"><strong>${escapeHTML(box.booking.name)}</strong><small>${escapeHTML(box.booking.email)}</small><span>${sessionRange(box.booking.start)}</span><small>${timingLabels[box.booking.timing]}</small></div>`:`<button class="slot" data-slot="${slot.start}" data-box="${box.box}" ${box.available?'':'disabled'} aria-label="${rooms[box.box-1]}, ${slot.label}, ${box.available?'disponible':'no disponible'}">${box.available?'Disponible':'—'}</button>`}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section><p class="data-note">${adminMode()?'La agenda muestra reservas y su horario; no confirma presencia física en el centro. ':''}Las reservas se confirman con saldo y verificación vigentes. Un profesional no puede ocupar dos boxes a la misma hora.</p>`;
}
function bookings() {
  return head('Tu agenda, en orden','Una sola modificación por reserva, con al menos 2 horas de anticipación.','<button class="btn" data-go="availability">Nueva reserva →</button>')+
  `<section class="card"><div class="card-head"><h2>Próximas reservas</h2><span class="pill neutral">${upcoming().length} reservas</span></div>${upcoming().map(b=>bookingItem(b)).join('')||empty('No tienes reservas próximas.')}</section><section class="card spaced-card"><div class="card-head"><h2>Historial</h2></div>${state.bookings.filter(b=>!upcoming().some(u=>u.id===b.id)).map(b=>bookingItem(b,false)).join('')||empty('Aquí aparecerán tus reservas anteriores o canceladas.')}</section><p class="data-note">Para cancelar una reserva, contacta a administración. En este prototipo, el administrador puede cancelar reservas futuras y devolver la hora al paquete original.</p>`;
}
function packages() {
  return head('Planes mixtos · todos los boxes','Una sesión de 55 minutos por cada hora contratada. Puedes combinar los tres boxes.')+
  `<div class="notice info">${state.webpay_mode==='integration'?'Webpay en ambiente de pruebas: usa únicamente tarjetas de prueba. No se realizan cargos reales. El saldo se carga automáticamente al aprobarse el pago.':'Los pagos siguen siendo simulados; solicitar un plan no genera un cobro real.'} Para reservar necesitas correo confirmado, identidad validada y saldo disponible.</div>`+
  `<div class="grid package-grid">${state.catalog.map(p=>`<section class="package ${p.hours===10?'featured':''}">${p.hours===10?'<span class="recommend">Plan de 10 horas</span>':''}<div class="eyebrow">${p.hours===1?'SESIÓN INDIVIDUAL':'PLAN MIXTO · TODOS LOS BOXES'}</div><div class="hours">${p.hours} <span>${p.hours===1?'hora':'horas'}</span></div><p class="muted small-text">${p.hours} ${p.hours===1?'sesión':'sesiones'} · 55 minutos cada una</p><div class="price">${money(p.price)}</div><small>${money(p.price/p.hours)} por sesión</small><ul><li>Usable en cualquiera de los 3 boxes</li><li>Inicios cada hora, de 08:00 a 19:00</li><li>5 minutos de margen entre atenciones</li><li>Uso exclusivo de tu cuenta</li></ul><button class="btn ${p.hours===10?'':'secondary'} full" data-buy="${p.id}">${state.webpay_mode==='integration'?'Comprar en Webpay · prueba':'Solicitar'} ${p.hours===1?'sesión':'plan'} →</button></section>`).join('')}</div>
  <section class="card"><div class="card-head"><h2>Mis planes</h2><span class="pill neutral">${pendingBalance()} sesiones disponibles</span></div>${state.packages.length?`<div class="table-wrap"><table><thead><tr><th>Plan</th><th>Sesiones restantes</th><th>Total</th><th>Origen / estado</th></tr></thead><tbody>${state.packages.map(p=>`<tr><td>#${p.id} · ${p.hours} ${p.hours===1?'hora':'horas'}</td><td>${p.status==='paid'?p.remaining:'No habilitado'}</td><td>${money(p.price)}</td><td>${p.source==='migration'?'Saldo anterior':p.source==='webpay_integration'?'Webpay · pruebas':'Pago simulado'} ${pill(p.status)}</td></tr>`).join('')}</tbody></table></div>`:empty('Aún no has solicitado un plan.')}</section>${paymentList()}<p class="data-note">Cada reserva descuenta una sesión. Los planes son personales y sus sesiones se comparten entre boxes. Los planes existentes conservan su saldo y precio histórico.</p>`;
}
function profile() {
  return head('Mi perfil profesional','Tu cuenta conserva el mismo correo, saldo y reservas al cambiar de perfil.')+
    `<section class="card"><form id="profile-form" class="form-stack"><label>Nombre completo<input name="name" autocomplete="name" required minlength="2" maxlength="100" value="${escapeHTML(state.user.name)}"></label><label>Profesión o especialidad<input name="specialty" required minlength="2" maxlength="100" value="${escapeHTML(state.user.specialty)}" placeholder="Ej. Psicología"></label><label>Correo de la cuenta<input type="email" value="${escapeHTML(state.user.email)}" disabled></label><p class="data-note">Cambiar tu nombre requiere una nueva revisión del carnet. Tu correo y permisos se conservan.</p>${googleEnabled&&state.user.role==='professional'?`<div class="notice"><strong>Acceso con Google</strong><p>${state.user.google_linked?'Tu cuenta está vinculada. Puedes iniciar sesión con Google.':'Vincula la cuenta de Google que utiliza este mismo correo. Conservarás tus reservas y saldo.'}</p>${state.user.google_linked?'':'<button class="btn secondary small" type="button" id="link-google">Vincular Google</button>'}</div>`:''}<p id="profile-error" class="error" role="alert"></p><button class="btn" type="submit">Guardar perfil</button></form></section>`;
}
function documentLinks(document) {
  const files=document.files||[{filename:document.filename,download_url:'/api/documents/'+document.id}];
  return files.map(file=>`<a class="btn ghost small" href="${escapeHTML(file.download_url)}">${escapeHTML(file.filename)}</a>`).join(' ');
}
function documents() {
  if(state.user.identity_source==='legacy')return head('Identidad validada','Tu validación del sistema anterior fue reconocida por administración.')+'<section class="card"><h2>No necesitas reenviar el carnet</h2><p>Tu saldo anterior está disponible en Paquetes. Para reservar, confirma tu correo y usa las sesiones de tu cuenta.</p></section>';
  return head('Verificación de identidad','Carnet obligatorio para cada perfil profesional, incluido el del administrador.')+
  `<div class="notice ${state.user.verified?'info':''}">${state.user.verified?'✓ Tu cuenta está verificada. Puedes reservar con saldo disponible.':'Tu carnet aún no está aprobado. Aunque tengas saldo pagado, no podrás reservar hasta completar la revisión.'}</div><section class="card"><h2>Envía tu carnet de identidad</h2><p class="section-intro">Adjunta el anverso y reverso de tu carnet, juntos o en archivos separados. Se aceptan fotos, PDF y cualquier otro formato. Administración debe revisar que sean legibles y correspondan a tu cuenta. Mientras probamos con almacenamiento temporal, usa un documento ficticio.</p><form id="upload-form"><div class="upload-card"><strong>Carnet de identidad · anverso y reverso</strong><p>Cualquier formato · Hasta 5 archivos y 10 MB en total · Acceso privado</p><label>Seleccionar archivos<input id="document-file" type="file" multiple required></label></div><p class="muted upload-note">Al adjuntar un nuevo carnet, la cuenta vuelve a revisión y se pausa la creación y modificación de reservas. Las reservas existentes se conservan.</p><p id="upload-error" class="error" role="alert"></p><button class="btn" type="submit">Enviar a revisión →</button></form></section>
  <section class="card spaced-card"><div class="card-head"><h2>Estado de tus documentos</h2></div>${state.documents.length?state.documents.map(d=>`<div class="booking-item"><div><strong class="document-name">${escapeHTML(d.filename)}</strong><p class="muted caption-text">${d.kind==='identity'?'Carnet de identidad':'Documento anterior: no acredita identidad'}</p><p class="muted caption-text">Enviado el ${dateText(d.created_at)}</p>${d.note?`<p class="small-text">Observación: ${escapeHTML(d.note)}</p>`:''}</div><div class="row">${pill(d.status)}${documentLinks(d)}</div></div>`).join(''):empty('Todavía no has enviado documentos.')}</section>`;
}
function migrationAdmin() {
  return `<h2>Migración de clientes anteriores</h2><div class="notice">Mientras la plataforma siga sin almacenamiento persistente, usa clientes y saldos ficticios. Antes de migrar saldos reales, confirma que existe un respaldo y que los datos se conservan al desplegar.</div><p class="section-intro">Carga únicamente el saldo restante, sin incluir sesiones ya usadas. Los clientes validados no tendrán que reenviar el carnet; conservarán su cuenta personal y deberán confirmar su correo.</p>
  <section class="migration-section"><h3>Importar una planilla</h3><p>Desde Excel guarda como CSV. Usa las columnas de la plantilla y conserva sus encabezados. Hasta 500 clientes y 1 MB por archivo.</p><button class="btn ghost small" id="migration-template">Descargar plantilla CSV</button><form id="migration-csv-form" class="form-stack"><label>Planilla CSV<input id="migration-csv" type="file" accept=".csv,text/csv" required></label><label class="identity-confirmation"><input id="migration-csv-validated" type="checkbox" required>Confirmo que todos estos clientes ya tienen su identidad validada en el sistema anterior y que revisé sus saldos.</label><p class="error" id="migration-csv-error" role="alert"></p><button class="btn" type="submit">Revisar planilla</button></form><div id="migration-preview" aria-live="polite"></div></section>
  <section class="migration-section"><h3>Cargar un cliente individual</h3><form id="migration-form" class="form-stack"><label>Nombre completo<input name="name" required minlength="2" maxlength="100"></label><label>Correo del cliente<input name="email" type="email" required maxlength="200"></label><label>Profesión o especialidad<input name="specialty" required minlength="2" maxlength="100"></label><label>Referencia del sistema anterior<input name="reference" required minlength="2" maxlength="100" placeholder="Ej. antiguo-juanita-001"></label><label>Sesiones restantes<input name="sessions" type="number" min="0" max="10000" step="1" required></label><label>Observación de respaldo<textarea name="note" required minlength="5" maxlength="500" placeholder="Origen del saldo y fecha de revisión"></textarea></label><label class="identity-confirmation"><input name="identity_confirmed" type="checkbox" required>Confirmo que este cliente ya fue validado y que el saldo es correcto.</label><p class="error" id="migration-error" role="alert"></p><button class="btn" type="submit">Revisar migración</button></form></section>
  <h3>Migraciones registradas</h3><div class="table-wrap"><table><thead><tr><th>Cliente</th><th>Referencia</th><th>Saldo inicial migrado</th><th>Cuenta</th><th>Registro</th></tr></thead><tbody>${(adminData.migrations||[]).map(m=>`<tr><td>${escapeHTML(m.name)}<small>${escapeHTML(m.email)}</small></td><td>${escapeHTML(m.reference)}</td><td>${m.sessions} sesiones<small>${m.identity_valid?'Identidad validada previamente':'Validación retirada: requiere revisión'}</small></td><td>${m.user_id?'Vinculada a #'+m.user_id:'Pendiente de registro'}</td><td>${dateText(m.created_at)}<small>${escapeHTML(m.operator_name)}</small><small>${escapeHTML(m.note)}</small></td></tr>`).join('')||'<tr><td colspan="5">No hay clientes migrados.</td></tr>'}</tbody></table></div>`;
}
function bindMigration() {
  const form=document.querySelector('#migration-form');if(!form)return;
  form.onsubmit=event=>{
    event.preventDefault();const data=Object.fromEntries(new FormData(form));data.sessions=Number(data.sessions);data.identity_confirmed=form.elements.identity_confirmed.checked;
    showModal('Confirmar migración',`<p><strong>${escapeHTML(data.name)}</strong> · ${escapeHTML(data.email)}</p><p>Se cargarán <strong>${data.sessions} sesiones</strong> del sistema anterior. No se cobrará dinero y no se solicitará nuevamente el carnet.</p><p>La referencia y el correo solo pueden importarse una vez. Verifica el titular y el saldo antes de continuar.</p>`,'Migrar cliente',async()=>{await api('admin/migrations',data);toast('Cliente migrado. El saldo quedará en su cuenta personal.');});
  };
  document.querySelector('#migration-template').onclick=()=>{
    const blob=new Blob(['\ufeffreferencia,nombre,correo,especialidad,sesiones,observacion\r\nantiguo-001,Juanita Ejemplo,juanita@example.com,Psicología,10,Saldo restante revisado en sistema anterior\r\n'],{type:'text/csv;charset=utf-8'});
    const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='plantilla-migracion-valhalla.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  const csvForm=document.querySelector('#migration-csv-form'),input=document.querySelector('#migration-csv'),box=document.querySelector('#migration-preview');let csvText=null;
  const clear=()=>{csvText=null;box.innerHTML='';};input.onchange=clear;document.querySelector('#migration-csv-validated').onchange=clear;
  csvForm.onsubmit=async event=>{
    event.preventDefault();clear();const button=csvForm.querySelector('button');button.disabled=true;
    document.querySelector('#migration-csv-error').textContent='';
    try{
      const file=input.files[0];if(!file||file.size>1000000)throw new Error('Selecciona un CSV de hasta 1 MB.');
      const bytes=await file.arrayBuffer();let text;try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{text=new TextDecoder('windows-1252').decode(bytes);}
      const result=await api('admin/migrations/preview',{csv:text,identity_confirmed:document.querySelector('#migration-csv-validated').checked});csvText=text;
      box.innerHTML=`<h3>Vista previa: ${result.count} clientes · ${result.sessions} sesiones</h3><p>No se ha guardado ningún cambio. Revisa nombres, correos y saldos.</p><div class="table-wrap"><table><thead><tr><th>Cliente</th><th>Correo</th><th>Sesiones</th><th>Referencia</th></tr></thead><tbody>${result.rows.map(row=>`<tr><td>${escapeHTML(row.name)}</td><td>${escapeHTML(row.email)}</td><td>${row.sessions}</td><td>${escapeHTML(row.reference)}</td></tr>`).join('')}</tbody></table></div><button class="btn" id="commit-migration">Confirmar importación</button>`;
      document.querySelector('#commit-migration').onclick=()=>showModal('Importar clientes validados',`<p>Se importarán <strong>${result.count} clientes</strong> con <strong>${result.sessions} sesiones restantes</strong>. La importación es completa: si una fila ya existe o es inválida, no se cargará ninguna.</p>`,'Importar clientes',async()=>{await api('admin/migrations/import',{csv:csvText,identity_confirmed:true});csvText=null;toast('Planilla importada correctamente.');});
    }catch(error){document.querySelector('#migration-csv-error').textContent=error.message;}finally{button.disabled=false;}
  };
}
const paymentStates={creating:'Preparando pago',ready:'Pendiente en Webpay',uncertain:'Pendiente de conciliación',review:'Revisión requerida',approved:'Aprobado',rejected:'Rechazado',processing:'Comprobando'};
function paymentList(admin=false) {
  const payments=admin?(adminData.payments||[]):(state.payments||[]);
  if(!payments.length)return '';
  return `<section class="card spaced-card"><h2>Compras Webpay · pruebas</h2><p>Sin cargos reales. El saldo se acredita solo después de una aprobación verificada con Transbank.</p><div class="table-wrap"><table><thead><tr>${admin?'<th>Profesional</th>':''}<th>Orden</th><th>Plan</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>${payments.map(p=>`<tr>${admin?`<td>${escapeHTML(p.name)}</td>`:''}<td>${escapeHTML(p.buy_order)}</td><td>${p.hours} sesiones<small>${money(p.amount)}</small></td><td>${paymentStates[p.state]||escapeHTML(p.state)}</td><td>${!['approved','rejected'].includes(p.state)?`<button class="btn ghost small" data-payment-status="${p.id}" data-payment-admin="${admin}">Consultar estado</button>${!admin&&p.state==='ready'?`<button class="btn small" data-payment-resume="${p.id}">Retomar pago</button>`:''}`:'—'}</td></tr>`).join('')}</tbody></table></div></section>`;
}
function sendToWebpay(checkout) {
  const target=new URL(checkout.url);
  if(target.protocol!=='https:'||target.hostname!=='webpay3gint.transbank.cl'||target.pathname!=='/webpayserver/initTransaction'||target.username||target.password||target.search||target.hash)throw new Error('Destino de pago inválido.');
  const form=document.createElement('form');form.method='POST';form.action=checkout.url;
  const token=document.createElement('input');token.type='hidden';token.name='token_ws';token.value=checkout.token;form.appendChild(token);document.body.appendChild(form);form.submit();
}
function bindPayments() {
  document.querySelectorAll('[data-payment-status]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{const result=await api(button.dataset.paymentAdmin==='true'?'admin/webpay/reconcile':'webpay/reconcile',{id:Number(button.dataset.paymentStatus)});await reload();toast(paymentStates[result.state]||'Estado actualizado.');}catch(error){toast(error.message);button.disabled=false;}});
  document.querySelectorAll('[data-payment-resume]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{const payment=state.payments.find(p=>p.id===Number(button.dataset.paymentResume));const checkout=await api('webpay/create',{catalog_id:String(payment.hours),idempotency_key:'resume-'+payment.id});sendToWebpay(checkout);}catch(error){toast(error.message);button.disabled=false;}});
}
const mailCategories={invalid_key:'Clave rechazada: revisa VALHALLA_RESEND_API_KEY o la clave del proveedor seleccionado.',permissions_or_domain:'Acceso rechazado: revisa el dominio verificado, el alcance de la clave o filtros del proveedor.',invalid_sender_or_payload:'Revisa el remitente y su dominio autorizado.',invalid_request:'Solicitud rechazada por el proveedor.',provider_endpoint:'No se encontró el servicio del proveedor.',rate_limited:'Se alcanzó un límite del proveedor; revisa el plan y reintenta más tarde.',provider_unavailable:'El proveedor no está disponible temporalmente.',network_error:'No se pudo conectar con el proveedor. Revisa la conexión desde Render.',unexpected_response:'El proveedor devolvió una respuesta no reconocida.',provider_rejected:'El proveedor rechazó la solicitud.',delivery_error:'El servicio no confirmó el envío.',account_or_limit:'No se envió: cuenta profesional no disponible o enlace solicitado recientemente.'};
function emailAdmin() {
  const config=adminData.email_config||{};
  return `<h2>Servicio de correo</h2><p class="section-intro">Comprueba primero el envío a tu correo administrador. Esta prueba no modifica contraseñas ni verifica cuentas. El proveedor debe aceptar el mensaje y después tu buzón debe recibirlo.</p><dl class="mail-config"><dt>Proveedor</dt><dd>${escapeHTML(config.provider||'Sin configurar')}</dd><dt>Remitente</dt><dd>${escapeHTML(config.sender||'Falta configurar')}</dd><dt>Dirección de los enlaces</dt><dd>${escapeHTML(config.public_url||'Falta configurar')}</dd><dt>Clave privada</dt><dd>${config.api_key_present?'Configurada (valor oculto)':'No configurada'}</dd><dt>Configuración básica</dt><dd>${config.configured?'Completa: falta comprobar envío y recepción':'Incompleta o inválida'}</dd></dl><button class="btn" id="email-test" ${config.configured?'':'disabled'}>Enviar prueba a mi correo administrador</button><p id="email-test-result" role="status"></p><p>El destinatario de prueba es únicamente el correo administrador autorizado. Espera un minuto entre pruebas.</p><button class="btn ghost small" id="refresh-mail">Actualizar diagnóstico</button><h3>Últimos intentos</h3><div class="table-wrap"><table><thead><tr><th>Fecha</th><th>Tipo / cuenta</th><th>Resultado</th><th>Diagnóstico</th></tr></thead><tbody>${(adminData.mail_events||[]).map(event=>`<tr><td>${dateText(event.created_at)}<small>${hourText(event.created_at)}</small></td><td>${{test:'Prueba administradora',verification:'Confirmación de correo',recovery:'Recuperación de contraseña',admin_signup:'Nueva inscripción → administración',admin_document:'Documento recibido → administración',booking_confirmed:'Comprobante de reserva',booking_modified:'Reserva modificada',booking_cancelled:'Reserva cancelada'}[event.kind]||escapeHTML(event.kind)}<small>${escapeHTML(event.name||'Sin cuenta destinataria')}</small></td><td>${{pending:'En proceso',accepted:'Aceptado por el proveedor',error:'Error de envío',skipped:'No se intentó enviar'}[event.status]||escapeHTML(event.status)}</td><td>${event.http_status?'HTTP '+event.http_status+' · ':''}${mailCategories[event.category]||'—'}</td></tr>`).join('')||'<tr><td colspan="4">Todavía no hay intentos registrados en esta versión. Envía una prueba.</td></tr>'}</tbody></table></div><h3>Avisos automáticos</h3><p>Inscripciones y documentos se notifican al administrador; los comprobantes se envían al profesional. Si hay un fallo, se reintenta hasta cinco veces.</p><div class="table-wrap"><table><thead><tr><th>Cuenta</th><th>Aviso</th><th>Estado</th><th>Intentos</th></tr></thead><tbody>${(adminData.notification_queue||[]).map(job=>`<tr><td>${escapeHTML(job.name)}</td><td>${{admin_signup:'Nueva inscripción',admin_document:'Documento recibido',booking_confirmed:'Reserva confirmada',booking_modified:'Reserva modificada',booking_cancelled:'Reserva cancelada'}[job.kind]||escapeHTML(job.kind)}</td><td>${{pending:'Pendiente',processing:'Enviando',retry:'Pendiente de reintento',failed:'No se pudo enviar',accepted:'Aceptado por el proveedor',superseded:'Sustituido por una actualización'}[job.status]||escapeHTML(job.status)}</td><td>${job.attempts} / 5</td></tr>`).join('')||'<tr><td colspan="4">No hay avisos automáticos nuevos.</td></tr>'}</tbody></table></div><p class="data-note">«Aceptado» no confirma la entrega en el buzón. Comprueba Resend → Emails para conocer entregas o rebotes. Los errores no muestran claves, contenido del correo ni enlaces privados.</p>`;
}
function bindEmailAdmin() {
  const button=document.querySelector('#email-test');
  if(button)button.onclick=async()=>{
    button.disabled=true;
    try{const result=await api('admin/email/test',{});await reload();document.querySelector('#email-test-result').textContent=result.message;}
    catch(error){document.querySelector('#email-test-result').textContent=error.message;button.disabled=false;}
  };
  const refresh=document.querySelector('#refresh-mail');if(refresh)refresh.onclick=()=>reload().catch(error=>toast(error.message));
}
function dailyContactsAdmin() {
  const report=dailyContacts||{contacts:[],emails:[],total_professionals:0,total_bookings:0};
  return `<h2>Correos de profesionales agendados</h2><p class="section-intro">Consolidado exclusivo de administración. Incluye solo reservas confirmadas del día elegido en horario de Chile; excluye cancelaciones y reúne una sola vez cada correo.</p><div class="row spread"><label>Día del consolidado<input id="daily-contacts-date" type="date" value="${escapeHTML(contactsDate)}" required></label><button class="btn ghost small" id="refresh-daily-contacts">Actualizar consolidado</button></div><p><strong>${report.total_professionals} ${report.total_professionals===1?'profesional':'profesionales'}</strong> · ${report.total_bookings} ${report.total_bookings===1?'reserva confirmada':'reservas confirmadas'} · Horario de Chile</p><label>Correos del día<textarea id="daily-email-list" rows="3" readonly placeholder="No hay personas agendadas este día.">${escapeHTML(report.emails.join('; '))}</textarea></label><button class="btn small" id="copy-daily-emails" ${report.emails.length?'':'disabled'}>Copiar correos</button><div class="table-wrap spaced-card"><table><thead><tr><th>Profesional</th><th>Correo</th><th>Reservas del día</th></tr></thead><tbody>${report.contacts.map(person=>`<tr><td>${escapeHTML(person.name)}</td><td>${escapeHTML(person.email)}</td><td>${person.bookings.map(booking=>`<div>${rooms[booking.box-1]} · ${sessionRange(booking.start)}<small>Reserva #${booking.id}</small></div>`).join('')}</td></tr>`).join('')||'<tr><td colspan="3">No hay reservas confirmadas para este día.</td></tr>'}</tbody></table></div><p class="data-note">Este panel permite consultar y copiar las direcciones; no envía correos masivos.</p>`;
}
function bindDailyContacts() {
  const date=document.querySelector('#daily-contacts-date');if(!date)return;
  date.onchange=()=>{if(date.value){contactsDate=date.value;render().catch(error=>toast(error.message));}};
  document.querySelector('#refresh-daily-contacts').onclick=()=>render().catch(error=>toast(error.message));
  document.querySelector('#copy-daily-emails').onclick=async()=>{
    const text=document.querySelector('#daily-email-list');
    try{await navigator.clipboard.writeText(text.value);toast('Correos del día copiados.');}
    catch{text.focus();text.select();toast('Seleccionados. Usa Copiar o Ctrl+C.');}
  };
}
function administration() {
  const pending=adminData.packages.filter(p=>p.status==='pending');
  let body='';
  if(adminTab==='daily_contacts') body=dailyContactsAdmin();
  if(adminTab==='email') body=emailAdmin();
  if(adminTab==='migration') body=migrationAdmin();
  if(adminTab==='payments') body=`<h2>Pagos y paquetes</h2><p class="section-intro">Confirmación manual de operaciones ficticias. Ninguna acción mueve dinero.</p><div class="table-wrap"><table class="admin-table"><thead><tr><th>Profesional</th><th>Paquete</th><th>Monto del plan</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>${adminData.packages.map(p=>`<tr><td>${escapeHTML(p.name)}<small>Titular #${p.user_id}</small></td><td>#${p.id} · ${p.hours} h<small>${p.remaining} h restantes · ${p.source==='migration'?'Saldo anterior':p.source==='webpay_integration'?'Webpay de pruebas':'Simulado'}</small></td><td>${money(p.price)}</td><td>${pill(p.status)}</td><td>${p.status==='pending'&&p.source==='simulated'?`<div class="row"><button class="btn small" data-admin="payment" data-id="${p.id}" data-decision="paid">Confirmar pago simulado</button><button class="btn ghost small" data-admin="payment" data-id="${p.id}" data-decision="rejected">Rechazar</button></div>`:'—'}</td></tr>`).join('')}</tbody></table></div>`;
  if(adminTab==='payments') body+=paymentList(true);
  if(adminTab==='documents') body=`<h2>Revisión de documentación</h2><p class="section-intro">Descarga todos los adjuntos del último carnet y revisa ambos lados y su correspondencia con el titular antes de aprobar. Un título u otro documento no reemplaza la verificación de identidad. La revisión es manual.</p>${adminData.documents.length?`<div class="table-wrap"><table class="admin-table"><thead><tr><th>Profesional</th><th>Documento</th><th>Estado</th><th>Revisión</th></tr></thead><tbody>${adminData.documents.map(d=>{const latest=!adminData.documents.some(x=>x.user_id===d.user_id&&x.kind===d.kind&&x.id>d.id);return `<tr><td>${escapeHTML(d.name)}<small>Titular #${d.user_id}</small></td><td>${documentLinks(d)}<small>${dateText(d.created_at)} · ${d.kind==='identity'?'Carnet':'Acreditación anterior'}</small></td><td>${pill(d.status)}${d.note?`<small>${escapeHTML(d.note)}</small>`:''}</td><td>${latest?`<div class="row"><button class="btn small" data-admin="document" data-id="${d.id}" data-decision="approved">Aprobar</button><button class="btn ghost small" data-admin="document" data-id="${d.id}" data-decision="rejected">Rechazar</button></div>`:'Documento anterior'}</td></tr>`;}).join('')}</tbody></table></div>`:empty('No hay documentos para revisar.')}`;
  if(adminTab==='users') body=`<h2>Profesionales y usuarios</h2><p class="section-intro">Desactivar una cuenta cierra sus sesiones e impide nuevas operaciones. Sus reservas existentes se conservan.</p><div class="table-wrap"><table class="admin-table"><thead><tr><th>Nombre</th><th>Correo</th><th>Especialidad</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>${adminData.users.map(u=>`<tr><td>${escapeHTML(u.name)}<small>${u.role==='admin'?'Administrador':'Profesional'}</small></td><td>${escapeHTML(u.email)}</td><td>${escapeHTML(u.specialty)||'—'}</td><td><span class="pill ${u.active?'approved':'rejected'}">${u.active?'Activa':'Inactiva'}</span><small>${u.verified?'Verificada':'Por verificar'}</small></td><td>${u.role==='professional'?`<button class="btn ghost small" data-admin="user" data-id="${u.id}" data-active="${!u.active}">${u.active?'Desactivar':'Activar'}</button>`:'—'}</td></tr>`).join('')}</tbody></table></div>`;
  if(adminTab==='bookings') body=`<h2>Agenda general</h2><p class="section-intro">Cancelar una reserva futura libera el box y devuelve 1 hora al paquete original, una sola vez.</p>${adminData.bookings.length?`<div class="table-wrap"><table class="admin-table"><thead><tr><th>Profesional</th><th>Box / fecha</th><th>Estado</th><th>Modificaciones</th><th>Acciones</th></tr></thead><tbody>${adminData.bookings.map(b=>`<tr><td>${escapeHTML(b.name)}<small>Reserva #${b.id}</small></td><td>${rooms[b.box-1]}<small>${dateText(b.start)} · ${sessionRange(b.start)}</small></td><td>${pill(b.status)}</td><td>${b.modifications} / 1</td><td>${b.status==='confirmed'&&new Date(b.start)>new Date()?`<button class="btn danger small" data-admin="cancel" data-id="${b.id}">Cancelar y devolver hora</button>`:'—'}</td></tr>`).join('')}</tbody></table></div>`:empty('No hay reservas registradas.')}`;
  if(adminTab==='audit') body=`<h2>Registro de operaciones</h2><p class="section-intro">Últimas 30 acciones realizadas en el prototipo.</p>${adminData.audit.map(a=>`<div class="audit-item"><strong>${escapeHTML(a.name||'Sistema')}</strong><p>${escapeHTML(a.action)} · registro #${escapeHTML(a.target)}</p><small>${dateText(a.created_at)} · ${hourText(a.created_at)}</small></div>`).join('')||empty('Aún no hay operaciones.')}`;
  return head('Administración','La operación de Valhalla, en un solo lugar.','<button class="btn" data-go="availability">Ver agenda por box →</button>')+`<div class="grid stats"><div class="stat"><div class="stat-top">Profesionales</div><div class="value">${adminData.users.filter(u=>u.role==='professional').length}</div><small>Cuentas registradas</small></div><div class="stat"><div class="stat-top">Pagos pendientes</div><div class="value">${pending.length}</div><small>Operaciones simuladas</small></div><div class="stat"><div class="stat-top">Reservas próximas</div><div class="value">${adminData.bookings.filter(b=>b.status==='confirmed'&&new Date(b.start)>new Date()).length}</div><small>Entre los tres boxes</small></div></div><div class="tabs" role="group" aria-label="Secciones de administración">${[['payments','Pagos'],['documents','Documentos'],['users','Usuarios'],['email','Correos'],['daily_contacts','Correos del día'],['migration','Migración'],['bookings','Reservas'],['audit','Actividad']].map(([id,label])=>`<button class="tab ${adminTab===id?'active':''}" data-tab="${id}" aria-pressed="${adminTab===id}">${label}</button>`).join('')}</div><section class="card">${body}</section>`;
}

async function render() {
  shell(); const content = document.querySelector('#content');
  if(adminMode()&&view==='admin'&&adminTab==='daily_contacts'){
    contactsDate=contactsDate||state.today;const requestedDate=contactsDate;
    const report=await api('admin/daily-contacts?date='+encodeURIComponent(requestedDate));
    if(!content.isConnected||!adminMode()||adminTab!=='daily_contacts'||contactsDate!==requestedDate)return;
    dailyContacts=report;
  }
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
  bindMigration();bindPayments();bindEmailAdmin();bindDailyContacts();
  const refreshAgenda=document.querySelector('#refresh-agenda');if(refreshAgenda)refreshAgenda.onclick=()=>render().catch(e=>toast(e.message));
  bindGallery();
  const linkGoogle=document.querySelector('#link-google');if(linkGoogle)linkGoogle.onclick=()=>continueWithGoogle(true);
  const profileForm=document.querySelector('#profile-form');if(profileForm)profileForm.onsubmit=async event=>{event.preventDefault();const button=profileForm.querySelector('button[type=submit]');button.disabled=true;try{await api('profile',Object.fromEntries(new FormData(profileForm)));await reload();toast('Perfil profesional actualizado.');}catch(e){document.querySelector('#profile-error').textContent=e.message;}finally{button.disabled=false;}};
  document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>{view=b.dataset.go;modifying=null;render().catch(e=>toast(e.message));});
  document.querySelectorAll('[data-modify]').forEach(b=>b.onclick=()=>{modifying=state.bookings.find(x=>x.id===Number(b.dataset.modify));selectedDate=new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Santiago'}).format(new Date(modifying.start));view='availability';render().catch(e=>toast(e.message));});
  const abort=document.querySelector('#abort-change');if(abort)abort.onclick=()=>{modifying=null;view='bookings';render().catch(e=>toast(e.message));};
  const date=document.querySelector('#schedule-date');if(date)date.onchange=()=>{if(date.value){selectedDate=date.value;render().catch(e=>toast(e.message));}};
  document.querySelectorAll('[data-slot]').forEach(b=>b.onclick=()=>{
    if(adminMode()){toast('La agenda administrativa es de consulta. Las reservas se crean desde la cuenta profesional.');return;}
    if(!state.user.email_verified){toast('Confirma tu correo electrónico antes de reservar.');return;}
    if(!state.user.verified){toast('Primero envía tu carnet de identidad y espera su aprobación.');return;}
    if(!modifying && pendingBalance()===0){toast('Necesitas sesiones de un plan con pago confirmado.');return;}
    const box=Number(b.dataset.box),start=b.dataset.slot, current=modifying;
    showModal(current?'Confirma tu único cambio':'Tu próxima atención',`<p>${current?'Se conservará el saldo y se utilizará tu única modificación.':'Se descontará 1 sesión de tu plan confirmado más antiguo con saldo.'}</p><div class="modal-detail"><strong>${rooms[box-1]}</strong><br>${dateText(start)} · ${sessionRange(start)}<br>Atención: 55 minutos · 5 minutos de margen</div><p>${current?'El horario original se libera solo si el cambio se confirma.':'La reserva y su saldo pertenecen exclusivamente a tu cuenta.'}</p>`,current?'Confirmar cambio':'Confirmar reserva',async()=>{
      await api(current?'bookings/'+current.id:'bookings',{box,start});modifying=null;view='bookings';toast(current?'Reserva modificada. Has utilizado tu único cambio.':'Reserva confirmada. ¡Tu espacio te espera!');
    });
  });
  document.querySelectorAll('[data-buy]').forEach(b=>b.onclick=()=>{
    const p=state.catalog.find(p=>p.id===b.dataset.buy);
    if(state.webpay_mode==='integration'){
      if(!state.user.email_verified){toast('Confirma tu correo antes de comprar.');return;}
      const key=crypto.randomUUID();showModal('Comprar plan · Webpay de pruebas',`<p>${p.hours} sesiones · ${money(p.price)}. Continuarás en el sitio de pruebas de Transbank. Usa únicamente sus tarjetas de prueba; no habrá un cargo real.</p>`,'Ir a Webpay de pruebas',async()=>sendToWebpay(await api('webpay/create',{catalog_id:p.id,idempotency_key:key})));return;
    }
    showModal('Solicitar paquete',`<p>Esta operación es una simulación. No ingreses datos bancarios.</p><div class="modal-detail"><strong>${p.hours} ${p.hours===1?'sesión':'sesiones'} · ${money(p.price)}</strong><br>55 minutos por sesión. Inicios cada hora en cualquiera de los tres boxes.</div><p>El saldo se habilitará cuando administración confirme el pago de prueba.</p>`,'Solicitar sin cobro',async()=>{await api('packages',{catalog_id:p.id});toast('Solicitud registrada. Pago simulado pendiente de confirmación.');});
  });
  const upload=document.querySelector('#upload-form');if(upload)upload.onsubmit=async event=>{
    event.preventDefault();const selected=Array.from(document.querySelector('#document-file').files),button=upload.querySelector('button');
    if(!selected.length||selected.length>5||selected.some(file=>!file.size)||selected.reduce((sum,file)=>sum+file.size,0)>10*1024*1024){document.querySelector('#upload-error').textContent='Selecciona de 1 a 5 archivos, con un total de hasta 10 MB.';return;}
    button.disabled=true;
    try {
      const files=await Promise.all(selected.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('No se pudo leer el archivo.'));reader.onload=()=>resolve({filename:file.name,content:String(reader.result).split(',')[1]});reader.readAsDataURL(file);})));
      await api('documents',{files,kind:'identity'});toast('Archivos enviados. Tu carnet está en revisión.');await reload();
    }catch(e){document.querySelector('#upload-error').textContent=e.message;}
    finally{button.disabled=false;}
  };
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{adminTab=b.dataset.tab;render().catch(e=>toast(e.message));});
  document.querySelectorAll('[data-admin]').forEach(b=>b.onclick=()=>{
    const action=b.dataset.admin,decision=b.dataset.decision,active=b.dataset.active==='true';
    const identityReview=action==='document'&&decision==='approved'&&adminData.documents.find(d=>d.id===Number(b.dataset.id))?.kind==='identity';
    const title=action==='payment'?(decision==='paid'?'Confirmar pago simulado':'Rechazar solicitud'):action==='document'?(decision==='approved'?'Aprobar documentación':'Rechazar documentación'):action==='user'?(active?'Activar profesional':'Desactivar profesional'):'Cancelar reserva';
    let copy=action==='payment'?'No se realizará ningún cobro ni devolución real.':action==='document'?'Confirma que revisaste el documento más reciente del profesional.':action==='user'?'Las reservas existentes se conservan. Desactivar cierra las sesiones del profesional.':'Se libera el horario y se devuelve 1 hora al paquete del titular. Esta acción no se puede deshacer.';
    showModal(title,`<p>${copy}</p>${identityReview?'<label class="identity-confirmation"><input type="checkbox" id="identity-confirmed">He revisado ambos lados del carnet y corresponden al titular de esta cuenta.</label>':''}${action==='document'?`<label class="review-label">Observación ${decision==='rejected'?'(obligatoria)':'(opcional)'}<textarea id="review-note" rows="3" maxlength="500"></textarea></label>`:''}`,'Confirmar',async()=>{await api('admin',{action,id:Number(b.dataset.id),decision,active,identity_confirmed:document.querySelector('#identity-confirmed')?.checked===true,note:document.querySelector('#review-note')?.value||''});toast('Operación registrada.');});
  });
}
function passwordRecovery(reset = false, administrator = false) {
  disposeWelcomeCarousel();
  root.innerHTML=`<main class="email-confirmation">${brand}<section class="card"><div class="eyebrow">ACCESO A TU CUENTA</div><h1>${reset?'Crea una nueva contraseña':'Recuperar contraseña'}</h1><p>${reset?'El enlace dura 30 minutos y se utiliza una sola vez. Al guardar, se cerrarán las sesiones anteriores.':administrator?'La contraseña administradora se configura en Render → tu servicio → Environment → VALHALLA_ADMIN_PASSWORD. Actualízala allí y guarda con un nuevo despliegue. Para una cuenta profesional, usa la recuperación por correo.':'Ingresa el correo con el que registraste tu cuenta profesional. Te enviaremos un enlace para elegir otra contraseña.'}</p>${administrator?'':`<form id="recovery-form" class="form-stack">${reset?'<label>Nueva contraseña<input name="password" type="password" autocomplete="new-password" required minlength="10" maxlength="200"></label><label>Confirmar contraseña<input name="password_confirmation" type="password" autocomplete="new-password" required minlength="10" maxlength="200"></label>':'<label>Correo electrónico<input name="email" type="email" autocomplete="email" required maxlength="200"></label>'}<p id="recovery-error" class="error" role="alert"></p><p id="recovery-result" role="status"></p><button class="btn full" type="submit">${reset?'Guardar nueva contraseña':'Enviar enlace de recuperación'}</button></form>`}<button class="text-btn" id="back-to-login">Volver a iniciar sesión</button></section></main>`;
  document.querySelector('#back-to-login').onclick=()=>{passwordResetToken=null;auth(false,administrator);};
  const form=document.querySelector('#recovery-form');if(!form)return;
  form.onsubmit=async event=>{
    event.preventDefault();const button=form.querySelector('button[type=submit]');button.disabled=true;
    document.querySelector('#recovery-error').textContent='';
    try{
      const body=Object.fromEntries(new FormData(form));
      if(reset&&body.password!==body.password_confirmation)throw new Error('Las contraseñas no coinciden.');
      if(reset)body.token=passwordResetToken;
      const result=await api(reset?'password-reset/confirm':'password-reset/request',body);
      document.querySelector('#recovery-result').textContent=reset?'Contraseña actualizada. Inicia sesión con tu nueva contraseña.':result.message;
      if(reset){passwordResetToken=null;state=null;form.querySelectorAll('input').forEach(input=>{input.value='';input.disabled=true;});button.textContent='Contraseña actualizada';}
      else button.disabled=false;
    }catch(error){document.querySelector('#recovery-error').textContent=error.message;button.disabled=false;}
  };
}
function emailConfirmation() {
  disposeWelcomeCarousel();
  root.innerHTML=`<main class="email-confirmation">${brand}<section class="card"><div class="eyebrow">CONFIRMACIÓN DE CUENTA</div><h1>Confirma tu correo</h1><p>Pulsa el botón para confirmar que este correo te pertenece. El enlace vence a las 24 horas y se usa una sola vez.</p><p class="error" id="email-confirmation-error" role="alert"></p><button class="btn full" id="confirm-email-button">Confirmar mi correo</button><button class="text-btn" id="back-to-account">Volver a mi cuenta</button></section></main>`;
  document.querySelector('#back-to-account').onclick=async()=>{emailConfirmationToken=null;try{await reload();}catch{auth();}};
  document.querySelector('#confirm-email-button').onclick=async event=>{
    const button=event.currentTarget;button.disabled=true;
    try{await api('verify-email',{token:emailConfirmationToken});emailConfirmationToken=null;button.textContent='Correo confirmado';document.querySelector('#email-confirmation-error').textContent='';toast('Correo confirmado. Ya puedes continuar con tu carnet y tus reservas.');}
    catch(e){document.querySelector('#email-confirmation-error').textContent=e.message;button.disabled=false;}
  };
}
async function startApp() {
  if(passwordResetToken){passwordRecovery(true);return;}
  if(emailConfirmationToken){emailConfirmation();return;}
  try{googleEnabled=Boolean((await api('google/config')).enabled);}catch{googleEnabled=false;}
  try {officeGallery=(await api('gallery')).groups||[];}catch{officeGallery=[];}
  try{const data=await api('me');state=data;selectedDate=data.today;if(paymentReturn){view='packages';toast('Consulta el estado actualizado de tu compra en Paquetes.');}if(googleResult==='profile_required')view='profile';if(data.user.role==='admin'){adminData=await api('admin');view='admin';}await render();}
  catch{auth(false,googleResult==='admin_password');}
  const googleMessages={success:'Ingresaste con Google.',profile_required:'Completa tu profesión o especialidad en Mi perfil. Para reservar también necesitas tu identidad validada y saldo.',link_required:'Ya existe una cuenta con ese correo. Ingresa con tu contraseña y selecciona Mi perfil → Vincular Google. Si la olvidaste, puedes recuperarla.',cancelled:'Se canceló el acceso con Google. Puedes volver a intentarlo.',admin_password:'La administración requiere tu contraseña privada. Google está disponible para cuentas profesionales.',error:'No se pudo completar el acceso con Google. Vuelve a intentarlo desde el inicio.'};
  if(googleResult&&googleMessages[googleResult])toast(googleMessages[googleResult]);
}
startApp();
