const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
try{
 const page=await browser.newPage({viewport:{width:390,height:844}});const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const base=process.env.VALHALLA_TEST_URL;
 await page.goto(base);await page.getByRole('button',{name:'Acceso administrador',exact:true}).click();
 await page.getByLabel('Correo electrónico').fill('santibanezf.carlos@gmail.com');await page.getByLabel('Contraseña',{exact:true}).fill(process.env.VALHALLA_TEST_ADMIN_PASSWORD);
 await page.getByRole('button',{name:/Iniciar sesión/}).click();await page.getByRole('heading',{name:'Administración',exact:true}).waitFor();
 await page.getByRole('button',{name:'Correos del día',exact:true}).click();await page.locator('#daily-contacts-date').waitFor();
 async function chooseDay(day){await page.locator('#daily-contacts-date').fill(day);await page.waitForFunction(day=>document.querySelector('#daily-contacts-date')?.value===day,day);}
 async function importFile(){
  await page.locator('.skedu-import summary').click();await page.locator('#skedu-contacts-file').setInputFiles(process.env.VALHALLA_TEST_XLSX);
  await page.getByRole('button',{name:'Revisar Excel por fecha',exact:true}).click();
  await page.getByRole('heading',{name:'Revisar destinatarios de Skedu',exact:true}).waitFor();
  await page.getByText(/3 agendamientos vigentes; 2 sin confirmación/).waitFor();
  assert.equal((await(await page.request.get(base+'/api/admin/daily-contacts?date=2026-10-23')).json()).total_skedu_bookings,0);
  await page.locator('#modal-confirm').click();await page.waitForFunction(()=>!document.querySelector('#modal').open);
 }
 await chooseDay('2026-10-23');await page.waitForFunction(()=>document.querySelector('.skedu-import')!==null);
 await importFile();await page.waitForFunction(()=>document.querySelector('#daily-email-list')?.value==='person@example.cl');
 assert.equal(await page.getByRole('button',{name:'Copiar correos',exact:true}).isEnabled(),true);
 let report=await(await page.request.get(base+'/api/admin/daily-contacts?date=2026-10-23')).json();assert.equal(report.total_skedu_bookings,2);assert.equal(report.total_professionals,1);
 await page.reload();await page.getByRole('button',{name:'Correos del día',exact:true}).click();await page.locator('#daily-contacts-date').waitFor();
 await chooseDay('2026-10-24');await page.waitForFunction(()=>document.querySelector('#daily-email-list')?.value==='other@example.cl');
 await chooseDay('2026-10-23');await page.waitForFunction(()=>document.querySelector('#daily-email-list')?.value==='person@example.cl');
 await page.getByRole('button',{name:'Quitar de Skedu para este día',exact:true}).click();await page.getByRole('heading',{name:'Quitar contacto de Skedu',exact:true}).waitFor();await page.locator('#modal-confirm').click();
 await page.waitForFunction(()=>document.querySelector('#daily-email-list')?.value==='');
 const other=await(await page.request.get(base+'/api/admin/daily-contacts?date=2026-10-24')).json();assert.deepEqual(other.emails,['other@example.cl']);
 const admin=await(await page.request.get(base+'/api/admin')).json();assert.equal(admin.notification_queue.length,0);assert.equal(admin.bookings.length,0);assert.equal(admin.packages.length,0);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(errors,[]);
 console.log('PASS: mobile XLSX preview, second sheet import, unconfirmed inclusion, dated persistent contacts, copy, removal, no bookings/saldo/emails.');
}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
