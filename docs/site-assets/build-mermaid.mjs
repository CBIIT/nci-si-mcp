// Bundle the external-dependency entry point: the vendor UMD embeds its own old KaTeX.
import { build } from "esbuild";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL(".", import.meta.url));
const require = createRequire(import.meta.url);
const config = JSON.parse(await readFile(new URL("package.json", import.meta.url)));
const katexPackage = require.resolve("katex/package.json", {
  paths: [require.resolve("mermaid")],
});
const katex = JSON.parse(await readFile(katexPackage));
assert.equal(katex.version, config.overrides.mermaid.katex, "Run npm ci before rebuilding");

const result = await build({
  absWorkingDir: root,
  stdin: {
    contents: 'import mermaid from "mermaid"; globalThis.mermaid = mermaid;',
    resolveDir: root,
    sourcefile: "mermaid-entry.js",
  },
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2022",
  minify: true,
  legalComments: "linked",
  metafile: true,
  write: false,
  outfile: "node_modules/.cache/nci-si/mermaid.min.js",
});
const inputs = Object.keys(result.metafile.inputs);
assert(inputs.some((path) => path.endsWith("katex/dist/katex.mjs")), "KaTeX was not bundled");
assert(!inputs.some((path) => path.endsWith("mermaid.min.js")), "Do not use the vendor UMD");
await mkdir(new URL("node_modules/.cache/nci-si/", import.meta.url), { recursive: true });
for (const output of result.outputFiles) await writeFile(output.path, output.contents);
console.log(`Built Mermaid ${config.dependencies.mermaid} with KaTeX ${katex.version}`);
