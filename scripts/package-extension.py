#!/usr/bin/env python3
"""Build the production extension and publish a deterministic landing-page download."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / 'frontend/packages/extension'
BACKEND = 'https://api.tryblade.in'
env = {**os.environ, 'VITE_BACKEND_URL': BACKEND}
subprocess.run(['pnpm', '--filter', '@bladedlp/extension', 'build'], cwd=ROOT / 'frontend', env=env, check=True)
dist = EXTENSION / 'dist'
licenses = dist / 'licenses'
licenses.mkdir(exist_ok=True)
for dependency, license_file in (
    ('pdfjs-dist', 'LICENSE'),
    ('tesseract.js', 'LICENSE.md'),
    ('tesseract.js-core', 'LICENSE'),
):
    shutil.copyfile(EXTENSION / 'node_modules' / dependency / license_file,
                    licenses / f'{dependency}.txt')
shutil.copyfile(EXTENSION / 'node_modules/tesseract.js/dist/worker.min.js.LICENSE.txt',
                licenses / 'tesseract-worker.txt')
manifest = json.loads((dist / 'manifest.json').read_text())
assert manifest['manifest_version'] == 3
assert manifest['key'], 'A stable extension ID is required'
assert manifest['host_permissions'] == ['https://mail.google.com/*', BACKEND + '/*']
assert 'localhost' not in json.dumps(manifest)
assert manifest['version'] == json.loads((EXTENSION / 'package.json').read_text())['version']
assert (dist / 'src/offscreen/index.html').is_file(), 'Missing attachment scanner'
assert (dist / 'ocr/eng.traineddata.gz').is_file(), 'Missing packaged OCR data'
files = sorted(path for path in dist.rglob('*') if path.is_file())
for path in files:
    relative = path.relative_to(dist)
    assert not any(part.startswith('.') for part in relative.parts), f'Hidden build file: {relative}'
    assert path.suffix not in {'.map', '.ts', '.tsx', '.pem', '.key'}, f'Source or secret file: {relative}'
    if path.suffix in {'.js', '.html', '.json'}:
        content = path.read_text()
        assert '@vite/client' not in content and 'localhost:5174' not in content, f'Development runtime: {relative}'
output = ROOT / 'landing/public/downloads'
output.mkdir(parents=True, exist_ok=True)
filename = f"blade-extension-{manifest['version']}.zip"
archive = output / filename
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
    for path in files:
        entry = zipfile.ZipInfo('blade-extension/' + path.relative_to(dist).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry.external_attr = 0o644 << 16
        bundle.writestr(entry, path.read_bytes(), compresslevel=9)
release = {
    'version': manifest['version'],
    'filename': filename,
    'downloadPath': '/downloads/' + filename,
    'bytes': archive.stat().st_size,
    'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
    'backendUrl': BACKEND,
}
(ROOT / 'landing/src/lib/extension-release.json').write_text(json.dumps(release, indent=2) + '\n')
(output / (filename + '.sha256')).write_text(f"{release['sha256']}  {filename}\n")
print(json.dumps(release, indent=2))
