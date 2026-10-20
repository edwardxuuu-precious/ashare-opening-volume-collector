// Production readers on real downloaded published artifacts, with no synthetic prices.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {parseMarketManifest,parseMarketScreen,parseMarketStock,currentMarketReasons,tradingGeometry} from '../src/lib/chip-market.ts';

const dataset=process.argv[2];
assert.match(dataset??'',/^[0-9a-f]{16}$/);
const dir=path.resolve('var/chip-publication-validation',dataset);
const read=name=>JSON.parse(fs.readFileSync(path.join(dir,name),'utf8'));
const manifest=parseMarketManifest(read('manifest.json'));
const rows=parseMarketScreen(read('screening.json'),manifest);
const byCode=new Map(rows.map(r=>[r.security_id,r]));
let checked=0,obsoleteNotesReplaced=0;
for(const file of fs.readdirSync(dir).filter(f=>/^\d{6}\.json$/.test(f))){
  const code=file.slice(0,6),value=parseMarketStock(read(file),code,manifest);
  assert.deepEqual(value.screening_row,byCode.get(code));
  const row=byCode.get(code),notes=currentMarketReasons(value,row);
  assert.ok(row.reasons.every(reason=>notes.includes(reason)));
  assert.ok(value.reasons.filter(reason=>/^(MODEL_|CHIP_MODEL_|SOURCE_OR_MODEL_ERROR:|BSE_HISTORY_UNAVAILABLE)/.test(reason)).every(reason=>notes.includes(reason)));
  if(row.prefilter_status==='UNKNOWN')assert.ok(value.reasons.every(reason=>notes.includes(reason)));
  if(row.prefilter_status!=='UNKNOWN'&&!row.reasons.includes('LIMIT_HISTORY_INCOMPLETE')){
    assert.ok(!notes.includes('LIMIT_HISTORY_INCOMPLETE'));
    if(value.reasons.includes('LIMIT_HISTORY_INCOMPLETE'))obsoleteNotesReplaced++;
  }
  for(const period of [60,120,250,500]){
    const g=tradingGeometry(value.history,value.snapshots[manifest.as_of],value.snapshots[manifest.previous_date]??null,period);
    assert.ok(g);assert.equal(g.candles.length,Math.min(period,value.history.length));
    for(const candle of g.candles){
      assert.ok(Number.isFinite(candle.x)&&Number.isFinite(candle.volumeY));
      assert.ok(candle.volumeY>=g.volTop-1e-9&&candle.volumeY<=g.volBottom+1e-9);
      assert.equal(candle.closeY,g.y(Number(candle.close)));
    }
    assert.ok(g.chips.every(c=>Number.isFinite(c.y)&&Number.isFinite(c.currentWidth)&&c.currentWidth>=0));
    const mass=g.chips.reduce((v,c)=>v+c.mass,0)+g.outsideShare;
    assert.ok(Math.abs(mass-100)<1e-7);
  }
  checked++;
}
const result={dataset_id:dataset,real_screen_rows:rows.length,real_stock_frames:checked,periods:[60,120,250,500],
  production_readers_pass:true,candle_and_volume_geometry_pass:true,coefficient:2,unchanged_threshold:null,
  current_screening_notes_checked:true,real_frames_with_obsolete_notes_replaced:obsoleteNotesReplaced,model_and_current_unknown_warnings_preserved:true,
  scope:'Production parsing and chart geometry on downloaded published data; not an authenticated browser screenshot.'};
fs.writeFileSync(`outputs/chips-coverage-20261018/frontend-publication-validation-${dataset}.json`,JSON.stringify(result,null,2)+'\n');
process.stdout.write(JSON.stringify(result)+'\n');
