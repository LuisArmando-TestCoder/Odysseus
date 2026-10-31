// Copies the installer "executables" from installer/bootstrap/ (the single
// source of truth) into public/ so the download page always serves the same
// scripts as the repo. Runs before every build (npm "prebuild").
const fs = require("fs");
const path = require("path");

const src = path.resolve(__dirname, "..", "..", "installer", "bootstrap");
const dst = path.resolve(__dirname, "..", "public");
fs.mkdirSync(dst, { recursive: true });
for (const name of fs.readdirSync(src)) {
  if (!/^install-.*\.(sh|ps1|cmd)$/.test(name)) continue;
  fs.copyFileSync(path.join(src, name), path.join(dst, name));
  console.log(`synced ${name}`);
}
