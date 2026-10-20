import fs from 'node:fs';
import sharp from 'sharp';
const dir='outputs/chips-daily-20261018/';
let svg=fs.readFileSync(dir+'live-chart.svg','utf8');
if(!svg.includes('xmlns='))svg=svg.replace('<svg','<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="568"');
await sharp(Buffer.from(svg)).png().toFile(dir+'live-chart.png');
console.log('Exported the actual rendered SVG at 1200 x 568');
