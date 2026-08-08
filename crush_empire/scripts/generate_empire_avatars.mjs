/*
 * Generate the Crush Empire card portraits.
 *
 *   stdin    JSON array of {seed, file}
 *   argv[2]  output directory
 *
 * DiceBear "lorelei" (CC0 1.0) — deterministic per seed, so re-running never
 * churns committed files. Invoked by `manage.py generate_empire_avatars` at
 * dev time; production only ever serves the committed SVGs.
 */
import { lorelei } from "@dicebear/collection";
import { createAvatar } from "@dicebear/core";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const outDir = process.argv[2];
if (!outDir) {
    console.error("usage: node generate_empire_avatars.mjs <outDir> < seeds.json");
    process.exit(2);
}

let raw = "";
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) raw += chunk;
const items = JSON.parse(raw);

mkdirSync(outDir, { recursive: true });
for (const { seed, file } of items) {
    const svg = createAvatar(lorelei, { seed }).toString();
    writeFileSync(join(outDir, file), svg);
}
console.log(`wrote ${items.length} portrait(s) to ${outDir}`);
