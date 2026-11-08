// Uses only the pre-existing bundled document runtime. No npm installation.
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require('/Users/caoruixin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
(async () => {
  const root = path.resolve(__dirname,'publication-figures');
  for (const name of ['latency-by-scene','gpu-telemetry','utilization-cost']) {
    await sharp(path.join(root,name+'.svg')).png().toFile(path.join(root,name+'.png'));
  }
  console.log('Existing bundled Sharp rendered3 standalone data figures.');
})().catch(e => {console.error(e.message);process.exitCode=1;});
