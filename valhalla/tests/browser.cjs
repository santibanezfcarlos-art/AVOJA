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
      await p.getByRole('button',{name:label,exact:true}).click();
      await p.locator('#modal').waitFor({state:'hidden'});
    };
    const tomorrow = date => new Date(new Date(date+'T12:00:00Z').getTime()+86400000).toISOString().slice(0,10);

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
    await page.getByRole('button',{name:/Crear cuenta/}).click();
    await page.getByRole('heading',{name:/Hola, Profesional/}).waitFor();
    const initial=await profile(page);
    assert.equal(initial.user.verified,0);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);

    await goto(page,'documents');
    await page.locator('#document-file').setInputFiles({name:'carnet-ficticio.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\n% Documento sintetico para pruebas\n%%EOF')});
    await page.getByRole('button',{name:/Enviar a revisión/}).click();
    await page.getByText('carnet-ficticio.pdf',{exact:true}).waitFor();
    await goto(page,'packages');
    assert.equal(await page.locator('.package-grid .package').count(),7);
    assert.match(await page.locator('[data-buy="1"]').locator('..').innerText(),/5[.,]990/);
    await page.locator('[data-buy="5"]').click();
    await confirm(page,'Solicitar sin cobro');
    let current=await profile(page);
    assert.equal(current.packages[0].status,'pending');
    const packageId=current.packages[0].id, documentId=current.documents[0].id;

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
    await admin.locator('[data-tab="documents"]').click();
    await admin.locator(`[data-admin="document"][data-id="${documentId}"][data-decision="approved"]`).click();
    await admin.locator('#review-note').fill('Carnet ficticio aprobado para pruebas.');
    await admin.locator('#identity-confirmed').check();
    await confirm(admin);

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
    await page.locator('[data-box="2"]:not(:disabled)').nth(1).click();
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
    await admin.setViewportSize({width:390,height:844});
    assert.equal(await admin.evaluate(()=>document.documentElement.scrollWidth),390);
    await admin.screenshot({path:'/tmp/valhalla-agenda-identificada-mobile.png',fullPage:true});
    await admin.setViewportSize({width:1440,height:1000});
    await admin.locator('[data-view="admin"]').click();
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
    await admin.getByText('carnet-propietario-ficticio.pdf',{exact:true}).waitFor();
    const ownerIdentity=(await profile(admin)).documents[0].id;
    await admin.locator('[data-mode="admin"]').click();
    await admin.locator('[data-tab="documents"]').click();
    await admin.locator(`[data-admin="document"][data-id="${ownerIdentity}"][data-decision="approved"]`).click();
    await admin.locator('#identity-confirmed').check();
    await confirm(admin);
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
    await admin.setViewportSize({width:390,height:844});
    assert.equal(await admin.evaluate(()=>document.documentElement.scrollWidth),390);
    await admin.locator('[data-mode="admin"]').click();
    await admin.getByRole('heading',{name:'Administración',exact:true}).waitFor();
    assert.deepEqual(pageErrors,[]);
    console.log('PASS: flujo profesional completo y administrador con perfil profesional, saldo propio, cambio de perfil y móvil (390 px).');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
