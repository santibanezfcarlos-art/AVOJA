// All bank approvals here use an isolated FakeGateway; no external payment is made.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base=process.env.VALHALLA_TEST_URL;
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
  try{
    const adminContext=await browser.newContext({viewport:{width:1440,height:1000}});
    const proContext=await browser.newContext({viewport:{width:390,height:844}});
    const admin=await adminContext.newPage(),page=await proContext.newPage(),errors=[];
    for(const p of [page,admin])p.on('pageerror',e=>errors.push(e.message));
    const me=async()=>page.evaluate(async()=>await(await fetch('/api/me')).json());
    const email=`legacy.${Date.now()}@example.cl`;
    await admin.goto(base);await admin.getByRole('button',{name:'Acceso administrador',exact:true}).click();
    await admin.getByLabel('Correo electrónico').fill('santibanezf.carlos@gmail.com');
    await admin.getByLabel('Contraseña',{exact:true}).fill(process.env.VALHALLA_TEST_ADMIN_PASSWORD);
    await admin.getByRole('button',{name:/Iniciar sesión/}).click();
    await admin.getByRole('heading',{name:'Administración',exact:true}).waitFor();
    await admin.locator('[data-tab="migration"]').click();
    const csv=`referencia,nombre,correo,especialidad,sesiones,observacion\nlegacy-browser-001,Juanita Ejemplo,${email},Psicología,10,Saldo restante revisado\n`;
    await admin.locator('#migration-csv').setInputFiles({name:'legacy.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});
    await admin.locator('#migration-csv-validated').check();
    await admin.getByRole('button',{name:'Revisar planilla',exact:true}).click();
    await admin.getByRole('heading',{name:'Vista previa: 1 clientes · 10 sesiones',exact:true}).waitFor();
    assert.equal(await admin.locator('#migration-preview tbody tr').count(),1);
    await admin.getByRole('button',{name:'Confirmar importación',exact:true}).click();
    await admin.getByRole('button',{name:'Importar clientes',exact:true}).click();
    await admin.locator('#modal').waitFor({state:'hidden'});
    await admin.getByText('Pendiente de registro',{exact:true}).waitFor();

    await page.goto(base);await page.getByRole('button',{name:'Acceso profesional',exact:true}).click();
    await page.getByLabel('Nombre completo').fill('Juanita Ejemplo');await page.getByLabel('Profesión o especialidad').fill('Psicología');
    await page.getByLabel('Correo electrónico').fill(email);await page.getByLabel('Contraseña',{exact:true}).fill('LegacyPassword2026!');
    await page.getByLabel('Confirmar contraseña',{exact:true}).fill('LegacyPassword2026!');
    await page.getByRole('button',{name:/Crear cuenta/}).click();await page.getByRole('heading',{name:/Hola, Juanita/}).waitFor();
    let state=await me();assert.equal(state.user.identity_source,'legacy');assert.equal(state.packages[0].remaining,10);assert.equal(state.documents.length,0);
    const mail=await(await page.request.get(process.env.VALHALLA_TEST_MAILBOX_URL+'/?email='+encodeURIComponent(email))).json();
    await page.goto(base+'/?verify_email='+encodeURIComponent(mail.token));await page.getByRole('button',{name:'Confirmar mi correo',exact:true}).click();
    await page.getByRole('button',{name:'Correo confirmado',exact:true}).waitFor();await page.getByRole('button',{name:'Volver a mi cuenta',exact:true}).click();
    await page.locator('[data-view="documents"]').click();await page.getByRole('heading',{name:'No necesitas reenviar el carnet',exact:true}).waitFor();assert.equal(await page.locator('#document-file').count(),0);

    await page.locator('[data-view="availability"]').click();
    state=await me();const tomorrow=new Date(new Date(state.today+'T12:00:00Z').getTime()+86400000).toISOString().slice(0,10);
    await page.locator('#schedule-date').fill(tomorrow);await page.locator('#schedule-date').dispatchEvent('change');
    await page.waitForFunction(date=>document.querySelector('[data-slot]')?.dataset.slot?.startsWith(date),tomorrow);
    await page.locator('[data-box="1"]:not(:disabled)').first().click();await page.getByRole('button',{name:'Confirmar reserva',exact:true}).click();await page.locator('#modal').waitFor({state:'hidden'});
    assert.equal((await me()).packages[0].remaining,9);

    // Intercept the hosted checkout in this browser only, leaving the app's real form navigation intact.
    await proContext.route('https://webpay3gint.transbank.cl/webpayserver/initTransaction',async route=>{
      const token=new URLSearchParams(route.request().postData()).get('token_ws');assert.ok(token);
      await route.fulfill({contentType:'text/html',body:`<html lang="es"><body><h1>Checkout ficticio de pruebas aisladas</h1><form method="post" action="${base}/api/webpay/return"><input type="hidden" name="token_ws" value="${token}"><button>Aprobar pago ficticio</button></form></body></html>`});
    });
    await page.locator('[data-view="packages"]').click();await page.locator('[data-buy="5"]').click();
    await page.getByRole('button',{name:'Ir a Webpay de pruebas',exact:true}).click();
    await page.getByRole('heading',{name:'Checkout ficticio de pruebas aisladas',exact:true}).waitFor();
    await page.getByRole('button',{name:'Aprobar pago ficticio',exact:true}).click();
    await page.getByRole('heading',{name:'Planes mixtos · todos los boxes',exact:true}).waitFor();
    state=await me();assert.equal(state.payments[0].state,'approved');assert.equal(state.packages.reduce((n,p)=>n+(p.status==='paid'?p.remaining:0),0),14);
    const response=await page.request.post(base+'/api/webpay/reconcile',{headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},data:{id:state.payments[0].id}});assert.equal(response.status(),200);
    await page.reload();await page.getByRole('heading',{name:/Hola, Juanita/}).waitFor();
    assert.equal((await me()).packages.reduce((n,p)=>n+(p.status==='paid'?p.remaining:0),0),14);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
    assert.deepEqual(errors,[]);
    console.log('PASS: CSV preview/import, migration without carnet, reservation, hosted checkout return and automatic credit exactly once on mobile. Bank responses were simulated locally.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
