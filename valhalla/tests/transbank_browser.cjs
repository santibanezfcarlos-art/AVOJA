const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});try{
const page=await browser.newPage({viewport:{width:390,height:844}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(process.env.VALHALLA_TEST_URL);await page.getByRole('button',{name:'Acceso administrador',exact:true}).click();await page.getByLabel('Correo electrónico').fill('santibanezf.carlos@gmail.com');await page.getByLabel('Contraseña',{exact:true}).fill(process.env.VALHALLA_TEST_ADMIN_PASSWORD);await page.getByRole('button',{name:/Iniciar sesión/}).click();await page.getByRole('heading',{name:'Administración',exact:true}).waitFor();
await page.locator('[data-tab="transbank"]').click();await page.getByRole('heading',{name:'Pagos con Transbank',exact:true}).waitFor();
const toggle=()=>page.getByRole('switch');assert.equal(await toggle().isChecked(),false);
await page.getByLabel('Código de comercio Webpay Plus').fill('597012345678');await toggle().check();await page.getByRole('button',{name:'Guardar configuración',exact:true}).click();await page.getByText('Pruebas activas',{exact:true}).waitFor();assert.equal(await toggle().isChecked(),true);
await page.reload();await page.locator('[data-tab="transbank"]').click();await page.getByRole('heading',{name:'Pagos con Transbank',exact:true}).waitFor();assert.equal(await page.getByLabel('Código de comercio Webpay Plus').inputValue(),'597012345678');assert.equal(await toggle().isChecked(),true);
await toggle().uncheck();await page.getByRole('button',{name:'Guardar configuración',exact:true}).click();await page.getByText('Desactivado',{exact:true}).waitFor();
assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(errors,[]);console.log('PASS: Transbank administración en móvil, código persistido, interruptor de pruebas y desactivación; ningún cobro ni conexión bancaria.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
