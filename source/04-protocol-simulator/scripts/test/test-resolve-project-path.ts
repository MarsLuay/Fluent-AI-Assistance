import assert from "node:assert/strict";
import { resolveProjectPath } from "../../vite.config";
import path from "node:path";

console.log("Testing resolveProjectPath...");

// Should throw if path traverses out of project root
assert.throws(() => {
  resolveProjectPath("../../../../etc/passwd");
}, (error: Error) => {
  assert(error.message.includes("Path outside project root"));
  return true;
}, "Should throw for path outside project root");

assert.throws(() => {
  resolveProjectPath("../../some-other-project/file.ts");
}, (error: Error) => {
  assert(error.message.includes("Path outside project root"));
  return true;
}, "Should throw for path traversing sibling directories");

// Should resolve valid paths within project
assert.doesNotThrow(() => {
  resolveProjectPath("source/04-protocol-simulator/vite.config.ts");
}, "Should resolve paths inside the project");

// Verify that it correctly resolves a relative path to absolute
const resultPath = resolveProjectPath("source/04-protocol-simulator/vite.config.ts");
assert.equal(path.isAbsolute(resultPath), true, "Should return an absolute path");
assert(resultPath.endsWith(path.join("source", "04-protocol-simulator", "vite.config.ts")), "Should end with the correct relative path segments");

console.log("All tests passed!");
