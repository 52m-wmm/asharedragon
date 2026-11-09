// Run with ego-browser nodejs < scripts/browser_dragon_final_review.mjs.
// Real API responses are delayed, never replaced with synthetic study data.
const task=await taskSpace('AShareDragon final sample acceptance');
const page=task.page('p1');
const fs=await import('node:fs/promises');
const output=(await import('node:os')).homedir()+'/Projects/asharedragon/docs/dragon_research_final_review';
const results=[];
for(const device of [{name:'desktop',width:1440,height:1000,mobile:false},{name:'mobile',width:390,height:844,mobile:true}]){
 await page.cdp('Emulation.setDeviceMetricsOverride',{width:device.width,height:device.height,deviceScaleFactor:1,mobile:device.mobile});
 await page.cdp('Network.setCacheDisabled',{cacheDisabled:true});
 await page.goto('http://127.0.0.1:3001/dragons');
 await page.waitForFunction(()=>document.querySelector('.seasonality-study')?.textContent.includes('有效结局 5374'));
 await page.evaluate(()=>{
  const native=window.fetch.bind(window);window.__review={pending:[],checks:0,violations:[],hold:true};
  window.fetch=async (url,opts={})=>{
   if(!String(url).includes('/seasonality-study-v1'))return native(url,opts);
   const response=await native(url,opts);
   if(!window.__review.hold)return response;
   await new Promise((resolve,reject)=>{
    const entry={url:String(url),release:resolve,aborted:false};window.__review.pending.push(entry);
    const abort=()=>{entry.aborted=true;reject(new DOMException('Aborted','AbortError'))};
    if(opts.signal?.aborted)abort();else opts.signal?.addEventListener('abort',abort,{once:true});
   });
   return response;
  };
  const inspect=()=>{
   const rows=[...document.querySelectorAll('#seasonality-history tbody tr')];if(!rows.length)return;
   const lo=Number(document.querySelector('select[aria-label="起始年份"]').value),hi=Number(document.querySelector('select[aria-label="结束年份"]').value);
   const title=document.querySelector('#seasonality-history h3').textContent;
   const mm=title.match(/ · (\d+)月/),threshold=Number(document.querySelector('select[aria-label="连板高度"]').value);
   for(const r of rows){const day=r.children[1].textContent,h=Number(r.children[2].textContent.match(/≥(\d+)/)?.[1]);if(Number(day.slice(0,4))<lo||Number(day.slice(0,4))>hi||(mm&&Number(day.slice(5,7))!==Number(mm[1]))||h<threshold)window.__review.violations.push({day,lo,hi,title,h,threshold});}
   window.__review.checks++;
  };
  window.__review.observer=new MutationObserver(inspect);window.__review.observer.observe(document.querySelector('.seasonality-study'),{childList:true,subtree:true,characterData:true});
 });
 await page.selectOption('select[aria-label="结束年份"]','2020');
 await page.waitForFunction(()=>window.__review.pending.length>=1);
 const loading=await page.evaluate(()=>({listHidden:!document.querySelector('#seasonality-history'),loading:document.querySelector('[role=status]')?.textContent}));
 if(!loading.listHidden||!loading.loading)throw Error(device.name+' stale results while request held');
 await page.selectOption('select[aria-label="结束年份"]','2024');
 await page.waitForFunction(()=>window.__review.pending.length>=2);
 await page.selectOption('select[aria-label="结束年份"]','2023');
 await page.waitForFunction(()=>window.__review.pending.length>=3);
 await page.evaluate(()=>{const a=window.__review.pending;a[a.length-1].release()});
 await page.waitForFunction(()=>document.querySelector('#seasonality-history tbody tr')&&[...document.querySelectorAll('#seasonality-history tbody tr')].every(r=>Number(r.children[1].textContent.slice(0,4))<=2023));
 await page.evaluate(()=>{window.__review.pending.forEach(x=>x.release());window.__review.hold=false});
 await page.selectOption('select[aria-label="结束年份"]','2026');
 await page.waitForFunction(()=>document.querySelector('.seasonality-study')?.textContent.includes('有效结局 5374'));
 await page.click('.heatmap tbody tr:nth-child(4) td:nth-child(4) button');
 await page.waitForFunction(()=>document.querySelector('#seasonality-history')?.textContent.includes('10个周期')&&[...document.querySelectorAll('#seasonality-history tbody tr')].every(r=>r.children[1].textContent.startsWith('2020-03')));
 const heatmap=await page.evaluate(()=>({count:document.querySelectorAll('#seasonality-history tbody tr').length,dates:[...document.querySelectorAll('#seasonality-history tbody tr')].map(r=>r.children[1].textContent)}));
 // Verify both confirmed case cycles in their proper independent-evidence years.
 await page.selectOption('select[aria-label="结束年份"]','2023');await page.selectOption('select[aria-label="起始年份"]','2023');
 await page.waitForFunction(()=>document.querySelector('#seasonality-history')?.textContent.includes('603178.SH'));
 const shenglong=await page.evaluate(()=>document.querySelector('#seasonality-history tbody').innerText);
 if(!shenglong.includes('2023-09-28')||!shenglong.includes('≥14'))throw Error('Shenglong cycle wrong');
 await page.selectOption('select[aria-label="结束年份"]','2024');await page.selectOption('select[aria-label="起始年份"]','2024');
 await page.waitForFunction(()=>document.querySelector('#seasonality-history')?.textContent.includes('603580.SH'));
 const aiai=await page.evaluate(()=>document.querySelector('#seasonality-history tbody').innerText);
 if(!aiai.includes('2024-03-05')||!aiai.includes('≥13'))throw Error('Aiai cycle wrong');
 await page.selectOption('select[aria-label="历史ST状态"]','confirmed_non_st');
 await page.waitForFunction(()=>document.querySelector('#seasonality-history')?.textContent.includes('0个周期'));
 const stExcluded=await page.evaluate(()=>!document.querySelector('#seasonality-history tbody tr'));
 await page.selectOption('select[aria-label="历史ST状态"]','all');await page.selectOption('select[aria-label="起始年份"]','2017');await page.selectOption('select[aria-label="结束年份"]','2026');
 await page.selectOption('select[aria-label="结局证据等级"]','unknown');
 await page.waitForFunction(()=>document.querySelector('.seasonality-study')?.textContent.includes('unknown 2,895'));
 const unknown=await page.evaluate(()=>({coverage:[...document.querySelectorAll('.seasonality-study p')].find(x=>x.textContent.startsWith('当前首板'))?.textContent,rates:[...document.querySelectorAll('.seasonality-study table')[0].querySelectorAll('tbody tr')].map(r=>r.lastChild.textContent)}));
 if(!unknown.coverage.includes('有效结局 0')||!unknown.rates.every(x=>x==='— / —'))throw Error('Unknown converted into zero rates');
 await page.selectOption('select[aria-label="结局证据等级"]','all');await page.selectOption('select[aria-label="结束年份"]','2019');
 await page.waitForFunction(()=>document.querySelectorAll('.heatmap tbody tr').length===3&&[...document.querySelectorAll('.heatmap td')].every(x=>x.textContent==='—'));
 const missingCells=await page.evaluate(()=>document.querySelectorAll('.heatmap td').length);
 await page.selectOption('select[aria-label="结束年份"]','2026');
 await page.waitForFunction(()=>document.querySelector('.seasonality-study')?.textContent.includes('有效结局 5374'));
 const audit=await page.evaluate(()=>({checks:window.__review.checks,violations:window.__review.violations,aborted:window.__review.pending.filter(x=>x.aborted).length,viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth}));
 if(audit.violations.length)throw Error(JSON.stringify(audit.violations));
 if(audit.scrollWidth>audit.viewport)throw Error('Viewport overflow');
 await page.evaluate(()=>document.querySelector('#seasonality-history').scrollIntoView({block:'start'}));
 await page.screenshot({path:output+'/'+device.name+'-list.png'});
 await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:output+'/'+device.name+'.png'});
 results.push({device:device.name,loading,heatmap,shenglong,aiai,stExcluded,unknown,missingCells,...audit});
 console.log({device:device.name,status:'PASS',checks:audit.checks,violations:0,heatmapRows:heatmap.count,missingCells});
}
await fs.writeFile(output+'/browser_results.json',JSON.stringify(results,null,2));
console.log({spaceId:task.spaceId,resultFile:output+'/browser_results.json'});
// Keep the task active until evidence has been inspected; finish separately after acceptance.
