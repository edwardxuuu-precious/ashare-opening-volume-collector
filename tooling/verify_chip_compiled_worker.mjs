import fs from 'node:fs';
import vm from 'node:vm';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {sameLocalSnapshot} from '../src/lib/chip-local-history.ts';
const out='outputs/chips-local-preview-20261020',cases=JSON.parse(fs.readFileSync(out+'/real-parity-input.json','utf8'));
const worker=fs.readdirSync('dist/client/_next/static').find(n=>n.startsWith('chip-history.worker-')),code=fs.readFileSync('dist/client/_next/static/'+worker,'utf8');
let matched=0;
for(const {stock,expected} of cases){
 let reply;const self={postMessage:value=>{reply=value;}};vm.runInContext(code,vm.createContext({self}));self.onmessage({data:stock});
 assert.equal(reply.dataset_id,stock.dataset_id);assert.equal(reply.security_id,stock.security_id);
 if(stock.history.length===500)assert.equal(Object.keys(reply.frames).length,290);
 for(const [day,local] of Object.entries(reply.frames)){
  const server=expected[day];assert.ok(server);assert.equal(local.previous_date,server.previous_date);
  for(const [date,snapshot] of Object.entries(local.snapshots)){
   assert.ok(sameLocalSnapshot(snapshot,server.snapshots[date]),`${stock.security_id} ${day} ${date}`);
   for(const [i,mass] of snapshot.raw_node_masses.entries())assert.equal(Number(mass),Number(server.snapshots[date].raw_node_masses[i]));
  }
  matched++;
 }
}
const result={worker,sha256:crypto.createHash('sha256').update(code).digest('hex'),matched_dates:matched,matched_snapshots:matched*2,all_pass:true};
fs.writeFileSync(out+'/compiled-worker-verification.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
