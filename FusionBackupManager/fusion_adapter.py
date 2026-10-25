"""Read-only cloud access and local exports, called on Fusion's main thread."""
from .core import Item, Cancelled
from pathlib import Path


def collection(values):
    for index in range(values.count):
        value = values.item(index)
        if value is None:
            raise RuntimeError('Fusion returned an inaccessible collection item')
        yield value


class FusionAdapter:
    def __init__(self, app, fusion, pump=lambda: None, activity=None):
        self.app, self.fusion, self.pump = app, fusion, pump
        self.activity = activity or (lambda event: None)

    def report(self, phase, item, fmt=''):
        # Do not pump UI events while owning an open export document.
        self.activity(dict(phase=phase, project=item.project_name,
                           folder='/'.join(f[0] for f in item.folders) or 'プロジェクト直下',
                           file=item.name, format=fmt))

    def enumerate(self, projects, cancelled):
        items, warnings, seen = [], [], set()

        def check():
            self.pump()
            if cancelled():
                raise Cancelled()

        def visit(data_file):
            check()
            try:
                key = (data_file.id, data_file.versionId)
                if not all(key):
                    raise ValueError('Missing file or version ID')
                if key in seen:
                    return
                seen.add(key)
                project = data_file.parentProject
                folder = data_file.parentFolder
                parts, folder_ids = [], set()
                while folder and not folder.isRoot:
                    if folder.id in folder_ids:
                        raise ValueError('Cyclic folder hierarchy')
                    folder_ids.add(folder.id)
                    parts.insert(0, (folder.name, folder.id))
                    folder = folder.parentFolder
                item = Item(key[0], key[1], data_file.versionNumber, data_file.name,
                            project.id, project.name, parts, data_file.dateModified,
                            (data_file.fileExtension or '').lower().lstrip('.'), source=data_file)
                children = []
                self.activity(dict(phase='references', project=item.project_name, folder='/'.join(f[0] for f in parts) or 'プロジェクト直下',
                                   file=item.name, discovered=len(items) + 1))
                check()
                if item.extension in ('f3d', 'f3z'):
                    try:
                        has_children = data_file.hasChildReferences
                        children = list(collection(data_file.childReferences))
                        item.reference_state = 'external' if has_children or children else 'none'
                        if bool(children) != bool(has_children):
                            raise RuntimeError('Reference count and hasChildReferences disagree')
                        for child in children:
                            item.references.append(dict(file_id=child.id, version_id=child.versionId,
                                                        version=child.versionNumber, project_id=child.parentProject.id,
                                                        project_name=child.parentProject.name, name=child.name,
                                                        version_binding='DataFile.childReferences; assembly binding requires manual restore test'))
                    except Exception as exc:
                        item.reference_state = 'unknown'
                        item.warnings.append(item.location() + ': cannot fully inspect references: ' + str(exc))
                items.append(item)
                for child in children:
                    visit(child)  # retain referenced version; do not advance to latest
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append('File enumeration failed: ' + str(exc))

        def walk(folder, project_name, parts=()):
            self.activity(dict(phase='enumerating', project=project_name, folder='/'.join(parts) or 'プロジェクト直下',
                               file='', discovered=len(items), project_index=project_index, project_total=len(projects)))
            check()
            try:
                for data_file in collection(folder.dataFiles):
                    visit(data_file)
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append('Folder file listing failed: ' + str(exc))
            try:
                for child in collection(folder.dataFolders):
                    walk(child, project_name, parts + (child.name,))
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append('Subfolder listing failed: ' + str(exc))

        projects = list(projects)
        for project_index, project in enumerate(projects, 1):
            try:
                self.activity(dict(phase='enumerating', project=project.name, folder='プロジェクト直下', file='',
                                   discovered=len(items), project_index=project_index, project_total=len(projects)))
                check()
                walk(project.rootFolder, project.name)
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append('Project listing failed: ' + str(exc))
        return items, warnings

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
            source = pending.pop()
            key = (source.id, source.versionId)
            if key in visited:
                continue
            visited.add(key)
            protected.add(source.id)
            pending.extend(collection(source.childReferences))
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
