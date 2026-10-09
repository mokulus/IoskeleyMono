// Print every Iosevka variant prime with its affected characters and option keys.
// Usage: node variants.mjs <iosevka checkout>
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const ios = path.resolve(process.argv[2]);
const Toml = createRequire(path.join(ios, "package.json"))("@iarna/toml");
const { parse } = await import(pathToFileURL(path.join(ios, "packages/param/src/variant.mjs")));

const data = Toml.parse(fs.readFileSync(path.join(ios, "params/variants.toml"), "utf8"));
const out = [];
for (const prime of parse(data).primes.values()) {
	const j = prime.toJson();
	out.push({
		key: j.key,
		hotChars: j.hotChars.join(""),
		slopeDependent: j.slopeDependent,
		variants: j.variants.map(v => v.key),
	});
}
process.stdout.write(JSON.stringify(out));
