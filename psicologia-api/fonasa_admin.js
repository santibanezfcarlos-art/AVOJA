"use strict";
const $=id=>document.getElementById(id);
let current=null,link=null,pollTimer=null,polls=0;
const states={
  pdf_read:"PDF leído",checking:"Consultando Fonasa",ready_to_activate:"Fonasa: Activar disponible",
  link_issued:"Enlace generado",appointment_requested:"Solicitud recibida",
  check_failed:"No se comprobó el bono"};
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
 $("linkCard").hidden=!["ready_to_activate","link_issued","appointment_requested"].includes(state);
 if(state==="checking"){note("checkStatus","Consultando Fonasa…");polls=0;poll();}
 else if(state==="appointment_requested"){note("checkStatus","La persona ya registró una solicitud de hora. Revisa el historial.");}
 else if(state==="ready_to_activate"||state==="link_issued"){note("checkStatus","Fonasa muestra el botón Activar. No se ha pulsado.");}
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
  note("uploadStatus","Bono leído. Prestador y prestación verificados.");
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
   note("checkStatus","Bono reconocido: Fonasa mostró el botón Activar. No se ha activado.");
   $("linkCard").hidden=false;
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
  if(!result.items.length){const p=document.createElement("p");p.textContent="Aún no hay bonos cargados.";container.append(p);return}
  const table=document.createElement("table"),tr=document.createElement("tr");
  ["Fecha","Beneficiario","Folio","Estado","Hora solicitada","Acción"].forEach(n=>{
   const th=document.createElement("th");th.textContent=n;tr.append(th)});
  table.append(tr);
  for(const item of result.items){
   const row=document.createElement("tr");
   const cells=[(item.created_at||"").slice(0,10),item.patient_name,item.folio,
      states[item.state]||item.state,(item.date||"")+(item.time?" "+item.time:"")];
   cells.forEach(text=>{const td=document.createElement("td");td.textContent=text||"—";row.append(td)});
   const td=document.createElement("td"),button=document.createElement("button");
   button.type="button";button.className="secondary";button.textContent="Ver";
   button.onclick=()=>{choose({...item,emitted:item.emitted||""});$("detailCard").scrollIntoView({behavior:"smooth",block:"start"})};
   td.append(button);
   if(item.state==="appointment_requested"){
     const notify=document.createElement("button");
     notify.type="button";notify.className="secondary";notify.textContent="Enviar avisos";
     notify.style.marginLeft="8px";
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
   row.append(td);table.append(row);
  }
  container.append(table);
 }catch(err){container.textContent=err.message}
}
$("refreshBtn").onclick=refresh;
refresh();