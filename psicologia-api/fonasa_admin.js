"use strict";
const $=id=>document.getElementById(id);
let current=null,link=null,pollTimer=null,polls=0;
const states={
  pdf_read:"PDF leído",checking:"Consultando Fonasa",ready_to_activate:"Fonasa: Activar disponible",
  link_issued:"Enlace generado",appointment_requested:"Solicitud recibida",
  check_failed:"No se comprobó el bono",calendar_pending:"Confirmación Google pendiente",calendar_confirmed:"Google Calendar confirmado"};
function note(id,message,warning=false){const x=$(id);x.hidden=false;x.textContent=message;x.classList.toggle("warn",warning)}
async function api(path,{method="GET",body}={}){
 const opt={method,cache:"no-store",credentials:"same-origin",headers:{}};
 if(method!=="GET")opt.headers["X-Billing-Action"]="manual-invoice";
 if(body instanceof FormData)opt.body=body;
 else if(body!==undefined){opt.headers["Content-Type"]="application/json";opt.body=JSON.stringify(body)}
 const response=await fetch(path,opt);
 const data=await response.json().catch(()=>({error:"No hubo respuesta válida del servidor."}));
 if(!response.ok)throw Error(data.error||"No se pudo completar la solicitud.");
 return data;
}
function choose(record){
 clearTimeout(pollTimer);
 current=record;link=null;$("linkOutput").textContent="";$("linkOutput").removeAttribute("href");$("copyBtn").hidden=true;
 $("detailCard").hidden=false;
 $("patientName").value=record.patient_name||"";
 $("patientRut").value=record.rut||"";
 $("patientFolio").value=record.folio||"";
 $("issuedOn").value=record.emitted||"";
 const state=record.state||"pdf_read";
 $("checkBtn").disabled=!["pdf_read","check_failed"].includes(state);
 $("checkStatus").hidden=true;
 $("linkCard").hidden=record.activation_status!=="activated"||!["ready_to_activate","link_issued"].includes(state);
 if(state==="checking"){note("checkStatus","Consultando Fonasa…");polls=0;poll();}
 else if(state==="appointment_requested"){note("checkStatus","La persona ya registró una solicitud de hora. Revisa el historial.");}
 else if(state==="ready_to_activate"||state==="link_issued"){note("checkStatus",record.activation_status==="activated"?"Bono activado en Fonasa. Ya puedes generar el enlace de agenda.":"Bono verificado. Actívalo desde el historial antes de generar el enlace de agenda.");}
 else if(state==="check_failed"){note("checkStatus","Consulta sin validación. Puedes volver a intentar.",true);}
}
$("uploadForm").addEventListener("submit",async event=>{
 event.preventDefault();
 const file=$("bonoPdf").files[0];
 if(!file)return;
 if(file.size>2_000_000){note("uploadStatus","El PDF supera el límite de 2 MB.",true);return}
 $("uploadBtn").disabled=true;
 note("uploadStatus","Leyendo el bono Fonasa…");
 try{
  const form=new FormData();form.append("bono",file);
  const result=await api("/admin/fonasa/upload",{method:"POST",body:form});
  note("uploadStatus",result.existing?"Este bono ya estaba cargado. Abrimos su registro para continuar sin duplicarlo.":"Bono leído. Prestador y prestación verificados.");
  choose({...result.bono,state:result.state});
  await refresh();
 }catch(err){note("uploadStatus",err.message,true)}
 finally{$("uploadBtn").disabled=false}
});
$("checkBtn").addEventListener("click",async()=>{
 if(!current)return;
 $("checkBtn").disabled=true;
 note("checkStatus","Iniciando consulta autenticada en Bono Web Fonasa…");
 try{
  const r=await api("/admin/fonasa/check/"+encodeURIComponent(current.id),{method:"POST",body:{}});
  current.state=r.state;polls=0;poll();
 }catch(err){note("checkStatus",err.message,true);$("checkBtn").disabled=false;}
});
async function poll(){
 if(!current||current.state!=="checking")return;
 try{
  const r=await api("/admin/fonasa/status/"+encodeURIComponent(current.id));
  if(r.state==="checking"){
   if(++polls>25){note("checkStatus","La consulta sigue en proceso; pulsa Actualizar historial para revisarla.",true);return}
   note("checkStatus","Chromium está consultando Fonasa…");
   pollTimer=setTimeout(poll,3500);return;
  }
  current.state=r.state;
  if(r.state==="ready_to_activate"){
   note("checkStatus",r.activated?"Fonasa confirmó que el bono ya está activado. Ya puedes generar el enlace.":"Bono reconocido. Pulsa Activar bono en el historial antes de generar el enlace.");
   $("linkCard").hidden=!r.activated;
  }else{
   note("checkStatus","No se pudo confirmar la opción Activar. Revisa el resultado antes de generar el enlace.",true);
   $("checkBtn").disabled=false;
  }
  await refresh();
 }catch(err){
  if(++polls<=25){pollTimer=setTimeout(poll,5000)}
  else note("checkStatus","No se pudo consultar el resultado. "+err.message,true);
 }
}
$("linkBtn").addEventListener("click",async()=>{
 if(!current)return;
 $("linkBtn").disabled=true;
 try{
  const r=await api("/admin/fonasa/link/"+encodeURIComponent(current.id),{method:"POST",body:{}});
  link=r.link;
  $("linkOutput").href=link;$("linkOutput").textContent=link;
  $("copyBtn").hidden=false;
  note("checkStatus","Enlace privado generado, válido durante 7 días. Puedes copiarlo y enviarlo al beneficiario.");
  await refresh();
 }catch(err){note("checkStatus",err.message,true)}
 finally{$("linkBtn").disabled=false}
});
$("copyBtn").onclick=async()=>{
 if(!link)return;
 try{await navigator.clipboard.writeText(link);note("checkStatus","Enlace copiado.")}
 catch{note("checkStatus","Selecciona el enlace para copiarlo manualmente.",true)}
};
async function refresh(){
 const container=$("history");
 try{
  const result=await api("/admin/fonasa/items");
  container.replaceChildren();
  if(current){const updated=result.items.find(x=>x.id===current.id);if(updated){current={...current,...updated};$("linkCard").hidden=current.activation_status!=="activated"||!["ready_to_activate","link_issued"].includes(current.state)}}
  if(!result.items.length){const p=document.createElement("p");p.textContent="Aún no hay bonos cargados.";container.append(p);return}
  const table=document.createElement("div");table.className="history-list";
  for(const item of result.items){
   const row=document.createElement("article");row.className="history-item";
   const header=document.createElement("div");header.className="history-heading";
   const name=document.createElement("h3");name.textContent=item.patient_name;
   const meta=document.createElement("p");meta.className="muted";
   meta.textContent="Folio "+item.folio+" · Ingreso "+(item.created_at||"").slice(0,10);
   header.append(name,meta);row.append(header);
   const tracking=document.createElement("span");tracking.className="history-status";
   tracking.textContent=item.manual_completed_at?"Atención completada":item.state==="checking"?"Verificando con Fonasa":item.state==="check_failed"||item.activation_status==="needs_review"?"Requiere revisión":"En seguimiento";
   if(item.state==="check_failed"||item.activation_status==="needs_review")tracking.classList.add("needs-review");
   row.append(tracking);
   const stages=document.createElement("ol");stages.className="bono-stages";
   const activation=item.activation_status||"not_requested";
   const activationLabel={activated:"Activado",running:"Verificando…",needs_review:"Requiere revisión",not_requested:"Por activar"}[activation]||"Por activar";
   const delivery=item.link_delivery_method==="patient_screen"?"Mostrado al paciente":item.link_delivered_at?"Enlace enviado":item.link_generated?"Por enviar":"Aún no generado";
   const booked=item.state==="calendar_confirmed";
   const pending=["appointment_requested","calendar_pending"].includes(item.state);
   const steps=[
    ["Bono leído","Leído",true,false],
    ["Activación Fonasa",activationLabel,activation==="activated",activation==="needs_review"],
    ["Enlace de agenda",delivery,Boolean(item.link_delivered_at),false],
    ["Agendamiento",booked?"Cita confirmada":pending?"Confirmación en curso":"Sin hora todavía",booked,false]
   ];
   for(let [label,value,done,warning] of steps){
    if(item.manual_completed_at){value="Completado";done=true;warning=false}
    const step=document.createElement("li");step.className=done?"done":warning?"attention":"pending";
    const title=document.createElement("span");title.textContent=label;
    const status=document.createElement("strong");status.textContent=value;
    step.append(title,status);stages.append(step);
   }
   row.append(stages);
   if(item.manual_completed_at){const audit=document.createElement("p");audit.className="muted";audit.textContent="Completado manualmente · Atención ya realizada, regularizada por el profesional.";row.append(audit)}
   if(item.date){const date=document.createElement("p");date.className="appointment-detail";date.textContent="Hora "+(booked?"confirmada: ":"solicitada: ")+item.date.split("-").reverse().join("/")+" · "+(item.time||"")+" (Chile)";row.append(date)}
   if(item.state==="checking"||item.state==="check_failed"){
    const status=document.createElement("p");status.className="muted";status.textContent=item.check_detail||states[item.state];row.append(status);
   }
   const td=document.createElement("div"),button=document.createElement("button");
   button.type="button";button.className="secondary";button.textContent="Ver";
   button.onclick=()=>{choose({...item,emitted:item.emitted||""});$("detailCard").scrollIntoView({behavior:"smooth",block:"start"})};
   td.className="history-actions";td.append(button);
   if(item.manual_completed_at){row.append(td);table.append(row);continue}
   if(["appointment_requested","calendar_pending","calendar_confirmed"].includes(item.state)){
     const calendar=document.createElement("button");
     calendar.type="button";calendar.className="secondary";
     calendar.textContent=item.state==="calendar_confirmed"?"Calendario OK":"Confirmar en Google";
     calendar.disabled=item.state==="calendar_confirmed";
     calendar.onclick=async()=>{
       calendar.disabled=true;
       try{
         const result=await api("/admin/fonasa/confirm/"+encodeURIComponent(item.id),{method:"POST",body:{}});
         alert(result.state==="calendar_confirmed"?"Cita confirmada en Google Calendar.":"Aún pendiente de confirmación en Google Calendar.");
         await refresh();
       }catch(err){alert(err.message)}
       finally{calendar.disabled=false}
     };
     td.append(calendar);
     const notify=document.createElement("button");
     notify.type="button";notify.className="secondary";notify.textContent="Enviar avisos";
     
     notify.disabled=item.state!=="calendar_confirmed";
     notify.onclick=async()=>{
       notify.disabled=true;notify.textContent="Enviando…";
       try{
         const result=await api("/admin/fonasa/notify/"+encodeURIComponent(item.id),{method:"POST",body:{}});
         const notices=result.notifications||{};
         const details="Coordinación: "+(notices.professional||"pendiente")+" · Paciente: "+(notices.patient||"pendiente");
         alert(details);
       }catch(err){alert(err.message)}
       finally{notify.disabled=false;notify.textContent="Revisar avisos"}
     };
     td.append(notify);



   }
     if(["ready_to_activate","link_issued"].includes(item.state)){
       const activate=document.createElement("button");
       const labels={not_requested:"Activar bono",running:"Consultando activación…",activated:"Bono activado",needs_review:"Revisar activación"};
       activate.type="button";activate.className="secondary";
       const status=item.activation_status||"not_requested";
       activate.textContent=labels[status]||"Revisar activación";
       activate.disabled=status==="activated"||status==="running";
       activate.onclick=async()=>{
         activate.disabled=true;
         try{
           const result=await api("/admin/fonasa/activate/"+encodeURIComponent(item.id),{method:"POST",body:{}});
           if(result.activation_status==="running")pollActivation(item.id,0);
           await refresh();
         }catch(err){alert(err.message);await refresh()}
       };
       td.append(activate);
       if(status==="running")pollActivation(item.id,0);
     }
   if(item.link_generated&&!item.link_delivered_at){
    const sent=document.createElement("button");sent.type="button";sent.className="secondary";sent.textContent="Confirmar envío del enlace";
    sent.onclick=async()=>{
     if(!confirm("¿Ya enviaste el enlace de agenda al paciente? Esto solo registra el envío que realizaste; no envía un correo."))return;
     sent.disabled=true;
     try{await api("/admin/fonasa/link-delivery/"+encodeURIComponent(item.id),{method:"POST",body:{}});await refresh()}
     catch(err){alert(err.message);sent.disabled=false}
    };td.append(sent);
   }
   row.append(td);table.append(row);
  }
  container.append(table);
 }catch(err){container.textContent=err.message}
}
$("refreshBtn").onclick=refresh;

const activationPolls=new Map();
function pollActivation(id,attempt){
 if(activationPolls.has(id))return;
 activationPolls.set(id,setTimeout(async()=>{
   activationPolls.delete(id);
   try{
     const result=await api("/admin/fonasa/activation/"+encodeURIComponent(id));
     if(result.activation_status==="running"&&attempt<35){pollActivation(id,attempt+1);return}
     await refresh();
   }catch(err){if(attempt<35)pollActivation(id,attempt+1)}
 },3000));
}

refresh();

