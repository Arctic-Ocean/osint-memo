// assets/icon.svg → icon.png(512) · icon.ico(16~256). Playwright(Chromium)로 SVG 를 그린다.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const assets = path.join(here, '..', 'assets');
const { chromium } = (await import(process.env.PLAYWRIGHT_PATH || '/opt/node-tools/node_modules/playwright/index.js')).default;
const svg = fs.readFileSync(path.join(assets, 'icon.svg'), 'utf8');
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const page = await browser.newPage({ viewport: { width: 512, height: 512 } });
async function render(size) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(`<html><body style="margin:0;background:transparent">${svg.replace('<svg ', `<svg width="${size}" height="${size}" `)}</body></html>`);
  return page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
}
fs.writeFileSync(path.join(assets, 'icon.png'), await render(512));
const sizes = [16, 32, 48, 64, 128, 256];
const pngs = [];
for (const s of sizes) pngs.push(await render(s));
await browser.close();
// ICO: PNG 를 그대로 담는다(Vista 이상). 헤더 6B + 항목 16B × n + 이미지들
const head = Buffer.alloc(6); head.writeUInt16LE(0, 0); head.writeUInt16LE(1, 2); head.writeUInt16LE(pngs.length, 4);
let offset = 6 + 16 * pngs.length;
const entries = pngs.map((png, i) => {
  const e = Buffer.alloc(16); const s = sizes[i];
  e[0] = s >= 256 ? 0 : s; e[1] = s >= 256 ? 0 : s; e[2] = 0; e[3] = 0;
  e.writeUInt16LE(1, 4); e.writeUInt16LE(32, 6); e.writeUInt32LE(png.length, 8); e.writeUInt32LE(offset, 12);
  offset += png.length; return e;
});
fs.writeFileSync(path.join(assets, 'icon.ico'), Buffer.concat([head, ...entries, ...pngs]));
console.log('icons written');
