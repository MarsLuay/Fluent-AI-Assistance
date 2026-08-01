import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildSampleZeiaSampleCache, SAMPLE_ZEIA_CACHE_RELATIVE } from "./zeiaSampleCache";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");

async function main(): Promise<void> {
  const force = process.argv.includes("--force");
  const started = Date.now();
  const result = await buildSampleZeiaSampleCache(projectRoot, { force });
  const elapsedSeconds = ((Date.now() - started) / 1000).toFixed(1);
  console.log(`Wrote ${result.files.length} cached ZEIA sample files.`);
  console.log(`Cache: ${path.relative(projectRoot, result.cacheDir).replace(/\\/g, "/") || SAMPLE_ZEIA_CACHE_RELATIVE}`);
  console.log(`Done in ${elapsedSeconds}s.`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
