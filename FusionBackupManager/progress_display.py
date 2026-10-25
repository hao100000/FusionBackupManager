"""Left-aligned progress palette using Fusion's documented HTML palette API."""
import json
from pathlib import Path
import adsk.core

PALETTE_ID = 'FusionBackupManagerProgress'


class Incoming(adsk.core.HTMLEventHandler):
    def __init__(self, display):
        super().__init__()
        self.display = display

    def notify(self, args):
        if args.action == 'ready':
            self.display.ready = True
            self.display.flush()
        elif args.action == 'cancel':
            self.display.wasCancelled = True
        args.returnData = 'OK'


class Closed(adsk.core.UserInterfaceGeneralEventHandler):
    def __init__(self, display):
        super().__init__()
        self.display = display

    def notify(self, args):
        self.display.wasCancelled = True


class ProgressDisplay:
    """Small ProgressDialog-compatible facade. No document API or doEvents calls."""
    def __init__(self, ui):
        self.ui = ui
        self.palette = None
        self.ready = False
        self.wasCancelled = False
        self.isCancelButtonShown = True
        self.cancelButtonText = 'Stop Backup'
        self._message = ''
        self._maximum = 1
        self._value = 0
        self._handlers = []
        self._sent = None

    def show(self, title, message, minimum, maximum, delay=0):
        previous = self.ui.palettes.itemById(PALETTE_ID)
        if previous:
            previous.deleteMe()
        self._message = message
        self._maximum = maximum
        self._value = minimum
        url = (Path(__file__).resolve().parent / 'progress.html').as_uri()
        self.palette = self.ui.palettes.add(PALETTE_ID, title, url, False, True, True, 660, 360, True)
        if not self.palette:
            raise RuntimeError('Could not create backup progress display')
        self._handlers = [Incoming(self), Closed(self)]
        self.palette.incomingFromHTML.add(self._handlers[0])
        self.palette.closed.add(self._handlers[1])
        self.palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateFloating
        self.palette.isVisible = True
        return True

    def flush(self):
        if not self.palette or not self.ready or not self.palette.isValid:
            return
        payload = json.dumps(dict(message=self._message, maximum=self._maximum, value=self._value,
                                  cancel_label=self.cancelButtonText, cancel_enabled=not self.wasCancelled,
                                  cancel_visible=self.isCancelButtonShown), ensure_ascii=False)
        if payload != self._sent:
            response = self.palette.sendInfoToHTML('progress', payload)
            if response != 'OK':
                raise RuntimeError('Backup progress display did not acknowledge update')
            self._sent = payload

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
        if self.palette and self.palette.isValid:
            self.palette.deleteMe()
        self.palette = None
        self.ready = False
        self._handlers.clear()
