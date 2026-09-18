import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

export function setup() {
  const fixtureRoot = resolve('tests/fixtures');
  const result = spawnSync(process.execPath, [resolve('node_modules/vite/bin/vite.js'), 'build'], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      SCOTUS_DATA_ROOT: resolve(fixtureRoot, 'data'),
      SCOTUS_MANIFEST_ROOT: resolve(fixtureRoot, 'manifests')
    },
    encoding: 'utf8'
  });
  if (result.status !== 0) {
    throw new Error(`Fixture site build failed:\n${result.stdout}\n${result.stderr}`);
  }
}
