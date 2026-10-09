// Uses the already installed Playwright and Edge; no downloads or network.
const { chromium } = require(process.env.CAD_PLAYWRIGHT);
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CAD_EDGE,headless:true});
  const page=await browser.newPage({viewport:{width:1400,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(pathToFileURL(path.join(__dirname,'cases','2222','index.html')).href);
  const drawings=await page.locator('svg.drawing').count();
  if(drawings!==4)throw Error('Expected four neutral drawing regions');
  const regions=await page.locator('svg.drawing').evaluateAll(nodes=>nodes.map(x=>x.querySelectorAll('polyline').length));
  if(regions.some(x=>x===0))throw Error('An empty region was produced');
  await page.locator('polyline').first().dispatchEvent('click');
  const selection=await page.locator('#selected').textContent();
  if(!selection.startsWith('已选择：'))throw Error('Source-line picking failed');
  const svg=page.locator('svg.drawing').first();const initial=await svg.getAttribute('viewBox');
  await svg.dispatchEvent('wheel',{deltaY:-100});
  if(await svg.getAttribute('viewBox')===initial)throw Error('Zoom failed');
  await svg.dispatchEvent('dblclick');
  if(await svg.getAttribute('viewBox')!==initial)throw Error('Reset failed');
  const broken=await page.locator('a').evaluateAll(nodes=>nodes.map(a=>a.href).filter(x=>x.startsWith('file:')));
  for(const url of broken){const p=require('url').fileURLToPath(url);if(!fs.existsSync(p))throw Error('Missing link '+p);}
  await page.screenshot({path:path.join(__dirname,'verification','工作台预览.png'),fullPage:false});
  if(errors.length)throw Error(errors.join(';'));
  fs.writeFileSync(path.join(__dirname,'verification','ui-report.json'),JSON.stringify({ok:true,regions,selection,zoom:true,reset:true,broken_links:0,page_errors:errors},null,2));
  console.log(JSON.stringify({ok:true,regions,zoom:true,selection:true}));
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
