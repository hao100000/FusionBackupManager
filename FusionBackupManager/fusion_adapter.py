"""Read-only cloud access and local exports, called on Fusion's main thread."""
from .core import Item, Cancelled
from pathlib import Path


def collection(values, checkpoint=None):
    if checkpoint:
        checkpoint()
    count = values.count
    for index in range(count):
        if checkpoint:
            checkpoint()
        value = values.item(index)
        if value is None:
            raise RuntimeError('Fusion returned an inaccessible collection item')
        yield value


class FusionAdapter:
    def __init__(self, app, fusion, pump=lambda: None, activity=None):
        self.app, self.fusion, self.pump = app, fusion, pump
        self.activity = activity or (lambda event: None)
        self.trace = lambda event: None
        self._cancelled = lambda: False

    def set_trace(self, trace):
        self.trace = trace or (lambda event: None)

    def notify(self, event):
        self.trace(event)
        self.activity(event)

    def checkpoint(self):
        self.pump()
        if self._cancelled():
            raise Cancelled()

    def report(self, phase, item, fmt=''):
        # Do not pump UI events while owning an open export document.
        self.notify(dict(phase=phase, project=item.project_name,
                           folder='/'.join(f[0] for f in item.folders) or 'プロジェクト直下',
                           file=item.name, format=fmt))

    def enumerate(self, projects, cancelled):
        # Compatibility helper; the real backup consumes iter_items incrementally.
        warnings = []
        return list(self.iter_items(projects, cancelled, warnings)), warnings

    def iter_items(self, projects, cancelled, warnings):
        self._cancelled = cancelled
        projects = list(projects)
        seen, folders_seen = set(), set()
        discovered = 0

        def notice(phase, project, parts=(), file=''):
            self.notify(dict(phase=phase, project=project, folder='/'.join(parts) or 'プロジェクト直下',
                             file=file, discovered=discovered, project_index=project_index,
                             project_total=len(projects)))
            self.checkpoint()

        def visit(data_file, project_name, folder_names):
            nonlocal discovered
            pending = [data_file]
            while pending:
                current = pending.pop()
                notice('file_metadata', project_name, folder_names)
                try:
                    key = (current.id, current.versionId)
                    if not all(key):
                        raise ValueError('Missing file or version ID')
                    if key in seen:
                        continue
                    project = current.parentProject
                    folder = current.parentFolder
                    parts, folder_ids = [], set()
                    while folder and not folder.isRoot:
                        self.checkpoint()
                        if folder.id in folder_ids:
                            raise ValueError('Cyclic folder hierarchy')
                        folder_ids.add(folder.id)
                        parts.insert(0, (folder.name, folder.id))
                        folder = folder.parentFolder
                    item = Item(key[0], key[1], current.versionNumber, current.name,
                                project.id, project.name, parts, current.dateModified,
                                (current.fileExtension or '').lower().lstrip('.'), source=current)
                    children = []
                    notice('references', item.project_name, tuple(f[0] for f in parts), item.name)
                    if item.extension in ('f3d', 'f3z'):
                        try:
                            has_children = current.hasChildReferences
                            self.checkpoint()
                            children = list(collection(current.childReferences, self.checkpoint))
                            item.reference_state = 'external' if has_children or children else 'none'
                            if bool(children) != bool(has_children):
                                raise RuntimeError('Reference count and hasChildReferences disagree')
                            for child in children:
                                self.checkpoint()
                                item.references.append(dict(file_id=child.id, version_id=child.versionId,
                                                            version=child.versionNumber, project_id=child.parentProject.id,
                                                            project_name=child.parentProject.name, name=child.name,
                                                            version_binding='DataFile.childReferences; assembly binding requires manual restore test'))
                        except Cancelled:
                            raise
                        except Exception as exc:
                            item.reference_state = 'unknown'
                            item.warnings.append(item.location() + ': cannot fully inspect references: ' + str(exc))
                    seen.add(key)
                    discovered += 1
                    yield item  # Save this file before requesting more cloud metadata.
                    pending.extend(reversed(children))
                except Cancelled:
                    raise
                except Exception as exc:
                    warnings.append('File enumeration failed in ' + project_name + '/' + '/'.join(folder_names) + ': ' + str(exc))

        for project_index, project in enumerate(projects, 1):
            project_name = '(project ' + str(project_index) + ')'
            try:
                project_name = project.name
                notice('enumerating', project_name)
                pending_folders = [(project.rootFolder, ())]
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append('Project listing failed in ' + project_name + ': ' + str(exc))
                continue
            while pending_folders:
                folder, names = pending_folders.pop()
                notice('listing_files', project_name, names)
                try:
                    folder_key = (project.id, folder.id)
                    if folder_key in folders_seen:
                        warnings.append('Repeated/cyclic folder skipped: ' + project_name + '/' + '/'.join(names))
                        continue
                    folders_seen.add(folder_key)
                    files = folder.dataFiles
                    for data_file in collection(files, lambda: notice('listing_files', project_name, names)):
                        yield from visit(data_file, project_name, names)
                except Cancelled:
                    raise
                except Exception as exc:
                    warnings.append('Folder file listing failed in ' + project_name + '/' + '/'.join(names) + ': ' + str(exc))
                notice('listing_folders', project_name, names)
                try:
                    children = []
                    for child in collection(folder.dataFolders, lambda: notice('listing_folders', project_name, names)):
                        self.checkpoint()
                        children.append((child, names + (child.name,)))
                    pending_folders.extend(reversed(children))
                except Cancelled:
                    raise
                except Exception as exc:
                    warnings.append('Subfolder listing failed in ' + project_name + '/' + '/'.join(names) + ': ' + str(exc))

    def export(self, item, fmt, path):
        data_file = item.source
        if data_file.id != item.file_id or data_file.versionId != item.version_id:
            raise RuntimeError('Cloud version changed since enumeration; rerun backup')
        if not data_file.isComplete:
            raise RuntimeError('Cloud version is still processing')
        if item.extension not in ('f3d', 'f3z'):
            self.report('downloading', item, fmt)
            if not data_file.download(str(path), None):
                raise RuntimeError('Download returned failure')
            path = Path(path)
            if not path.is_file():
                # Some translators retain the source filename despite the requested
                # name. Only accept a single matching file in our isolated staging dir.
                candidates = [p for p in path.parent.iterdir() if p.is_file()
                              and p.suffix.lower() == path.suffix.lower()
                              and p.resolve().parent == path.parent.resolve()]
                if len(candidates) == 1:
                    from .core import publish
                    publish(candidates[0], path)
                else:
                    raise RuntimeError('Download reported success but expected output is missing: ' + item.name
                                       + '; matching staged files=' + str(len(candidates)))
            return
        # Never reuse, activate, close, or export user-owned documents.
        protected = {item.file_id} | {r['file_id'] for r in item.references}
        self.report('references', item, fmt)
        # Inspect transitive references too; failure is a safe refusal.
        pending = [data_file]
        visited = set()
        while pending:
            self.checkpoint()
            source = pending.pop()
            key = (source.id, source.versionId)
            if key in visited:
                continue
            visited.add(key)
            protected.add(source.id)
            pending.extend(collection(source.childReferences, self.checkpoint))
        before = list(collection(self.app.documents))
        for existing in before:
            source = existing.dataFile
            if source and source.id in protected:
                raise RuntimeError('Design or dependency is open in Fusion. Close it manually; only saved cloud versions are backed up. Unsaved edits are excluded.')
        if getattr(data_file, 'isConfiguration', False) or getattr(data_file, 'isConfiguredDesign', False):
            raise RuntimeError('Configured designs require manual archive and restore validation')
        original = self.app.activeDocument
        document = None
        owned = False
        try:
            self.report('opening', item, fmt)
            document = self.app.documents.open(data_file, False)
            if document is None:
                raise RuntimeError('Fusion could not open saved cloud version')
            owned = not any(document == prior for prior in before)
            if not owned:
                raise RuntimeError('Fusion reused an existing document; export refused')
            actual = document.dataFile
            if actual is None or actual.id != item.file_id or actual.versionId != item.version_id:
                raise RuntimeError('Opened document does not match requested saved version')
            if document.isModified:
                raise RuntimeError('Opened document is modified; export refused')
            fusion_document = self.fusion.FusionDocument.cast(document)
            if not fusion_document or not fusion_document.design:
                raise RuntimeError('Not a supported Fusion design')
            manager = fusion_document.design.exportManager
            if fmt == 'f3d' and item.reference_state == 'none':
                options = manager.createFusionArchiveExportOptions(str(path))
            elif fmt == 'step':
                options = manager.createSTEPExportOptions(str(path))
            else:
                raise RuntimeError('Unsupported export format')
            self.report('exporting', item, fmt)
            if options is None or not manager.execute(options):
                raise RuntimeError('Fusion export returned failure')
            if document.isModified or document.dataFile.versionId != item.version_id:
                raise RuntimeError('Document state changed during export; output refused')
        finally:
            if owned and document:
                self.report('closing', item, fmt)
                if not document.close(False):
                    raise RuntimeError('Could not close backup-owned document without saving; inspect Fusion')
            if original and original.isValid and self.app.activeDocument != original:
                if not original.activate():
                    raise RuntimeError('Could not restore original active document; inspect Fusion')
