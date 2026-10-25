"""Left-aligned progress palette; transport failures never become file failures."""
import json
from pathlib import Path
import adsk.core

PALETTE_ID = 'FusionBackupManagerProgress'


class Incoming(adsk.core.HTMLEventHandler):
    def __init__(self, display):
        super().__init__()
        self.display = display

    def notify(self, args):
        args.returnData = 'OK'
        if args.action == 'ready':
            # Do not synchronously send back into HTML while handling its request.
            # The next ordinary progress update/poll sends the buffered state.
            self.display.ready = True
        elif args.action == 'cancel':
            self.display.wasCancelled = True


class Closed(adsk.core.UserInterfaceGeneralEventHandler):
    def __init__(self, display):
        super().__init__()
        self.display = display

    def notify(self, args):
        self.display.wasCancelled = True


class ProgressDisplay:
    """ProgressDialog facade with a cancellable native fallback. No doEvents calls."""
    def __init__(self, ui):
        self.ui = ui
        self.palette = None
        self.native = None
        self.ready = False
        self._was_cancelled = False
        self.isCancelButtonShown = True
        self.cancelButtonText = 'Stop Backup'
        self._message = ''
        self._maximum = 1
        self._minimum = 0
        self._value = 0
        self._title = 'Fusion Backup'
        self._handlers = []
        self._sent = None
        self.warnings = []

    @property
    def wasCancelled(self):
        if self.native:
            try:
                return self._was_cancelled or self.native.wasCancelled
            except Exception:
                self._was_cancelled = True
        return self._was_cancelled

    @wasCancelled.setter
    def wasCancelled(self, value):
        self._was_cancelled = bool(value)

    def show(self, title, message, minimum, maximum, delay=0):
        self._title, self._message = title, message
        self._minimum, self._maximum, self._value = minimum, maximum, minimum
        try:
            previous = self.ui.palettes.itemById(PALETTE_ID)
            if previous:
                previous.deleteMe()
            url = (Path(__file__).resolve().parent / 'progress.html').as_uri()
            self.palette = self.ui.palettes.add(PALETTE_ID, title, url, False, True, True, 660, 360, True)
            if not self.palette:
                raise RuntimeError('Could not create backup progress display')
            self._handlers = [Incoming(self), Closed(self)]
            self.palette.incomingFromHTML.add(self._handlers[0])
            self.palette.closed.add(self._handlers[1])
            self.palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateFloating
            self.palette.isVisible = True
        except Exception as exc:
            self.fallback(exc)
        return not self.wasCancelled

    def fallback(self, reason):
        if self.native or self._was_cancelled:
            return
        self.warnings.append('進捗表示を標準画面に切り替えました: ' + str(reason))
        if self.palette:
            try:
                self.palette.isVisible = False
            except Exception:
                pass  # Still create an independently cancellable display.
        try:
            self.native = self.ui.createProgressDialog()
            self.native.isCancelButtonShown = True
            self.native.cancelButtonText = self.cancelButtonText
            shown = self.native.show(self._title, self._message, self._minimum, max(1, self._maximum), 0)
            if shown is False:
                raise RuntimeError('Native progress display did not open')
            self.native.progressValue = self._value
        except Exception as exc:
            self.warnings.append('進捗画面を表示できないため中断します: ' + str(exc))
            self._was_cancelled = True

    def flush(self):
        if self._was_cancelled:
            return
        if self.native:
            try:
                self.native.maximumValue = max(1, self._maximum)
                self.native.progressValue = self._value
                self.native.message = self._message
            except Exception as exc:
                self.warnings.append('標準進捗画面を更新できないため中断します: ' + str(exc))
                self._was_cancelled = True
            return
        if not self.palette or not self.ready:
            return
        try:
            if not self.palette.isValid:
                self.fallback('Progress palette is unavailable')
                return
            payload = json.dumps(dict(message=self._message, maximum=self._maximum, value=self._value,
                                      cancel_label=self.cancelButtonText, cancel_enabled=not self.wasCancelled,
                                      cancel_visible=self.isCancelButtonShown), ensure_ascii=False)
            if payload != self._sent:
                # Qt browser return values are not a reliable synchronous HTML ACK.
                # A missing/non-OK return is not a backup error.
                response = self.palette.sendInfoToHTML('progress', payload)
                if response in ('Invalid progress data', 'Unsupported action'):
                    self.fallback(response)
                    return
                self._sent = payload
        except Exception as exc:
            self.fallback(exc)

    def poll(self):
        # Called only after the existing safe doEvents boundary, not from HTML.
        self.flush()

    @property
    def message(self):
        return self._message

    @message.setter
    def message(self, value):
        self._message = value
        self.flush()

    @property
    def maximumValue(self):
        return self._maximum

    @maximumValue.setter
    def maximumValue(self, value):
        self._maximum = max(1, value)

    @property
    def progressValue(self):
        return self._value

    @progressValue.setter
    def progressValue(self, value):
        self._value = value

    def hide(self):
        try:
            if self.native:
                self.native.hide()
            if self.palette and self.palette.isValid:
                self.palette.deleteMe()
        except Exception as exc:
            self.warnings.append('進捗画面を閉じられませんでした: ' + str(exc))
        finally:
            self.palette = None
            self.ready = False
            self._handlers.clear()
