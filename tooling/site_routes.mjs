import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [major, minor] = process.versions.node.split('.').map(Number);
if (major < 22 || (major === 22 && minor < 13)) {
  throw new Error('Node.js >=22.13.0 is required to read sitePaths.');
}
if (process.argv.length !== 3) {
  throw new Error('Pass the project root to read its sitePaths.');
}
const { sitePaths } = await import(pathToFileURL(resolve(process.argv[2], 'src/lib/site-routes.ts')).href);
process.stdout.write(JSON.stringify(sitePaths));
