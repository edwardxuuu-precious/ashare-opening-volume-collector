// Run only the audited, dependency-free calculator module extracted verbatim
// from the public Eastmoney quotechart2022.js bundle. No vendor code is ported.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const out = path.resolve(process.argv[2]);
const read = name => JSON.parse(fs.readFileSync(path.join(out, name), 'utf8'));
const source = fs.readFileSync(path.join(out, 'quotechart2022.js'), 'utf8');
const marker = '"./src/modules/tools/indicator/cyq.ts":';
const start = source.indexOf('function(e,t)', source.indexOf(marker));
const end = source.indexOf('},"./', start) + 1;
if (start < 0 || end <= start) throw new Error('Vendor module boundary missing');
const exactModule = source.slice(start, end);
const exportsObject = {};
vm.runInNewContext('(' + exactModule + ')(null, exports)', {exports: exportsObject}, {timeout: 2000});
const Calculator = exportsObject.CYQCalculator;
if (typeof Calculator !== 'function') throw new Error('Native export missing');
const result = {source_sha256:crypto.createHash('sha256').update(source).digest('hex'),
  module_sha256:crypto.createHash('sha256').update(exactModule).digest('hex'),
  module_locator:marker, settings:{factor:150,range:null,adjustment:'bfq',loaded_rows:210,end:'20261016'},stocks:{}};
const dates = ['2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16'];
for (const fixture of read('eastmoney-browser-histories.json')) {
  const lines = fixture.data.data.klines;
  const bars = lines.map(line => {
    const p = line.split(',');
    return {date:p[0],open:+p[1],close:+p[2],high:+p[3],low:+p[4],volume:+p[5],volume_money:+p[6],hsl:+p[10]};
  });
  const calculator = new Calculator(bars);
  const snapshots = {};
  for (const date of dates) {
    const i = bars.findIndex(b => b.date === date);
    if (i < 0) throw new Error('Requested date missing: ' + date);
    const native = calculator.calc(i);
    const windowBars = bars.slice(0,i+1);
    const low = Math.min(...windowBars.map(b=>b.low));
    const high = Math.max(...windowBars.map(b=>b.high));
    const step = Math.max(.01, (high-low)/149);
    const sum = native.x.reduce((a,b)=>a+b,0);
    const max = Math.max(...native.x);
    const peaks = native.x.map((v,k)=>v===max?k:-1).filter(k=>k>=0);
    snapshots[date]={date,rows:i+1,low,high,step,raw_total:sum,
      peak_indices:peaks,peak_prices:peaks.map(k=>native.y[k]),
      peak_unrounded_prices:peaks.map(k=>low+step*k),
      peak_share_percent:max/sum*100,
      benefit_percent:native.benefitPart*100,median_cost:native.avgCost,
      percent_chips:native.percentChips,
      profile:native.x.map((x,k)=>({price:low+step*k,display_price:native.y[k],share_percent:x/sum*100,raw:x}))};
  }
  result.stocks[fixture.code]={name:fixture.data.data.name,first_date:bars[0].date,
    last_date:bars.at(-1).date,bars,snapshots};
}
fs.writeFileSync(path.join(out,'eastmoney-native-calculated.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify({stocks:Object.keys(result.stocks).length,snapshots:20,module_sha256:result.module_sha256}));
