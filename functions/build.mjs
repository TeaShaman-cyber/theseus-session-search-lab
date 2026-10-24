import { build } from 'esbuild';
import { mkdir, rm } from 'node:fs/promises';

await rm(new URL('./dist/', import.meta.url), { recursive: true, force: true });
await mkdir(new URL('./dist/shard/', import.meta.url), { recursive: true });
await mkdir(new URL('./dist/router/', import.meta.url), { recursive: true });

for (const [entry, outfile] of [
  ['shard/index.mjs', 'dist/shard/index.mjs'],
  ['router/index.mjs', 'dist/router/index.mjs'],
]) {
  await build({
    entryPoints: [new URL(entry, import.meta.url).pathname],
    outfile: new URL(outfile, import.meta.url).pathname,
    bundle: true,
    platform: 'node',
    format: 'esm',
    target: 'node24',
    banner: { js: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);" },
    sourcemap: false,
    minify: false,
  });
}
