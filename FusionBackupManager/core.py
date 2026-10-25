"""Backup engine. No Fusion imports, credentials, or cloud write operations."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from contextlib import contextmanager


def now():
    return datetime.now(timezone.utc).isoformat()


def component(name, identity):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name)).strip(' .')[:32] or 'Untitled'
    if stem.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        stem = '_' + stem
    return stem + '__' + hashlib.sha256(str(identity).encode()).hexdigest()[:16]


def fingerprint(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
            size += len(block)
    if not size:
        raise ValueError('Empty backup file')
    return size, digest.hexdigest()


def publish(source, target):
    """Atomic no-overwrite publication on the same volume."""
    if os.name == 'nt':
        # On Windows rename fails if the destination exists (unlike POSIX).
        os.rename(source, target)
    else:
        os.link(source, target)


@contextmanager
def staging_directory(parent, warning):
    """A cleanup failure must not discard a successfully published export."""
    parent = Path(parent).resolve()
    temporary = Path(tempfile.mkdtemp(prefix='.export-', dir=parent)).resolve()
    if temporary.parent != parent or not temporary.name.startswith('.export-'):
        raise ValueError('Unsafe staging directory')
    try:
        yield temporary
    finally:
        for attempt in range(3):
            try:
                shutil.rmtree(temporary)
                break
            except FileNotFoundError:
                break
            except OSError as exc:
                if attempt == 2:
                    warning('Temporary cleanup deferred (backup output is retained): ' + str(temporary) + ': ' + str(exc))
                else:
                    time.sleep(0.1 * (attempt + 1))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@dataclass
class Item:
    file_id: str
    version_id: str
    version: int
    name: str
    project_id: str
    project_name: str
    folders: list = field(default_factory=list)  # [(original name, folder id)]
    modified: object = None
    extension: str = 'f3d'
    references: list = field(default_factory=list)
    reference_state: str = 'none'  # none, external, unknown
    warnings: list = field(default_factory=list)
    source: object = field(default=None, repr=False)

    def metadata(self):
        return {k: v for k, v in vars(self).items() if k != 'source'}

    def key(self):
        return self.file_id, self.version_id

    def location(self):
        return '/'.join([self.project_name] + [f[0] for f in self.folders] + [self.name])


DEFAULTS = dict(backup_directory='', backup_scope='selected', project_ids=[],
                export_fusion_archive=True, export_step_for_assemblies=True,
                export_step=False, incremental=True, keep_versions=True)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / 'manifest.json'
        self.data = {'schema_version': 1, 'files': [], 'runs': []}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            if (self.data.get('schema_version') != 1 or not isinstance(self.data.get('files'), list)
                    or not isinstance(self.data.get('runs'), list)):
                raise ValueError('Invalid manifest schema; preserve it and follow TESTING.md recovery')
            for entry in self.data['files']:
                if not isinstance(entry, dict) or not all(k in entry for k in ('file_id', 'version_id', 'local_path', 'sha256', 'size', 'format')):
                    raise ValueError('Invalid manifest entry')
                self.safe_path(entry['local_path'])

    def safe_path(self, relative):
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError('Invalid relative backup path')
        path = (self.root / relative).resolve()
        if self.root not in path.parents:
            raise ValueError('Backup path escapes destination')
        return path

    def valid(self, entry):
        try:
            return (entry.get('status') == 'verified' and
                    fingerprint(self.safe_path(entry['local_path'])) == (entry['size'], entry['sha256']))
        except (OSError, ValueError, KeyError):
            return False

    def current(self, item, fmt):
        return any(e['file_id'] == item.file_id and e['version_id'] == item.version_id
                   and e['format'] == fmt and self.valid(e) for e in self.data['files'])

    def save(self):
        if self.path.exists():
            # Preserve the previous on-disk revision before replacing the current one.
            old = json.loads(self.path.read_text(encoding='utf-8'))
            atomic_json(self.root / 'manifest.previous.json', old)
        atomic_json(self.path, self.data)

    def relative_destination(self, item, fmt, reserve=0):
        """Keep legacy names where possible; fit long paths without moving old data."""
        parts = [component(item.project_name, item.project_id)]
        parts += [component(name, ident) for name, ident in item.folders]
        stem = component(item.name, item.file_id) + f'_v{item.version}__' + hashlib.sha256(item.version_id.encode()).hexdigest()[:16]
        def fits(relative):
            # Reserve room for a no-overwrite repair suffix and Fusion's staging path.
            return len(str(self.root / relative)) + reserve <= 240
        relative = Path(*parts, stem + '.' + fmt)
        if fits(relative):
            return relative
        short_stem = component(item.name[:8], item.file_id) + f'_v{item.version}__' + hashlib.sha256(item.version_id.encode()).hexdigest()[:16]
        short_parts = [component(item.project_name[:8], item.project_id)]
        short_parts += [component(name[:6], ident) for name, ident in item.folders]
        relative = Path(*short_parts, short_stem + '.' + fmt)
        if fits(relative):
            return relative
        # Arbitrarily deep trees cannot fit a legacy Windows path. Retain the full
        # hierarchy in the manifest and group by the entire folder-ID chain.
        chain = json.dumps([item.project_id, [ident for _, ident in item.folders]], ensure_ascii=False)
        folder_key = hashlib.sha256(chain.encode()).hexdigest()[:32]
        relative = Path(short_parts[0], '_deep__' + folder_key, short_stem + '.' + fmt)
        if fits(relative):
            return relative
        # Extremely long destination roots still cannot be repaired safely.
        raise ValueError('Destination path too long even after compaction; choose a shorter backup directory')

    def destination(self, item, fmt):
        path = self.safe_path(self.relative_destination(item, fmt).as_posix())
        if path.exists():
            relative = self.relative_destination(item, fmt, reserve=10)
            candidate = self.safe_path(relative.as_posix())
            while True:
                path = candidate.with_name(candidate.stem + '__' + uuid.uuid4().hex[:8] + candidate.suffix)
                if not path.exists():
                    break
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def formats(item, settings):
    if item.extension not in ('f3d', 'f3z'):
        # Fusion drawings/electronics are not generic downloadable files.
        if item.extension in ('f2d', 'fsch', 'fbrd', 'flbr') or not re.fullmatch(r'[a-z0-9]{1,12}', item.extension):
            raise ValueError('Unsupported Fusion file type: ' + item.extension)
        return [item.extension]
    result = []
    if item.reference_state == 'none' and settings['export_fusion_archive']:
        result.append('f3d')
    if settings.get('export_step') or (item.reference_state != 'none' and settings['export_step_for_assemblies']):
        result.append('step')
    return result


class Cancelled(Exception):
    pass


class Backup:
    def __init__(self, root, adapter, settings=None, progress=None, cancelled=None, activity=None):
        self.root = Path(root).resolve()
        self.adapter = adapter
        self.settings = dict(DEFAULTS, **(settings or {}))
        self.progress = progress or (lambda result, name: None)
        self.cancelled = cancelled or (lambda: False)
        self.activity = activity or (lambda event: None)

    def report(self, result, phase, item=None, fmt=''):
        self.activity(dict(phase=phase, project=item.project_name if item else '',
                           folder='/'.join(f[0] for f in item.folders) or 'プロジェクト直下' if item else '',
                           file=item.name if item else '', format=fmt,
                           counts={k: result[k] for k in ('total', 'total_known', 'completed', 'exported', 'skipped', 'failed')}))
        self.progress(result, item.name if item else '')

    def run(self, projects):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / '.backup.lock'
        # Exclusive local lock; a stale lock must be inspected manually.
        with lock.open('x', encoding='utf-8') as stream:
            stream.write(f'pid={os.getpid()} started={now()}')
        result = dict(started=now(), ended=None, status='FAILED', total=0, total_known=False, completed=0,
                      exported=0, skipped=0, failed=0, manual=[], warnings=[], errors=[], references=[])
        log_dir = self.root / 'logs'
        log = None
        try:
            log_dir.mkdir(exist_ok=True)
            log_path = log_dir / (datetime.now().strftime('%Y-%m-%d_%H%M%S') + '_' + uuid.uuid4().hex[:8] + '.log')
            result['log_path'] = str(log_path)
            log = log_path.open('w', encoding='utf-8')
            self.log = lambda msg: (log.write(now() + ' ' + msg + '\n'), log.flush())
            self.log('START destination=' + str(self.root))
            self.report(result, 'preparing')
            store = Store(self.root)  # malformed manifest fails before any export
            set_trace = getattr(self.adapter, 'set_trace', None)
            if callable(set_trace):
                set_trace(lambda event: self.log('ACTIVITY ' + json.dumps(event, ensure_ascii=False)))
            stream = getattr(self.adapter, 'iter_items', None)
            streaming = callable(stream)
            if streaming:
                items = stream(projects, self.cancelled, result['warnings'])
            else:
                items, warnings = self.adapter.enumerate(projects, self.cancelled)
                result['warnings'].extend(warnings)
                result['total'] = len(items)
                result['total_known'] = True
            for item in items:
                if self.cancelled():
                    raise Cancelled()
                if streaming:
                    result['total'] += 1
                for ref in item.references:
                    result['references'].append(dict(source_file_id=item.file_id, source_version_id=item.version_id, **ref))
                self.report(result, 'checking', item)
                self.log('FILE ' + json.dumps(item.metadata(), ensure_ascii=False))
                result['warnings'].extend(item.warnings)
                if item.extension in ('f3d', 'f3z') and not self.settings['export_fusion_archive']:
                    result['warnings'].append(item.location() + ': native archive disabled; STEP preserves shape only')
                if item.reference_state != 'none':
                    result['manual'].append(dict(file_id=item.file_id, version_id=item.version_id,
                                                  location=item.location(), reason='Manual F3Z export and restore test required; references=' + item.reference_state))
                changed = False
                file_failed = False
                try:
                    wanted = formats(item, self.settings)
                    if not wanted:
                        raise ValueError('No export format enabled for this file')
                    for fmt in wanted:
                        self.report(result, 'checking', item, fmt)
                        if self.cancelled():
                            raise Cancelled()
                        if self.settings['incremental'] and store.current(item, fmt):
                            self.log('SKIP verified ' + fmt)
                            self.report(result, 'skipped', item, fmt)
                            continue
                        target = store.destination(item, fmt)
                        # Export into an isolated sibling directory, with the real extension.
                        def cleanup_warning(message):
                            result['warnings'].append(message)
                            self.log('WARNING ' + message)
                        with staging_directory(target.parent, cleanup_warning) as temporary:
                            temp = temporary / ('payload.' + fmt)
                            self.report(result, 'exporting', item, fmt)
                            if self.cancelled():
                                raise Cancelled()
                            self.adapter.export(item, fmt, temp)
                            self.report(result, 'verifying', item, fmt)
                            size, digest = fingerprint(temp)
                            self.report(result, 'publishing', item, fmt)
                            publish(temp, target)
                        entry = dict(item.metadata(), local_path=target.relative_to(self.root).as_posix(),
                                     format=fmt, size=size, sha256=digest, status='verified',
                                     verification='bytes_sha256_only; Fusion restore untested', backed_up_at=now())
                        store.data['files'].append(entry)
                        self.report(result, 'recording', item, fmt)
                        store.save()
                        if not changed:
                            result['exported'] += 1
                        changed = True
                        self.log('EXPORTED ' + entry['local_path'])
                except Cancelled:
                    raise
                except Exception as exc:
                    file_failed = True
                    result['failed'] += 1
                    result['errors'].append(item.location() + ': ' + str(exc))
                    self.log('ERROR ' + result['errors'][-1])
                if not changed and not file_failed:
                    result['skipped'] += 1
                result['completed'] += 1
                self.report(result, 'file_error' if file_failed else 'file_done', item)
                if self.cancelled():
                    raise Cancelled()
            result['total_known'] = True
            if self.cancelled():
                raise Cancelled()
            result['status'] = ('FAILED' if result['failed'] and result['failed'] == result['total']
                                else 'PARTIAL' if result['failed'] or result['manual'] or result['warnings'] else 'SUCCESS')
        except Cancelled:
            result['status'] = 'CANCELLED'
        except Exception as exc:
            result['errors'].append(str(exc))
            result['status'] = 'FAILED'
        finally:
            result['ended'] = now()
            try:
                if 'store' in locals():
                    self.report(result, 'finalizing')
                    for ref in result['references']:
                        ref['backed_up'] = any(e['file_id'] == ref['file_id'] and e['version_id'] == ref['version_id']
                                              and e['format'] == 'f3d' and store.valid(e) for e in store.data['files'])
                    store.data['runs'].append(result)
                    store.save()
                atomic_json(self.root / 'last-result.json', result)
                if log:
                    self.log('END ' + json.dumps(result, ensure_ascii=False))
            except Exception as exc:
                result['status'] = 'FAILED'
                result['errors'].append('Could not persist final report: ' + str(exc))
                if log:
                    self.log('FAILED final report persistence: ' + str(exc))
                try:
                    atomic_json(self.root / 'last-result.json', result)
                except OSError:
                    pass  # The returned UI result and error log still report this failure.
            finally:
                if 'items' in locals() and callable(getattr(items, 'close', None)):
                    items.close()
                if callable(getattr(self.adapter, 'set_trace', None)):
                    self.adapter.set_trace(None)
                if log:
                    log.close()
                lock.unlink()
        return result


def verify(root):
    store = Store(root)
    return [dict(local_path=e['local_path'], valid=store.valid(e), format=e['format'])
            for e in store.data['files']]
