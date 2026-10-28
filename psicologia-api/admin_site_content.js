(()=>{'use strict';
const $=id=>document.getElementById(id);
let schema,values={},version=0,dirty=false,current='inicio',loaded=false;
const status=text=>{$('contentStatus').textContent=text;};
async function call(body){
 const response=await fetch('/admin/site-content',{cache:'no-store',...(body?{method:'POST',headers:{'Content-Type':'application/json','X-Billing-Action':'manual-invoice'},body:JSON.stringify(body)}:{})});
 const data=await response.json();if(!response.ok)throw Error(data.error||'No se pudo cargar el contenido.');return data;
}
function preview(){
 const box=$('contentPreview');box.replaceChildren();
 document.querySelectorAll('#contentFields textarea').forEach(input=>{
  const f=schema.fields[input.dataset.key],p=document.createElement(f.label.startsWith('Título')?'h3':'p');p.textContent=input.value;box.append(p);
 });
}
function render(){
 $('contentFields').replaceChildren();
 for(const [key,field]of Object.entries(schema.fields)){
  if(field.section!==current)continue;
  const label=document.createElement('label'),name=document.createElement('span'),input=document.createElement('textarea');
  name.textContent=field.label;input.value=values[key];input.dataset.key=key;input.rows=values[key].length>130?4:2;input.maxLength=3000;input.required=true;
  input.addEventListener('input',()=>{dirty=true;status('Tienes cambios sin publicar.');preview();});
  label.append(name,input);$('contentFields').append(label);
 }
 const page=current==='enfoque'?'enfoque.html':current==='formacion'?'formacion-experiencia.html':current==='servicios'?'#prestaciones':current==='preguntas'?'#preguntas':current==='agenda'?'#agenda':'#inicio';
 $('contentViewPage').href='https://pscsantibanez.cl/'+page;
 $('contentSectionTitle').textContent=schema.sections[current];preview();
}
async function load(){
 if(dirty&&!confirm('Hay cambios sin publicar. ¿Descartarlos y recargar?'))return;
 $('contentSave').disabled=true;status('Cargando textos de la página…');
 try{
  const data=await call();schema=data.schema;values=data.values;version=data.version;dirty=false;loaded=true;
  const select=$('contentSection');select.replaceChildren();
  for(const [key,title]of Object.entries(schema.sections)){const option=document.createElement('option');option.value=key;option.textContent=title;select.append(option);}
  select.value=current;render();$('contentSave').disabled=false;status('Textos listos. Edita y pulsa Guardar y publicar.');
 }catch(error){status(error.message);}
}
$('contentSection').addEventListener('change',event=>{
 if(dirty&&!confirm('Hay cambios sin publicar. ¿Descartarlos y cambiar de sección?')){event.target.value=current;return;}
 current=event.target.value;dirty=false;render();status('Editando '+schema.sections[current]+'.');
});
$('contentReload').addEventListener('click',load);
$('contentForm').addEventListener('submit',async event=>{
 event.preventDefault();if(!loaded)return;
 const changes={};document.querySelectorAll('#contentFields textarea').forEach(input=>{changes[input.dataset.key]=input.value;});
 $('contentSave').disabled=true;status('Publicando cambios…');
 try{const result=await call({version,changes});values=result.values;version=result.version;dirty=false;status('Cambios publicados. Se verán al abrir o actualizar la página.');}
 catch(error){status(error.message);}
 finally{$('contentSave').disabled=false;}
});
addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
load();
})();
