// Optional rendering check: PLAYWRIGHT_MODULE points to an installed Playwright.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const candidates = [chromium.executablePath(),
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe'];
  const executablePath = candidates.find(candidate => fs.existsSync(candidate));
  assert(executablePath, 'No test browser found');
  const browser = await chromium.launch({executablePath, headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 660, height: 360}, colorScheme: 'dark'});
    await page.addInitScript(() => {
      window.bridgeActions = [];
      window.adsk = {fusionSendData: async action => { window.bridgeActions.push(action); return 'OK'; }};
    });
    await page.goto(pathToFileURL(path.join(__dirname, '../FusionBackupManager/progress.html')).href);
    await page.waitForFunction(() => window.bridgeActions.includes('ready'));
    const message = '出力ファイルのサイズ・SHA-256 を検証中\nプロジェクト: ABU2027\nフォルダ: Robot/Parts\nファイル: MotorMount\n形式: F3D\n完了 12 / 100 件 ｜ 今回保存 8 ｜ スキップ 4 ｜ エラー 0';
    const payload = {message, maximum: 100, value: 12, cancel_label: 'Stop Backup', cancel_enabled: true, cancel_visible: true};
    assert.equal(await page.evaluate(state => window.fusionJavaScriptHandler.handle('progress', JSON.stringify(state)), payload), 'OK');
    assert.equal(await page.locator('#message').textContent(), message);
    for (const selector of ['body', '#message', '#cancel', '#cancel-note']) {
      assert.equal(await page.locator(selector).evaluate(node => getComputedStyle(node).textAlign), 'left');
    }
    assert.equal(await page.locator('#bar').evaluate(node => node.value), 12);
    if (process.env.FBM_PROGRESS_PREVIEW) await page.screenshot({path: process.env.FBM_PROGRESS_PREVIEW});
    await page.locator('#cancel').click();
    await page.waitForFunction(() => window.bridgeActions.includes('cancel'));
    assert(await page.locator('#cancel').isDisabled());
    assert(await page.locator('#cancel-note').isVisible());
    const malicious = '<img src=x onerror=alert(1)> ' + '長いフォルダ名'.repeat(40);
    await page.evaluate(state => window.fusionJavaScriptHandler.handle('progress', JSON.stringify(state)), {...payload, message: malicious});
    assert.equal(await page.locator('#message img').count(), 0);
    await page.setViewportSize({width: 320, height: 360});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));
    console.log('Browser checks passed: left alignment, wrapping, safe names, progress values, cancellation.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
