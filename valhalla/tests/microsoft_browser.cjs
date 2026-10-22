// Microsoft pages and authorization starts are simulated; no provider or user is contacted.
const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});try{
const page=await browser.newPage({viewport:{width:390,height:844}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
const base=process.env.VALHALLA_TEST_URL;let enabled=true,starts=0,links=0;
await page.route('**/api/microsoft/config',route=>route.fulfill({json:{enabled}}));
await page.route('**/api/microsoft/start',route=>{starts++;return route.fulfill({json:{authorization_url:'https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?fake=1'}});});
await page.route('**/api/microsoft/link',route=>{links++;return route.fulfill({json:{authorization_url:'https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?fake=1'}});});
await page.route('https://login.microsoftonline.com/**',route=>route.fulfill({contentType:'text/html',body:'<h1>Microsoft simulado</h1>'}));
await page.goto(base);await page.getByRole('button',{name:'Ingresar con Microsoft',exact:true}).waitFor();
await page.getByRole('button',{name:'Acceso administrador',exact:true}).click();assert.equal(await page.locator('#microsoft-access').count(),0);
await page.getByRole('button',{name:'Volver al inicio',exact:true}).click();await page.getByRole('button',{name:'Ingresar con Microsoft',exact:true}).click();await page.waitForURL('https://login.microsoftonline.com/**');assert.equal(starts,1);
await page.goto(base);await page.getByRole('button',{name:'Regístrate',exact:true}).click();assert.equal(await page.locator('#microsoft-access').count(),1);
await page.getByLabel('Nombre completo').fill('Profesional Microsoft');await page.getByLabel('Profesión o especialidad').fill('Psicología');await page.getByLabel('Correo electrónico',{exact:true}).fill('browser@hotmail.com');await page.getByLabel('Contraseña',{exact:true}).fill('MicrosoftBrowser123!');await page.getByLabel('Confirmar contraseña').fill('MicrosoftBrowser123!');await page.getByRole('button',{name:/Crear cuenta/}).click();
await page.locator('[data-view="profile"]').click();await page.getByRole('button',{name:'Vincular Microsoft',exact:true}).waitFor();
await page.getByRole('button',{name:'Guardar perfil',exact:true}).click();await page.getByText('Perfil profesional actualizado.',{exact:true}).waitFor();
await page.getByRole('button',{name:'Vincular Microsoft',exact:true}).click();await page.waitForURL('https://login.microsoftonline.com/**');assert.equal(links,1);
await page.goto(base+'/?microsoft_result=profile_required');await page.locator('#profile-form').waitFor();assert.equal(new URL(page.url()).search,'');
await page.getByLabel('Profesión o especialidad').fill('Psicología clínica');await page.getByRole('button',{name:'Guardar perfil',exact:true}).click();await page.getByRole('heading',{name:'Documentación profesional',exact:true}).waitFor();
assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
await page.locator('[data-logout]:visible').first().click();enabled=false;await page.reload();await page.getByRole('button',{name:/Iniciar sesión/}).waitFor();assert.equal(await page.locator('#microsoft-access').count(),0);
assert.deepEqual(errors,[]);console.log('PASS: Microsoft button on login/register, administrator isolation, Hotmail profile linking, specialty onboarding, hidden until configured, mobile.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
