"""Fusion entry point and UI. Deferred launch pattern adapted from FusionProjectExporter (MIT)."""
import json
import os
from pathlib import Path
import traceback
import adsk.core
import adsk.fusion
from .core import Backup, DEFAULTS, atomic_json
from .fusion_adapter import FusionAdapter, collection
from .progress import progress_text

COMMAND = 'FusionBackupManagerButton'
PANEL = 'FusionBackupManagerPanel'
EVENT = 'FusionBackupManagerDeferredRun'
_handlers = []
_pending = None
_queued = None
_running = False
_projects = []
_config = Path(os.environ.get('APPDATA', str(Path.home()))) / 'FusionBackupManager' / 'settings.json'


def bind(event, handler):
    event.add(handler)
    _handlers.append(handler)


def settings():
    if not _config.exists():
        return dict(DEFAULTS)
    value = json.loads(_config.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('backup_scope') not in ('all', 'selected', 'current'):
        raise ValueError('Invalid settings. Inspect or rename ' + str(_config))
    return dict(DEFAULTS, **value)


class Created(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        global _projects
        try:
            app = adsk.core.Application.get()
            if _running or _queued:
                app.userInterface.messageBox('Backup is already running or queued.')
                return
            saved = settings()
            _projects = list(collection(app.data.dataProjects))
            inputs = args.command.commandInputs
            notice = (
                '保存済みのクラウド版をバックアップします。未保存の編集は含みません。\n'
                '開いている設計やその参照先は保護のため出力せず、過去のバックアップは保持します。\n'
                '外部参照のある設計は、手動のF3Z出力・復元確認が必要です。\n\n'
                'Projects (multiple) は Selected Projects（複数選択）のときだけ反映されます。\n'
                'All Projects（全プロジェクト）／Current Project（現在のプロジェクト）では無視されます。')
            inputs.addTextBoxCommandInput('notice', '', notice, 8, True)
            scope = inputs.addDropDownCommandInput('scope', 'Backup Target', adsk.core.DropDownStyles.TextListDropDownStyle)
            for key, label in [('all', 'All Projects'), ('selected', 'Selected Projects'), ('current', 'Current Project')]:
                scope.listItems.add(label, saved['backup_scope'] == key, '')
            projects = inputs.addDropDownCommandInput('projects', 'Projects (multiple)', adsk.core.DropDownStyles.CheckBoxDropDownStyle)
            for index, project in enumerate(_projects):
                projects.listItems.add(f'{project.name} [{index + 1}]', project.id in saved['project_ids'], '')
            inputs.addStringValueInput('directory', 'Output Directory', saved['backup_directory'])
            inputs.addBoolValueInput('browse', 'Browse...', False, '', False)
            for key, label in [('export_fusion_archive', 'Fusion Archive (standalone)'),
                               ('export_step_for_assemblies', 'STEP for Assemblies'),
                               ('export_step', 'STEP for All Designs'), ('incremental', 'Skip Unchanged Files')]:
                inputs.addBoolValueInput(key, label, True, '', saved[key])
            inputs.addBoolValueInput('keep_versions', 'Keep Old Versions (required)', True, '', True).isEnabled = False
            args.command.okButtonText = 'Start Backup'
            bind(args.command.inputChanged, Changed())
            bind(args.command.validateInputs, Validate())
            bind(args.command.execute, Execute())
            bind(args.command.destroy, Destroy())
        except Exception:
            adsk.core.Application.get().userInterface.messageBox(traceback.format_exc())


class Changed(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        if args.input.id == 'browse':
            ui = adsk.core.Application.get().userInterface
            dialog = ui.createFolderDialog()
            dialog.title = 'Choose Backup Directory'
            if dialog.showDialog() == adsk.core.DialogResults.DialogOK:
                args.inputs.itemById('directory').value = dialog.folder


class Validate(adsk.core.ValidateInputsEventHandler):
    def notify(self, args):
        inputs = args.inputs
        scope = inputs.itemById('scope').selectedItem.index
        selected = any(x.isSelected for x in collection(inputs.itemById('projects').listItems))
        args.areInputsValid = bool(inputs.itemById('directory').value.strip()) and (scope != 1 or selected) and (
            inputs.itemById('export_fusion_archive').value or inputs.itemById('export_step').value)


class Execute(adsk.core.CommandEventHandler):
    def notify(self, args):
        global _pending
        inputs = args.command.commandInputs
        try:
            scope = ('all', 'selected', 'current')[inputs.itemById('scope').selectedItem.index]
            app = adsk.core.Application.get()
            selected = ([p for i, p in enumerate(_projects) if inputs.itemById('projects').listItems.item(i).isSelected]
                        if scope == 'selected' else list(_projects) if scope == 'all' else [app.data.activeProject])
            if not selected or any(p is None for p in selected):
                raise ValueError('No accessible project selected')
            root = Path(inputs.itemById('directory').value.strip()).expanduser()
            if not root.is_absolute():
                raise ValueError('Use an absolute output directory')
            chosen = dict(DEFAULTS, backup_directory=str(root), backup_scope=scope,
                          project_ids=[p.id for p in selected])
            for key in ('export_fusion_archive', 'export_step_for_assemblies', 'export_step', 'incremental'):
                chosen[key] = inputs.itemById(key).value
            atomic_json(_config, chosen)
            _pending = (chosen, selected)
        except Exception as exc:
            _pending = None
            args.executeFailed = True
            args.executeFailedMessage = str(exc)


class Destroy(adsk.core.CommandEventHandler):
    def notify(self, args):
        global _queued, _pending
        if _pending and not _queued and not _running:
            _queued, _pending = _pending, None
            adsk.core.Application.get().fireCustomEvent(EVENT)


class Launch(adsk.core.CustomEventHandler):
    def notify(self, args):
        global _queued, _running
        if not _queued or _running:
            return
        chosen, projects = _queued
        _queued = None
        _running = True
        app = adsk.core.Application.get()
        ui = app.userInterface
        panel = ui.allToolbarPanels.itemById(PANEL)
        control = panel.controls.itemById(COMMAND) if panel else None
        progress = ui.createProgressDialog()
        progress.isCancelButtonShown = True
        progress.cancelButtonText = 'Stop Backup'
        counts = {}
        current_activity = dict(phase='preparing')
        def activity(event):
            nonlocal current_activity
            current_activity = dict(event)
            counts.update(event.get('counts', {}))
            progress.message = progress_text(counts, current_activity)
        def update(result, name):
            counts.update(result)
            progress.maximumValue = max(1, result['total'])
            progress.progressValue = result['completed']
            progress.message = progress_text(counts, current_activity)
            adsk.doEvents()
        try:
            progress.show('Fusion Backup', progress_text(counts, current_activity), 0, 1, 0)
            if control:
                control.isEnabled = False
            adapter = FusionAdapter(app, adsk.fusion, adsk.doEvents, activity=activity)
            result = Backup(chosen['backup_directory'], adapter, chosen, update,
                            lambda: progress.wasCancelled, activity=activity).run(projects)
            progress.hide()
            report = '\n'.join(f'{key}: {result[key]}' for key in ('status', 'total', 'completed', 'exported', 'skipped', 'failed'))
            report += '\nManual Action: ' + str(len(result['manual']))
            report += '\n\n' + '\n'.join(x['location'] for x in result['manual'][:20])
            report += '\n\n' + '\n'.join((result['errors'] + result['warnings'])[:10])
            report += '\n\nFull results: ' + str(Path(chosen['backup_directory']) / 'last-result.json')
            ui.messageBox(report, 'Backup Result')
            if ui.messageBox('Open Backup Folder?', 'Backup Result', adsk.core.MessageBoxButtonTypes.YesNoButtonType) == adsk.core.DialogResults.DialogYes:
                os.startfile(chosen['backup_directory'])
            if result.get('log_path') and ui.messageBox('Open Log?', 'Backup Result', adsk.core.MessageBoxButtonTypes.YesNoButtonType) == adsk.core.DialogResults.DialogYes:
                os.startfile(result['log_path'])
        except Exception:
            ui.messageBox('Backup failed:\n' + traceback.format_exc())
        finally:
            progress.hide()
            if control and control.isValid:
                control.isEnabled = True
            _running = False


def run(context):
    if _handlers:
        return
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        event = app.registerCustomEvent(EVENT)
        if not event:
            raise RuntimeError('Could not register deferred backup event')
        bind(event, Launch())
        definition = ui.commandDefinitions.addButtonDefinition(COMMAND, 'Fusion Backup', 'Back up saved cloud versions locally')
        bind(definition.commandCreated, Created())
        workspace = ui.workspaces.itemById('FusionSolidEnvironment')
        tab = workspace.toolbarTabs.itemById('ToolsTab')
        if not tab:
            raise RuntimeError('Utilities tab (ToolsTab) is unavailable')
        panel = tab.toolbarPanels.add(PANEL, 'Backup')
        control = panel.controls.addCommand(definition)
        control.isPromoted = True
        control.isPromotedByDefault = True
    except Exception:
        ui.messageBox('Could not install Fusion Backup:\n' + traceback.format_exc())
        stop(context)


def stop(context):
    global _pending, _queued
    app = adsk.core.Application.get()
    if _running:
        app.userInterface.messageBox('Stop the backup before unloading this add-in.')
        return
    for values, identity in [(app.userInterface.allToolbarPanels, PANEL), (app.userInterface.commandDefinitions, COMMAND)]:
        value = values.itemById(identity)
        if value:
            value.deleteMe()
    app.unregisterCustomEvent(EVENT)
    _pending = _queued = None
    _handlers.clear()
