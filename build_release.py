"""Build a ZIP containing source, documentation, tests and license notices."""
from pathlib import Path
import hashlib
import zipfile


def build():
    root = Path(__file__).resolve().parent
    output = root / 'dist' / 'FusionBackupManager-1.0.5.zip'
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(root.rglob('*')):
            relative = source.relative_to(root)
            if (not source.is_file() or any(p in ('dist', '__pycache__', '.git') for p in relative.parts)
                    or source.suffix == '.pyc' or any(p.startswith(('tmp', 'installed_backup_')) for p in relative.parts)):
                continue
            archive.write(source, 'FusionBackupManager/' + relative.as_posix())
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(checksum + '  ' + output.name + '\n', encoding='ascii')
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Release ZIP integrity check failed')
        for required in ('FusionBackupManager.py', 'FusionBackupManager.manifest', 'core.py', 'fusion_adapter.py', 'progress.py', 'LICENSE.FusionProjectExporter'):
            assert 'FusionBackupManager/FusionBackupManager/' + required in archive.namelist()
    print(output)
    print('SHA-256: ' + checksum)


if __name__ == '__main__':
    build()
