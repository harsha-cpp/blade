import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
const release = JSON.parse(readFileSync(new URL('../src/lib/extension-release.json', import.meta.url)));
const archive = readFileSync(new URL(`../public/downloads/${release.filename}`, import.meta.url));
if (archive.byteLength !== release.bytes || createHash('sha256').update(archive).digest('hex') !== release.sha256) {
  throw new Error('Extension download does not match the release metadata. Run python3 scripts/package-extension.py from the repository root.');
}
if (release.downloadPath !== `/downloads/${release.filename}` || release.backendUrl !== 'https://api.tryblade.in') {
  throw new Error('Invalid extension release target');
}
console.log(`Verified Blade ${release.version} download (${release.bytes} bytes)`);
