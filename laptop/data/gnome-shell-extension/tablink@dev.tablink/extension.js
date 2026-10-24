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
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

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

const MODE_TEXT = {
    usb: 'USB cable', lan: 'Wi-Fi', laptop_hotspot: 'laptop hotspot',
    tablet_hotspot: 'tablet hotspot', tailscale: 'internet', bluetooth: 'Bluetooth',
};

// Top-bar status for TabLink (spec F10): connected / syncing / offline, plus the main actions.
// It asks the TabLink daemon over its user-only socket every 5 seconds.
class StatusIndicator {
    constructor() {
        this._button = new PanelMenu.Button(0.0, 'TabLink', false);
        this._icon = new St.Icon({icon_name: 'input-tablet-symbolic', style_class: 'system-status-icon'});
        this._button.add_child(this._icon);
        const menu = this._button.menu;
        this._status = new PopupMenu.PopupMenuItem('TabLink: checking…', {reactive: false});
        menu.addMenuItem(this._status);
        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const tablink = GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'tablink']);
        const action = (label, args) => {
            const item = new PopupMenu.PopupMenuItem(label);
            item.connect('activate', () => {
                try {
                    Gio.Subprocess.new([tablink, ...args], Gio.SubprocessFlags.STDOUT_SILENCE |
                        Gio.SubprocessFlags.STDERR_SILENCE);
                } catch (e) {
                    logError(e, 'TabLink');
                }
            });
            menu.addMenuItem(item);
        };
        action('Show tablet on this laptop', ['connect']);
        action('Show this laptop on the tablet', ['remote']);
        action('Wake tablet', ['wake']);
        const folder = new PopupMenu.PopupMenuItem('Open shared folder');
        folder.connect('activate', () => {
            const dir = Gio.File.new_for_path(GLib.build_filenamev([GLib.get_home_dir(), 'TabLink']));
            Gio.AppInfo.launch_default_for_uri(dir.get_uri(), null);
        });
        menu.addMenuItem(folder);
        Main.panel.addToStatusArea('tablink-status', this._button);
        this._refresh();
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _show(status) {
        let text = 'TabLink: service not running';
        let opacity = 120;
        if (status && status.running !== false) {
            if (status.mode) {
                const syncing = (status.services || []).some(s => s.state === 'syncing');
                text = `Tablet connected · ${MODE_TEXT[status.mode] ?? status.mode}` +
                    (syncing ? ' · syncing' : '');
                opacity = 255;
            } else {
                text = 'Tablet offline';
            }
        }
        this._status.label.text = text;
        this._icon.opacity = opacity;
    }

    _refresh() {
        const path = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'tablink', 'tablinkd.sock']);
        const client = new Gio.SocketClient();
        client.connect_async(new Gio.UnixSocketAddress({path}), null, (c, res) => {
            let conn;
            try {
                conn = c.connect_finish(res);
            } catch (e) {
                this._show(null);
                return;
            }
            const out = conn.get_output_stream();
            out.write_all(new TextEncoder().encode('{"cmd": "status"}\n'), null);
            const input = new Gio.DataInputStream({base_stream: conn.get_input_stream()});
            input.read_line_async(GLib.PRIORITY_DEFAULT, null, (s, r) => {
                try {
                    const [line] = s.read_line_finish_utf8(r);
                    this._show(line ? JSON.parse(line) : null);
                } catch (e) {
                    this._show(null);
                }
                conn.close(null);
            });
        });
    }

    destroy() {
        if (this._timer)
            GLib.source_remove(this._timer);
        this._timer = 0;
        this._button.destroy();
    }
}

export default class TabLinkExtension extends Extension {
    enable() {
        this._bridge = new ClipboardBridge();
        this._bridge.export();
        try {
            // Kept separate: a problem here must never break clipboard sharing.
            this._indicator = new StatusIndicator();
        } catch (e) {
            logError(e, 'TabLink status icon');
            this._indicator = null;
        }
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
        this._bridge?.unexport();
        this._bridge = null;
    }
}
