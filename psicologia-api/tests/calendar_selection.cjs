// Regression checks against the actual frontend refresh function; no network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../../psicologia/index.html'), 'utf8');
for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
const source = html.slice(html.indexOf('async function loadCalendar(){'), html.indexOf('const DEFAULT='));
function fixture() {
  const context = {
    calendarSeq: 0, calendarRetry: null, start: '2026-10-23',
    todayISO: () => '2026-10-23', plus: () => '2026-11-12',
    currentService: () => ({ minutes: 45 }), CAL_API: 'test:',
    selectedService: 'psicoterapia', MAX: 3,
    hold: {date: '2026-10-24', time: '10:00', service: 'psicoterapia', expires: Date.now()+600000},
    render: () => {}, clearTimeout: () => {},
    setTimeout: () => 123, message: () => {},
    calendarBusy: () => false, localBusy: () => false, booked: () => [],
    schedule: () => ['10:00'], fitsSlot: () => true, past: () => false,
    fetch: async () => ({ok: true, status: 200, json: async () => ({
      duration_minutes: 45, configured: true, status: 'ok', blocked: {}
    })}),
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return context;
}
(async () => {
  const c = fixture(), hold = c.hold, expiry = hold.expires;
  await c.loadCalendar();
  assert.equal(c.hold, hold);
  assert.equal(c.hold.expires, expiry); // Refresh does not reset the ten-minute clock.
  c.calendarBusy = () => true;
  await c.loadCalendar();
  assert.equal(c.hold, null);
  const failed = fixture(), selected = failed.hold;
  failed.fetch = async () => ({ok: false, status: 503});
  await failed.loadCalendar();
  assert.equal(failed.calendarState, 'error');
  assert.equal(failed.hold, selected);
  assert.equal(failed.calendarRetry, 123);
  const expired = fixture(); expired.hold.expires = Date.now()-1000;
  await expired.loadCalendar(); assert.equal(expired.hold, null);
  const full = fixture(); full.booked = () => ['09:00','11:00','12:00'];
  await full.loadCalendar(); assert.equal(full.hold, null);
  console.log('Frontend syntax and 5 selection regression scenarios passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
