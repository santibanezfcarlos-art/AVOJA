// Full browser flow against an isolated test instance with a private admin password.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.VALHALLA_TEST_URL || 'http://127.0.0.1:8081';

(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium',args:['--no-sandbox']});
  try {
    const professionalContext = await browser.newContext({viewport:{width:390,height:844}});
    const adminContext = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await professionalContext.newPage();
    const admin = await adminContext.newPage();
    const pageErrors=[];
    for (const p of [page,admin]) {
      p.on('pageerror',e=>pageErrors.push(e.message));
      p.on('console',m=>{if(m.type()==='error'&&/Content Security Policy/.test(m.text()))pageErrors.push(m.text());});
    }
    const profile = p => p.evaluate(async()=> (await fetch('/api/me')).json());
    const goto = async(p,view) => {
      await p.locator(`[data-view="${view}"]`).click();
      await p.locator('#content h1').waitFor();
    };
    const confirm = async(p,label='Confirmar') => {
      if(await p.locator('#purchase-rules-accepted').count()){assert.equal(await p.locator('#modal-confirm').isDisabled(),true);assert.equal(await p.locator('.purchase-rules li').count(),12);await p.locator('#purchase-rules-accepted').check();}
      await p.getByRole('button',{name:label,exact:true}).click();
      await p.locator('#modal').waitFor({state:'hidden'});
    };
    const tomorrow = date => new Date(new Date(date+'T12:00:00Z').getTime()+2*86400000).toISOString().slice(0,10);

    await page.goto(base);
    assert.equal(await page.getByLabel('Correo electrónico').inputValue(),'');
    assert.equal(await page.getByLabel('Contraseña',{exact:true}).inputValue(),'');
    assert.equal(await page.locator('[data-demo]').count(),0);
    await page.getByRole('button',{name:'Acceso profesional',exact:true}).click();
    await page.getByRole('heading',{name:'Crea tu cuenta profesional',exact:true}).waitFor();
    const email=`prueba.${Date.now()}@valhalla.demo`;
    await page.getByLabel('Nombre completo').fill('Profesional Prueba');
    await page.getByLabel('Profesión o especialidad').fill('Psicología');
    await page.getByLabel('Correo electrónico').fill(email);
    await page.getByLabel('Contraseña',{exact:true}).fill('PruebaValhalla2026!');
    await page.getByLabel('Confirmar contraseña',{exact:true}).fill('NoCoincide2026!');
    await page.getByRole('button',{name:/Crear cuenta/}).click();
    await page.getByText('Las contraseñas no coinciden.',{exact:true}).waitFor();
    await page.getByLabel('Confirmar contraseña',{exact:true}).fill('PruebaValhalla2026!');
    await page.getByRole('button',{name:/Crear cuenta/}).click();
    await page.getByRole('heading',{name:/Hola, Profesional/}).waitFor();
    const initial=await profile(page);
    assert.equal(initial.user.verified,0);
    assert.equal(initial.user.email_verified,0);
    if(!process.env.VALHALLA_TEST_MAILBOX_URL)throw Error('Use python3 tests/run_browser.py for the isolated verification mailbox.');
    const delivery=await (await page.request.get(process.env.VALHALLA_TEST_MAILBOX_URL+'/?email='+encodeURIComponent(email))).json();
    assert.ok(delivery.token);
    await page.goto(base+'/?verify_email='+encodeURIComponent(delivery.token));
    assert.equal(new URL(page.url()).searchParams.has('verify_email'),false);
    await page.getByRole('button',{name:'Confirmar mi correo',exact:true}).click();
    await page.getByRole('button',{name:'Correo confirmado',exact:true}).waitFor();
    await page.getByRole('button',{name:'Volver a mi cuenta',exact:true}).click();
    await page.getByRole('heading',{name:/Hola, Profesional/}).waitFor();
    assert.equal((await profile(page)).user.email_verified,1);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);

    await page.locator('[data-logout]:visible').first().click();
    await page.getByRole('button',{name:'Olvidé mi contraseña',exact:true}).click();
    await page.getByLabel('Correo electrónico').fill(email);
    await page.getByRole('button',{name:'Enviar enlace de recuperación',exact:true}).click();
    await page.locator('#recovery-result').filter({hasText:'Si existe una cuenta'}).waitFor();
    const resetMail=await (await page.request.get(process.env.VALHALLA_TEST_MAILBOX_URL+'/?email='+encodeURIComponent('reset:'+email))).json();
    assert.ok(resetMail.token);
    await page.goto(base+'/?reset_password='+encodeURIComponent(resetMail.token));
    assert.equal(new URL(page.url()).searchParams.has('reset_password'),false);
    await page.getByLabel('Nueva contraseña',{exact:true}).fill('RecoveredPassword2026!');
    await page.getByLabel('Confirmar contraseña',{exact:true}).fill('RecoveredPassword2026!');
    await page.getByRole('button',{name:'Guardar nueva contraseña',exact:true}).click();
    await page.getByRole('button',{name:'Contraseña actualizada',exact:true}).waitFor();
    await page.getByRole('button',{name:'Volver a iniciar sesión',exact:true}).click();
    await page.getByLabel('Correo electrónico').fill(email);
    await page.getByLabel('Contraseña',{exact:true}).fill('RecoveredPassword2026!');
    await page.getByRole('button',{name:/Iniciar sesión/}).click();
    await page.getByRole('heading',{name:/Hola, Profesional/}).waitFor();

    await goto(page,'documents');
    await page.locator('#document-file').setInputFiles([{name:'carnet-ficticio.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\n% Documento sintetico para pruebas\n%%EOF')},{name:'reverso-ficticio.heic',mimeType:'application/octet-stream',buffer:Buffer.from('synthetic identity reverse')}]);
    await page.getByRole('button',{name:/Enviar a revisión/}).click();
    await page.locator('.document-name').filter({hasText:'carnet-ficticio.pdf'}).waitFor();
    await page.locator('#degree-file').setInputFiles({name:'titulo-ficticio.pdf',mimeType:'application/pdf',buffer:Buffer.from('Synthetic degree')});
    await page.getByRole('button',{name:/Enviar certificado a revisión/}).click();
    await page.locator('.document-name').filter({hasText:'titulo-ficticio.pdf'}).waitFor();
    await goto(page,'packages');
    assert.equal(await page.locator('.package-grid .package').count(),7);
    assert.match(await page.locator('[data-buy="1"]').locator('..').innerText(),/5[.,]990/);
    await page.locator('[data-buy="5"]').click();
    await confirm(page,'Solicitar sin cobro');
    let current=await profile(page);
    assert.equal(current.packages[0].status,'pending');
    const packageId=current.packages[0].id, documentId=current.documents.find(d=>d.kind==='identity').id;

    await admin.goto(base);
    await admin.getByRole('button',{name:'Acceso administrador',exact:true}).click();
    assert.equal(await admin.getByLabel('Correo electrónico').inputValue(),'');
    assert.equal(await admin.getByLabel('Contraseña',{exact:true}).inputValue(),'');
    if(!process.env.VALHALLA_TEST_ADMIN_PASSWORD)throw Error('Set VALHALLA_TEST_ADMIN_PASSWORD for the isolated test instance.');
    await admin.getByLabel('Correo electrónico').fill('santibanezf.carlos@gmail.com');
    await admin.getByLabel('Contraseña',{exact:true}).fill(process.env.VALHALLA_TEST_ADMIN_PASSWORD);
    await admin.getByRole('button',{name:/Iniciar sesión/}).click();
    await admin.getByRole('heading',{name:'Administración',exact:true}).waitFor();
    await admin.locator(`[data-admin="payment"][data-id="${packageId}"][data-decision="paid"]`).click();
    await confirm(admin);
    await admin.locator('[data-tab="email"]').click();
    await admin.getByRole('heading',{name:'Servicio de correo',exact:true}).waitFor();
    await admin.getByRole('button',{name:'Enviar prueba a mi correo administrador',exact:true}).click();
    await admin.locator('#email-test-result').filter({hasText:'El proveedor aceptó la prueba'}).waitFor();
    await admin.getByRole('cell',{name:'Aceptado por el proveedor',exact:true}).first().waitFor();
    await admin.locator('[data-tab="payments"]').click();
    await admin.locator('[data-tab="documents"]').click();
    await admin.locator(`[data-admin="document"][data-id="${documentId}"][data-decision="approved"]`).click();
    await admin.locator('#review-note').fill('Carnet ficticio aprobado para pruebas.');
    await admin.locator('#identity-confirmed').check();
    await confirm(admin);
    assert.equal((await profile(page)).user.verified,0);
    const degreeId=(await profile(page)).documents.find(d=>d.kind==='degree').id;
    await admin.locator(`[data-admin="document"][data-id="${degreeId}"][data-decision="approved"]`).click();await confirm(admin);

    await page.reload();
    await page.locator('[data-view="overview"]').waitFor();
    current=await profile(page);
    assert.equal(current.user.verified,1);
    assert.equal(current.packages[0].status,'paid');
    await goto(page,'availability');
    await page.locator('#schedule-date').fill(tomorrow(current.today));
    await page.locator('#schedule-date').dispatchEvent('change');
    await page.waitForFunction(date=>document.querySelector('[data-slot]')?.dataset.slot?.startsWith(date),tomorrow(current.today));
    const first=page.locator('[data-box="1"]:not(:disabled)').first();
    const original=await first.getAttribute('data-slot');
    await first.click();assert.match(await page.locator('#modal').innerText(),/55 minutos/);await confirm(page,'Confirmar reserva');
    await page.getByRole('heading',{name:'Tu agenda, en orden'}).waitFor();
    current=await profile(page);
    assert.equal(current.packages[0].remaining,4);
    const booking=current.bookings[0];
    await page.locator(`[data-modify="${booking.id}"]`).click();
    await page.locator('[data-box="2"]:not(:disabled)').nth(2).click();
    await confirm(page,'Confirmar cambio');
    await page.getByRole('heading',{name:'Tu agenda, en orden'}).waitFor();
    assert.equal(await page.locator('[data-modify]').count(),0);
    current=await profile(page);
    assert.equal(current.bookings[0].modifications,1);
    assert.equal(current.packages[0].remaining,4);

    // A second change is rejected by the API, even if the interface is bypassed.
    const secondChange=await page.evaluate(async({id,start,csrf})=>{
      const r=await fetch('/api/bookings/'+id,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({box:3,start})});return r.status;
    },{id:booking.id,start:original,csrf:current.csrf});
    assert.equal(secondChange,409);

    await admin.reload();
    await admin.locator('[data-view="availability"]').click();
    await admin.locator('#schedule-date').fill(tomorrow(current.today));
    await admin.locator('#schedule-date').dispatchEvent('change');
    await admin.locator('.admin-occupied strong').filter({hasText:'Profesional Prueba'}).waitFor();
    assert.match(await admin.locator('.admin-occupied').innerText(),/09:00–09:55/);
    await admin.locator('.admin-occupied [data-user-detail]').click();
    await admin.getByRole('heading',{name:'Ficha de Profesional Prueba',exact:true}).waitFor();
    assert.match(await admin.locator('#modal').innerText(),/Planes y saldos/);
    await admin.getByRole('button',{name:'Cerrar ficha',exact:true}).click();
    await admin.locator('.admin-occupied').waitFor();

    await admin.setViewportSize({width:390,height:844});
    assert.equal(await admin.evaluate(()=>document.documentElement.scrollWidth),390);
    await admin.screenshot({path:'/tmp/valhalla-agenda-identificada-mobile.png',fullPage:true});
    await admin.locator('.admin-occupied [data-user-detail]').click();
    await admin.getByRole('heading',{name:'Ficha de Profesional Prueba',exact:true}).waitFor();
    await admin.getByRole('button',{name:'Cerrar ficha',exact:true}).click();
    await admin.locator('.admin-occupied').waitFor();

    await admin.setViewportSize({width:1440,height:1000});
    await admin.locator('[data-view="admin"]').click();
    await admin.locator('[data-tab="daily_contacts"]').click();
    await admin.locator('#daily-contacts-date').fill(tomorrow(current.today));
    await admin.locator('#daily-contacts-date').dispatchEvent('change');
    await admin.waitForFunction(value=>document.querySelector('#daily-email-list')?.value===value,email);
    assert.equal(await admin.locator('#daily-email-list').inputValue(),email);
    await admin.setViewportSize({width:390,height:844});
    await admin.screenshot({path:'/tmp/valhalla-correos-dia-mobile.png',fullPage:true});
    assert.equal(await admin.evaluate(()=>document.documentElement.scrollWidth),390);
    await admin.setViewportSize({width:1440,height:1000});
    const forbiddenContacts=await page.request.get(base+'/api/admin/daily-contacts?date='+tomorrow(current.today));
    assert.equal(forbiddenContacts.status(),403);
    const mailbox=async recipient=>(await(await page.request.get(process.env.VALHALLA_TEST_MAILBOX_URL+'/?email='+encodeURIComponent(recipient))).json()).notifications||[];
    let messages=[];
    for(let attempt=0;attempt<20;attempt++){
      messages=await mailbox(email);
      if(messages.some(message=>message.kind==='booking_modified'))break;
      await page.waitForTimeout(100);
    }
    assert.ok(messages.some(message=>message.kind==='booking_modified'&&message.text.includes('Box 2')&&message.text.includes('09:00 a 09:55')));
    const administratorMessages=await mailbox('santibanezf.carlos@gmail.com');
    assert.ok(administratorMessages.some(message=>message.kind==='admin_signup'&&message.text.includes(email)));
    assert.ok(administratorMessages.some(message=>message.kind==='admin_document'&&message.text.includes(email)));
    await admin.locator('[data-tab="bookings"]').click();
    await admin.locator(`[data-admin="cancel"][data-id="${booking.id}"]`).click();
    await confirm(admin);
    current=await profile(page);
    assert.equal(current.packages[0].remaining,5);
    assert.equal(current.bookings[0].status,'cancelled');

    await admin.locator('[data-tab="users"]').click();
    await admin.locator(`[data-admin="user"][data-id="${current.user.id}"]`).click();
    await confirm(admin);
    const invalid=await page.evaluate(async()=> (await fetch('/api/me')).status);
    assert.equal(invalid,401);
    // The owner can alternate views without duplicating the account or mixing balances.
    const ownerBefore=await profile(admin);
    await admin.locator('[data-mode="professional"]').click();
    await admin.getByRole('heading',{name:/Hola,/}).waitFor();
    await goto(admin,'profile');
    await admin.getByLabel('Nombre completo').fill('Carlos Santibáñez');
    await admin.getByLabel('Profesión o especialidad').fill('Psicología');
    await admin.getByRole('button',{name:'Guardar perfil',exact:true}).click();
    await admin.getByRole('heading',{name:'Mi perfil profesional',exact:true}).waitFor();
    await goto(admin,'documents');
    await admin.locator('#document-file').setInputFiles({name:'carnet-propietario-ficticio.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\n% Documento sintetico para pruebas\n%%EOF')});
    await admin.getByRole('button',{name:/Enviar a revisión/}).click();
    await admin.locator('.document-name').filter({hasText:'carnet-propietario-ficticio.pdf'}).waitFor();
    const ownerIdentity=(await profile(admin)).documents[0].id;
    await admin.locator('#degree-file').setInputFiles({name:'titulo-owner.pdf',mimeType:'application/pdf',buffer:Buffer.from('Owner synthetic degree')});await admin.getByRole('button',{name:/Enviar certificado a revisión/}).click();await admin.locator('.document-name').filter({hasText:'titulo-owner.pdf'}).waitFor();
    const ownerDegree=(await profile(admin)).documents.find(d=>d.kind==='degree').id;
    await admin.locator('[data-mode="admin"]').click();
    await admin.locator('[data-tab="documents"]').click();
    await admin.locator(`[data-admin="document"][data-id="${ownerIdentity}"][data-decision="approved"]`).click();
    await admin.locator('#identity-confirmed').check();
    await confirm(admin);
    await admin.locator(`[data-admin="document"][data-id="${ownerDegree}"][data-decision="approved"]`).click();await confirm(admin);
    await admin.locator('[data-mode="professional"]').click();
    await goto(admin,'packages');
    await admin.locator('[data-buy="5"]').click();
    await confirm(admin,'Solicitar sin cobro');
    const ownerPending=await profile(admin);
    const ownerPid=ownerPending.packages[0].id;
    assert.equal(ownerPending.user.id,ownerBefore.user.id);
    assert.equal(ownerPending.packages[0].user_id,ownerBefore.user.id);
    await admin.locator('[data-mode="admin"]').click();
    await admin.locator('[data-tab="payments"]').click();
    await admin.locator(`[data-admin="payment"][data-id="${ownerPid}"][data-decision="paid"]`).click();
    await confirm(admin);
    await admin.locator('[data-mode="professional"]').click();
    await goto(admin,'availability');
    await admin.locator('#schedule-date').fill(tomorrow(ownerPending.today));
    await admin.locator('#schedule-date').dispatchEvent('change');
    await admin.waitForFunction(date=>document.querySelector('[data-slot]')?.dataset.slot?.startsWith(date),tomorrow(ownerPending.today));
    await admin.locator('[data-box="3"]:not(:disabled)').first().click();
    await confirm(admin,'Confirmar reserva');
    await admin.getByRole('heading',{name:'Tu agenda, en orden'}).waitFor();
    const ownerBooked=await profile(admin);
    assert.equal(ownerBooked.packages[0].remaining,4);
    assert.equal(ownerBooked.bookings[0].user_id,ownerBefore.user.id);
    assert.equal(ownerBooked.user.role,'admin');
    // Owner may reserve the other two rooms at the same time through batch UI.
    const parallelStart=ownerBooked.bookings[0].start;
    await goto(admin,'availability');
    await admin.locator('#schedule-date').fill(tomorrow(ownerPending.today));
    await admin.locator('#schedule-date').dispatchEvent('change');
    await admin.waitForFunction(date=>document.querySelector('[data-slot]')?.dataset.slot?.startsWith(date),tomorrow(ownerPending.today));
    await admin.locator('#toggle-multiple').click();
    for(const box of [1,2]){
      const slot=admin.locator(`[data-box="${box}"][data-slot="${parallelStart}"]`);
      assert.equal(await slot.isDisabled(),false);await slot.click();
      await admin.locator(`[data-box="${box}"][data-slot="${parallelStart}"][aria-pressed="true"]`).waitFor();
    }
    await admin.locator('#confirm-multiple').click();await confirm(admin,'Confirmar horas seleccionadas');
    await admin.getByRole('heading',{name:'Tu agenda, en orden'}).waitFor();
    const ownerParallel=await profile(admin);
    assert.equal(ownerParallel.packages[0].remaining,2);
    assert.equal(ownerParallel.bookings.filter(b=>b.start===parallelStart).length,3);

    await admin.setViewportSize({width:390,height:844});
    assert.equal(await admin.evaluate(()=>document.documentElement.scrollWidth),390);
    await admin.locator('[data-mode="admin"]').click();
    await admin.getByRole('heading',{name:'Administración',exact:true}).waitFor();
    assert.deepEqual(pageErrors,[]);
    console.log('PASS: flujo profesional completo y administrador con perfil profesional, saldo propio, cambio de perfil y móvil (390 px).');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
