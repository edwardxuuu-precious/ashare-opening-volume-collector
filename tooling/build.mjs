// Portable protected build: use the caller's Python, then preserve both guards.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
const python=process.env.STOCK_PYTHON||(process.platform==='win32'?'python':'python3');
function run(command,args){const r=spawnSync(command,args,{stdio:'inherit',env:process.env});if(r.error)throw r.error;if(r.status!==0)process.exit(r.status??1);}
run(python,['deploy/auth/prepare_build.py']);
const cli='node_modules/vinext/dist/cli.js';
if(!existsSync(cli))throw Error('Install locked dependencies with npm ci first.');
run(process.execPath,[cli,'build']);
run(python,['deploy/auth/strip_public_data.py']);
