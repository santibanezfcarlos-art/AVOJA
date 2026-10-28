/* Text-only public content. Original HTML remains usable if the API is offline. */
(()=>{'use strict';
const endpoint='https://psicologia-carlos-calendar-api.onrender.com/api/site-content';
const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),5000);
fetch(endpoint,{cache:'no-store',credentials:'omit',signal:controller.signal})
 .then(response=>{if(!response.ok)throw Error('unavailable');return response.json()})
 .then(data=>{
  for(const field of Object.values(data.content||{})){
   if(!/^[a-z0-9-]+$/.test(field.node)||!Number.isInteger(field.index)||typeof field.value!=='string')continue;
   document.querySelectorAll('[data-site-node="'+field.node+'"]').forEach(element=>{
    const node=Array.from(element.childNodes).filter(n=>n.nodeType===Node.TEXT_NODE)[field.index];
    if(node)node.nodeValue=field.value;
   });
  }
 }).catch(()=>{}).finally(()=>clearTimeout(timer));
})();
