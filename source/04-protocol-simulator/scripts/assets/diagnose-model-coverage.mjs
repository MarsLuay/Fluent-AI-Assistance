#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const simulatorRoot = path.resolve(scriptDir, "../..");
const diagnosticPath = path.resolve(simulatorRoot, "../tools/simulator/diagnose_model_coverage.py");
const forwardedArgs = process.argv.slice(2);

const candidates =
  process.platform === "win32"
    ? [
        { command: "py", args: ["-3"] },
        { command: "python", args: [] },
        { command: "python3", args: [] }
      ]
    : [
        { command: "python3", args: [] },
        { command: "python", args: [] },
        { command: "py", args: ["-3"] }
      ];

for (const candidate of candidates) {
  const result = spawnSync(candidate.command, [...candidate.args, diagnosticPath, ...forwardedArgs], {
    cwd: simulatorRoot,
    stdio: "inherit",
    shell: false
  });

  if (result.error?.code === "ENOENT") continue;
  if (result.error) {
    console.error(`Failed to run ${candidate.command}: ${result.error.message}`);
    process.exit(1);
  }
  process.exit(result.status ?? 0);
}

console.error("Could not find Python. Install Python 3 or make py/python3/python available on PATH.");
process.exit(1);
