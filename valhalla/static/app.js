const root = document.querySelector('#root');
const modal = document.querySelector('#modal');
let state = null, adminData = null, view = 'overview', adminTab = 'payments', selectedDate = '', modifying = null;
let activeProfile = 'admin';
let multiBooking=false, selectedBookings=[];
let menuScrollLeft=0;
let liveRefreshBusy=false,liveFormDirty=false,liveRefreshPending=false,liveEditRevision=0,renderGeneration=0;
for(const eventName of ['input','change'])document.addEventListener(eventName,event=>{if(event.target.closest?.('#content form')){liveFormDirty=true;liveEditRevision++;}});
let confirmedBookingIds=[],confirmationModified=false;
let bookingsDeepLink=location.hash==='#mis-reservas';
try{if(bookingsDeepLink)sessionStorage.setItem('valhalla-bookings-link','1');else bookingsDeepLink=sessionStorage.getItem('valhalla-bookings-link')==='1';}catch{}
const chosenSlot=(box,start)=>selectedBookings.some(slot=>slot.box===box&&slot.start===start);
let contactsDate='',dailyContacts=null;
let paymentsMonth='';
let showArchivedPlans=false;
let officeGallery = [];
let googleEnabled = false;
const googleResult = new URLSearchParams(location.search).get('google_result');
if(googleResult){const clean=new URL(location.href);clean.searchParams.delete('google_result');history.replaceState(null,'',clean.pathname+clean.search+clean.hash);}
async function continueWithGoogle(link=false){
  const button=document.querySelector(link?'#link-google':'#google-access');if(button)button.disabled=true;
  try{const result=await api(link?'google/link':'google/start',{});const target=new URL(result.authorization_url);if(target.origin!=='https://accounts.google.com')throw new Error('No se pudo abrir Google.');location.assign(target.href);}
  catch(e){toast(e.message);if(button)button.disabled=false;}
}
const canBookParallel = () => state?.user.role==='admin' && state.user.email==='santibanezf.carlos@gmail.com';
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
const brand = `<a class="brand" href="https://valhallaoffice.cl/" aria-label="Ir al inicio de Valhalla Office"><img src="/images/valhalla-logo.png" alt="Valhalla Office · logo oficial" width="210" height="210" decoding="async"></a>`;
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
  return `<section class="welcome-carousel" role="region" aria-roledescription="carrusel" aria-label="Fotos de Valhalla Office"><button type="button" class="welcome-photo-frame" id="welcome-photo-next" aria-label="Ver la siguiente foto de Valhalla Office"><img id="welcome-photo" src="${escapeHTML(slides[0].image_url)}" alt="${escapeHTML(slides[0].alt)}" decoding="async" fetchpriority="high"></button><div class="welcome-photo-caption"><div><span class="eyebrow">CONOCE TU PRÓXIMO ESPACIO</span><strong id="welcome-photo-title">${escapeHTML(slides[0].title)}</strong></div><span id="welcome-photo-count">1 / ${slides.length}</span></div><div class="welcome-carousel-controls"><button type="button" id="welcome-previous" aria-label="Mostrar foto anterior">←</button><div class="welcome-dots" role="group" aria-label="Elegir foto">${slides.map((slide,index)=>`<button type="button" data-slide="${index}" aria-label="Foto ${index+1}: ${escapeHTML(slide.title)}" aria-pressed="${index===0}"></button>`).join('')}</div><button type="button" id="welcome-next" aria-label="Mostrar foto siguiente">→</button><button type="button" id="welcome-pause" aria-label="Pausar carrusel" aria-pressed="false">Pausar</button></div></section>`;
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
  carousel.querySelector('#welcome-photo-next').onclick=()=>show(index+1);
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
  multiBooking=false;selectedBookings=[];menuScrollLeft=0;
  disposeWelcomeCarousel();
  root.innerHTML = `<div class="auth"><section class="auth-story">${brand}<div><div class="eyebrow">UN LUGAR PARA CRECER</div><h1>Sigue tu vocación.<br><em>Nosotros ponemos el espacio.</em></h1><p>Tres boxes para atender a tu ritmo, una recepción para tus pacientes y un entorno pensado para que crezcas como profesional.</p><div class="welcome-benefits"><span>3 boxes profesionales</span><span>Paquetes de horas</span><span>Box infantil</span></div>${welcomeCarousel()}<a class="welcome-gallery-link" href="#office-gallery-title">Recorre todos nuestros espacios <span aria-hidden="true">↓</span></a></div><footer>COLO COLO 222 · OFICINA 710</footer></section>
    <section class="auth-form"><div class="auth-inner"><span class="demo-label">TU ESPACIO PROFESIONAL</span><h2>${administrator ? 'Acceso administrador' : register ? 'Crea tu cuenta profesional' : 'Bienvenido a tu espacio'}</h2><p class="muted">${administrator ? 'Ingresa con tu correo autorizado y tu contraseña privada.' : register ? 'Regístrate y envía tu documentación para comenzar.' : 'Organiza tus atenciones, con todo en un solo lugar.'}</p>
    ${googleEnabled&&!administrator?'<button type="button" class="btn google-signin full" id="google-access"><svg class="google-icon" xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 48 48" aria-hidden="true" focusable="false"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59A14.4 14.4 0 0 1 9.75 24c0-1.59.27-3.13.76-4.59l-7.98-6.19A23.87 23.87 0 0 0 0 24c0 3.87.93 7.53 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.91-5.8l-7.73-6c-2.15 1.45-4.92 2.3-8.18 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg><span>Ingresar con Google</span></button><p class="muted">O continúa con tu correo y contraseña.</p>':''}
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
  if (!selectedDate) selectedDate=state.earliest_booking_date||state.today;
  applyBookingsDeepLink();
  if(adminMode()) { adminData=await api('admin');view='admin'; }
  else if(view==='admin') view='overview';
  await render();
}
function shell() {
  disposeWelcomeCarousel();
  const u = state.user;
  const links = adminMode() ? [['admin','▦','Administración'],['availability','▤','Disponibilidad']] : [['overview','▦','Mi espacio'],['availability','▤','Disponibilidad'],['bookings','◷','Mis reservas'],['packages','◇','Comprar horas'],['documents','▱','Documentación'],['profile','○','Mi perfil']];
  root.innerHTML=`<div class="shell"><aside class="sidebar">${brand}<nav aria-label="Navegación principal">${links.map(([id,icon,label])=>`<button class="nav-item ${view===id?'active':''}" data-view="${id}" ${view===id?'aria-current="page"':''}><span class="nav-icon" aria-hidden="true">${icon}</span>${label}</button>`).join('')}</nav><div class="sidebar-bottom"><div class="sidebar-note"><strong>Un saldo, tres espacios</strong><p>Tus horas te acompañan en cualquiera de nuestros boxes.</p></div><div class="profile"><span class="avatar">${escapeHTML(u.name.split(' ').map(s=>s[0]).slice(0,2).join(''))}</span><div><div class="profile-name">${escapeHTML(u.name)}</div><small>${adminMode()?'Administración':escapeHTML(u.specialty)||'Perfil profesional'}</small></div></div><button class="btn ghost small full" data-logout>Cerrar sesión</button></div></aside>
  <main class="main"><header class="topbar"><span class="topbar-date">Tu espacio profesional / ${links.find(l=>l[0]===view)?.[2]||'Reservas'}</span>${brand}<div class="topbar-right">${state.webpay_mode==='production'?'':'<span class="demo-label">DEMO · SIN COBROS</span>'}${adminMode()?'<span class="pill">Administración</span>':`<span class="pill ${u.verified?'approved':'pending'}">${u.verified?(u.identity_source==='personal'?'✓ Aprobación personal':u.identity_source==='legacy'?'✓ Validación anterior':'✓ Documentos aprobados'):'Por verificar'}</span>`}<button class="btn ghost small mobile-logout" data-logout aria-label="Cerrar sesión">Salir</button></div></header>${u.role==='admin'?`<section class="profile-switcher row spread" aria-label="Cambiar perfil"><div><strong>Perfil activo</strong><p>${adminMode()?'Administración del centro':'Tu agenda y saldo personal'}</p></div><div class="row"><button class="btn ${adminMode()?'':'ghost'} small" data-mode="admin" aria-pressed="${adminMode()}">Administración</button><button class="btn ${adminMode()?'ghost':''} small" data-mode="professional" aria-pressed="${!adminMode()}">Perfil profesional</button></div></section>`:''}${!u.email_verified?`<div class="notice" role="status"><strong>Confirma tu correo electrónico</strong><p>${state.email_delivery_available?(state.email_verification_delivery==='accepted'?'El proveedor aceptó tu correo de confirmación. Revisa entrada y spam.':state.email_verification_delivery==='error'?'No se pudo enviar la confirmación. Administración debe revisar el diagnóstico de correo.':'Tu correo aún no está confirmado. Solicita un enlace con el botón de abajo.'):'El envío de confirmaciones todavía no está habilitado. Administración debe configurarlo antes de que puedas confirmar tu correo y reservar.'}</p><button class="btn ghost small" id="resend-email" ${state.email_delivery_available?'':'disabled'}>Reenviar confirmación</button></div>`:adminMode()&&!state.email_delivery_available?'<div class="notice">El envío de correos aún no está habilitado. Configura el servicio de correo para que los profesionales puedan confirmar sus cuentas y reservar.</div>':''}<div id="content"></div></main></div>`;
  const navigation=document.querySelector('.sidebar nav');navigation.scrollLeft=menuScrollLeft;
  if(window.matchMedia('(max-width:720px)').matches){const active=navigation.querySelector('[aria-current="page"]');if(active){const item=active.getBoundingClientRect(),frame=navigation.getBoundingClientRect();if(item.right>frame.right)navigation.scrollLeft+=item.right-frame.right;else if(item.left<frame.left)navigation.scrollLeft+=item.left-frame.left;}}
  menuScrollLeft=navigation.scrollLeft;navigation.onscroll=()=>{menuScrollLeft=navigation.scrollLeft;};
  const resend=document.querySelector('#resend-email');if(resend)resend.onclick=async()=>{resend.disabled=true;try{const result=await api('email-verification/resend',{});toast(result.message||'Tu correo ya está confirmado.');await reload();}catch(e){toast(e.message);}finally{resend.disabled=false;}};
  document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=async()=>{activeProfile=b.dataset.mode;multiBooking=false;selectedBookings=[];view=adminMode()?'admin':'overview';modifying=null;try{await reload();}catch(e){toast(e.message);}});
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
function bookingLinks(b){
  const links=b.links||{};
  if(!links.calendar){
    const compact=value=>new Date(value).toISOString().replace(/[-:]/g,'').replace(/\.\d{3}Z$/,'Z');
    links.calendar='https://calendar.google.com/calendar/render?'+new URLSearchParams({action:'TEMPLATE',text:'Valhalla Office · '+rooms[b.box-1],dates:compact(b.start)+'/'+compact(new Date(new Date(b.start).getTime()+55*60000)),ctz:'America/Santiago',location:'Colo Colo 222, oficina 710',details:'Consulta tu reserva en https://valhallaoffice.cl/#mis-reservas'});
    links.directions='https://www.google.com/maps/search/?'+new URLSearchParams({api:'1',query:'Valhalla Office, Colo Colo 222, oficina 710'});
  }
  return `<div class="row booking-links"><a class="btn ghost small" href="${escapeHTML(links.calendar)}" target="_blank" rel="noopener noreferrer">Agregar a mi calendario</a><a class="btn ghost small" href="${escapeHTML(links.directions)}" target="_blank" rel="noopener noreferrer">Cómo llegar</a><button class="btn ghost small" data-go="bookings">Mis reservas</button></div>`;
}
function bookingConfirmation(){
  const bookings=state.bookings.filter(booking=>confirmedBookingIds.includes(booking.id)&&booking.status==='confirmed');if(!bookings.length)return '';
  return `<section class="card booking-confirmation" role="region" aria-label="Confirmación de reserva"><span class="eyebrow">Tu espacio está listo</span><h2>${confirmationModified?'Reserva modificada':bookings.length===1?'Reserva confirmada':'Reservas confirmadas'}</h2>${bookings.map(booking=>`<div class="confirmation-reservation"><strong>${rooms[booking.box-1]}</strong><p>${dateText(booking.start)} · ${sessionRange(booking.start)} · Hora de Chile</p><p>Colo Colo 222, oficina 710</p>${bookingLinks(booking)}</div>`).join('')}<p class="confirmation-balance"><strong>Saldo restante: ${pendingBalance()} sesiones</strong></p><p class="data-note">Agregar al calendario abre Google Calendar para que guardes el evento. Si modificas una reserva, actualiza también tu calendario.</p></section>`;
}
function applyBookingsDeepLink(){
  if(!bookingsDeepLink)return;bookingsDeepLink=false;try{sessionStorage.removeItem('valhalla-bookings-link');}catch{}activeProfile='professional';view='bookings';history.replaceState(null,'',location.pathname+location.search);
}
addEventListener('hashchange',()=>{if(location.hash==='#mis-reservas'){bookingsDeepLink=true;try{sessionStorage.setItem('valhalla-bookings-link','1');}catch{}if(state){applyBookingsDeepLink();render().catch(error=>toast(error.message));}}});
function bookingItem(b, actions=true) {
  return `<div class="booking-item"><div class="booking-info"><span class="box-avatar">0${b.box}</span><div><strong>${rooms[b.box-1]}</strong><p>${dateText(b.start)} · ${sessionRange(b.start)} · 55 min</p>${pill(b.status)} ${b.modifications?'<span class="pill neutral">Cambio utilizado</span>':''}${b.status==='confirmed'?bookingLinks(b):''}</div></div>${actions&&canModify(b)?`<button class="btn ghost small" data-modify="${b.id}">Modificar</button>`:''}</div>`;
}
function stats() {
  return `<div class="grid stats"><div class="stat"><div class="stat-top"><span>Sesiones disponibles</span><span>◇</span></div><div class="value">${pendingBalance()} <small>ses.</small></div><small>Compartidas entre los 3 boxes</small></div><div class="stat"><div class="stat-top"><span>Próximas reservas</span><span>◷</span></div><div class="value">${upcoming().length}</div><small>Tu agenda en Valhalla</small></div><div class="stat"><div class="stat-top"><span>Tu cuenta</span><span>✓</span></div><div class="value account-value">${state.user.verified?'Verificada':'En revisión'}</div><small>${state.user.verified?(state.user.email_verified?'Identidad validada':'Falta confirmar tu correo'):'Revisa carnet y certificado de título'}</small></div></div>`;
}
function overview() {
  const first = escapeHTML(state.user.name.split(' ')[0]);
  return head(`Hola, ${first} <span aria-hidden="true">✦</span>`, 'Un buen día empieza con un espacio para ti.', '<button class="btn" data-go="availability">Reservar un box →</button>')+stats()+
  `${!state.user.verified?'<div class="notice">Tu cuenta necesita carnet de identidad aprobado para reservar. <button class="text-btn" data-go="documents">Enviar carnet →</button></div>':''}
  <section class="hero"><div><div class="eyebrow">ESPACIO PARA LO QUE IMPORTA</div><h2>Tú cuidas de tus pacientes.<br>Nosotros, de tu espacio.</h2><p>Elige el box y el horario que mejor se adapten a ti. Tus horas funcionan en los tres espacios.</p><button class="btn" data-go="availability">Ver disponibilidad <span aria-hidden="true">→</span></button></div><div class="hero-art" aria-hidden="true"><div class="arch">01</div><div class="arch">02</div><div class="arch">03</div></div></section>
  <div class="grid columns"><section class="card"><div class="card-head"><h2>Tus próximas atenciones</h2><button class="text-btn" data-go="bookings">Ver todas →</button></div>${upcoming().slice(0,3).map(b=>bookingItem(b)).join('')||empty('Tu agenda está abierta. Reserva tu primera hora.')}</section><section class="card"><h2>Tres boxes. Tu elección.</h2><p class="section-intro">Un entorno privado para tu práctica profesional.</p>${rooms.map((r,i)=>`<div class="room">${roomPhoto(i+1)||`<span class="room-badge">0${i+1}</span>`}<div><p>${r}</p><small>Uso profesional · Atención de 55 minutos</small></div></div>`).join('')}<small>Horarios: 07:00 a 23:00 · Última reserva: 22:00 · Chile</small></section></div>${gallerySection()}`;
}
function batchBookingPanel(){
  const count=selectedBookings.length;
  return `<section class="card batch-booking"><div class="row spread"><div><h2>Organiza tus horas</h2><p>Tienes ${pendingBalance()} sesiones disponibles. Puedes reservar varias ahora y dejar las demás para después.</p></div><button class="btn ${multiBooking?'ghost':'secondary'} small" id="toggle-multiple">${multiBooking?'Volver a reserva individual':'Seleccionar varias horas'}</button></div>${multiBooking?`<p class="data-note">Marca horarios en uno o varios días y boxes. Esta selección todavía no reserva ni descuenta saldo.</p><div class="batch-list">${selectedBookings.map((slot,index)=>`<div class="row spread"><span>${rooms[slot.box-1]} · ${dateText(slot.start)} · ${sessionRange(slot.start)}</span><button class="text-btn" data-remove-selected="${index}" aria-label="Quitar ${rooms[slot.box-1]}, ${dateText(slot.start)}, ${hourText(slot.start)}">Quitar</button></div>`).join('')}</div><div class="row spread"><strong aria-live="polite">${count} horas seleccionadas · ${Math.max(0,pendingBalance()-count)} sesiones para después</strong><button class="btn" id="confirm-multiple" ${count?'':'disabled'}>Revisar y confirmar horas</button></div>`:''}</section>`;
}
function bindBatchBookings(){
  const toggle=document.querySelector('#toggle-multiple');if(toggle)toggle.onclick=()=>{multiBooking=!multiBooking;selectedBookings=[];render().catch(e=>toast(e.message));};
  document.querySelectorAll('[data-remove-selected]').forEach(button=>button.onclick=()=>{selectedBookings.splice(Number(button.dataset.removeSelected),1);render().catch(e=>toast(e.message));});
  const confirm=document.querySelector('#confirm-multiple');if(!confirm)return;
  confirm.onclick=()=>{
    const slots=selectedBookings.map(slot=>({...slot})).sort((a,b)=>a.start.localeCompare(b.start));
    const remaining=pendingBalance()-slots.length;
    if(!slots.length||remaining<0){toast('Revisa tu selección y el saldo disponible.');return;}
    const request_key=crypto.randomUUID();
    showBookingModal('Confirma tus horas',`<p>Se descontarán ${slots.length} sesiones de tu saldo confirmado. Cada hora dura 55 minutos.</p><ul class="batch-review">${slots.map(slot=>`<li>${rooms[slot.box-1]} · ${dateText(slot.start)} · ${sessionRange(slot.start)}</li>`).join('')}</ul>${remaining>0?`<label class="identity-confirmation"><input type="checkbox" id="book-rest-later" checked>Agendar las ${remaining} sesiones restantes más adelante.</label><p class="data-note">El saldo restante se conserva en tu cuenta, sin elegir fechas ahora.</p>`:'<p>Usarás todas tus sesiones disponibles.</p>'}<p>Se confirma la selección completa. Si algún horario deja de estar disponible, no se confirma ninguno ni se descuenta saldo. Cada reserva tendrá su propia modificación, según las normas.</p>`,'Confirmar horas seleccionadas',async rules_acceptance=>{
      if(remaining>0&&!document.querySelector('#book-rest-later').checked)throw new Error('Marca la opción de agendar el resto después o vuelve para seleccionar más horas.');
      const result=await api('bookings/batch',{slots,request_key,rules_acceptance});selectedBookings=[];multiBooking=false;confirmedBookingIds=result.ids;confirmationModified=false;view='bookings';toast(`${result.ids.length} reservas confirmadas. ${result.remaining??remaining} sesiones quedan disponibles para después.`);
    });
  };
}
async function availability() {
  if(!adminMode()&&selectedDate<(state.earliest_booking_date||state.today))selectedDate=state.earliest_booking_date||state.today;
  const data=await api((adminMode()?'admin/availability':'availability')+'?date='+selectedDate);
  return head(adminMode()?'Agenda y ocupación de los boxes':modifying?'Modifica tu reserva':'Encuentra tu próximo espacio',adminMode()?'Consulta quién reservó cada box, con nombre y horario de atención.':modifying?'Un único cambio, sin consumir sesiones adicionales.':'Sesiones de 55 minutos, con inicios cada hora.')+
  `${modifying?`<div class="notice info">Reserva #${modifying.id}: ${rooms[modifying.box-1]} · ${dateText(modifying.start)} · ${hourText(modifying.start)}. <button class="text-btn" id="abort-change">Salir sin modificar</button></div>`:''}
  ${!state.user.verified&&!adminMode()?'<div class="notice">Para reservar necesitas carnet y certificado de título aprobados, correo confirmado y saldo disponible.</div>':''}
  ${!adminMode()?manualRulesNotice():''}
  ${!adminMode()&&!modifying?batchBookingPanel():''}
  <section class="card"><div class="calendar-toolbar"><label>Fecha de atención<input id="schedule-date" type="date" value="${selectedDate}" min="${adminMode()?state.today:state.earliest_booking_date||state.today}" max="${new Date(new Date(state.today+'T12:00:00Z').getTime()+90*86400000).toISOString().slice(0,10)}"></label><div><button class="btn ghost small" id="refresh-agenda">Actualizar agenda</button><div class="legend"><span><i class="dot"></i>Disponible</span><span><i class="dot busy"></i>No disponible</span></div><p class="data-note">Hora de Chile · 55 min de atención + 5 min de margen · Reserva con al menos 24 horas de anticipación</p></div></div><div class="table-wrap"><table class="availability"><thead><tr><th scope="col">Hora</th>${rooms.map((r,index)=>`<th scope="col"><span class="room-heading">${r}</span><span class="room-subtitle">${index===2?'Atención infantil':'Espacio profesional'}</span></th>`).join('')}</tr></thead><tbody>${data.slots.map(slot=>`<tr><td>${slot.label}</td>${slot.boxes.map(box=>`<td>${adminMode()&&box.booking?`<div class="admin-occupied ${box.booking.timing==='in_session'?'current':''}"><strong>${adminUserLink(box.booking.user_id,box.booking.name)}</strong><small>${escapeHTML(box.booking.email)}</small><span>${sessionRange(box.booking.start)}</span><small>${timingLabels[box.booking.timing]}</small></div>`:`<button class="slot ${multiBooking&&!modifying&&chosenSlot(box.box,slot.start)?'selected-slot':''}" data-slot="${slot.start}" data-box="${box.box}" ${multiBooking&&!modifying&&!adminMode()?`aria-pressed="${chosenSlot(box.box,slot.start)}"`:""} ${box.available?'':'disabled'} aria-label="${rooms[box.box-1]}, ${slot.label}, ${box.available?'disponible':'no disponible'}">${multiBooking&&!modifying&&chosenSlot(box.box,slot.start)?'Seleccionada':box.available?'Disponible':'—'}</button>`}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section><p class="data-note">${adminMode()?'La agenda muestra reservas y su horario; no confirma presencia física en el centro. ':''}Las reservas se confirman con saldo y verificación vigentes. ${canBookParallel()?'Tu cuenta administradora puede reservar varios boxes disponibles a la misma hora; cada box descuenta una sesión.':'Un profesional no puede ocupar dos boxes a la misma hora.'}</p>${!adminMode()&&(state.rooms||[]).length?`<section class="room-gallery" aria-label="Fotos de los boxes">${state.rooms.map(room=>roomPhoto(room.box,true)).join('')}</section>`:''}`;
}
function bookings() {
  return bookingConfirmation()+head('Tu agenda, en orden','Una sola modificación por reserva, con al menos 2 horas de anticipación.','<button class="btn" data-go="availability">Nueva reserva →</button>')+
  `<section class="card"><div class="card-head"><h2>Próximas reservas</h2><span class="pill neutral">${upcoming().length} reservas</span></div>${upcoming().map(b=>bookingItem(b)).join('')||empty('No tienes reservas próximas.')}</section><section class="card spaced-card"><div class="card-head"><h2>Historial</h2></div>${state.bookings.filter(b=>!upcoming().some(u=>u.id===b.id)).map(b=>bookingItem(b,false)).join('')||empty('Aquí aparecerán tus reservas anteriores o canceladas.')}</section><p class="data-note">Para cancelar una reserva, contacta a administración. En este prototipo, el administrador puede cancelar reservas futuras y devolver la hora al paquete original.</p>`;
}
function packages() {
  return manualRulesNotice()+head('Comprar horas','Primero compra tus horas y, cuando el pago esté confirmado y tu cuenta validada, agenda con tu saldo en Disponibilidad. Cada hora incluye una sesión de 55 minutos en cualquiera de los tres boxes.')+
  `<div class="notice info">${state.webpay_mode==='production'?(state.webpay_live_sales_enabled?'Compra tus horas con tarjeta a través de Webpay. El saldo se acredita al verificarse el pago.':'Las compras se están habilitando. Contacta a administración para más información.'):state.webpay_mode==='integration'?'Webpay en ambiente de pruebas: usa únicamente tarjetas de prueba. No se realizan cargos reales. El saldo se carga automáticamente al aprobarse el pago.':'Los pagos siguen siendo simulados; solicitar un plan no genera un cobro real.'} Para reservar necesitas correo confirmado, identidad validada y saldo disponible.</div>`+
  `<div class="grid package-grid">${state.catalog.map(p=>`<section class="package ${p.hours===10?'featured':''}">${p.hours===10?'<span class="recommend">Plan de 10 horas</span>':''}<div class="eyebrow">${p.hours===1?'SESIÓN INDIVIDUAL':'PLAN MIXTO · TODOS LOS BOXES'}</div><div class="hours">${p.hours} <span>${p.hours===1?'hora':'horas'}</span></div><p class="muted small-text">${p.hours} ${p.hours===1?'sesión':'sesiones'} · 55 minutos cada una</p><div class="price">${money(p.price)}</div><small>${money(p.price/p.hours)} por sesión</small><ul><li>Usable en cualquiera de los 3 boxes</li><li>Inicios cada hora, de 07:00 a 22:00 · Cierre: 23:00</li><li>5 minutos de margen entre atenciones</li><li>Uso exclusivo de tu cuenta</li></ul><button class="btn ${p.hours===10?'':'secondary'} full" data-buy="${p.id}" ${state.webpay_mode==='production'&&!state.webpay_live_sales_enabled?'disabled':''}>${state.webpay_mode==='production'?(state.webpay_live_sales_enabled?'Comprar':'Próximamente'):state.webpay_mode==='integration'?'Comprar en Webpay · prueba':'Solicitar'} ${p.hours===1?'sesión':'plan'} →</button></section>`).join('')}</div>
  <section class="card"><div class="card-head"><h2>Mis planes</h2><span class="pill neutral">${pendingBalance()} sesiones disponibles</span></div>${state.packages.length?`<div class="table-wrap"><table><thead><tr><th>Plan</th><th>Sesiones restantes</th><th>Total</th><th>Origen / estado</th></tr></thead><tbody>${state.packages.map(p=>`<tr><td>#${p.id} · ${p.hours} ${p.hours===1?'hora':'horas'}${p.balance_adjustment?`<small>Ajuste administrativo: ${signedSessions(p.balance_adjustment)} sesiones</small>`:''}</td><td>${p.status==='paid'?p.remaining:'No habilitado'}</td><td>${money(p.price)}${p.discount_code?`<small>Código ${escapeHTML(p.discount_code)} · ${p.discount_percent}% de descuento</small>`:''}</td><td>${packageSource(p)} ${pill(p.status)}</td></tr>`).join('')}</tbody></table></div>`:empty('Aún no has solicitado un plan.')}</section>${paymentList()}<p class="data-note">Cada reserva descuenta una sesión. Los planes son personales y sus sesiones se comparten entre boxes. Los planes existentes conservan su saldo y precio histórico.</p>`;
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
  if(state.user.identity_source==='personal')return head('Aprobado personalmente por administración','Administración te conoce y aprobó personalmente tu cuenta.')+'<section class="card"><h2>No necesitas presentar carnet ni certificado de título</h2><p>Para reservar necesitas confirmar tu correo, aceptar las normas de tu plan y contar con sesiones disponibles.</p></section>';
  if(state.user.identity_source==='legacy')return head('Validación anterior reconocida','Administración reconoció tu validación del sistema anterior.')+'<section class="card"><h2>No necesitas reenviar documentos</h2><p>Tu validación anterior se conserva. Para reservar necesitas correo confirmado y sesiones disponibles.</p></section>';
  const uploadSection=(kind,title,copy,input,form)=>`<section class="card spaced-card"><h2>${title}</h2><p class="section-intro">${copy}</p><form id="${form}" data-document-kind="${kind}"><div class="upload-card"><strong>${title}</strong><p>Hasta 5 archivos y 10 MB por envío · Acceso privado</p><label>${kind==='identity'?'Seleccionar archivos':'Seleccionar certificado de título'}<input id="${input}" type="file" multiple required></label></div><p class="muted upload-note">Un nuevo envío de este documento requiere otra revisión. Tus reservas existentes se conservan.</p><p class="error" data-upload-error role="alert"></p><button class="btn" type="submit">${kind==='identity'?'Enviar a revisión →':'Enviar certificado a revisión →'}</button></form></section>`;
  return head('Documentación profesional','Se requieren dos aprobaciones separadas: carnet de identidad y certificado de título.')+
  `<div class="notice ${state.user.verified?'info':''}">${state.user.verified?'✓ Ambos documentos están aprobados. Puedes reservar con correo confirmado y saldo disponible.':'Para reservar deben estar aprobados ambos documentos.'}<p>Carnet: ${state.user.identity_approved?'aprobado':'pendiente de aprobación'} · Certificado de título: ${state.user.degree_approved?'aprobado':'pendiente de aprobación'}</p></div>`+
  uploadSection('identity','Envía tu carnet de identidad','Adjunta el anverso y reverso, juntos o en archivos separados. Administración revisará su legibilidad y correspondencia con tu cuenta.','document-file','upload-form')+
  uploadSection('degree','Envía tu certificado de título','Adjunta tu certificado de título profesional. Administración debe comprobarlo y aprobarlo por separado del carnet.','degree-file','degree-upload-form')+
  `<section class="card spaced-card"><h2>Estado de tus documentos</h2>${state.documents.length?state.documents.map(d=>`<div class="booking-item"><div><strong class="document-name">${escapeHTML(d.filename)}</strong><p class="muted caption-text">${d.kind==='identity'?'Carnet de identidad':d.kind==='degree'?'Certificado de título':'Documento anterior: requiere nueva acreditación'}</p><p class="muted caption-text">Enviado el ${dateText(d.created_at)}</p>${d.note?`<p class="small-text">Observación: ${escapeHTML(d.note)}</p>`:''}</div><div class="row">${pill(d.status)}${documentLinks(d)}</div></div>`).join(''):empty('Todavía no has enviado documentos.')}</section>`;
}
function migrationAdmin() {
  return `<h2>Migración de clientes anteriores</h2><div class="notice">Mientras la plataforma siga sin almacenamiento persistente, usa clientes y saldos ficticios. Antes de migrar saldos reales, confirma que existe un respaldo y que los datos se conservan al desplegar.</div><p class="section-intro">Carga únicamente el saldo restante, sin incluir sesiones ya usadas. Los clientes validados no tendrán que reenviar el carnet; conservarán su cuenta personal y deberán confirmar su correo.</p>
  <section class="migration-section"><h3>Importar una planilla</h3><p>Desde Excel guarda como CSV. Usa las columnas de la plantilla y conserva sus encabezados. Hasta 500 clientes y 1 MB por archivo.</p><button class="btn ghost small" id="migration-template">Descargar plantilla CSV</button><form id="migration-csv-form" class="form-stack"><label>Planilla CSV<input id="migration-csv" type="file" accept=".csv,text/csv" required></label><label class="identity-confirmation"><input id="migration-csv-validated" type="checkbox" required>Confirmo que todos estos clientes ya tienen su identidad validada en el sistema anterior y que revisé sus saldos.</label><p class="error" id="migration-csv-error" role="alert"></p><button class="btn" type="submit">Revisar planilla</button></form><div id="migration-preview" aria-live="polite"></div></section>
  <section class="migration-section"><h3>Cargar un cliente individual</h3><form id="migration-form" class="form-stack"><label>Nombre completo<input name="name" required minlength="2" maxlength="100"></label><label>Correo del cliente<input name="email" type="email" required maxlength="200"></label><label>Profesión o especialidad<input name="specialty" required minlength="2" maxlength="100"></label><label>Referencia del sistema anterior<input name="reference" required minlength="2" maxlength="100" placeholder="Ej. antiguo-juanita-001"></label><label>Sesiones restantes<input name="sessions" type="number" min="0" max="10000" step="1" required></label><label>Observación de respaldo (opcional)<textarea name="note" maxlength="500" placeholder="Origen del saldo y fecha de revisión"></textarea></label><label class="identity-confirmation"><input name="identity_confirmed" type="checkbox" required>Confirmo que este cliente ya fue validado y que el saldo es correcto.</label><p class="error" id="migration-error" role="alert"></p><button class="btn" type="submit">Revisar migración</button></form></section>
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
  const payments=(admin?(adminData.payments||[]):(state.payments||[])).filter(payment=>!admin||showArchivedPlans||!payment.archived);
  if(!payments.length)return '';
  return `<section class="card spaced-card"><h2>Compras Webpay</h2><p>Cada operación indica su ambiente. El saldo se acredita solo tras una aprobación verificada con Transbank.</p><div class="table-wrap"><table><thead><tr>${admin?'<th>Profesional</th>':''}<th>Orden</th><th>Plan</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>${payments.map(p=>`<tr>${admin?`<td>${escapeHTML(p.name)}</td>`:''}<td>${escapeHTML(p.buy_order)}</td><td>${p.hours===0?'Validación de $50 · sin sesiones':p.hours+' sesiones'}<small>${money(p.amount)} · ${p.environment==='production'?'Cobro real':'Prueba sin cobro'}</small></td><td>${paymentStates[p.state]||escapeHTML(p.state)}${p.archived?'<span class="pill neutral">Archivado</span>':''}</td><td>${!['approved','rejected'].includes(p.state)?`<button class="btn ghost small" data-payment-status="${p.id}" data-payment-admin="${admin}">Consultar estado</button>${!admin&&p.state==='ready'&&p.environment===state.webpay_mode?`<button class="btn small" data-payment-resume="${p.id}">Retomar pago</button>`:''}`:'—'}</td></tr>`).join('')}</tbody></table></div></section>`;
}
function sendToWebpay(checkout) {
  const target=new URL(checkout.url);
  if(target.protocol!=='https:'||target.hostname!==(checkout.mode==='production'?'webpay3g.transbank.cl':'webpay3gint.transbank.cl')||!['integration','production'].includes(checkout.mode)||target.pathname!=='/webpayserver/initTransaction'||target.username||target.password||target.search||target.hash)throw new Error('Destino de pago inválido.');
  const form=document.createElement('form');form.method='POST';form.action=checkout.url;
  const token=document.createElement('input');token.type='hidden';token.name='token_ws';token.value=checkout.token;form.appendChild(token);document.body.appendChild(form);form.submit();
}
function discountsAdmin(){
  return `<h2>Códigos de descuento</h2><p class="section-intro">Personaliza un código y su porcentaje (1% a 99%). Se aplica a cualquier plan, una vez por compra. Puedes desactivarlo para futuras compras.</p><form id="discount-form" class="form-stack"><label>Código personalizado<input name="code" required minlength="3" maxlength="40" pattern="[A-Za-z0-9_-]{3,40}" placeholder="Ej. BIENVENIDA20"></label><label>Porcentaje de descuento<input name="percent" type="number" min="1" max="99" step="1" required placeholder="Ej. 20"></label><p class="error" id="discount-admin-error" role="alert"></p><button class="btn" type="submit">Guardar código</button></form><div class="table-wrap"><table><thead><tr><th>Código</th><th>Descuento</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>${(adminData.discount_codes||[]).map(d=>`<tr><td>${escapeHTML(d.code)}</td><td>${d.percent}%</td><td>${d.active?'Activo':'Desactivado'}</td><td><button class="btn ghost small" data-discount-edit="${escapeHTML(d.code)}">Editar</button> <button class="btn ghost small" data-discount-toggle="${escapeHTML(d.code)}">${d.active?'Desactivar':'Activar'}</button></td></tr>`).join('')}</tbody></table></div>`;
}
function bindDiscounts(){
  const form=document.querySelector('#discount-form');if(!form)return;
  form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('button');button.disabled=true;try{await api('admin/discounts',{code:form.elements.code.value,percent:Number(form.elements.percent.value),active:true});await reload();toast('Código guardado.');}catch(e){document.querySelector('#discount-admin-error').textContent=e.message;}finally{button.disabled=false;}};
  document.querySelectorAll('[data-discount-edit]').forEach(b=>b.onclick=()=>{const d=adminData.discount_codes.find(d=>d.code===b.dataset.discountEdit);form.elements.code.value=d.code;form.elements.percent.value=d.percent;form.elements.percent.focus();});
  document.querySelectorAll('[data-discount-toggle]').forEach(b=>b.onclick=async()=>{b.disabled=true;const d=adminData.discount_codes.find(d=>d.code===b.dataset.discountToggle);try{await api('admin/discounts',{code:d.code,percent:d.percent,active:!d.active});await reload();}catch(e){toast(e.message);b.disabled=false;}});
}
function purchaseModal(plan, resumeId=null){
  const rules=state.purchase_rules;
  if(!rules?.items?.length){toast('No se pudieron cargar las normas. Actualiza la página antes de comprar.');return;}
  const live=state.webpay_mode==='production',validation=plan.id==='validation_50';
  const bank=['integration','production'].includes(state.webpay_mode);
  if(bank&&!state.user.email_verified){toast('Confirma tu correo antes de comprar.');return;}
  const existingPayment=state.payments.find(p=>p.id===resumeId),existingPlan=existingPayment?state.packages.find(p=>p.id===existingPayment.package_id):null;
  let appliedCode=existingPlan?.discount_code||'';
  const shownPrice=existingPayment?.amount??plan.price;
  let quotedPrice=shownPrice;
  const key=resumeId?'resume-'+resumeId:crypto.randomUUID();
  const acceptance=()=>({accepted:document.querySelector('#purchase-rules-accepted')?.checked===true,version:rules.version});
  showModal(live?(validation?'Validación de producción · $50':'Comprar horas · Webpay'):bank?'Comprar plan · Webpay de pruebas':'Solicitar paquete',`<p>${validation?'Compra de validación · no acredita sesiones':plan.hours+' sesiones'} · ${money(shownPrice)}. ${live?'Se realizará un cobro real con tu tarjeta.':bank?'Continuarás al sitio de pruebas de Transbank. No habrá cargos reales.':'Esta operación es simulada y no genera un cobro real.'}</p><section class="purchase-rules" aria-label="Normas de uso"><h3>Normas de Valhalla Office</h3><ol>${rules.items.map(item=>`<li><strong>${escapeHTML(item.title)}</strong><p>${escapeHTML(item.text)}</p></li>`).join('')}</ol><p><a href="/normas" target="_blank" rel="noopener">Abrir las normas en otra pestaña</a></p></section><label class="identity-confirmation"><input type="checkbox" id="purchase-rules-accepted">He leído y acepto las 12 normas de uso de Valhalla Office para esta compra.</label><section class="discount-checkout"><h3>¿Tienes un código de descuento?</h3>${validation?'<p>La validación tiene un monto fijo de $50 y no admite descuentos.</p>':resumeId?'<p>Esta compra conserva el precio y el código con los que fue iniciada.</p>':'<label>Agrega tu código antes de comprar<input id="purchase-discount-code" maxlength="40" autocomplete="off" placeholder="Ej. BIENVENIDA20"></label><button type="button" class="btn secondary" id="apply-discount">Aplicar código</button>'}<p id="discount-result" role="status" aria-live="polite">${appliedCode?`Código ${escapeHTML(appliedCode)} aplicado. `:''}Total a pagar: <strong>${money(shownPrice)}</strong></p><p class="error" id="discount-error" role="alert"></p></section>`,live?(validation?'Pagar $50 con tarjeta real':'Pagar con Webpay'):bank?'Ir a Webpay de pruebas':'Solicitar sin cobro',async()=>{
    const input=document.querySelector('#purchase-discount-code');if(input&&input.value.trim().toUpperCase()!==appliedCode)throw new Error('Aplica el código para revisar el precio antes de comprar, o deja el campo vacío.');
    const rules_acceptance=acceptance();if(!rules_acceptance.accepted)throw new Error('Debes aceptar las normas para continuar.');
    if(bank)sendToWebpay(await api('webpay/create',{catalog_id:plan.id,idempotency_key:key,rules_acceptance,discount_code:appliedCode,expected_price:quotedPrice}));
    else{await api('packages',{catalog_id:plan.id,rules_acceptance,discount_code:appliedCode,expected_price:quotedPrice});toast('Solicitud registrada. Pago simulado pendiente de confirmación.');}
  });
  const checkbox=document.querySelector('#purchase-rules-accepted'),button=document.querySelector('#modal-confirm');button.disabled=true;checkbox.onchange=()=>button.disabled=!checkbox.checked;
  const apply=document.querySelector('#apply-discount'),input=document.querySelector('#purchase-discount-code');
  if(apply){
    input.oninput=()=>{appliedCode='';quotedPrice=plan.price;document.querySelector('#discount-result').textContent='Total sin descuento: '+money(plan.price)+'. Aplica el código para ver el nuevo precio.';};
    apply.onclick=async()=>{apply.disabled=true;button.disabled=true;input.disabled=true;try{const quote=await api('discounts/quote',{catalog_id:plan.id,discount_code:input.value});appliedCode=quote.code;quotedPrice=quote.price;input.value=quote.code;document.querySelector('#discount-error').textContent='';document.querySelector('#discount-result').innerHTML=quote.code?`Código <strong>${escapeHTML(quote.code)}</strong> aplicado · ${quote.percent}% de descuento.<br>Precio original: <s>${money(quote.original_price)}</s><br>Ahorras: ${money(quote.discount)}<br>Total a pagar: <strong>${money(quote.price)}</strong>`:`Total a pagar: <strong>${money(quote.price)}</strong>`;}catch(e){appliedCode='';document.querySelector('#discount-error').textContent=e.message;}finally{apply.disabled=false;input.disabled=false;button.disabled=!checkbox.checked;}};
  }

}
function bindPayments() {
  document.querySelectorAll('[data-payment-status]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{const result=await api(button.dataset.paymentAdmin==='true'?'admin/webpay/reconcile':'webpay/reconcile',{id:Number(button.dataset.paymentStatus)});await reload();toast(paymentStates[result.state]||'Estado actualizado.');}catch(error){toast(error.message);button.disabled=false;}});
  document.querySelectorAll('[data-payment-resume]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{const payment=state.payments.find(p=>p.id===Number(button.dataset.paymentResume));purchaseModal(payment.hours===0?{id:'validation_50',hours:0,price:50}:state.catalog.find(p=>p.id===String(payment.hours)),payment.id);button.disabled=false;}catch(error){toast(error.message);button.disabled=false;}});
}
const mailCategories={invalid_key:'Clave rechazada: revisa VALHALLA_RESEND_API_KEY o la clave del proveedor seleccionado.',permissions_or_domain:'Acceso rechazado: revisa el dominio verificado, el alcance de la clave o filtros del proveedor.',invalid_sender_or_payload:'Revisa el remitente y su dominio autorizado.',invalid_request:'Solicitud rechazada por el proveedor.',provider_endpoint:'No se encontró el servicio del proveedor.',rate_limited:'Se alcanzó un límite del proveedor; revisa el plan y reintenta más tarde.',provider_unavailable:'El proveedor no está disponible temporalmente.',network_error:'No se pudo conectar con el proveedor. Revisa la conexión desde Render.',unexpected_response:'El proveedor devolvió una respuesta no reconocida.',provider_rejected:'El proveedor rechazó la solicitud.',delivery_error:'El servicio no confirmó el envío.',account_or_limit:'No se envió: cuenta profesional no disponible o enlace solicitado recientemente.'};
function emailAdmin() {
  const config=adminData.email_config||{};
  return `<h2>Servicio de correo</h2><p class="section-intro">Comprueba primero el envío a tu correo administrador. Esta prueba no modifica contraseñas ni verifica cuentas. El proveedor debe aceptar el mensaje y después tu buzón debe recibirlo.</p><dl class="mail-config"><dt>Proveedor</dt><dd>${escapeHTML(config.provider||'Sin configurar')}</dd><dt>Remitente</dt><dd>${escapeHTML(config.sender||'Falta configurar')}</dd><dt>Dirección de los enlaces</dt><dd>${escapeHTML(config.public_url||'Falta configurar')}</dd><dt>Clave privada</dt><dd>${config.api_key_present?'Configurada (valor oculto)':'No configurada'}</dd><dt>Configuración básica</dt><dd>${config.configured?'Completa: falta comprobar envío y recepción':'Incompleta o inválida'}</dd></dl><button class="btn" id="email-test" ${config.configured?'':'disabled'}>Enviar prueba a mi correo administrador</button><p id="email-test-result" role="status"></p><p>El destinatario de prueba es únicamente el correo administrador autorizado. Espera un minuto entre pruebas.</p><button class="btn ghost small" id="refresh-mail">Actualizar diagnóstico</button><h3>Últimos intentos</h3><div class="table-wrap"><table><thead><tr><th>Fecha</th><th>Tipo / cuenta</th><th>Resultado</th><th>Diagnóstico</th></tr></thead><tbody>${(adminData.mail_events||[]).map(event=>`<tr><td>${dateText(event.created_at)}<small>${hourText(event.created_at)}</small></td><td>${{test:'Prueba administradora',verification:'Confirmación de correo',recovery:'Recuperación de contraseña',admin_signup:'Nueva inscripción → administración',admin_document:'Documento recibido → administración',document_approved:'Documento aprobado → profesional',booking_confirmed:'Comprobante de reserva',booking_modified:'Reserva modificada',booking_cancelled:'Reserva cancelada'}[event.kind]||escapeHTML(event.kind)}<small>${escapeHTML(event.name||'Sin cuenta destinataria')}</small></td><td>${{pending:'En proceso',accepted:'Aceptado por el proveedor',error:'Error de envío',skipped:'No se intentó enviar'}[event.status]||escapeHTML(event.status)}</td><td>${event.http_status?'HTTP '+event.http_status+' · ':''}${mailCategories[event.category]||'—'}</td></tr>`).join('')||'<tr><td colspan="4">Todavía no hay intentos registrados en esta versión. Envía una prueba.</td></tr>'}</tbody></table></div><h3>Avisos automáticos</h3><p>Inscripciones y documentos se notifican al administrador; las aprobaciones de carnet y título y los comprobantes se envían al profesional. Si hay un fallo, se reintenta hasta cinco veces.</p><div class="table-wrap"><table><thead><tr><th>Cuenta</th><th>Aviso</th><th>Estado</th><th>Intentos</th></tr></thead><tbody>${(adminData.notification_queue||[]).map(job=>`<tr><td>${escapeHTML(job.name)}</td><td>${{admin_signup:'Nueva inscripción',admin_document:'Documento recibido',document_approved:'Documento aprobado',booking_confirmed:'Reserva confirmada',booking_modified:'Reserva modificada',booking_cancelled:'Reserva cancelada'}[job.kind]||escapeHTML(job.kind)}</td><td>${{pending:'Pendiente',processing:'Enviando',retry:'Pendiente de reintento',failed:'No se pudo enviar',accepted:'Aceptado por el proveedor',superseded:'Sustituido por una actualización'}[job.status]||escapeHTML(job.status)}</td><td>${job.attempts} / 5</td></tr>`).join('')||'<tr><td colspan="4">No hay avisos automáticos nuevos.</td></tr>'}</tbody></table></div><p class="data-note">«Aceptado» no confirma la entrega en el buzón. Comprueba Resend → Emails para conocer entregas o rebotes. Los errores no muestran claves, contenido del correo ni enlaces privados.</p>`;
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
function transbankAdmin() {
  const config=adminData.webpay_config||{mode:'disabled',commerce_code:'',sandbox_enabled:false};
  if(config.mode==='production')return `<section class="webpay-settings"><div class="eyebrow">WEBPAY PLUS · PRODUCCIÓN</div><h2>Pagos con Transbank</h2><p>Código de comercio: <strong>${escapeHTML(config.commerce_code)}</strong></p><p>${config.live_sales_enabled?'Ventas reales habilitadas.':'Ventas normales pausadas hasta completar la validación de $50 y activarlas.'}</p><button class="btn" id="webpay-validation">Realizar compra de validación de $50</button><p class="data-note">Usa una tarjeta real. Esta compra no agrega horas a tu cuenta.</p>${config.validation_paid?`<p>Compra de $50 aprobada y verificada con Transbank.</p><button class="btn secondary" id="webpay-live-sales" data-enabled="${!config.live_sales_enabled}">${config.live_sales_enabled?'Pausar ventas reales':'Activar ventas reales'}</button>`:'<p>Cuando se confirme el pago, aparecerá la opción de activar las ventas normales.</p>'}<p>La clave privada se guarda únicamente en Render.</p></section>${paymentList(true)}`;
  return `<div class="webpay-settings"><div class="row spread"><div><span class="eyebrow webpay-brand">WEBPAY PLUS · TRANSBANK</span><h2>Pagos con Transbank</h2></div><span class="pill ${config.sandbox_enabled?'pending':'neutral'}">${config.sandbox_enabled?'Pruebas activas':'Desactivado'}</span></div><p>Permite preparar la compra de planes con tarjetas de crédito o débito a través de Webpay Plus.</p><form id="webpay-settings-form" class="form-stack"><label>Código de comercio Webpay Plus<input name="commerce_code" inputmode="numeric" pattern="[0-9]{8,12}" maxlength="12" value="${escapeHTML(config.commerce_code)}" placeholder="Código de comercio entregado por Transbank" aria-describedby="commerce-help"></label><p id="commerce-help" class="muted">Usa el código que Transbank confirme para tu integración directa, tal como fue entregado, sin agregar ceros. Guardarlo aquí prepara la configuración; no activa cargos reales.</p><label class="webpay-toggle"><span><strong>Habilitar Webpay de pruebas</strong><small>Usa únicamente tarjetas de prueba. No mueve dinero.</small></span><input type="checkbox" role="switch" name="sandbox_enabled" ${config.sandbox_enabled?'checked':''}></label><div class="notice info"><strong>Activación de cobros reales pendiente</strong><p>Se necesita almacenamiento persistente para conservar cuentas, compras y saldos; las credenciales de producción de tu contrato directo y una prueba completa de integración. La clave secreta se guarda únicamente en Render.</p></div><p id="webpay-settings-error" class="error" role="alert"></p><button class="btn" type="submit">Guardar configuración</button></form><div class="webpay-operation"><h3>Acreditación de planes</h3><p>El pago aprobado debe coincidir con la orden, el importe y la cuenta que inició la compra. Solo entonces se cargan las sesiones, una vez. Un pago rechazado o pendiente no carga saldo.</p><p class="muted">Retorno de Webpay: <code>${escapeHTML(config.return_url||'https://valhallaoffice.cl/api/webpay/return')}</code></p></div></div>${paymentList(true)}`;
}
function bindTransbankAdmin(){
  const validation=document.querySelector('#webpay-validation');if(validation)validation.onclick=()=>purchaseModal({id:'validation_50',hours:0,price:50});
  const sales=document.querySelector('#webpay-live-sales');if(sales)sales.onclick=()=>{const enabled=sales.dataset.enabled==='true';showModal(enabled?'Activar ventas reales':'Pausar ventas reales',`<p>${enabled?'Los clientes podrán comprar planes con tarjetas reales y se acreditarán las horas tras la aprobación de Transbank.':'Se pausarán nuevas compras. Los pagos ya iniciados seguirán conciliándose y los saldos se conservan.'}</p>`,enabled?'Activar ventas':'Pausar ventas',async()=>{await api('admin/webpay/live-sales',{enabled,confirmed:true});toast(enabled?'Ventas reales activadas.':'Ventas pausadas.');});};
  const form=document.querySelector('#webpay-settings-form');if(!form)return;
  form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('button[type=submit]');button.disabled=true;
    try{await api('admin/webpay/settings',{commerce_code:form.elements.commerce_code.value,sandbox_enabled:form.elements.sandbox_enabled.checked});await reload();toast('Configuración guardada. Los cobros reales siguen desactivados.');}
    catch(error){document.querySelector('#webpay-settings-error').textContent=error.message;button.disabled=false;}
  };
}
const manualMethods={cash:'Efectivo',transfer:'Transferencia',courtesy:'Cortesía administrativa'};
const packageSource=plan=>plan.source==='manual_credit'?(manualMethods[plan.manual_method]||'Carga administrativa'):plan.source==='migration'?'Saldo anterior':plan.source==='webpay_integration'?'Webpay · pruebas':plan.source==='webpay_production'?'Webpay · pago real':plan.source==='webpay_validation'?'Validación productiva · $50 · sin sesiones':'Pago simulado';
function manualCreditsAdmin(){
  const users=adminData.users.filter(user=>user.active&&(user.role==='professional'||user.id===state.user.id));
  return `<h2>Cargar horas a un profesional</h2><p class="section-intro">Registra un pago que ya recibiste en efectivo o transferencia, o una cortesía autorizada. No se cobra ni se transfiere dinero desde esta pantalla.</p><form id="manual-credit-form" class="form-stack manual-credit-form"><label>Profesional<select name="user_id" required><option value="">Selecciona una cuenta existente</option>${users.map(user=>`<option value="${user.id}">${escapeHTML(user.name)} · ${escapeHTML(user.email)}</option>`).join('')}</select></label><div class="manual-credit-fields"><label>Sesiones a cargar<input name="hours" type="number" min="1" max="200" step="1" required placeholder="Ej. 5"></label><label>Medio de pago o motivo<select name="method" required><option value="cash">Efectivo</option><option value="transfer">Transferencia</option><option value="courtesy">Cortesía administrativa</option></select></label><label>Monto recibido (CLP)<input name="amount" type="number" min="1" max="100000000" step="1" required placeholder="Ej. 28990"></label></div><label>Fecha de recepción<input name="received_on" type="date" value="${state.today}" max="${state.today}" required></label><label><span id="manual-reference-label">Referencia única del pago o carga</span><input name="reference" minlength="3" maxlength="120" required placeholder="Ej. Banco/fecha/operación o Caja/fecha/comprobante"></label><label>Observación de respaldo (opcional)<textarea name="note" maxlength="500" placeholder="Indica qué verificaste o el motivo de la cortesía"></textarea></label><label class="identity-confirmation"><input type="checkbox" name="confirmed" required>Confirmo que verifiqué el pago recibido o autoricé esta cortesía.</label><p class="data-note">Las horas se cargarán de inmediato en esa cuenta. El profesional aceptará las normas al agendar, antes de confirmar su reserva. Esta carga no aprueba documentos ni confirma su correo.</p><p class="error" id="manual-credit-error" role="alert"></p><button class="btn" type="submit">Revisar carga de horas</button></form><h3 class="manual-credit-history">Historial de cargas manuales</h3>${(adminData.manual_credits||[]).length?`<div class="table-wrap"><table><thead><tr><th>Profesional</th><th>Sesiones</th><th>Pago / referencia</th><th>Respaldo</th><th>Registro</th></tr></thead><tbody>${adminData.manual_credits.map(item=>`<tr><td>${adminUserLink(item.user_id,item.name)}<small>${escapeHTML(item.email)}</small></td><td>${item.hours}<small>${item.remaining} restantes · plan #${item.package_id}</small></td><td>${manualMethods[item.method]} · ${money(item.amount)}<small>${escapeHTML(item.reference)}</small><small>Recepción: ${escapeHTML(item.received_on)}</small></td><td>${escapeHTML(item.note)}</td><td>${dateText(item.created_at)} · ${hourText(item.created_at)}<small>${escapeHTML(item.operator_name)}</small></td></tr>`).join('')}</tbody></table></div>`:empty('Todavía no hay cargas manuales.')}`;
}
function bindManualCredits(){
  const form=document.querySelector('#manual-credit-form');if(!form)return;
  const key=crypto.randomUUID();
  form.elements.method.onchange=()=>{const courtesy=form.elements.method.value==='courtesy';form.elements.amount.readOnly=courtesy;form.elements.amount.min=courtesy?'0':'1';form.elements.amount.value=courtesy?'0':'';form.elements.reference.required=!courtesy;document.querySelector('#manual-reference-label').textContent=courtesy?'Referencia de la cortesía (opcional)':'Referencia única del pago o carga';};
  form.onsubmit=event=>{event.preventDefault();
    const fields=Object.fromEntries(new FormData(form));const body={user_id:Number(fields.user_id),hours:Number(fields.hours),amount:Number(fields.amount),method:fields.method,received_on:fields.received_on,reference:fields.reference,note:fields.note,confirmed:form.elements.confirmed.checked,request_key:key};
    const user=adminData.users.find(user=>user.id===body.user_id);if(!user)return;
    showModal('Confirma la carga de horas',`<p><strong>${escapeHTML(user.name)}</strong><br>${escapeHTML(user.email)}</p><div class="modal-detail">${body.hours} sesiones · ${manualMethods[body.method]} · ${money(body.amount)}<br>Referencia: ${escapeHTML(body.reference)||'Se asignará una referencia interna'}<br>Fecha de recepción: ${escapeHTML(body.received_on)}</div><p>Se registrará el saldo exclusivamente en esta cuenta y quedará una constancia administrativa. No se realizará ningún cobro por internet.</p>`,'Confirmar carga de horas',async()=>{const result=await api('admin/manual-credits',body);toast(result.repeated?'La carga ya estaba registrada. No se duplicaron las horas.':'Horas cargadas correctamente.');});
  };
}
function manualRulesNotice(){
  const pending=state.packages.filter(plan=>plan.source==='manual_credit'&&plan.status==='paid'&&plan.remaining>0&&!plan.rules_accepted);
  if(!pending.length)return '';
  return `<div class="notice"><strong>Tienes nuevas horas disponibles</strong><p>Al elegir un horario podrás leer y aceptar las normas antes de confirmar tu reserva. Si prefieres, también puedes revisarlas aquí.</p><div class="row">${pending.map(plan=>`<button class="btn ghost small" data-accept-manual="${plan.id}">Aceptar normas del plan #${plan.id} · ${plan.remaining} sesiones</button>`).join('')}</div></div>`;
}
function bindManualRules(){
  document.querySelectorAll('[data-accept-manual]').forEach(button=>button.onclick=()=>{
    const rules=state.purchase_rules;
    showModal('Normas de tus horas cargadas',`<section class="purchase-rules"><ol>${rules.items.map(item=>`<li><strong>${escapeHTML(item.title)}</strong><p>${escapeHTML(item.text)}</p></li>`).join('')}</ol></section><label class="identity-confirmation"><input type="checkbox" id="accept-manual-rules">He leído y acepto las 12 normas para usar estas horas.</label>`,'Aceptar normas',async()=>{if(!document.querySelector('#accept-manual-rules').checked)throw new Error('Debes aceptar las normas para continuar.');await api('packages/accept-rules',{package_id:Number(button.dataset.acceptManual),rules_acceptance:{accepted:true,version:rules.version}});toast('Normas aceptadas. Tus horas están listas para reservar cuando completes la validación de tu cuenta.');});
    const checkbox=document.querySelector('#accept-manual-rules'),confirm=document.querySelector('#modal-confirm');confirm.disabled=true;checkbox.onchange=()=>confirm.disabled=!checkbox.checked;
  });
}
function adminUserLink(id,name){return `<button class="text-btn user-detail-link" data-user-detail="${id}">${escapeHTML(name)}</button>`;}
function monthlyPayments(){
  paymentsMonth=paymentsMonth||state.today.slice(0,7);
  const realWebpay=(adminData.payments||[]).filter(p=>p.environment==='production'&&p.state==='approved').map(p=>({...p,method:'webpay',reference:p.buy_order,received_on:new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Santiago'}).format(new Date(p.approved_at||p.created_at))}));
  const records=[...(adminData.manual_credits||[]),...realWebpay].filter(item=>item.received_on?.slice(0,7)===paymentsMonth&&['cash','transfer','webpay'].includes(item.method));
  const sum=method=>records.filter(item=>!method||item.method===method).reduce((total,item)=>total+item.amount,0);
  return `<section class="monthly-payments" aria-label="Ingresos mensuales"><div class="row spread"><h2>Pagos recibidos por mes</h2><label>Mes de recepción<input id="payments-month" type="month" value="${paymentsMonth}" max="${state.today.slice(0,7)}"></label></div><div class="grid stats"><div class="stat"><div class="stat-top">Total recibido</div><div class="value" data-monthly-total>${money(sum())}</div><small>${records.length} pagos registrados · CLP</small></div><div class="stat"><div class="stat-top">Efectivo</div><div class="value">${money(sum('cash'))}</div></div><div class="stat"><div class="stat-top">Transferencias</div><div class="value">${money(sum('transfer'))}</div></div></div><p class="data-note">Montos brutos: efectivo y transferencia según recepción registrada; Webpay según fecha de aprobación. Se excluyen pagos simulados, Webpay de pruebas, solicitudes pendientes, saldos migrados y cortesías.</p>${records.length?`<div class="table-wrap"><table><thead><tr><th>Recepción</th><th>Profesional</th><th>Medio</th><th>Monto</th><th>Referencia</th></tr></thead><tbody>${records.map(item=>`<tr><td>${escapeHTML(item.received_on)}</td><td>${adminUserLink(item.user_id,item.name)}</td><td>${item.method==='webpay'?'Webpay':manualMethods[item.method]}</td><td>${money(item.amount)}</td><td>${escapeHTML(item.reference)}</td></tr>`).join('')}</tbody></table></div>`:empty('No hay pagos recibidos registrados para este mes.')}</section>`;
}
const signedSessions=value=>value>0?'+'+value:String(value);
function archiveToggle(detail=false){return `<button class="btn ghost small" id="${detail?'detail-toggle-archived':'toggle-archived'}">${showArchivedPlans?'Ocultar planes archivados':'Mostrar planes archivados'}</button>`;}
function planActions(plan){
  if(plan.archived_at)return `<span class="pill neutral">Archivado</span><small>${escapeHTML(plan.archive_reason)}</small>`;
  return plan.status==='paid'?`<button class="btn ghost small" data-adjust-plan="${plan.id}">Ajustar saldo</button>`:['pending','rejected'].includes(plan.status)?`<button class="btn danger small" data-archive-plan="${plan.id}">Eliminar de la vista</button>`:'—';
}
function archiveHistory(uid){
  const entries=(adminData.package_archive_events||[]).filter(entry=>entry.user_id===uid);
  return entries.length?`<h3>Historial de planes archivados</h3>${entries.map(entry=>`<p>Plan #${entry.package_id} · ${dateText(entry.created_at)}<br>${escapeHTML(entry.reason)}<br><small>${escapeHTML(entry.operator_name)}</small></p>`).join('')}`:'';
}
function adjustmentHistory(uid){
  const entries=(adminData.session_adjustments||[]).filter(entry=>entry.user_id===uid);
  return `<h3>Historial de ajustes de saldo</h3>${entries.length?`<div class="table-wrap"><table><thead><tr><th>Fecha / plan</th><th>Ajuste</th><th>Saldo anterior</th><th>Saldo final</th><th>Motivo / responsable</th></tr></thead><tbody>${entries.map(entry=>`<tr><td>${dateText(entry.created_at)} · ${hourText(entry.created_at)}<small>Plan #${entry.package_id}</small></td><td>${signedSessions(entry.delta)}</td><td>${entry.before_balance}</td><td>${entry.after_balance}</td><td>${escapeHTML(entry.reason)}<small>${escapeHTML(entry.operator_name)}</small></td></tr>`).join('')}</tbody></table></div>`:'<p>No hay ajustes administrativos registrados.</p>'}`;
}
function bindPlanActions(returnUser=null){
  const scope=returnUser?modal:document;
  scope.querySelectorAll('[data-adjust-plan]').forEach(button=>button.onclick=()=>{
    const plan=adminData.packages.find(plan=>plan.id===Number(button.dataset.adjustPlan));if(!plan)return;
    const user=adminData.users.find(user=>user.id===plan.user_id),key=crypto.randomUUID();modal.close();
    showModal('Ajustar saldo de sesiones',`<p><strong>${escapeHTML(user.name)}</strong> · ${escapeHTML(user.email)}<br>Plan #${plan.id} · saldo disponible: <strong>${plan.remaining} sesiones</strong></p><form id="balance-adjustment-form" class="form-stack"><label>Tipo de ajuste<select name="direction"><option value="subtract">Descontar sesiones</option><option value="add">Sumar sesiones</option></select></label><label>Sesiones a ajustar<input name="sessions" type="number" min="1" max="200" step="1" required></label><label>Motivo del ajuste (opcional)<textarea name="reason" maxlength="500" placeholder="Ej. 3 atenciones realizadas fuera de la plataforma durante la migración"></textarea></label></form><p class="modal-detail" id="adjustment-preview" aria-live="polite">Elige cuántas sesiones sumar o descontar.</p><p class="data-note">Se guardará un historial con el saldo anterior, el saldo final y tu usuario. No se registra un pago ni se modifican las reservas.</p>`,'Guardar ajuste',async()=>{
      const form=document.querySelector('#balance-adjustment-form');if(!form.reportValidity())throw new Error('Completa la cantidad de sesiones.');
      const delta=Number(form.elements.sessions.value)*(form.elements.direction.value==='add'?1:-1);
      await api('admin/session-adjustments',{user_id:plan.user_id,package_id:plan.id,delta,expected_remaining:plan.remaining,reason:form.elements.reason.value,request_key:key});toast('Saldo ajustado y registrado en el historial.');
    },()=>openUserDetail(returnUser||plan.user_id));
    const form=document.querySelector('#balance-adjustment-form');form.oninput=form.onchange=()=>{const amount=Number(form.elements.sessions.value),delta=amount*(form.elements.direction.value==='add'?1:-1);document.querySelector('#adjustment-preview').textContent=`Saldo anterior: ${plan.remaining} · Ajuste: ${signedSessions(delta)} · Saldo final: ${plan.remaining+delta} sesiones`;};
  });
  scope.querySelectorAll('[data-archive-plan]').forEach(button=>button.onclick=()=>{
    const plan=adminData.packages.find(plan=>plan.id===Number(button.dataset.archivePlan));if(!plan)return;modal.close();
    showModal('Eliminar plan de la vista',`<p>Plan #${plan.id} de <strong>${escapeHTML(plan.name)}</strong>.</p><p>Se archivará y dejará de aparecer en las listas habituales de administración y de la cuenta profesional. Su historial se conserva en “Mostrar planes archivados”.</p><form id="archive-plan-form"><label>Motivo (opcional)<textarea name="reason" maxlength="500" placeholder="Ej. Compra pendiente abandonada"></textarea></label></form><p class="data-note">Esto no cancela ni devuelve un pago en Transbank. Si posteriormente se verifica un pago aprobado, el plan volverá a mostrarse y sus sesiones se acreditarán.</p>`,'Eliminar de la vista',async()=>{const form=document.querySelector('#archive-plan-form');if(!form.reportValidity())throw new Error('Revisa los datos del plan.');await api('admin/packages/archive',{package_id:plan.id,reason:form.elements.reason.value});toast('Plan archivado. El historial se conserva.');},returnUser?()=>openUserDetail(returnUser):null);
  });
}
function personalApprovalsAdmin(){
  const users=adminData.users.filter(u=>u.role==='professional'&&u.active);
  return `<section class="card spaced-card"><h2>Aprobación personal de administración</h2><p class="section-intro">Para colegas que conoces personalmente. Esta aprobación sustituye la presentación del carnet y del título; no agrega horas ni confirma pagos o correos.</p><form id="personal-approval-form" class="form-stack"><label>Profesional a aprobar<select name="user_id" required><option value="">Selecciona una cuenta</option>${users.filter(u=>!u.personally_approved).map(u=>`<option value="${u.id}">${escapeHTML(u.name)} · ${escapeHTML(u.email)}</option>`).join('')}</select></label><button class="btn" type="submit">Aprobar personalmente</button></form><h3>Cuentas aprobadas personalmente</h3>${users.filter(u=>u.personally_approved).map(u=>{const record=(adminData.personal_approvals||[]).find(p=>p.user_id===u.id&&!p.revoked_at);return `<div class="booking-item"><div>${adminUserLink(u.id,u.name)}<p>${escapeHTML(u.email)}</p><small>${record?`${dateText(record.created_at)} · ${escapeHTML(record.operator_name)}`:''}</small></div><button class="btn ghost small" data-revoke-personal="${u.id}">Retirar aprobación personal</button></div>`;}).join('')||'<p>Sin aprobaciones personales activas.</p>'}</section>`;
}
function confirmPersonalApproval(uid,revoke=false){
  const user=adminData.users.find(u=>u.id===uid);if(!user)return;
  showModal(revoke?'Retirar aprobación personal':'Aprobar personalmente',`<p><strong>${escapeHTML(user.name)}</strong><br>${escapeHTML(user.email)}</p><p>${revoke?'Volverán a aplicarse los requisitos de documentos o validación anterior. Sus reservas y saldo se conservan.':'Confirmas que conoces personalmente a esta persona y autorizas que reserve sin presentar carnet ni certificado de título. Se mantienen correo confirmado, saldo y normas.'}</p><label class="identity-confirmation"><input id="personal-approval-confirmed" type="checkbox">${revoke?'Confirmo que deseo retirar esta aprobación.':'Conozco personalmente a esta persona y autorizo esta excepción.'}</label>`,revoke?'Retirar aprobación':'Confirmar aprobación personal',async()=>{if(!document.querySelector('#personal-approval-confirmed').checked)throw new Error('Confirma la decisión antes de continuar.');await api('admin',{action:'personal_approval',id:uid,decision:revoke?'revoked':'approved',confirmed:true});toast(revoke?'Aprobación personal retirada.':'Cuenta aprobada personalmente por administración.');});
}
function bindAdminDetails(){
  const personalForm=document.querySelector('#personal-approval-form');if(personalForm)personalForm.onsubmit=event=>{event.preventDefault();confirmPersonalApproval(Number(personalForm.elements.user_id.value));};
  document.querySelectorAll('[data-revoke-personal]').forEach(b=>b.onclick=()=>confirmPersonalApproval(Number(b.dataset.revokePersonal),true));
  const month=document.querySelector('#payments-month');if(month)month.onchange=()=>{if(/^\d{4}-\d{2}$/.test(month.value)){paymentsMonth=month.value;render().catch(error=>toast(error.message));}};
  document.querySelectorAll('[data-user-detail]').forEach(button=>button.onclick=()=>openUserDetail(Number(button.dataset.userDetail)));
  bindPlanActions();
  const toggle=document.querySelector('#toggle-archived');if(toggle)toggle.onclick=()=>{showArchivedPlans=!showArchivedPlans;render().catch(error=>toast(error.message));};
}
function openUserDetail(userId){
    const user=adminData.users.find(user=>user.id===userId);if(!user)return;
    const plans=adminData.packages.filter(plan=>plan.user_id===user.id&&(showArchivedPlans||!plan.archived_at)),paid=plans.filter(plan=>plan.status==='paid');
    const bookings=adminData.bookings.filter(booking=>booking.user_id===user.id).sort((a,b)=>b.start.localeCompare(a.start));
    const confirmed=bookings.filter(booking=>booking.status==='confirmed'),time=Date.now();
    const completed=confirmed.filter(booking=>new Date(booking.start).getTime()+55*60000<=time).length;
    const reserved=confirmed.length-completed;
    const remaining=paid.reduce((total,plan)=>total+plan.remaining,0),credited=paid.reduce((total,plan)=>total+plan.hours,0);
    const docs=adminData.documents.filter(doc=>doc.user_id===user.id),credits=(adminData.manual_credits||[]).filter(item=>item.user_id===user.id);
    modal.innerHTML=`<h2 id="modal-title">Ficha de ${escapeHTML(user.name)}</h2><p>${escapeHTML(user.email)} · ${escapeHTML(user.specialty)||'Sin especialidad registrada'}</p><p>Cuenta ${user.active?'activa':'inactiva'} · Correo ${user.email_verified?'confirmado':'pendiente'} · ${user.personally_approved?'Aprobado personalmente por administración':`Documentación ${user.verified?'validada':'pendiente'}`}</p><div class="grid user-balance-stats"><div class="stat"><div class="stat-top">Horas acreditadas</div><div class="value">${credited}</div></div><div class="stat"><div class="stat-top">Atenciones realizadas</div><div class="value">${completed}</div></div><div class="stat"><div class="stat-top">Reservadas / en curso</div><div class="value">${reserved}</div></div><div class="stat"><div class="stat-top">Saldo disponible</div><div class="value" data-user-balance>${remaining}</div></div></div><p class="data-note">Cada hora equivale a una sesión de 55 minutos. Las horas acreditadas incluyen los saldos migrados; el consumo anterior a la migración no se reconstruye. Las reservas canceladas no cuentan como uso.</p><h3>Planes y saldos</h3><p class="data-note">Los ajustes suman o descuentan saldo sin registrar pagos ni cambiar reservas.</p>${archiveToggle(true)}<div class="table-wrap"><table><thead><tr><th>Plan / origen</th><th>Estado</th><th>Acreditadas</th><th>Ajustes</th><th>Consumidas o reservadas</th><th>Disponibles</th><th>Monto</th><th>Acciones</th></tr></thead><tbody>${plans.map(plan=>`<tr data-detail-plan="${plan.id}"><td>#${plan.id} · ${packageSource(plan)}<small>${dateText(plan.created_at)}</small><small>${plan.rules_accepted_at?'Normas aceptadas':'Sin aceptación registrada'}</small></td><td>${pill(plan.status)}</td><td>${plan.status==='paid'?plan.hours:0}</td><td>${signedSessions(plan.balance_adjustment||0)}</td><td>${plan.status==='paid'?plan.hours+(plan.balance_adjustment||0)-plan.remaining:0}</td><td>${plan.status==='paid'?plan.remaining:0}</td><td>${money(plan.price)}</td><td>${planActions(plan)}</td></tr>`).join('')||'<tr><td colspan="8">Sin planes registrados.</td></tr>'}</tbody></table></div>${adjustmentHistory(user.id)}${archiveHistory(user.id)}<h3>Historial de reservas</h3><div class="table-wrap"><table><thead><tr><th>Reserva</th><th>Espacio</th><th>Fecha / horario</th><th>Estado</th><th>Modificaciones</th></tr></thead><tbody>${bookings.map(booking=>`<tr><td>#${booking.id}</td><td>${rooms[booking.box-1]}</td><td>${dateText(booking.start)}<small>${sessionRange(booking.start)}</small></td><td>${pill(booking.status)}</td><td>${booking.modifications} / 1</td></tr>`).join('')||'<tr><td colspan="5">Sin reservas registradas.</td></tr>'}</tbody></table></div><h3>Pagos externos y cortesías</h3>${credits.map(item=>`<p>${escapeHTML(item.received_on)} · ${manualMethods[item.method]} · ${money(item.amount)} · ${item.hours} sesiones<br>Referencia: ${escapeHTML(item.reference)}<br>${escapeHTML(item.note)}<br><small>Registrado por ${escapeHTML(item.operator_name)} · ${dateText(item.created_at)}</small></p>`).join('')||'<p>Sin cargas manuales.</p>'}<h3>Documentación</h3>${docs.map(doc=>`<p>${doc.kind==='identity'?'Carnet':doc.kind==='degree'?'Certificado de título':'Acreditación anterior'} · ${pill(doc.status)}<br>${documentLinks(doc)}</p>`).join('')||'<p>Sin documentos adjuntos.</p>'}<div class="modal-actions"><button class="btn" id="close-user-detail">Cerrar ficha</button></div>`;
    modal.classList.add('user-detail-modal');modal.showModal();document.querySelector('#close-user-detail').onclick=()=>modal.close();
    bindPlanActions(user.id);const toggle=document.querySelector('#detail-toggle-archived');if(toggle)toggle.onclick=()=>{showArchivedPlans=!showArchivedPlans;modal.close();openUserDetail(user.id);};
}

function administration() {
  const pending=adminData.packages.filter(p=>p.status==='pending'&&!p.archived_at);
  let body='';
  if(adminTab==='discounts') body=discountsAdmin();
  if(adminTab==='daily_contacts') body=dailyContactsAdmin();
  if(adminTab==='email') body=emailAdmin();
  if(adminTab==='transbank') body=transbankAdmin();
  if(adminTab==='manual_credits') body=manualCreditsAdmin();
  if(adminTab==='migration') body=migrationAdmin();
  if(adminTab==='payments') body=`<h2>Pagos y paquetes</h2><p class="section-intro">Consulta los planes y su origen. Para pagos recibidos fuera de Webpay, utiliza la pestaña Cargar horas. Las solicitudes simuladas se identifican por separado.</p><div class="table-wrap"><table class="admin-table"><thead><tr><th>Profesional</th><th>Paquete</th><th>Monto del plan</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>${adminData.packages.filter(p=>showArchivedPlans||!p.archived_at).map(p=>`<tr><td>${adminUserLink(p.user_id,p.name)}<small>Titular #${p.user_id}</small></td><td>#${p.id} · ${p.hours} h<small>${p.remaining} h restantes · ${packageSource(p)}</small>${p.rules_accepted_at?`<small>Normas aceptadas: ${dateText(p.rules_accepted_at)} · ${hourText(p.rules_accepted_at)}</small>`:'<small>Sin aceptación registrada (plan anterior)</small>'}</td><td>${money(p.price)}${p.discount_code?`<small>Código ${escapeHTML(p.discount_code)} · ${p.discount_percent}% de descuento</small>`:''}</td><td>${pill(p.status)}</td><td>${p.status==='pending'&&!p.archived_at&&p.source==='simulated'?`<div class="row"><button class="btn small" data-admin="payment" data-id="${p.id}" data-decision="paid">Confirmar pago simulado</button><button class="btn ghost small" data-admin="payment" data-id="${p.id}" data-decision="rejected">Rechazar</button></div>`:''}${planActions(p)}</td></tr>`).join('')}</tbody></table></div>`;
  if(adminTab==='payments') body=monthlyPayments()+archiveToggle()+body+paymentList(true);
  if(adminTab==='documents') body=`<h2>Revisión de documentación</h2><p class="section-intro">Descarga todos los adjuntos del último carnet y revisa ambos lados y su correspondencia con el titular antes de aprobar. Carnet y certificado de título se revisan y aprueban por separado. La revisión habitual requiere ambos documentos; la aprobación personal de administración se gestiona por separado.</p>${adminData.documents.length?`<div class="table-wrap"><table class="admin-table"><thead><tr><th>Profesional</th><th>Documento</th><th>Estado</th><th>Revisión</th></tr></thead><tbody>${adminData.documents.map(d=>{const latest=!adminData.documents.some(x=>x.user_id===d.user_id&&x.kind===d.kind&&x.id>d.id);return `<tr><td>${adminUserLink(d.user_id,d.name)}<small>Titular #${d.user_id}</small></td><td>${documentLinks(d)}<small>${dateText(d.created_at)} · ${d.kind==='identity'?'Carnet':d.kind==='degree'?'Certificado de título':'Acreditación anterior'}</small></td><td>${pill(d.status)}${d.note?`<small>${escapeHTML(d.note)}</small>`:''}</td><td>${latest?`<div class="row"><button class="btn small" data-admin="document" data-id="${d.id}" data-decision="approved">Aprobar</button><button class="btn ghost small" data-admin="document" data-id="${d.id}" data-decision="rejected">Rechazar</button></div>`:'Documento anterior'}</td></tr>`;}).join('')}</tbody></table></div>`:empty('No hay documentos para revisar.')}`;
  if(adminTab==='documents') body=personalApprovalsAdmin()+body;
  if(adminTab==='users') body=`<h2>Profesionales y usuarios</h2><p class="section-intro">Desactivar una cuenta cierra sus sesiones e impide nuevas operaciones. Sus reservas existentes se conservan.</p><div class="table-wrap"><table class="admin-table"><thead><tr><th>Nombre</th><th>Correo</th><th>Especialidad</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>${adminData.users.map(u=>`<tr><td>${adminUserLink(u.id,u.name)}<small>${u.role==='admin'?'Administrador':'Profesional'}</small></td><td>${escapeHTML(u.email)}</td><td>${escapeHTML(u.specialty)||'—'}</td><td><span class="pill ${u.active?'approved':'rejected'}">${u.active?'Activa':'Inactiva'}</span><small>${u.personally_approved?'Aprobado personalmente por administración':u.verified?'Verificada':'Por verificar'}</small></td><td>${u.role==='professional'?`<button class="btn ghost small" data-admin="user" data-id="${u.id}" data-active="${!u.active}">${u.active?'Desactivar':'Activar'}</button>`:'—'}</td></tr>`).join('')}</tbody></table></div>`;
  if(adminTab==='bookings') body=`<h2>Agenda general</h2><p class="section-intro">Cancelar una reserva futura libera el box y devuelve 1 hora al paquete original, una sola vez.</p>${adminData.bookings.length?`<div class="table-wrap"><table class="admin-table"><thead><tr><th>Profesional</th><th>Box / fecha</th><th>Estado</th><th>Modificaciones</th><th>Acciones</th></tr></thead><tbody>${adminData.bookings.map(b=>`<tr><td>${adminUserLink(b.user_id,b.name)}<small>Reserva #${b.id}</small></td><td>${rooms[b.box-1]}<small>${dateText(b.start)} · ${sessionRange(b.start)}</small></td><td>${pill(b.status)}</td><td>${b.modifications} / 1</td><td>${b.status==='confirmed'&&new Date(b.start)>new Date()?`<button class="btn danger small" data-admin="cancel" data-id="${b.id}">Cancelar y devolver hora</button>`:'—'}</td></tr>`).join('')}</tbody></table></div>`:empty('No hay reservas registradas.')}`;
  if(adminTab==='audit') body=`<h2>Registro de operaciones</h2><p class="section-intro">Últimas 30 acciones realizadas en el prototipo.</p>${adminData.audit.map(a=>`<div class="audit-item"><strong>${escapeHTML(a.name||'Sistema')}</strong><p>${escapeHTML(a.action)} · registro #${escapeHTML(a.target)}</p><small>${dateText(a.created_at)} · ${hourText(a.created_at)}</small></div>`).join('')||empty('Aún no hay operaciones.')}`;
  return head('Administración','La operación de Valhalla, en un solo lugar. Actualización automática cada 30 segundos; se pausa mientras editas o revisas una confirmación.','<button class="btn" data-go="availability">Ver agenda por box →</button>')+`<div class="grid stats"><div class="stat"><div class="stat-top">Profesionales</div><div class="value">${adminData.users.filter(u=>u.role==='professional').length}</div><small>Cuentas registradas</small></div><div class="stat"><div class="stat-top">Pagos pendientes</div><div class="value">${pending.length}</div><small>Operaciones simuladas</small></div><div class="stat"><div class="stat-top">Reservas próximas</div><div class="value">${adminData.bookings.filter(b=>b.status==='confirmed'&&new Date(b.start)>new Date()).length}</div><small>Entre los tres boxes</small></div></div><div class="tabs" role="group" aria-label="Secciones de administración">${[['payments','Pagos'],['manual_credits','Cargar horas'],['discounts','Descuentos'],['transbank','Transbank'],['documents','Documentos'],['users','Usuarios'],['email','Correos'],['daily_contacts','Correos del día'],['migration','Migración'],['bookings','Reservas'],['audit','Actividad']].map(([id,label])=>`<button class="tab ${adminTab===id?'active':''}" data-tab="${id}" aria-pressed="${adminTab===id}">${label}</button>`).join('')}</div><section class="card">${body}</section>`;
}

async function render(guard=null) {
  const generation=++renderGeneration;
  if(adminMode()&&view==='admin'&&adminTab==='daily_contacts'){
    contactsDate=contactsDate||state.today;const requestedDate=contactsDate;
    const report=await api('admin/daily-contacts?date='+encodeURIComponent(requestedDate));
    if(generation!==renderGeneration||!adminMode()||adminTab!=='daily_contacts'||contactsDate!==requestedDate)return;
    dailyContacts=report;
  }
  const html=view==='availability'?await availability():view==='packages'?packages():view==='bookings'?bookings():view==='documents'?documents():view==='profile'?profile():view==='admin'?administration():overview();
  if(generation!==renderGeneration||(guard&&!guard()))return;
  shell();document.querySelector('#content').innerHTML=html;liveFormDirty=false;liveRefreshPending=false;bind();
}
function canRefreshLive(){
  return state&&!document.hidden&&!modal.open&&!liveFormDirty&&!modifying&&!selectedBookings.length&&
    !document.activeElement?.matches('input,textarea,select,[contenteditable="true"]')&&
    !document.querySelector('#content form button:disabled');
}
async function refreshLiveData(){
  if(liveRefreshBusy||!canRefreshLive())return;
  liveRefreshBusy=true;
  const previousState=state,previousAdmin=adminData,content=document.querySelector('#content'),revision=liveEditRevision;
  const context=[view,adminTab,activeProfile,selectedDate,contactsDate,paymentsMonth].join('|');
  const safe=()=>state===previousState&&content===document.querySelector('#content')&&liveEditRevision===revision&&canRefreshLive()&&[view,adminTab,activeProfile,selectedDate,contactsDate,paymentsMonth].join('|')===context;
  try{
    const fresh=await api('me');if(!safe())return;
    const freshAdmin=adminMode()?await api('admin'):null;if(!safe())return;
    const changed=liveRefreshPending||JSON.stringify(fresh)!==JSON.stringify(previousState)||(freshAdmin&&JSON.stringify(freshAdmin)!==JSON.stringify(previousAdmin));
    if(!changed&&view!=='availability'&&!(adminMode()&&adminTab==='daily_contacts'))return;
    state=fresh;liveRefreshPending=true;if(freshAdmin)adminData=freshAdmin;
    const afterFetchSafe=()=>state===fresh&&content===document.querySelector('#content')&&liveEditRevision===revision&&canRefreshLive()&&[view,adminTab,activeProfile,selectedDate,contactsDate,paymentsMonth].join('|')===context;
    const position={x:scrollX,y:scrollY,tables:[...document.querySelectorAll('.table-wrap')].map(table=>table.scrollLeft),sidebar:document.querySelector('.sidebar')?.scrollTop||0};
    await render(afterFetchSafe);
    if(state===fresh&&document.querySelector('#content')!==content){window.scrollTo(position.x,position.y);document.querySelectorAll('.table-wrap').forEach((table,index)=>table.scrollLeft=position.tables[index]||0);const sidebar=document.querySelector('.sidebar');if(sidebar)sidebar.scrollTop=position.sidebar;}
  }catch{}finally{liveRefreshBusy=false;}
}
setInterval(refreshLiveData,30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshLiveData();});
window.addEventListener('focus',refreshLiveData);
modal.addEventListener('close',()=>{if(!modal.open)modal.classList.remove('user-detail-modal');});
function showBookingModal(title,content,label,action){
  const rules=state.purchase_rules;
  if(!rules?.items?.length){toast('No se pudieron cargar las normas. Actualiza la página antes de reservar.');return;}
  const acceptance={accepted:true,version:rules.version};
  showModal('Normas para tu reserva',`<p>Lee y acepta las normas antes de revisar la confirmación. Se aplican a cada hora reservada, también a las horas de cortesía.</p><section class="purchase-rules"><ol>${rules.items.map(item=>`<li><strong>${escapeHTML(item.title)}</strong><p>${escapeHTML(item.text)}</p></li>`).join('')}</ol></section><label class="identity-confirmation"><input id="booking-rules-accepted" type="checkbox">He leído y acepto las 12 normas para esta reserva o selección de horas.</label>`,'Continuar a la confirmación',async()=>{
    if(!document.querySelector('#booking-rules-accepted').checked)throw new Error('Debes aceptar las normas para continuar.');
    modal.close();showModal(title,content,label,()=>action(acceptance));
    const later=document.querySelector('#book-rest-later');if(later)later.onchange=()=>document.querySelector('#modal-confirm').disabled=!later.checked;
    return false;
  });
  const checkbox=document.querySelector('#booking-rules-accepted'),button=document.querySelector('#modal-confirm');button.disabled=true;checkbox.onchange=()=>button.disabled=!checkbox.checked;
}
function showModal(title, content, confirmLabel, action, afterRefresh=null) {
  modal.classList.remove('user-detail-modal');
  modal.innerHTML=`<h2 id="modal-title">${title}</h2>${content}<p class="error modal-error" id="modal-error" role="alert"></p><div class="modal-actions"><button class="btn ghost" id="modal-close">Volver</button><button class="btn" id="modal-confirm">${confirmLabel}</button></div>`;
  modal.showModal();document.querySelector('#modal-close').onclick=()=>modal.close();
  document.querySelector('#modal-confirm').onclick=async event=>{
    const button=event.currentTarget;button.disabled=true;
    try {if(await action()===false)return;modal.close();await reload();if(afterRefresh)afterRefresh();}
    catch(e){document.querySelector('#modal-error').textContent=e.message;}
    finally{button.disabled=false;}
  };
}
function bind() {
  bindDiscounts();bindMigration();bindPayments();bindEmailAdmin();bindDailyContacts();bindTransbankAdmin();bindBatchBookings();bindManualCredits();bindManualRules();bindAdminDetails();
  const refreshAgenda=document.querySelector('#refresh-agenda');if(refreshAgenda)refreshAgenda.onclick=()=>render().catch(e=>toast(e.message));
  bindGallery();
  const linkGoogle=document.querySelector('#link-google');if(linkGoogle)linkGoogle.onclick=()=>continueWithGoogle(true);
  const profileForm=document.querySelector('#profile-form');if(profileForm)profileForm.onsubmit=async event=>{event.preventDefault();const button=profileForm.querySelector('button[type=submit]');button.disabled=true;try{await api('profile',Object.fromEntries(new FormData(profileForm)));await reload();toast('Perfil profesional actualizado.');}catch(e){document.querySelector('#profile-error').textContent=e.message;}finally{button.disabled=false;}};
  document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>{view=b.dataset.go;modifying=null;render().catch(e=>toast(e.message));});
  document.querySelectorAll('[data-modify]').forEach(b=>b.onclick=()=>{multiBooking=false;selectedBookings=[];modifying=state.bookings.find(x=>x.id===Number(b.dataset.modify));selectedDate=new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Santiago'}).format(new Date(modifying.start));view='availability';render().catch(e=>toast(e.message));});
  const abort=document.querySelector('#abort-change');if(abort)abort.onclick=()=>{modifying=null;view='bookings';render().catch(e=>toast(e.message));};
  const date=document.querySelector('#schedule-date');if(date)date.onchange=()=>{if(date.value){selectedDate=date.value;render().catch(e=>toast(e.message));}};
  document.querySelectorAll('[data-slot]').forEach(b=>b.onclick=()=>{
    if(adminMode()){toast('La agenda administrativa es de consulta. Las reservas se crean desde la cuenta profesional.');return;}
    if(!state.user.email_verified){toast('Confirma tu correo electrónico antes de reservar.');return;}
    if(!state.user.verified){toast('Primero envía tu carnet y certificado de título y espera la aprobación de ambos.');return;}
    if(!modifying && pendingBalance()===0){toast('Necesitas sesiones de un plan con pago confirmado.');return;}
    const box=Number(b.dataset.box),start=b.dataset.slot, current=modifying;
    if(multiBooking&&!current){
      const index=selectedBookings.findIndex(slot=>slot.box===box&&slot.start===start);
      if(index>=0)selectedBookings.splice(index,1);
      else{
        if(selectedBookings.length>=Math.min(pendingBalance(),200)){toast('No puedes seleccionar más horas que tu saldo disponible (máximo 200 por operación).');return;}
        if(!canBookParallel()&&(selectedBookings.some(slot=>slot.start===start)||state.bookings.some(booking=>booking.start===start&&booking.status==='confirmed'))){toast('Ya tienes esa hora seleccionada o reservada. No puedes ocupar dos boxes a la vez.');return;}
        selectedBookings.push({box,start});
      }
      render().catch(e=>toast(e.message));return;
    }

    showBookingModal(current?'Confirma tu único cambio':'Tu próxima atención',`<p>${current?'Se conservará el saldo y se utilizará tu única modificación.':'Se descontará 1 sesión de tu plan confirmado más antiguo con saldo.'}</p><div class="modal-detail"><strong>${rooms[box-1]}</strong><br>${dateText(start)} · ${sessionRange(start)}<br>Atención: 55 minutos · 5 minutos de margen</div><p>${current?'El horario original se libera solo si el cambio se confirma.':'La reserva y su saldo pertenecen exclusivamente a tu cuenta.'}</p>`,current?'Confirmar cambio':'Confirmar reserva',async rules_acceptance=>{
      const result=await api(current?'bookings/'+current.id:'bookings',{box,start,rules_acceptance});confirmedBookingIds=[current?current.id:result.id];confirmationModified=Boolean(current);modifying=null;view='bookings';toast(current?'Reserva modificada. Has utilizado tu único cambio.':'Reserva confirmada. ¡Tu espacio te espera!');
    });
  });
  document.querySelectorAll('[data-buy]').forEach(button=>button.onclick=()=>purchaseModal(state.catalog.find(p=>p.id===button.dataset.buy)));
  document.querySelectorAll('[data-document-kind]').forEach(upload=>upload.onsubmit=async event=>{
    event.preventDefault();const selected=Array.from(upload.querySelector('input[type=file]').files),button=upload.querySelector('button');
    if(!selected.length||selected.length>5||selected.some(file=>!file.size)||selected.reduce((sum,file)=>sum+file.size,0)>10*1024*1024){upload.querySelector('[data-upload-error]').textContent='Selecciona de 1 a 5 archivos, con un total de hasta 10 MB.';return;}
    button.disabled=true;
    try {
      const files=await Promise.all(selected.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('No se pudo leer el archivo.'));reader.onload=()=>resolve({filename:file.name,content:String(reader.result).split(',')[1]});reader.readAsDataURL(file);})));
      await api('documents',{files,kind:upload.dataset.documentKind});toast('Documento enviado a revisión.');await reload();
    }catch(e){upload.querySelector('[data-upload-error]').textContent=e.message;}
    finally{button.disabled=false;}
  });
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{adminTab=b.dataset.tab;render().catch(e=>toast(e.message));});
  document.querySelectorAll('[data-admin]').forEach(b=>b.onclick=()=>{
    const action=b.dataset.admin,decision=b.dataset.decision,active=b.dataset.active==='true';
    const identityReview=action==='document'&&decision==='approved'&&adminData.documents.find(d=>d.id===Number(b.dataset.id))?.kind==='identity';
    const title=action==='payment'?(decision==='paid'?'Confirmar pago simulado':'Rechazar solicitud'):action==='document'?(decision==='approved'?'Aprobar documentación':'Rechazar documentación'):action==='user'?(active?'Activar profesional':'Desactivar profesional'):'Cancelar reserva';
    let copy=action==='payment'?'No se realizará ningún cobro ni devolución real.':action==='document'?'Confirma que revisaste el documento más reciente del profesional.':action==='user'?'Las reservas existentes se conservan. Desactivar cierra las sesiones del profesional.':'Se libera el horario y se devuelve 1 hora al paquete del titular. Esta acción no se puede deshacer.';
    showModal(title,`<p>${copy}</p>${identityReview?'<label class="identity-confirmation"><input type="checkbox" id="identity-confirmed">He revisado ambos lados del carnet y corresponden al titular de esta cuenta.</label>':''}${action==='document'?`<label class="review-label">Observación (opcional)<textarea id="review-note" rows="3" maxlength="500"></textarea></label>`:''}`,'Confirmar',async()=>{await api('admin',{action,id:Number(b.dataset.id),decision,active,identity_confirmed:document.querySelector('#identity-confirmed')?.checked===true,note:document.querySelector('#review-note')?.value||''});toast('Operación registrada.');});
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
  try{const data=await api('me');state=data;selectedDate=data.earliest_booking_date||data.today;if(paymentReturn){view='packages';toast('Consulta el estado actualizado de tu compra en Comprar horas.');}if(googleResult==='profile_required')view='profile';if(data.user.role==='admin'){adminData=await api('admin');view='admin';selectedDate=data.today;}applyBookingsDeepLink();await render();}
  catch{auth(false,googleResult==='admin_password');}
  const googleMessages={success:'Ingresaste con Google.',profile_required:'Completa tu profesión o especialidad en Mi perfil. Para reservar también necesitas tu identidad validada y saldo.',link_required:'Ya existe una cuenta con ese correo. Ingresa con tu contraseña y selecciona Mi perfil → Vincular Google. Si la olvidaste, puedes recuperarla.',cancelled:'Se canceló el acceso con Google. Puedes volver a intentarlo.',admin_password:'La administración requiere tu contraseña privada. Google está disponible para cuentas profesionales.',error:'No se pudo completar el acceso con Google. Vuelve a intentarlo desde el inicio.'};
  if(googleResult&&googleMessages[googleResult])toast(googleMessages[googleResult]);
}
// Keep contact available across login, registration and account views.
const whatsappContact=document.createElement('a');
whatsappContact.className='whatsapp-contact';
whatsappContact.href='https://wa.me/56936680947?text='+encodeURIComponent('Hola, quisiera hacer una consulta sobre Valhalla Office.');
whatsappContact.target='_blank';
whatsappContact.rel='noopener noreferrer';
whatsappContact.setAttribute('aria-label','Hablar con Valhalla Office por WhatsApp (abre una nueva pestaña)');
whatsappContact.innerHTML=`<svg viewBox="0 0 32 32" width="26" height="26" fill="currentColor" aria-hidden="true"><path d="M16 3a13 13 0 0 0-11.2 19.6L3 29l6.6-1.7A13 13 0 1 0 16 3zm0 2.4a10.6 10.6 0 1 1-5.7 19.5l-.4-.3-3.5.9.9-3.4-.3-.4A10.6 10.6 0 0 1 16 5.4zm-4.3 5c-.3 0-.6.1-.8.4-.5.5-1.1 1.2-1.1 2.5s1 2.6 1.2 2.8c.1.2 2.1 3.3 5.2 4.5 2.6 1 3.1.8 3.7.7.6-.1 1.9-.8 2.1-1.6.3-.8.3-1.4.2-1.6-.1-.1-.3-.2-.7-.4l-2.1-1c-.3-.1-.5-.2-.7.2l-1 1.2c-.2.2-.4.3-.7.1-1-.4-1.9-.9-2.6-1.6-.7-.7-1.2-1.5-1.4-1.8-.1-.3 0-.4.2-.6l.6-.7.3-.6c.1-.2 0-.4 0-.6l-1-2.2c-.2-.6-.5-.6-.8-.6z"/></svg><span>¿Hablamos? <strong>WhatsApp</strong></span>`;
document.body.append(whatsappContact);
startApp();
