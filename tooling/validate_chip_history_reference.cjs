// Bounded offline parity against the previously audited frozen vendor module.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),out=path.resolve(process.argv[2]);
const frozen=path.join(root,'outputs/chips-eastmoney-20261019');
const source=fs.readFileSync(path.join(frozen,'quotechart2022.js'),'utf8');
const prior=JSON.parse(fs.readFileSync(path.join(frozen,'eastmoney-native-calculated.json'),'utf8'));
const hash=t=>crypto.createHash('sha256').update(t).digest('hex');
assert.equal(hash(source),prior.source_sha256);
const start=source.indexOf('function(e,t)',source.indexOf('"./src/modules/tools/indicator/cyq.ts":'));
const end=source.indexOf('},"./',start)+1,original=source.slice(start,end);
assert.equal(hash(original),prior.module_sha256);
const before='A=Math.min(1,d.hsl/100||0)',after='A=Math.min(1,2*d.hsl/100||0)';
assert.equal(original.split(before).length-1,1);
const exportsObject={};vm.runInNewContext('('+original.replace(before,after)+')(null,exports)',{exports:exportsObject},{timeout:2000});
const Calculator=exportsObject.CYQCalculator;
const input=JSON.parse(fs.readFileSync(path.join(out,'reference-cases.json'),'utf8'));
let snapshots=0,nodes=0,anchors=0;
const checks=[];
for(const entry of input.cases){
 const data=entry.input_bars,calculator=new Calculator(data),byDay={};
 for(const [day,snapshot] of Object.entries(entry.expected.snapshots)){
  const index=data.findIndex(b=>b.date===day);assert.ok(index>=0);
  const raw=Array.from(calculator.calc(index).x);
  for(const repair of snapshot.roundoff_repairs??[]){
   assert.equal(raw[repair.node_index],+repair.original_raw_mass);
   assert.ok(repair.events.every(e=>e.grid_numerator===Math.round(+e.exact_endpoint_cny*100)*149));
   raw[repair.node_index]=0;
  }
  assert.deepEqual(raw,snapshot.raw_node_masses.map(Number));
  const sum=raw.reduce((a,b)=>a+b,0),shares=raw.map(v=>v/sum*100);
  assert.deepEqual(shares,snapshot.profile.map(p=>+p.share_percent));
  const prefix=data.slice(0,index+1),low=Math.min(...prefix.map(b=>b.low)),high=Math.max(...prefix.map(b=>b.high)),step=Math.max(.01,(high-low)/149);
  assert.deepEqual(raw.map((v,i)=>low+step*i),snapshot.profile.map(p=>+p.price_cny));
  const peak=Math.max(...raw),indices=raw.map((v,i)=>v===peak?i:-1).filter(i=>i>=0);
  assert.deepEqual(indices,snapshot.peak_node_indices);
  byDay[day]={shares,low:Math.round(low*100),span:Math.max(149,Math.round(high*100)-Math.round(low*100)),indices};
  snapshots++;nodes+=150;
 }
 const previous=byDay[entry.expected.previous_date],current=byDay[entry.date];
 if(previous&&current){
  const coordinate=(s,i)=>BigInt(s.low)*149n+BigInt(s.span)*BigInt(i);
  for(const i of previous.indices){
   const center=2n*coordinate(previous,i),lower=center-BigInt(previous.span),upper=center+BigInt(previous.span);
   const mass=s=>s.shares.reduce((total,v,k)=>{const c=2n*coordinate(s,k);return lower<=c&&c<upper?total+v:total;},0);
   const a=mass(previous),b=mass(current),price=entry.expected.snapshots[entry.expected.previous_date].peak_prices_cny[previous.indices.indexOf(i)];
   assert.equal(a,+entry.expected.snapshots[entry.expected.previous_date].comparison_anchor_shares_percent[price]);
   assert.equal(b,+entry.expected.snapshots[entry.date].comparison_anchor_shares_percent[price]);
   if(entry.expected.comparison){const row=entry.expected.comparison.anchors.find(a=>a.anchor_price_cny===price);assert.equal(b-a,+row.change_percentage_points);}
   anchors++;
  }
 }
 checks.push({stock:entry.stock,date:entry.date,raw_share_price_and_original_peak_exact:true});
}
const result={all_pass:true,reference:'Frozen Eastmoney module locally set to coefficient 2; not official client certification',source_sha256:hash(source),module_sha256:hash(original),pairs:input.cases.length,snapshots,raw_nodes:nodes,share_nodes:nodes,anchors,max_difference:0,unchanged_threshold:null,checks};
fs.writeFileSync(path.join(out,'reference-parity.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify({pairs:result.pairs,snapshots,nodes,anchors,max_difference:0}));
