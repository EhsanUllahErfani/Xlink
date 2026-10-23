// TabLink clipboard bridge for GNOME Shell.
//
// On Wayland only the shell may read the clipboard while another app has focus, so this
// exports a tiny D-Bus interface on the user's session bus for the TabLink daemon:
//   GetText() -> s, SetText(s), signal Changed() when the clipboard owner changes.
// Clipboard text is never logged.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'dev.tablink.Shell';
const OBJECT_PATH = '/dev/tablink/Clipboard';
const IFACE = `
<node>
  <interface name="dev.tablink.Clipboard">
    <method name="GetText"><arg type="s" direction="out" name="text"/></method>
    <method name="SetText"><arg type="s" direction="in" name="text"/></method>
    <signal name="Changed"/>
  </interface>
</node>`;

class ClipboardBridge {
    constructor() {
        this._clipboard = St.Clipboard.get_default();
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
    }

    GetTextAsync(params, invocation) {
        this._clipboard.get_text(St.ClipboardType.CLIPBOARD, (_cb, text) => {
            invocation.return_value(new GLib.Variant('(s)', [text ?? '']));
        });
    }

    SetText(text) {
        this._clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
    }

    export() {
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        const selection = global.display.get_selection();
        this._ownerId = selection.connect('owner-changed', (_sel, type) => {
            // Meta.SelectionType.SELECTION_CLIPBOARD == 1
            if (type === 1)
                this._dbus.emit_signal('Changed', null);
        });
        this._nameId = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
            Gio.BusNameOwnerFlags.NONE, null, null, null);
    }

    unexport() {
        if (this._ownerId)
            global.display.get_selection().disconnect(this._ownerId);
        if (this._nameId)
            Gio.bus_unown_name(this._nameId);
        this._dbus.unexport();
    }
}

export default class TabLinkExtension extends Extension {
    enable() {
        this._bridge = new ClipboardBridge();
        this._bridge.export();
    }

    disable() {
        this._bridge?.unexport();
        this._bridge = null;
    }
}
