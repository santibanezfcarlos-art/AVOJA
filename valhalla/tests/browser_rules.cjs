const assert=require('node:assert/strict');
exports.acceptBookingRules=async function(page){
  const checkbox=page.locator('#booking-rules-accepted');
  if(!await checkbox.count())return;
  assert.equal(await page.locator('#modal-confirm').isDisabled(),true);
  assert.equal(await page.locator('.purchase-rules li').count(),12);
  await checkbox.check();
  await page.getByRole('button',{name:'Continuar a la confirmación',exact:true}).click();
  await checkbox.waitFor({state:'detached'});
};
