// Run the same offline tests on Windows and Unix without requiring shell activation.
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
const virtualPython=process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python';
const python=process.env.STOCK_PYTHON||(existsSync(virtualPython)?virtualPython:process.platform==='win32'?'python':'python3');
const result=spawnSync(python,['-X','utf8','-m','unittest','discover','-s','tests/python','-v',...process.argv.slice(2)],{stdio:'inherit',env:{...process.env,PYTHONUTF8:'1'}});
if(result.error)throw result.error;
process.exit(result.status??1);
