#!/usr/bin/python3
"""FastCopy tray helper (GPL-3.0-only).

Use the public XApp C ABI when installed (native Mint status icons, with its
built-in GTK fallback), otherwise the freedesktop X11 GTK status icon.
Keep GTK/GLib in a separate process from Tk. The parent owns the stdin pipe;
EOF means the GUI is gone. stdout carries only tray availability and clicks.
"""
import ctypes as C
import os
import select
import sys
import time


def function(lib, name, restype, *argtypes):
    fn = getattr(lib, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


def run(icon_path, force_gtk=False):
    ptr, integer, string = C.c_void_p, C.c_int, C.c_char_p
    gtk = C.CDLL('libgtk-3.so.0')
    glib = C.CDLL('libglib-2.0.so.0')
    gobject = C.CDLL('libgobject-2.0.so.0')
    function(glib, 'g_set_prgname', None, string)(b'fastcopy')
    function(glib, 'g_set_application_name', None, string)(b'FastCopy')
    if not function(gtk, 'gtk_init_check', integer, ptr, ptr)(None, None):
        return 1
    iterate = function(glib, 'g_main_context_iteration', integer, ptr, integer)
    connect = function(gobject, 'g_signal_connect_data', C.c_ulong,
                       ptr, string, ptr, ptr, ptr, integer)
    unref = function(gobject, 'g_object_unref', None, ptr)
    icon_path = os.fsencode(os.path.abspath(icon_path))
    tooltip = b'FastCopy'
    activation = False

    def clicked(*args):
        nonlocal activation
        activation = True

    try:
        if force_gtk:
            raise OSError('explicit GTK fallback')
        xapp = C.CDLL('libxapp.so.1')
        new = function(xapp, 'xapp_status_icon_new_with_name', ptr, string)
        set_icon = function(xapp, 'xapp_status_icon_set_icon_name', None, ptr, string)
        set_tip = function(xapp, 'xapp_status_icon_set_tooltip_text', None, ptr, string)
        visible = function(xapp, 'xapp_status_icon_set_visible', None, ptr, integer)
        state = function(xapp, 'xapp_status_icon_get_state', integer, ptr)
    except (OSError, AttributeError):
        xapp = None

    if xapp is not None:
        print('backend:xapp', flush=True)
        icon = new(b'FastCopy')
        if not icon:
            return 1
        set_icon(icon, icon_path)
        set_tip(icon, tooltip)
        # XAppStatusIconState: NATIVE=0, FALLBACK (embedded GtkStatusIcon)=1,
        # NO_SUPPORT=2. XApp handles panel changes and fallback internally.
        ready = lambda: state(icon) in (0, 1)
        callback = C.CFUNCTYPE(None, ptr, C.c_uint, C.c_uint, ptr)(
            lambda obj, button, stamp, data: clicked() if button == 1 else None)
    else:
        print('backend:gtk', flush=True)
        icon = function(gtk, 'gtk_status_icon_new_from_file', ptr, string)(icon_path)
        if not icon:
            return 1
        function(gtk, 'gtk_status_icon_set_title', None, ptr, string)(icon, b'FastCopy')
        function(gtk, 'gtk_status_icon_set_tooltip_text', None, ptr, string)(icon, tooltip)
        visible = function(gtk, 'gtk_status_icon_set_visible', None, ptr, integer)
        embedded = function(gtk, 'gtk_status_icon_is_embedded', integer, ptr)
        ready = lambda: bool(embedded(icon))
        callback = C.CFUNCTYPE(None, ptr, ptr)(clicked)
    # Keep the callback alive until after the native icon is unreferenced.
    connect(icon, b'activate', C.cast(callback, ptr), None, None, 0)
    visible(icon, 1)
    previous = None
    try:
        while True:
            if select.select([0], [], [], 0)[0] and not os.read(0, 4096):
                break
            for _ in range(64):
                if not iterate(None, 0):
                    break
            current = ready()
            if current != previous:
                print('ready' if current else 'unavailable', flush=True)
                previous = current
            if activation:
                print('restore', flush=True)
                activation = False
            time.sleep(0.04)
    except BrokenPipeError:
        pass
    finally:
        visible(icon, 0)
        unref(icon)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(run(sys.argv[1], force_gtk="--gtk" in sys.argv[2:]))
    except (OSError, AttributeError, IndexError) as exc:
        print(f'FastCopy tray unavailable: {exc}', file=sys.stderr)
        sys.exit(1)
