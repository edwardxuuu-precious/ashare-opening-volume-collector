import fs from 'node:fs';
import assert from 'node:assert/strict';
import {prepareLocalHistory,replayLocalHistory,sameLocalSnapshot} from '../src/lib/chip-local-history.ts';
import {parseChipHistory} from '../src/lib/chip-history.ts';
const out='outputs/chips-local-preview-20261020',cases=JSON.parse(fs.readFileSync(out+'/real-parity-input.json','utf8')),rows=[];
let days=0,profiles=0;
for(const {stock,expected} of cases){
 const started=performance.now(),frames=prepareLocalHistory(stock),elapsed=performance.now()-started;
 const row={code:stock.security_id,history_rows:stock.history.length,prepared_dates:Object.keys(frames).length,prepare_ms:elapsed,matched_dates:0,fallback_dates:0};
 for(const [day,server] of Object.entries(expected)){
  const local=frames[day];if(!local){row.fallback_dates++;continue;}
  parseChipHistory(local,stock,day);
  assert.equal(local.previous_date,server.previous_date);
  for(const [date,snapshot] of Object.entries(local.snapshots)){
   assert.ok(sameLocalSnapshot(snapshot,server.snapshots[date]),`${stock.security_id} ${day} ${date}`);
   for(const [i,mass] of snapshot.raw_node_masses.entries())assert.equal(Number(mass),Number(server.snapshots[date].raw_node_masses[i]),`raw ${stock.security_id} ${day} ${i}`);
   profiles++;
  }
  days++;row.matched_dates++;
 }
 if(stock.security_id!=='920196')assert.ok(row.matched_dates>250,'established stock must actually use direct previews');
 const corrupt=structuredClone(stock);corrupt.snapshots[stock.as_of].profile[0].share_percent='100';assert.deepEqual(prepareLocalHistory(corrupt),{},'failed sealed-snapshot guard must disable local path');
 rows.push(row);
}
const verification={dataset:cases[0].stock.dataset_id,model_source_sha256:'f95add1f60ec5fe4fdd5c9e9d6ffc8844b91283307b9525c61e8ba18a3529896',matched_dates:days,matched_snapshots:profiles,comparison:'strict numeric equality without tolerance; all nodes, raw masses, mean, peaks, fixed original-peak interval shares',rows};
fs.writeFileSync(out+'/real-parity-verification.json',JSON.stringify(verification,null,2));console.log(JSON.stringify(verification));
