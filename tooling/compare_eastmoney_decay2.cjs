// Local experimental variant only. Never writes to source or production files.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const frozen = path.resolve(process.argv[2]);
const out = path.resolve(process.argv[3]);
fs.mkdirSync(out, {recursive:true});
const read = name => JSON.parse(fs.readFileSync(path.join(frozen,name),'utf8'));
const hash = text => crypto.createHash('sha256').update(text).digest('hex');
const source = fs.readFileSync(path.join(frozen,'quotechart2022.js'),'utf8');
const prior = read('eastmoney-native-calculated.json');
assert.equal(hash(source),prior.source_sha256);
const marker = '"./src/modules/tools/indicator/cyq.ts":';
const markerIndex = source.indexOf(marker);
assert(markerIndex>=0);
const start = source.indexOf('function(e,t)',markerIndex);
const end = source.indexOf('},"./',start)+1;
assert(start>=0 && end>start);
const originalModule = source.slice(start,end);
assert.equal(hash(originalModule),prior.module_sha256);
const before = 'A=Math.min(1,d.hsl/100||0)';
const after = 'A=Math.min(1,2*d.hsl/100||0)';
assert.equal(originalModule.split(before).length-1,1);
const modifiedModule = originalModule.replace(before,after);
assert.equal(modifiedModule.replace(after,before),originalModule);
function load(moduleSource) {
  const exportsObject={};
  vm.runInNewContext('('+moduleSource+')(null,exports)',{exports:exportsObject},{timeout:2000});
  assert.equal(typeof exportsObject.CYQCalculator,'function');
  return exportsObject.CYQCalculator;
}
const Original=load(originalModule), Derived=load(modifiedModule);
const dates=['2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16'];
const result={schema:'eastmoney-local-decay2.v1',
  claim_boundary:'Locally modified Eastmoney source replay; NOT an official client coefficient setting or official client result',
  source_sha256:hash(source), original_module_sha256:hash(originalModule),
  modified_module_sha256:hash(modifiedModule),
  patch:{before,after,replacements:1,
    semantics:'A=min(1,2*hsl/100); same A used for historical decay (1-A) AND new daily chip injection; other code unchanged'},
  settings:{coefficient:2,node_count:150,range:null,adjustment:'bfq',
    first_source_date:'2025-12-02',last_source_date:'2026-10-16',loaded_rows:210,
    each_snapshot_uses_only_rows_through_its_date:true,unchanged_threshold:null},
  input_file_sha256:hash(fs.readFileSync(path.join(frozen,'eastmoney-browser-histories.json'))),
  stocks:{},validation:{original_20_snapshots_exactly_reproduced:true,checks:[]}};
for(const fixture of read('eastmoney-browser-histories.json')) {
  const code=fixture.code;
  const lines=fixture.data.data.klines;
  assert.equal(lines.length,210);
  const bars=lines.map(line=>{const p=line.split(',');return {date:p[0],open:+p[1],close:+p[2],
    high:+p[3],low:+p[4],volume:+p[5],volume_money:+p[6],hsl:+p[10]};});
  assert.equal(bars[0].date,'2025-12-02');assert.equal(bars.at(-1).date,'2026-10-16');
  const original=new Original(bars), derived=new Derived(bars), snapshots={};
  for(const date of dates) {
    const index=bars.findIndex(b=>b.date===date);assert(index>=0);
    const baseline=original.calc(index), target=derived.calc(index);
    const saved=prior.stocks[code].snapshots[date];
    assert.deepEqual(Array.from(baseline.x),saved.profile.map(p=>p.raw));
    const prefix=bars.slice(0,index+1);
    const low=Math.min(...prefix.map(b=>b.low)), high=Math.max(...prefix.map(b=>b.high));
    const step=Math.max(.01,(high-low)/149), total=target.x.reduce((a,b)=>a+b,0);
    assert(total>0 && Number.isFinite(total));
    assert(target.x.every(x=>Number.isFinite(x)&&x>=0));
    const maximum=Math.max(...target.x), peaks=target.x.map((x,i)=>x===maximum?i:-1).filter(i=>i>=0);
    const profile=target.x.map((raw,i)=>({price:low+step*i,display_price:target.y[i],raw,share_percent:raw/total*100}));
    assert(Math.abs(profile.reduce((s,p)=>s+p.share_percent,0)-100)<1e-10);
    // A complete future suffix must not affect an earlier snapshot.
    const truncated=new Derived(prefix).calc(prefix.length-1);
    assert.deepEqual(Array.from(truncated.x),Array.from(target.x));
    snapshots[date]={date,rows:prefix.length,low,high,step,raw_total:total,
      peak_indices:peaks,peak_prices:peaks.map(i=>target.y[i]),
      peak_unrounded_prices:peaks.map(i=>low+step*i),peak_share_percent:maximum/total*100,
      benefit_percent:target.benefitPart*100,median_cost:target.avgCost,percent_chips:target.percentChips,profile};
    result.validation.checks.push({code,date,original_frozen_replay_exact:true,future_suffix_invariant:true,mass_normalized:true});
  }
  result.stocks[code]={name:fixture.data.data.name,bars,snapshots};
}
fs.writeFileSync(path.join(out,'eastmoney-local-decay2.json'),JSON.stringify(result,null,2));
fs.writeFileSync(path.join(out,'source-patch.json'),JSON.stringify({source_sha256:result.source_sha256,
  original_module_sha256:result.original_module_sha256,modified_module_sha256:result.modified_module_sha256,
  ...result.patch},null,2));
console.log(JSON.stringify({stocks:Object.keys(result.stocks).length,snapshots:result.validation.checks.length,
  coefficient:2,patch_replacements:1,original_replay_checks:20,future_suffix_checks:20}));
