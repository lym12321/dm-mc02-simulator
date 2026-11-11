import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const output = process.argv[2];
if (!output) throw new Error("Usage: node tools/collect-desktop-notices.mjs <output-directory>");
mkdirSync(output, { recursive: true });
const run = (program, args) => execFileSync(program, args, { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 }).trim();
const host = run("rustc", ["-vV"]).match(/^host: (.+)$/m)[1];
const metadata = JSON.parse(run("cargo", ["metadata", "--locked", "--manifest-path", "src-tauri/Cargo.toml", "--format-version", "1", "--filter-platform", host]));
const resolved = new Set(metadata.resolve.nodes.map(node => node.id));
const supplementalRoot = fileURLToPath(new URL("../third_party/rust-notices/", import.meta.url));
const supplemental = JSON.parse(readFileSync(path.join(supplementalRoot, "PROVENANCE.json"), "utf8"));
const records = [];
for (const pkg of metadata.packages.filter(pkg => pkg.source && resolved.has(pkg.id))) {
  const source = path.dirname(pkg.manifest_path);
  const destination = path.join(output, `${pkg.name}-${pkg.version}`);
  const files = new Set(readdirSync(source).filter(name => /^(licen[cs]e|copying|notice)/i.test(name)));
  if (pkg.license_file) files.add(pkg.license_file);
  const copied = [];
  for (const name of files) {
    if (!existsSync(path.join(source, name))) continue;
    mkdirSync(destination, { recursive: true });
    cpSync(path.join(source, name), path.join(destination, name), { recursive: true });
    copied.push(name);
  }
  let noticeSource = { kind: "cargo-package" };
  if (!copied.length) {
    const upstream = supplemental.packages.find(item => item.name === pkg.name && item.version === pkg.version);
    if (!upstream) throw new Error(`No license notice found for ${pkg.name} ${pkg.version}. Add its upstream notice and provenance before packaging.`);
    mkdirSync(destination, { recursive: true });
    for (const notice of upstream.notices) {
      cpSync(path.join(supplementalRoot, upstream.directory, notice.file), path.join(destination, notice.file));
      copied.push(notice.file);
    }
    noticeSource = { kind: "vendored-upstream", repository: upstream.repository, revision: upstream.revision, files: upstream.notices };
  }
  records.push({ name: pkg.name, version: pkg.version, license: pkg.license, repository: pkg.repository, notices: copied, notice_source: noticeSource });
}
const redistributables = [];
for (const item of supplemental.redistributables) {
  if (!records.some(pkg => pkg.name === item.applies_to.name && pkg.version === item.applies_to.version)) continue;
  const destination = path.join(output, item.directory);
  mkdirSync(destination, { recursive: true });
  for (const file of item.notices) cpSync(path.join(supplementalRoot, item.directory, file), path.join(destination, file));
  redistributables.push(item);
}
cpSync(path.join(supplementalRoot, "PROVENANCE.json"), path.join(output, "supplemental-provenance.json"));
const rustDocs = path.join(run("rustc", ["--print", "sysroot"]), "share", "doc", "rust");
const rustOutput = path.join(output, "rust-standard-library");
mkdirSync(rustOutput, { recursive: true });
cpSync(path.join(rustDocs, "COPYRIGHT-library.html"), path.join(rustOutput, "COPYRIGHT-library.html"));
cpSync(path.join(rustDocs, "licenses"), path.join(rustOutput, "licenses"), { recursive: true });
writeFileSync(path.join(output, "dependencies.json"), JSON.stringify({ target: host, packages: records, redistributables }, null, 2) + "\n");
console.log(`Desktop dependency notices: ${output}`);
