// Bundles geometry.test.js (ES modules with extensionless imports, as the app's esbuild build expects)
// and runs it with Node.
const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

const esbuild = require(require.resolve("esbuild", { paths: [path.resolve(__dirname, "../../../../../frappe/node_modules")] }));
const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "cad-js-")), "test.mjs");
esbuild.buildSync({
	entryPoints: [path.join(__dirname, "geometry.test.js")],
	bundle: true,
	format: "esm",
	platform: "node",
	outfile: out,
	nodePaths: [path.resolve(__dirname, "../../../../node_modules")],
	logLevel: "error",
});
execFileSync(process.execPath, [out], { stdio: "inherit" });
