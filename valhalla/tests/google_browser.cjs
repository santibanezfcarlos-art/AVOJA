// UI checks use a fake Google authorization page; no provider or customer is contacted.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base=process.env.VALHALLA_TEST_URL;
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}});const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/google/config',route=>route.fulfill({json:{enabled:true}}));
  let starts=0,links=0;
  await page.route('**/api/google/start',route=>{starts++;return route.fulfill({json:{authorization_url:'https://accounts.google.com/o/oauth2/v2/auth?fake=1'}});});
  await page.route('**/api/google/link',route=>{links++;return route.fulfill({json:{authorization_url:'https://accounts.google.com/o/oauth2/v2/auth?fake=1'}});});
  await page.route('https://accounts.google.com/**',route=>route.fulfill({contentType:'text/html',body:'<h1>Google simulado</h1>'}));
  await page.goto(base);await page.getByRole('button',{name:'Continuar con Google',exact:true}).waitFor();
  await page.getByRole('button',{name:'Acceso administrador',exact:true}).click();assert.equal(await page.locator('#google-access').count(),0);
  await page.getByRole('button',{name:'Volver al inicio',exact:true}).click();
  await page.getByRole('button',{name:'Continuar con Google',exact:true}).click();await page.waitForURL('https://accounts.google.com/**');assert.equal(starts,1);
  await page.goto(base);await page.getByRole('button',{name:'Regístrate',exact:true}).click();assert.equal(await page.locator('#google-access').count(),1);
  await page.getByLabel('Nombre completo').fill('Google UI Profesional');await page.getByLabel('Profesión o especialidad').fill('Psicología');await page.getByLabel('Correo electrónico',{exact:true}).fill('google-ui@example.com');await page.getByLabel('Contraseña',{exact:true}).fill('GoogleBrowserTest123');await page.getByLabel('Confirmar contraseña').fill('GoogleBrowserTest123');await page.getByRole('button',{name:/Crear cuenta/}).click();
  await page.locator('[data-view="profile"]').click();await page.getByRole('button',{name:'Vincular Google',exact:true}).waitFor();
  // Profile saving must still submit even with an extra Google button in the form.
  await page.getByRole('button',{name:'Guardar perfil',exact:true}).click();await page.getByText('Perfil profesional actualizado.',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Vincular Google',exact:true}).click();await page.waitForURL('https://accounts.google.com/**');assert.equal(links,1);
  await page.goto(base+'/?google_result=profile_required');await page.locator('#profile-form').waitFor();assert.equal(new URL(page.url()).search,'');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);console.log('Google UI: acceso, registro, administrador sin Google, vinculación, perfil y móvil OK');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
