/* Real browser + real local HTTP/SQLite. QVAC_REAL=1 enables Qwen/Whisper.
 * Uses an isolated DB, synthetic microphone audio, and checks all UI views.
 * No remote request is allowed from the browser.
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const http = require('node:http');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'test-results');
fs.mkdirSync(output, { recursive: true });
const python = process.env.PYTHON_EXECUTABLE || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const real = process.env.QVAC_REAL === '1';
const database = path.join(output, `browser-${Date.now()}.db`);
const children = [];
const errors = [];
let browser;

function readLocal(url) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => resolve({ ok: response.statusCode === 200, body }));
    });
    request.setTimeout(3000, () => request.destroy(new Error('HTTP timeout')));
    request.on('error', reject);
  });
}

function launch(args, env) {
  const processHandle = spawn(python, args, { cwd: root, env: { ...process.env, ...env }, windowsHide: true });
  const log = fs.createWriteStream(path.join(output, args.includes('tests/serve_backend.py') ? 'backend.log' : 'frontend.log'));
  processHandle.stdout.pipe(log);
  processHandle.stderr.pipe(log);
  children.push(processHandle);
  return processHandle;
}

async function waitForServer(url) {
  for (let i = 0; i < 240; i++) {
    try { const response = await readLocal(url); if (response.ok) return; } catch (_) {}
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error(`Server did not start: ${url}`);
}

async function main() {
  for (const url of ['http://127.0.0.1:8001/api/health', 'http://127.0.0.1:5500']) {
    let existing = false;
    try { existing = (await readLocal(url)).ok; } catch (_) {}
    assert(!existing, `Port already occupied: ${url}. This test never uses a personal database.`);
  }
  launch(['-X', 'utf8', '-u', 'tests/serve_backend.py'], {
    DATABASE_URL: 'sqlite:///' + database.replaceAll('\\', '/'),
    QVAC_REQUIRED: String(real), QVAC_ENABLED: String(real),
  });
  launch(['-m', 'http.server', '5500', '--bind', '127.0.0.1', '--directory', 'frontend']);
  await Promise.all([waitForServer('http://127.0.0.1:8001/api/health'), waitForServer('http://127.0.0.1:5500')]);
  const audio = path.join(output, 'voice-demo.wav');
  const args = ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'];
  if (fs.existsSync(audio)) args.push(`--use-file-for-fake-audio-capture=${audio}`);
  browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'msedge', headless: true, args });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ['microphone'], reducedMotion: 'reduce' });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) {
      errors.push(`Nonlocal browser request: ${url.origin}`);
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto('http://127.0.0.1:5500');
  const nav = async name => { await page.locator(`.nav-item[data-view="${name}"]`).click(); };
  for (const name of ['customer', 'installed', 'map', 'dashboard', 'opportunities', 'queries', 'capture']) await nav(name);
  await page.locator('#reporterName').fill('Demo Browser Reporter');
  const submit = async text => {
    await page.locator('#chatInput').fill(text);
    const response = page.waitForResponse(r => r.url().endsWith('/api/observations/process'), { timeout: 150000 });
    await page.locator('#chatForm button[type="submit"]').click();
    const received = await response;
    assert(received.ok(), await received.text());
    return received.json();
  };
  const text = 'Visité Hospital DemoCare Pacific en Ciudad de Panamá, Panamá. Tienen dos MR Siemens de aproximadamente ocho años y un CT Philips.';
  let response = await submit(text);
  for (let i = 0; response.status === 'needs_more_info' && i < 2; i++) response = await submit('No sé');
  assert.equal(response.status, 'ready_for_confirmation');
  assert.equal(response.extracted.equipment.length, 2);
  await page.screenshot({ path: path.join(output, 'capture-desktop.png'), fullPage: true });
  const savedResponse = page.waitForResponse(r => r.url().endsWith('/api/observations/confirm'));
  await page.locator('#confirmObservation').click();
  const saved = await (await savedResponse).json();
  assert.equal(saved.status, 'success');
  await page.waitForFunction(() => document.querySelector('#confirmActions').hidden);
  assert.equal(await page.locator('#confirmActions').isVisible(), false);
  await page.locator('#chatInput').fill('No borrar esta nueva captura');
  await page.waitForTimeout(1700);
  assert.equal(await page.locator('#chatInput').inputValue(), 'No borrar esta nueva captura');
  assert.equal(await page.locator('#reporterName').inputValue(), 'Demo Browser Reporter');
  await nav('customer');
  await page.locator('#customerDetail h2').filter({ hasText: 'Hospital DemoCare Pacific' }).waitFor();
  assert.equal(await page.locator('.equipment-card').count(), 2);
  assert.match(await page.locator('#customerDetail').innerText(), /Estimado/);
  await page.screenshot({ path: path.join(output, 'customer-desktop.png'), fullPage: true });
  await nav('map');
  await page.locator('[data-level="region"][data-value="Centroamérica y Caribe"]').click();
  await page.locator('[data-level="country"][data-value="Panamá"]').click();
  await page.locator('[data-level="city"][data-value="Ciudad de Panamá"]').click();
  await page.locator('[data-level="customer"]').click();
  await page.locator('#customerDetail h2').filter({ hasText: 'Hospital DemoCare Pacific' }).waitFor();
  await nav('dashboard');
  await page.waitForFunction(() => document.querySelectorAll('.kpi-value').length >= 9);
  assert.match(await page.locator('#chartModality').innerText(), /MR/);
  await page.screenshot({ path: path.join(output, 'dashboard-desktop.png'), fullPage: true });
  await nav('queries');
  await page.locator('#localQueryInput').fill('MR en Panamá de más de siete años');
  await page.locator('#localQueryForm button[type="submit"]').click();
  await page.locator('#localQueryResults tbody tr').waitFor();
  assert.match(await page.locator('#localQueryResults').innerText(), /Hospital DemoCare Pacific/);
  await nav('customer');
  await page.locator('[data-delete-observation]').click();
  await page.locator('#confirmAccept').click();
  await page.waitForFunction(() => document.querySelectorAll('[data-delete-observation]').length === 0);
  assert.equal(await page.locator('.equipment-card').count(), 2);
  await nav('capture');
  await page.locator('#chatInput').fill('');
  // Real MediaStream with a synthetic input device; never the user's microphone.
  if (real && fs.existsSync(audio)) {
    for (let i = 0; i < 2; i++) {
      await page.locator('#voiceButton').click();
      await page.waitForFunction(() => document.querySelector('#voiceButton').getAttribute('aria-pressed') === 'true');
      await page.waitForTimeout(6500);
      const transcription = page.waitForResponse(r => r.url().endsWith('/api/voice/transcribe'), { timeout: 150000 });
      await page.locator('#voiceButton').click();
      const result = await transcription;
      assert(result.ok(), await result.text());
      await page.waitForFunction(() => document.querySelector('#chatInput').value.length > 0);
      assert.equal(await page.locator('#voiceButton').isEnabled(), true);
      await page.locator('#chatInput').fill('');
    }
  }
  await page.locator('#voiceButton').click();
  await page.waitForFunction(() => document.querySelector('#voiceButton').getAttribute('aria-pressed') === 'true');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#voiceButton').getAttribute('aria-pressed'), 'false');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(400);
  const micBounds = await page.locator('#voiceButton').boundingBox();
  const sendBounds = await page.locator('#chatForm button[type="submit"]').boundingBox();
  assert(sendBounds.x >= micBounds.x + micBounds.width, 'Send button must stay alongside the microphone');
  await page.screenshot({ path: path.join(output, 'capture-mobile.png'), fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  assert.equal(overflow, false, 'Mobile page overflows horizontally');
  assert.deepEqual(errors, []);
  const health = JSON.parse((await readLocal('http://127.0.0.1:8001/api/health')).body);
  fs.writeFileSync(path.join(output, 'browser-summary.json'), JSON.stringify({ passed: true, realQvac: real, health, errors }, null, 2));
  console.log(`PASS: browser capture, follow-up, save, views, delete, voice cancel, mobile; real QVAC=${real}`);
}

main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  // Only child processes launched by this test are stopped. Their DB is retained for QA.
  for (const child of children) {
    if (child.exitCode !== null) continue;
    child.stdin.end('stop\n');
    await new Promise(resolve => {
      const timeout = setTimeout(() => { child.kill(); resolve(); }, 8000);
      child.once('exit', () => { clearTimeout(timeout); resolve(); });
    });
  }
});
