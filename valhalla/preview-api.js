// In-memory adapter for the standalone ChatGPT preview. No network or real payments.
const previewDB = {
  discounts:[], users: [],
  batches:[], packages:[],
  documents:[], bookings:[], audit:[]
};
let previewUser = null;
const previewCatalog=[['1',1,5990],['5',5,28990],['10',10,54990],['30',30,144990],['60',60,254990],['100',100,364990],['200',200,599990]].map(([id,hours,price])=>({id,hours,price,duration_minutes:55,sessions:hours}));
const previewToday=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Santiago'}).format(new Date());
const previewId=rows=>Math.max(0,...rows.map(r=>r.id))+1;
const previewClone=value=>JSON.parse(JSON.stringify(value));
function previewStart(day,hour) {
  const target=new Date(day+'T'+String(hour).padStart(2,'0')+':00:00Z');
  let result=new Date(target);
  for(let i=0;i<3;i++){
    const parts=new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Santiago',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(result);
    const p=Object.fromEntries(parts.map(p=>[p.type,p.value]));
    const shown=new Date(`${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}Z`);
    result=new Date(result.getTime()+target.getTime()-shown.getTime());
  }
  return result.toISOString();
}
function previewRecord(action,target){previewDB.audit.unshift({id:previewId(previewDB.audit),actor_id:previewUser,name:previewDB.users.find(u=>u.id===previewUser)?.name,action,target:String(target),created_at:new Date().toISOString()});}
function previewSlot(body){
  const t=new Date(body.start),h=hourText(body.start);
  if(![1,2,3].includes(body.box)||!Number.isFinite(t.getTime())||t-Date.now()<86400000||t-Date.now()>90*86400000||!/^\d{2}:00$/.test(h)||Number(h.slice(0,2))<7||Number(h.slice(0,2))>=23)throw Error('Selecciona una hora válida con al menos 24 horas de anticipación con inicio entre 07:00 y 22:00 (cierre a las 23:00).');
  return t.toISOString();
}
async function api(path,body){
  const db=previewDB;
  if(path==='email-verification/resend')return {sent:false,available:false,message:'La confirmación de correo funciona en el sitio con servidor, después de configurar el servicio de envío.'};
  if(path.startsWith('password-reset/'))throw Error('La recuperación requiere el sitio con servidor y el servicio de correo configurado.');
  if(path==='verify-email')throw Error('Confirma tu correo en el sitio publicado con servidor.');
  if(path==='gallery')return {groups:globalThis.VALHALLA_GALLERY||[]};
  if(path==='login'){
    const user=db.users.find(u=>u.email===String(body.email).toLowerCase());
    const valid=user?.password;
    if(body.email==='santibanezf.carlos@gmail.com')throw Error('La administración requiere el sitio con servidor y tu contraseña privada.');
    if(!user||!user.active||body.password!==valid)throw Error('Correo o contraseña incorrectos, o cuenta desactivada.');
    previewUser=user.id;return {ok:true};
  }
  if(path==='register'){
    if(body.password!==body.password_confirmation)throw Error('Las contraseñas no coinciden.');
    const email=String(body.email).trim().toLowerCase();
    if(email==='santibanezf.carlos@gmail.com')throw Error('Este correo está reservado para administración.');
    if(db.users.some(u=>u.email===email))throw Error('Ya existe una cuenta con ese correo.');
    if(String(body.password).length<10)throw Error('La contraseña debe tener al menos 10 caracteres.');
    const user={id:previewId(db.users),name:body.name,email,specialty:body.specialty,password:body.password,role:'professional',active:1,verified:0,email_verified:0};
    db.users.push(user);previewUser=user.id;return {ok:true};
  }
  if(path==='logout'){previewUser=null;return {ok:true};}
  const user=db.users.find(u=>u.id===previewUser);
  if(!user||!user.active)throw Error('Inicia sesión para continuar.');
  if(path==='me'){user.identity_source=user.personally_approved?'personal':'document';user.identity_approved=db.documents.filter(d=>d.user_id===user.id&&d.kind==='identity').at(-1)?.status==='approved';user.degree_approved=db.documents.filter(d=>d.user_id===user.id&&d.kind==='degree').at(-1)?.status==='approved';}
  if(path==='me')return previewClone({purchase_rules:globalThis.VALHALLA_RULES,rooms:globalThis.VALHALLA_ROOMS||[],user:{...user,password:undefined},csrf:'preview-only',today:previewToday(),earliest_booking_date:new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Santiago'}).format(new Date(Date.now()+86400000)),catalog:previewCatalog,packages:db.packages.filter(p=>p.user_id===user.id),bookings:db.bookings.filter(b=>b.user_id===user.id).sort((a,b)=>a.start.localeCompare(b.start)),documents:db.documents.filter(d=>d.user_id===user.id)});
  if(path.startsWith('availability?')){
    const day=new URLSearchParams(path.split('?')[1]).get('date');
    return {date:day,slots:Array.from({length:16},(_,i)=>{
      const start=previewStart(day,7+i);
      return {label:String(7+i).padStart(2,'0')+':00',start,boxes:[1,2,3].map(box=>({box,available:new Date(start)-Date.now()>=86400000&&!db.bookings.some(b=>b.box===box&&b.start===start&&b.status==='confirmed')}))};
    })};
  }
  if(path==='profile'){if(user.name!==String(body.name).trim()){const identity=db.documents.filter(d=>d.user_id===user.id&&d.kind==='identity').at(-1);if(identity)identity.status='pending';user.verified=0;}user.name=String(body.name).trim();user.specialty=String(body.specialty).trim();return {ok:true};}
  if(path==='admin/discounts'){
    if(user.role!=='admin')throw Error('Acceso exclusivo de administración.');
    const code=String(body.code).trim().toUpperCase();if(!/^[A-Z0-9_-]{3,40}$/.test(code)||!Number.isInteger(body.percent)||body.percent<1||body.percent>99)throw Error('Código o porcentaje inválido.');
    const row=db.discounts.find(d=>d.code===code);if(row)Object.assign(row,{percent:body.percent,active:body.active});else db.discounts.push({code,percent:body.percent,active:body.active});return {code};
  }
  if(path==='discounts/quote'){
    const plan=previewCatalog.find(p=>p.id===String(body.catalog_id));if(!plan)throw Error('Plan inválido.');
    const code=String(body.discount_code||'').trim().toUpperCase(),d=db.discounts.find(d=>d.code===code&&d.active);
    if(code&&!d)throw Error('El código de descuento no existe o está desactivado.');
    const percent=d?.percent||0,price=Math.max(1,Math.floor((plan.price*(100-percent)+50)/100));return {code,percent,price,original_price:plan.price,discount:plan.price-price};
  }
  if(path==='packages'){
    if(body.rules_acceptance?.accepted!==true||body.rules_acceptance?.version!==globalThis.VALHALLA_RULES.version)throw Error('Debes aceptar las normas vigentes antes de comprar.');
    if(user.role!=='professional')throw Error('Solo profesionales pueden solicitar paquetes.');
    const p=previewCatalog.find(p=>p.id===body.catalog_id);if(!p)throw Error('Paquete inválido.');
    const quote=await api('discounts/quote',body);
    const row={discount_code:quote.code,discount_percent:quote.percent,original_price:quote.original_price,id:previewId(db.packages),user_id:user.id,hours:p.hours,remaining:p.hours,price:quote.price,status:'pending',created_at:new Date().toISOString()};db.packages.push(row);previewRecord('payment_requested_simulated',row.id);return {ok:true,id:row.id};
  }
  if(path==='bookings/batch'){
    if(!Array.isArray(body.slots)||!body.slots.length||body.slots.length>200||!body.request_key)throw Error('Selecciona las horas antes de confirmar.');
    const fingerprint=JSON.stringify(body.slots.slice().sort((a,b)=>a.start.localeCompare(b.start)));
    const previous=db.batches.find(batch=>batch.user_id===user.id&&batch.key===body.request_key);
    if(previous){if(previous.fingerprint!==fingerprint)throw Error('La confirmación corresponde a otra selección.');return {ids:previous.ids,repeated:true};}
    const before=previewClone({bookings:db.bookings,packages:db.packages,audit:db.audit});const ids=[];
    try{for(const slot of body.slots){await api('bookings',slot);ids.push(db.bookings.at(-1).id);}}
    catch(error){db.bookings=before.bookings;db.packages=before.packages;db.audit=before.audit;throw error;}
    db.batches.push({user_id:user.id,key:body.request_key,fingerprint,ids});return {ids,repeated:false};
  }
  if(path==='bookings'||path.startsWith('bookings/')){
    if(user.role!=='professional'||!user.verified)throw Error('Necesitas documentación aprobada para reservar.');
    const start=previewSlot(body),id=path.includes('/')?Number(path.split('/')[1]):null;
    const existing=id?db.bookings.find(b=>b.id===id&&b.user_id===user.id):null;
    if(id&&!existing)throw Error('Reserva no encontrada.');
    if(existing){
      if(existing.modifications||existing.status!=='confirmed')throw Error('Esta reserva ya no admite modificaciones.');
      if(new Date(existing.start)-Date.now()<7200000||new Date(start)-Date.now()<7200000)throw Error('El cambio requiere al menos dos horas de anticipación para ambos horarios.');
      if(existing.start===start&&existing.box===body.box)throw Error('Elige un box u horario diferente.');
    }
    if(db.bookings.some(b=>b.id!==id&&b.status==='confirmed'&&b.start===start&&(b.box===body.box||(b.user_id===user.id&&user.role!=='admin'))))throw Error('Ese box está ocupado o ya tienes una reserva a esa hora.');
    if(existing){existing.start=start;existing.box=body.box;existing.modifications++;previewRecord('modified',id);return {ok:true};}
    const p=db.packages.find(p=>p.user_id===user.id&&p.status==='paid'&&p.remaining>0);
    if(!p)throw Error('Necesitas saldo de un paquete confirmado.');
    const b={id:previewId(db.bookings),user_id:user.id,package_id:p.id,box:body.box,start,status:'confirmed',modifications:0,created_at:new Date().toISOString()};p.remaining--;db.bookings.push(b);previewRecord('reserved',b.id);return {ok:true,id:b.id};
  }
  if(path==='documents'){
    const files=body.files||[body];
    if(!Array.isArray(files)||!files.length||files.length>5)throw Error('Selecciona de 1 a 5 archivos.');
    let size=0;for(const file of files){const binary=atob(file.content);if(!binary.length)throw Error('Archivo vacío.');size+=binary.length;}
    if(size>10*1024*1024)throw Error('Los archivos deben sumar como máximo 10 MB.');
    const id=previewId(db.documents);
    db.documents.push({id,user_id:user.id,filename:files[0].filename,kind:body.kind||'accreditation',content:files[0].content,files:files.map((file,index)=>({...file,position:index+1,download_url:index?`/api/documents/${id}/files/${index+1}`:`/api/documents/${id}`})),status:'pending',note:'',created_at:new Date().toISOString()});user.verified=0;previewRecord('document_uploaded',user.id);return {ok:true};
  }
  if(path==='admin'){
    if(user.role!=='admin')throw Error('Acceso exclusivo de administración.');
    if(body===undefined)return previewClone({discount_codes:db.discounts,users:db.users.map(({password,...u})=>u),packages:db.packages.map(p=>({...p,name:db.users.find(u=>u.id===p.user_id)?.name})).reverse(),documents:db.documents.map(d=>({...d,name:db.users.find(u=>u.id===d.user_id)?.name})).reverse(),bookings:db.bookings.map(b=>({...b,name:db.users.find(u=>u.id===b.user_id)?.name})),audit:db.audit});
    if(body.action==='personal_approval'){
      if(body.confirmed!==true)throw Error('Confirma la decisión.');const target=db.users.find(u=>u.id===body.id&&u.role==='professional'&&u.active);if(!target)throw Error('Cuenta inválida.');target.personally_approved=body.decision==='approved';target.verified=Number(target.personally_approved);
    }else if(body.action==='payment'){
      const p=db.packages.find(p=>p.id===body.id);if(!p||p.status!=='pending')throw Error('El pago ya fue procesado.');p.status=body.decision;
    }else if(body.action==='document'){
      const d=db.documents.find(d=>d.id===body.id);if(!d)throw Error('Documento no encontrado.');
      if(db.documents.some(other=>other.user_id===d.user_id&&other.id>d.id))throw Error('Revisa el documento más reciente.');
      if(d.kind==='identity'&&body.decision==='approved'&&body.identity_confirmed!==true)throw Error('Confirma la revisión de ambos lados del carnet.');d.status=body.decision;d.note=body.note;const latest=kind=>db.documents.filter(x=>x.user_id===d.user_id&&x.kind===kind).at(-1)?.status==='approved';db.users.find(u=>u.id===d.user_id).verified=Number(latest('identity')&&latest('degree'));
    }else if(body.action==='user'){
      const u=db.users.find(u=>u.id===body.id&&u.role==='professional');if(!u)throw Error('Usuario inválido.');u.active=Number(body.active);
    }else if(body.action==='cancel'){
      const b=db.bookings.find(b=>b.id===body.id);if(!b||b.status!=='confirmed'||new Date(b.start)<=new Date())throw Error('Reserva no cancelable.');b.status='cancelled';db.packages.find(p=>p.id===b.package_id).remaining++;
    }else throw Error('Acción inválida.');
    previewRecord('admin_'+body.action,body.id);return {ok:true};
  }
  throw Error('Acción no disponible en esta vista previa.');
}
document.addEventListener('click',event=>{
  const link=event.target.closest('a[href^="/api/documents/"]');if(!link)return;
  event.preventDefault();const parts=link.getAttribute('href').split('/');const id=Number(parts[3]),position=Number(parts[5]||1);
  const document=previewDB.documents.find(d=>d.id===id);if(!document)return;const d=document.files?.[position-1]||document;
  const download=document.createElement('a');download.href='data:application/octet-stream;base64,'+d.content;download.download=d.filename;download.click();
});
