// Exercise delayed lookups and cancellation without bookings or network calls.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../../psicologia/index.html'),'utf8');
for(const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);
const nodes={};
function node(id){return nodes[id]??=( {textContent:'',hidden:false,attrs:{},classes:new Map(),classList:{toggle(k,v){nodes[id].classes.set(k,v)}},setAttribute(k,v){this.attrs[k]=v}} )}
const c={nearestDayPending:true,nearestDaySeq:0,nearestSearching:false,calendarState:'loading',configurationReady:true,hold:null,selectedService:'psicoterapia',start:'2026-11-09',chosen:'2026-11-10',currentService:()=>({minutes:45}),todayISO:()=> '2026-11-10',plus:(d,n)=>String(n),$:node,render:()=>c.updateCalendarMessage(),monday:()=> '2026-11-09',loadCalendar:()=>{},closestDayInAvailability:()=> '2026-11-10'};
vm.createContext(c);
vm.runInContext(html.slice(html.indexOf('function updateCalendarMessage(){'),html.indexOf('// Reutilizar')),c);
vm.runInContext(html.slice(html.indexOf('async function autoPickNearestDay(){'),html.indexOf('chosen=getAvailable(chosen);')),c);
vm.runInContext(html.match(/function cancelNearestDay\(\)\{[^}]+\}/)[0],c);
(async()=>{
 c.updateCalendarMessage();assert.equal(node('calendarSyncStatus').classes.get('is-loading'),true);assert.match(node('calendarSyncText').textContent,/más cercana/);
 let finish;c.getAvailability=()=>new Promise(r=>finish=r);c.calendarState='ready';
 const pending=c.autoPickNearestDay();assert.equal(c.nearestSearching,true);assert.equal(node('hours').attrs['aria-busy'],'true');
 finish({configured:true,status:'ok'});await pending;assert.equal(c.nearestSearching,false);assert.equal(node('hours').attrs['aria-busy'],'false');
 c.nearestDayPending=true;c.chosen='manual';const cancelled=c.autoPickNearestDay();c.cancelNearestDay();c.render();finish({configured:true,status:'ok'});await cancelled;assert.equal(c.chosen,'manual');assert.equal(c.nearestSearching,false);
 c.calendarState='error';c.updateCalendarMessage();assert.equal(node('calendarRetryButton').hidden,false);assert.match(node('calendarSyncText').textContent,/bloqueados/);
 c.calendarState='ready';c.updateCalendarMessage();assert.equal(node('calendarRetryButton').hidden,true);
 console.log('Loading feedback, delayed nearest search, cancellation and recovery passed');
})().catch(e=>{console.error(e);process.exitCode=1});
