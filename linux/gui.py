#!/usr/bin/python3
"""FastCopy 3.63 English resource-layout clone for Linux (GPL-3.0-only).
Native Win32 controls are represented by Tk widgets at original dialog-unit positions.
Unsupported operations are deliberately disabled or rejected, never silently simulated.
"""
import argparse
import ast
import configparser
import ctypes as C
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import time
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, font, simpledialog

HERE = Path(__file__).resolve().parent
ASSETS = HERE if (HERE/'rc_layout.json').is_file() else HERE.parent/'share/fastcopy-mint'
RESOURCE = json.loads((ASSETS/'rc_layout.json').read_text(encoding='utf-8'))
DIALOGS = RESOURCE['dialogs']
STRINGS = RESOURCE['strings']
MODE_IDS = ('IDS_COPY_NAME','IDS_COPY_ATTR','IDS_COPY_LASTEST','IDS_COPY_FORCE','IDS_SYNC_ATTR','IDS_MOVE_FORCE','IDS_DELETE')
MODES = dict(zip((STRINGS[k] for k in MODE_IDS), ('no-overwrite','diff','newer','overwrite','sync','move',None)))
DEFAULT_MODE = STRINGS['IDS_COPY_ATTR']
CONFIG_DIR = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'fastcopy-mint'
CONFIG = CONFIG_DIR/'jobs.json'
DEFAULTS = dict(verify=True, estimate=False, xattrs=False, extended=False, buffer=256, disk='auto', confirm=False,
                dirselect=True, history_count=10, errorlog=True, filelog=False, top=False, span=250, theme='auto', speed=100, log_split=0)
SHEETS = [STRINGS['IDS_SETUP_SHEET'+str(i)] for i in range(1,11)]
PALETTES = {
    'light': dict(bg='#d9d9d9', fg='#202020', field='#ffffff', disabled='#626262',
                  button='#d9d9d9', active='#ececec', select='#3465a4', selected='#ffffff',
                  trough='#b6b6b6', border='#909090'),
    'dark': dict(bg='#292b2d', fg='#eeeeee', field='#1c1e20', disabled='#a0a3a6',
                 button='#383b3e', active='#494d51', select='#365d83', selected='#ffffff',
                 trough='#55595d', border='#62666a'),
}


def _gsettings(schema, key):
    if not shutil.which('gsettings'):
        return ''
    try:
        result = subprocess.run(['gsettings', 'get', schema, key], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=0.75)
        if result.returncode != 0:
            return ''
        return str(ast.literal_eval(result.stdout.strip()))
    except (OSError, ValueError, SyntaxError, subprocess.TimeoutExpired):
        return ''


def _name_mode(name):
    name = name.lower().strip()
    if 'dark' in name or name in ('highcontrastinverse', 'adwaita:dark'):
        return 'dark'
    if 'light' in name:
        return 'light'
    return None


def detect_theme(env=None, read_setting=None, gtk_config=None, preference='auto'):
    """Prefer launch override, then saved preference, then active desktop theme."""
    env = os.environ if env is None else env
    read_setting = _gsettings if read_setting is None else read_setting
    override = env.get('FASTCOPY_MINT_THEME', 'auto').strip().lower()
    if override in PALETTES:
        return override
    if preference in ('dark', 'light') and env.get('FASTCOPY_MINT_THEME', '').strip().lower() != 'auto':
        return preference
    if env.get('GTK_THEME'):
        return _name_mode(env['GTK_THEME']) or 'light'
    desktop = (env.get('XDG_CURRENT_DESKTOP', '') + ':' + env.get('DESKTOP_SESSION', '')).lower()
    cinnamon = 'org.cinnamon.desktop.interface'
    mate = 'org.mate.interface'
    gnome = 'org.gnome.desktop.interface'
    if 'cinnamon' in desktop:
        schemas = [cinnamon, gnome]
    elif 'mate' in desktop:
        schemas = [mate, gnome]
    elif 'gnome' in desktop:
        schemas = [gnome]
    else:
        schemas = [cinnamon, mate, gnome]
    for schema in schemas:
        name = read_setting(schema, 'gtk-theme')
        scheme = read_setting(schema, 'color-scheme')
        mode = _name_mode(name)
        if mode:
            return mode
        if scheme in ('prefer-dark', 'prefer-light'):
            return 'dark' if scheme == 'prefer-dark' else 'light'
        # A known active desktop's named non-dark theme should not be overridden
        # by stale GSettings from another installed desktop.
        if name:
            return 'light'
    if gtk_config is None:
        gtk_config = Path(env.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'gtk-3.0/settings.ini'
    try:
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(gtk_config, encoding='utf-8')
        name = cfg.get('Settings', 'gtk-theme-name', fallback='')
        return _name_mode(name) or ('dark' if cfg.getboolean('Settings', 'gtk-application-prefer-dark-theme', fallback=False) else 'light')
    except (OSError, ValueError, configparser.Error):
        return 'light'


def apply_theme(root, mode):
    """Set classic widget options and themed widgets, including popup menus.
    Called before constructing dialogs; no positions, sizes or labels change.
    """
    p = PALETTES[mode]
    root.tk_setPalette(background=p['bg'], foreground=p['fg'],
                       activeBackground=p['active'], activeForeground=p['fg'],
                       disabledForeground=p['disabled'], highlightBackground=p['bg'],
                       highlightColor=p['select'], insertBackground=p['fg'],
                       selectBackground=p['select'], selectForeground=p['selected'],
                       selectColor=p['field'], troughColor=p['trough'])
    # Tk option database also styles newly created dialogs, menus and listboxes.
    for cls in ('Entry', 'Text', 'Listbox', 'Spinbox'):
        root.option_add(f'*{cls}.background', p['field'], 'interactive')
        root.option_add(f'*{cls}.foreground', p['fg'], 'interactive')
        root.option_add(f'*{cls}.selectBackground', p['select'], 'interactive')
        root.option_add(f'*{cls}.selectForeground', p['selected'], 'interactive')
    for cls in ('Entry', 'Spinbox'):
        root.option_add(f'*{cls}.disabledBackground', p['bg'], 'interactive')
        root.option_add(f'*{cls}.readonlyBackground', p['bg'], 'interactive')
    for cls in ('Button', 'Menubutton'):
        root.option_add(f'*{cls}.background', p['button'], 'interactive')
    root.option_add('*TCombobox*Listbox.background', p['field'], 'interactive')
    root.option_add('*TCombobox*Listbox.foreground', p['fg'], 'interactive')
    root.option_add('*TCombobox*Listbox.selectBackground', p['select'], 'interactive')
    root.option_add('*TCombobox*Listbox.selectForeground', p['selected'], 'interactive')
    style = ttk.Style(root)
    # The classic theme preserves the original geometry; clam exposes explicit
    # arrow and border colors and is more reliable for dark controls.
    wanted = 'clam' if mode == 'dark' else 'classic'
    if wanted in style.theme_names():
        style.theme_use(wanted)
    style.configure('.', background=p['bg'], foreground=p['fg'],
                    fieldbackground=p['field'], selectbackground=p['select'],
                    selectforeground=p['selected'], troughcolor=p['trough'],
                    bordercolor=p['border'], lightcolor=p['button'], darkcolor=p['bg'],
                    arrowcolor=p['fg'], insertcolor=p['fg'])
    style.map('.', foreground=[('disabled', p['disabled'])],
              background=[('active', p['active'])])
    style.configure('TCombobox', padding=0, background=p['button'], fieldbackground=p['field'],
                    foreground=p['fg'], arrowcolor=p['fg'], bordercolor=p['border'])
    style.map('TCombobox', fieldbackground=[('disabled', p['bg']), ('readonly', p['field'])],
              foreground=[('disabled', p['disabled']), ('readonly', p['fg'])],
              arrowcolor=[('disabled', p['disabled'])],
              background=[('disabled', p['bg']), ('active', p['active']), ('readonly', p['button'])],
              selectbackground=[('!disabled', p['select'])], selectforeground=[('!disabled', p['selected'])])
    for name in ('TScrollbar', 'Horizontal.TScrollbar', 'Vertical.TScrollbar'):
        style.configure(name, background=p['button'], troughcolor=p['trough'], arrowcolor=p['fg'], bordercolor=p['border'])
    root.configure(bg=p['bg'])
    return p


SX=2.0; SY=2.0


def engine_path():
    for p in [os.environ.get('FASTCOPY_MINT_ENGINE'), str(HERE/'fastcopy-mint-cli'), str(HERE.parent/'build/fastcopy-mint-cli'), shutil.which('fastcopy-mint-cli')]:
        if p and os.path.isfile(p) and os.access(p,os.X_OK):return p
    return None


def readable(s):return str(s).encode('utf-8','backslashreplace').decode('utf-8')
def px(v):return round(v*SX)
def py(v):return round(v*SY)
def label_text(s):return s.replace('\r\n','\n').replace('\r','\n').replace('&&','&')
def speed_label(percent):return 'Suspend' if percent==0 else 'Full Speed' if percent==100 else f'{percent}%'
def valid_speed(value):return value if type(value) is int and 0<=value<=100 and value%10==0 else 100
def read_json(path,default):
    try:
        obj=json.loads(path.read_text(encoding='utf-8'));return obj if isinstance(obj,type(default)) else default
    except (ValueError,OSError):return default

def write_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=True,indent=2)+'\n',encoding='utf-8');os.chmod(tmp,0o600);tmp.replace(path)

def display_path(p):
    # One path per line, JSON-quoted only for a literal newline, a leading quote or invalid UTF-8.
    if '\n' in p or '\r' in p or p.startswith('"') or readable(p)!=p:return json.dumps(p,ensure_ascii=True)
    return p

def parse_sources(text):
    result=[]
    for line in text.split('\n'):
        if not line:continue
        if line.startswith('"'):
            value=json.loads(line)
            if not isinstance(value,str) or not value:raise ValueError('Invalid quoted source path.')
            result.append(value)
        else:result.append(line)
    return result


class FolderPicker:
    """A folder-only picker using the same palette as the FastCopy dialogs."""
    TYPEAHEAD_TIMEOUT=1.0
    def __init__(self, parent, title, initial, palette, *, multiple=False, allow_create=False):
        self.parent=parent;self.palette=palette;self.multiple=multiple;self.allow_create=allow_create
        self.result=[] if multiple else '';self.names=[];self.selection_text=None
        self.reset_typeahead()
        self.previous=parent.grab_current();self.win=tk.Toplevel(parent);w=self.win
        w.folder_picker=self;w.title(title);w.transient(parent);w.configure(bg=palette['bg'])
        w.geometry(f'540x380+{max(0,parent.winfo_rootx()+20)}+{max(0,parent.winfo_rooty()+40)}');w.minsize(400,260)
        w.columnconfigure(0,weight=1);w.rowconfigure(1,weight=1)
        self.path=tk.StringVar();self.error=tk.StringVar();top=tk.Frame(w,bg=palette['bg']);top.grid(row=0,column=0,sticky='ew',padx=12,pady=(12,8));top.columnconfigure(1,weight=1)
        tk.Label(top,text='Folder:',bg=palette['bg']).grid(row=0,column=0,padx=(0,8))
        self.entry=tk.Entry(top,textvariable=self.path,bg=palette['field'],fg=palette['fg'],insertbackground=palette['fg']);self.entry.grid(row=0,column=1,sticky='ew')
        tk.Button(top,text='Go',command=lambda:self.navigate(self.path.get())).grid(row=0,column=2,padx=(6,0))
        tk.Button(top,text='Home',command=lambda:self.navigate(str(Path.home()))).grid(row=0,column=3,padx=(6,0))
        next_column=4
        if allow_create:
            self.new_folder_button=tk.Button(top,text='New Folder',command=self.new_folder)
            self.new_folder_button.grid(row=0,column=next_column,padx=(6,0));next_column+=1
        tk.Button(top,text='Up',command=lambda:self.navigate(str(self.current.parent))).grid(row=0,column=next_column,padx=(6,0))
        middle=tk.Frame(w,bg=palette['bg']);middle.grid(row=1,column=0,sticky='nsew',padx=12);middle.columnconfigure(0,weight=1);middle.rowconfigure(0,weight=1)
        self.listbox=tk.Listbox(middle,exportselection=False,selectmode='extended' if multiple else 'browse',bg=palette['field'],fg=palette['fg'],selectbackground=palette['select'],selectforeground=palette['selected'],highlightthickness=1,highlightbackground=palette['border'])
        self.listbox.grid(row=0,column=0,sticky='nsew');scroll=ttk.Scrollbar(middle,orient='vertical',command=self.listbox.yview);scroll.grid(row=0,column=1,sticky='ns');self.listbox.configure(yscrollcommand=scroll.set)
        tk.Label(w,textvariable=self.error,bg=palette['bg'],fg=palette['fg'],anchor='w',wraplength=480,justify='left').grid(row=2,column=0,sticky='ew',padx=12,pady=(4,0))
        buttons=tk.Frame(w,bg=palette['bg']);buttons.grid(row=3,column=0,sticky='ew',padx=12,pady=10)
        buttons.columnconfigure(0,weight=1)
        # Reserve separate columns so action buttons cannot hide the notes.
        notes=tk.Frame(buttons,bg=palette['bg']);notes.grid(row=0,column=0,sticky='w',padx=(0,8))
        tk.Label(notes,text='Folders only',bg=palette['bg'],fg=palette['fg']).grid(row=0,column=0,sticky='w')
        if multiple:
            tk.Label(notes,text='Multiple folders can be selected with CTRL-key',bg=palette['bg'],fg=palette['fg']).grid(row=1,column=0,sticky='w')
        self.open_button=tk.Button(buttons,text='Open',width=10,command=self.accept);self.open_button.grid(row=0,column=1,padx=(0,8))
        tk.Button(buttons,text='Cancel',width=10,command=w.destroy).grid(row=0,column=2)
        self.listbox.bind('<<ListboxSelect>>',self.select);self.listbox.bind('<Double-Button-1>',self.enter_selected);self.listbox.bind('<Return>',self.enter_selected)
        self.listbox.bind('<KeyPress>',self.typeahead)
        self.listbox.bind('<ButtonPress-1>',self.reset_typeahead,add='+')
        self.listbox.bind('<FocusOut>',self.reset_typeahead,add='+')
        self.entry.bind('<Return>',lambda e:self.accept());w.bind('<Escape>',lambda e:w.destroy());w.protocol('WM_DELETE_WINDOW',w.destroy)
        try:
            base=Path(initial or Path.home()).expanduser().absolute()
            while not base.is_dir() and base!=base.parent:base=base.parent
        except (OSError,ValueError):base=Path.home()
        self.current=Path.home();self.navigate(str(base));w.update_idletasks()
        # Account for outer padding, the full notes and navigation buttons.
        # Keep at least 120 pixels for the path entry at minimum width.
        minimum=max(400,buttons.winfo_reqwidth()+24,top.winfo_reqwidth()-self.entry.winfo_reqwidth()+120+24)
        w.minsize(minimum,260)
        if w.winfo_width()<minimum:w.geometry(f'{minimum}x{max(380,w.winfo_height())}')
        w.grab_set();self.listbox.focus_set()
    def reset_typeahead(self,event=None):
        self.type_prefix='';self.type_time=0.0
    def typeahead(self,event):
        # Do not consume Ctrl/Alt/Super shortcuts, navigation or selection keys.
        # Shift (uppercase) and AltGr (international keyboard layouts) are allowed.
        if event.state & (0x0004 | 0x0008 | 0x0040 | 0x20000):
            self.reset_typeahead();return
        char=event.char
        if not char or not char.isprintable():
            if event.keysym in ('Up','Down','Left','Right','Home','End','Prior','Next','Tab','ISO_Left_Tab','BackSpace','Delete','Escape'):
                self.reset_typeahead()
            return
        now=time.monotonic()
        prefix=self.type_prefix if now-self.type_time<self.TYPEAHEAD_TIMEOUT else ''
        # A standalone Space keeps Tk's normal selection behavior. Spaces
        # within a typed prefix are useful for names containing spaces.
        if char==' ' and not prefix:
            self.reset_typeahead();return
        key=char.casefold()
        repeated=prefix==key and len(char)==1
        self.type_prefix=key if repeated else prefix+key
        self.type_time=now
        matches=[i for i,name in enumerate(self.names) if name.casefold().startswith(self.type_prefix)]
        if matches:
            active=self.listbox.index('active')
            if repeated:
                # Repeated initial letters cycle through matches, wrapping.
                i=next((i for i in matches if i>active),matches[0])
            elif prefix and active in matches:
                i=active
            else:
                i=matches[0]
            # Keep selection, active row, anchor and displayed path in sync.
            # A plain letter starts a new selection; Ctrl-click still adds rows.
            self.listbox.selection_clear(0,'end');self.listbox.selection_set(i)
            self.listbox.activate(i);self.listbox.selection_anchor(i);self.listbox.see(i)
            self.select()
        return 'break'
    def folder(self,value):
        if not value:raise ValueError('Select an existing folder.')
        p=Path(value).expanduser()
        if not p.is_absolute():p=self.current/p
        p=p.resolve(strict=True)
        if not p.is_dir():raise ValueError('Select a folder, not a file.')
        return p
    def navigate(self,value):
        try:
            p=self.folder(value)
            with os.scandir(p) as entries:
                names=[]
                for e in entries:
                    try:
                        if e.is_dir():names.append(e.name)
                    except OSError:continue
            names.sort(key=lambda n:(n.casefold(),n))
        except (OSError,ValueError,RuntimeError) as e:self.error.set(readable(e));return
        self.current=p;self.names=names;self.selection_text=None;self.path.set(readable(p));self.error.set('');self.listbox.delete(0,'end')
        self.reset_typeahead()
        for n in names:self.listbox.insert('end',readable(n))
        self.listbox.focus_set()
    def new_folder(self):
        # Create under the displayed directory, not an ambiguously highlighted child.
        try:
            name=simpledialog.askstring('New Folder',f'Create a folder in:\n{readable(self.current)}\n\nFolder name:',parent=self.win)
        finally:
            # simpledialog releases its own grab but does not restore ours.
            if self.win.winfo_exists():self.win.grab_set()
        if name is None:return
        if not name or not name.strip() or name in ('.','..') or '/' in name or '\x00' in name:
            self.error.set('Enter a single valid folder name.');return
        try:
            path=self.current/name
            path.mkdir()  # Do not silently accept an existing directory or overwrite a file.
            self.navigate(str(self.current))
            if name not in self.names:
                self.error.set('Folder created, but could not refresh the list.');return
            i=self.names.index(name);self.listbox.selection_clear(0,'end');self.listbox.selection_set(i);self.listbox.see(i);self.select();self.listbox.focus_set()
        except (OSError,ValueError,RuntimeError) as e:self.error.set(readable(e))
    def select(self,event=None):
        indices=self.listbox.curselection()
        # With multiple selected children, show their common parent in the
        # navigation field and highlight the actual selection in the list.
        value=readable(self.current/self.names[indices[0]]) if len(indices)==1 else readable(self.current)
        self.selection_text=value if indices else None
        self.path.set(value);self.error.set('')
    def enter_selected(self,event=None):
        indices=self.listbox.curselection()
        if event is not None and getattr(event,'num',None)==1 and self.names:
            i=self.listbox.nearest(event.y);bounds=self.listbox.bbox(i)
            if bounds and bounds[1]<=event.y<bounds[1]+bounds[3]:
                self.navigate(str(self.current/self.names[i]))
        elif indices:
            i=self.listbox.index('active');i=i if i in indices else indices[0]
            self.navigate(str(self.current/self.names[i]))
        return 'break'
    def accept(self):
        try:
            value=self.path.get();indices=self.listbox.curselection()
            # Retain the actual selected paths, including non-UTF-8 names.
            # An independently typed path overrides the selection.
            if indices and value==self.selection_text:
                paths=[str(self.folder(str(self.current/self.names[i]))) for i in indices]
            else:
                paths=[str(self.folder(value))]
            self.result=list(dict.fromkeys(paths)) if self.multiple else paths[0]
        except (OSError,ValueError,RuntimeError) as e:self.error.set(readable(e));return
        self.win.destroy()
    def show(self):
        self.parent.wait_window(self.win)
        if self.previous is not None and self.previous.winfo_exists():
            try:self.previous.grab_set()
            except tk.TclError:pass
        return self.result


class Layout:
    """Render original RC resources with small Linux-specific layout corrections."""
    def __init__(self,parent,name,*,enabled=(),images=None):
        self.palette=getattr(parent._root(), "fastcopy_palette", PALETTES["light"]);self.bg=self.palette["bg"]
        self.name=name;self.spec=DIALOGS[name];self.frame=tk.Frame(parent,bg=self.bg,highlightthickness=0)
        self.frame.configure(width=px(self.spec['size'][0]),height=py(self.spec['size'][1]))
        self.frame.pack_propagate(False);self.controls={};self.variables={};self.items=[];self.images=images or {};self.enabled=set(enabled)
        self.font=('Liberation Sans',-11 if self.spec['font']==8 else -12)
        # Group-box borders are painted first, like Win32 sibling controls.
        for c in sorted(self.spec['controls'],key=lambda c:0 if c['kind']=='GROUPBOX' else 1):
            # Omit the Windows-only shortcut and its explanatory note.
            if self.name == 'JOB_DIALOG' and (c['id'] == 'TASKSCHE_BUTTON' or
                    (c['id'] == 'IDC_STATIC' and 'windows task scheduler' in c['text'].lower())):
                continue
            # Omit the Windows-only Play image and repeated settings help buttons.
            if self.name.startswith('SETUP_SHEET') and c['id'] in ('PLAY_BTN','HELP_BUTTON'):continue
            self.create(c)
        # Linux-only addition: keep the upstream RC and generated JSON unchanged.
        if self.name == 'SETUP_SHEET1':
            self.create(dict(kind='CONTROL', id='DARK_THEME_CHECK', text='Dark theme (need to restart)',
                             cls='Button', style='BS_AUTOCHECKBOX | WS_TABSTOP', rect=[9, 156, 180, 12]))
        self.relayout()

    def create(self,c):
        p=self.frame;i=c['id'];k=c['kind'];s=c['style'];t=label_text(c['text']);cls=c['cls'];r=c['rect'];var=None;inner=None
        common={'font':self.font}
        if k=='GROUPBOX':
            w=tk.LabelFrame(p,text=t,bg=self.bg,font=self.font,relief='groove',bd=1,labelanchor='nw')
        elif k in ('PUSHBUTTON','DEFPUSHBUTTON'):
            w=tk.Button(p,text=t,bg=self.palette['button'],activebackground=self.palette['active'],bd=1,padx=0,pady=0,highlightthickness=0,wraplength=max(8,px(r[2])-4),**common)
            icon={'HIST_BTN':'menu','PAUSELIST_BTN':'pause','PAUSEOK_BTN':'pause','PLAY_BTN':'play'}.get(i)
            if icon in self.images:w.config(image=self.images[icon],text='')
        elif k=='COMBOBOX':
            var=tk.StringVar();w=ttk.Combobox(p,textvariable=var,font=self.font,state='readonly' if 'CBS_DROPDOWNLIST' in s else 'normal')
        elif k=='LISTBOX':
            w=tk.Listbox(p,exportselection=False,selectmode='browse',font=self.font,bd=1,relief='sunken',highlightthickness=0)
        elif k=='EDITTEXT' or cls=='RICHEDIT50W':
            if 'ES_MULTILINE' in s or cls=='RICHEDIT50W':
                w=tk.Frame(p,bd=1,relief='sunken',bg=self.bg);inner=tk.Text(w,font=('Liberation Mono',-12) if i=='STATUS_EDIT' else self.font,wrap='word' if i!='STATUS_EDIT' else 'none',bd=0,highlightthickness=0,padx=3,pady=2,bg=self.bg if ('ES_READONLY' in s or i in ('PATH_EDIT','ERR_EDIT')) else self.palette['field'],undo=i=='SRC_EDIT')
                if 'WS_VSCROLL' in s:
                    sb=ttk.Scrollbar(w,orient='vertical',command=inner.yview);sb.pack(side='right',fill='y');inner.configure(yscrollcommand=sb.set)
                if 'WS_HSCROLL' in s:
                    sbx=ttk.Scrollbar(w,orient='horizontal',command=inner.xview);sbx.pack(side='bottom',fill='x');inner.configure(xscrollcommand=sbx.set);inner.configure(wrap='none')
                inner.pack(side='left',fill='both',expand=True)
                if 'ES_READONLY' in s or i in ('PATH_EDIT','ERR_EDIT'):inner.config(state='disabled')
            else:
                var=tk.StringVar();w=tk.Entry(p,textvariable=var,font=self.font,bd=1,highlightthickness=0,justify='right' if 'ES_RIGHT' in s else 'left')
        elif cls=='Button' and ('AUTOCHECKBOX' in s or 'AUTORADIOBUTTON' in s):
            var=tk.BooleanVar(value=False)
            w=tk.Checkbutton(p,text=t,variable=var,bg=self.bg,activebackground=self.palette['active'],highlightthickness=0,bd=0,padx=0,pady=0,anchor='w',justify='left',wraplength=max(10,px(r[2])-17),**common)
            if 'AUTORADIOBUTTON' in s:
                if '_radio' not in self.variables:self.variables['_radio']=tk.StringVar()
                var=self.variables['_radio'];w.destroy();w=tk.Radiobutton(p,text=t,variable=var,value=i,bg=self.bg,activebackground=self.palette['active'],bd=0,highlightthickness=0,padx=0,pady=0,anchor='w',**common)
        elif cls=='msctls_trackbar32':
            var=tk.IntVar(value=100);w=tk.Scale(p,from_=0,to=100,resolution=10,orient='horizontal',variable=var,showvalue=False,sliderlength=13,width=12,highlightthickness=0,bd=0,bg=self.bg,troughcolor=self.palette['trough'])
        elif k=='ICON':
            image=self.images.get('fastcopy')
            if image:
                factor=max(1,(image.width()+px(r[2])-1)//px(r[2]),(image.height()+py(r[3])-1)//py(r[3]))
                image=image.subsample(factor,factor)
            w=tk.Label(p,bg=self.bg,image=image or '',bd=0,padx=0,pady=0);w.image_ref=image
        else:
            var=tk.StringVar(value=t);w=tk.Label(p,textvariable=var,bg=self.bg,font=self.font,bd=1 if ('SS_SUNKEN' in s or 'WS_EX_STATICEDGE' in s) else 0,relief='sunken' if ('SS_SUNKEN' in s or 'WS_EX_STATICEDGE' in s) else 'flat',anchor='center' if k=='CTEXT' else 'w',justify='center' if k=='CTEXT' else 'left',wraplength=max(10,px(r[2])-3),padx=0,pady=0)
        widget=inner or w
        if i!='IDC_STATIC':
            self.controls[i]=widget
            if var is not None:self.variables[i]=var
        self.items.append((c,w,widget))
        interactive=k in ('PUSHBUTTON','DEFPUSHBUTTON','COMBOBOX','EDITTEXT','LISTBOX') or cls in ('Button','msctls_trackbar32')
        if interactive and i not in self.enabled and not ('ES_READONLY' in s):self.enable(i,False,widget)
        if 'ES_READONLY' in s and isinstance(widget,tk.Entry):widget.configure(state='readonly')

    def enable(self,i,state=True,widget=None):
        w=widget or self.controls.get(i)
        if w is None:return
        try:
            if isinstance(w,ttk.Combobox):
                c=next(c for c,_,_ in self.items if c['id']==i)
                w.configure(state=('readonly' if 'CBS_DROPDOWNLIST' in c['style'] else 'normal') if state else 'disabled')
            elif isinstance(w,(tk.Button,tk.Entry,tk.Checkbutton,tk.Radiobutton,tk.Scale,tk.Listbox,tk.Text)):
                w.configure(state='normal' if state else 'disabled')
        except tk.TclError:pass

    def relayout(self,hidden=(),adjust=None):
        hidden=set(hidden)
        for c,w,_ in self.items:
            i=c['id'];r=list(c['rect'])
            if i in hidden or ('NOT WS_VISIBLE' in c['style'] and i not in getattr(self,'force_visible',set())):
                w.place_forget();continue
            if c['kind']=='COMBOBOX':r[3]=13
            if adjust:r=adjust(c,r)
            x,y,width,height=px(r[0]),py(r[1]),max(1,px(r[2])),max(1,py(r[3]))
            if i=='SPEED_SLIDER' and self.name in ('MAIN_DIALOG','SETUP_SHEET1'):
                # Unlike Win32 trackbars, Tk draws the track at the top of
                # its widget. Center the natural-height track on the label row.
                neighbors=[n['rect'] for n in self.spec['controls'] if
                           n['id']=='SPEED_STATIC' or
                           (self.name=='SETUP_SHEET1' and n['text']=='Speed Control')]
                center=sum(py(n[1]+n[3]/2) for n in neighbors)/len(neighbors)
                height=w.winfo_reqheight()
                y=round(center-height/2)
            if self.name.startswith('SETUP_SHEET') and isinstance(w,(tk.Label,tk.Checkbutton,tk.Radiobutton)) and c['kind']!='ICON':
                # Win32 CRLF and font metrics differ from Tk. Preserve the original
                # rectangle and use the largest font that fits its available height.
                w.configure(font=self.font)
                if isinstance(w,tk.Label):w.configure(wraplength=max(1,width-2))
                elif isinstance(w,tk.Checkbutton):w.configure(wraplength=max(1,width-18))
                for size in range(abs(self.font[1]),8,-1):
                    w.configure(font=(self.font[0],-size))
                    if w.winfo_reqheight()<=height:break
            w.place(x=x,y=y,width=width,height=height)

    def get(self,i):
        if i in self.variables:return self.variables[i].get()
        w=self.controls[i]
        if isinstance(w,tk.Text):return w.get('1.0','end-1c')
        return None

    def set(self,i,value):
        if i in self.variables:self.variables[i].set(value);return
        w=self.controls[i]
        if isinstance(w,tk.Text):
            old=w.cget('state');w.configure(state='normal');w.delete('1.0','end');w.insert('1.0',readable(value));w.configure(state=old)

    def button(self,i,cmd):self.controls[i].configure(command=cmd);self.enable(i)
    def append_many(self,i,lines):
        if not lines:return
        w=self.controls[i];follow=w.yview()[1]>=0.999
        w.configure(state='normal');w.insert('end',''.join(readable(s)+'\n' for s in lines));w.configure(state='disabled')
        # Keep the complete listing and errors; never discard earlier actions.
        # Follow live output only while the user is already at the bottom.
        if follow:w.see('end')

    def append(self,i,s):self.append_many(i,[s])


class X11WindowState:
    """Read the WM's state, even if a compositing WM keeps Tk mapped.

    Tk's winfo_id() is the inner widget; the WM manages its parent wrapper.
    Query the ICCCM/EWMH properties on that wrapper, not the decoration frame.
    No extra X11 tools or desktop-specific Python bindings are needed.
    """
    def __init__(self, root):
        self.display = None
        self.client = None
        self.root = root
        if root.tk.call('tk', 'windowingsystem') != 'x11':
            return
        try:
            self.lib = C.CDLL('libX11.so.6')
            ptr, ulong, integer = C.c_void_p, C.c_ulong, C.c_int
            def bind(name, result, *args):
                f = getattr(self.lib, name)
                f.restype, f.argtypes = result, args
                return f
            self.open = bind('XOpenDisplay', ptr, C.c_char_p)
            self.close_display = bind('XCloseDisplay', integer, ptr)
            self.intern = bind('XInternAtom', ulong, ptr, C.c_char_p, integer)
            self.tree = bind('XQueryTree', integer, ptr, ulong, C.POINTER(ulong),
                             C.POINTER(ulong), C.POINTER(ptr), C.POINTER(C.c_uint))
            self.get = bind('XGetWindowProperty', integer, ptr, ulong, ulong,
                            C.c_long, C.c_long, integer, ulong, C.POINTER(ulong),
                            C.POINTER(integer), C.POINTER(ulong), C.POINTER(ulong), C.POINTER(ptr))
            self.free = bind('XFree', integer, ptr)
            self.display = self.open(os.fsencode(root.winfo_screen()))
            if self.display:
                self.atoms = {name: self.intern(self.display, name.encode(), 0) for name in (
                    'WM_STATE', '_NET_WM_STATE', '_NET_WM_STATE_HIDDEN', '_NET_WM_STATE_SHADED',
                    '_NET_WM_DESKTOP', '_NET_CURRENT_DESKTOP', '_NET_SHOWING_DESKTOP', '_NET_CLIENT_LIST')}
        except (OSError, AttributeError):
            self.close()

    def close(self):
        if self.display:
            self.close_display(self.display)
            self.display = None

    def wrapper(self):
        if self.client is not None:
            return self.client
        screen, parent, children, count = C.c_ulong(), C.c_ulong(), C.c_void_p(), C.c_uint()
        if not self.tree(self.display, self.root.winfo_id(), C.byref(screen), C.byref(parent),
                         C.byref(children), C.byref(count)):
            return None
        if children:
            self.free(children)
        if parent.value and parent.value != screen.value:
            self.client = parent.value
            self.screen = screen.value
        return self.client

    def property(self, window, name):
        actual, fmt, count, remaining, data = C.c_ulong(), C.c_int(), C.c_ulong(), C.c_ulong(), C.c_void_p()
        result = self.get(self.display, window, self.atoms[name], 0, 128, 0, 0,
                          C.byref(actual), C.byref(fmt), C.byref(count), C.byref(remaining), C.byref(data))
        try:
            if result == 0 and fmt.value == 32 and data:
                return tuple(C.cast(data, C.POINTER(C.c_ulong))[i] & 0xffffffff for i in range(count.value))
            return ()
        finally:
            if data:
                self.free(data)

    def minimized(self):
        tk_state = self.root.state()
        if tk_state == 'withdrawn':
            return False
        if not self.display or not self.wrapper():
            return tk_state == 'iconic'
        states = self.property(self.client, '_NET_WM_STATE')
        wm = self.property(self.client, 'WM_STATE')
        minimized = (tk_state == 'iconic' or bool(wm and wm[0] == 3)
                     or self.atoms['_NET_WM_STATE_HIDDEN'] in states)
        if not minimized:
            return False
        # Shading, Show Desktop and switching workspaces must not hide FastCopy
        # into the tray. Some WMs advertise HIDDEN for those operations too.
        if self.atoms['_NET_WM_STATE_SHADED'] in states:
            return False
        if self.property(self.screen, '_NET_SHOWING_DESKTOP') == (1,):
            return False
        desk = self.property(self.client, '_NET_WM_DESKTOP')
        current = self.property(self.screen, '_NET_CURRENT_DESKTOP')
        if desk and current and desk[0] not in (current[0], 0xffffffff):
            return False
        return True


class TrayController:
    """Withdraw a minimized window only after a reachable tray is confirmed.

    Prefer Mint's XApp API, then explicitly try GTK if XApp registration fails.
    If the panel or helper disappears, restore the window rather than losing it.
    """
    def __init__(self, root):
        self.root = root
        self.window_state = X11WindowState(root)
        self.helper = None
        self.buffer = b''
        self.attempted = False
        self.hidden = False
        self.deadline = 0
        self.backend = None
        self.force_gtk = False
        self.restore_until = 0
        self.timer = root.after(75, self.poll)
        root.bind('<Destroy>', self.destroyed, add='+')

    def start(self, force_gtk=False):
        self.attempted = True
        self.buffer = b''
        self.backend = None
        self.force_gtk = force_gtk
        self.deadline = time.monotonic() + 3.0
        try:
            args = [sys.executable, str(ASSETS/'tray.py'), str(ASSETS/'fastcopy.png')]
            if force_gtk:
                args.append('--gtk')
            self.helper = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            os.set_blocking(self.helper.stdout.fileno(), False)
        except OSError:
            self.stop()

    def stop(self):
        helper, self.helper = self.helper, None
        self.buffer = b''
        if helper is None:
            return
        # Closing stdin asks the helper to unregister its icon and exit.
        try:
            helper.stdin.close()
        except BrokenPipeError:
            pass
        try:
            helper.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            helper.kill()
            helper.wait()
        finally:
            helper.stdout.close()

    def restore(self, user=False):
        self.hidden = False
        # Withdraw/deiconify also works with WMs which retain mapped minimized
        # clients. Ignore stale WM properties during the restore animation.
        self.root.withdraw()
        self.root.deiconify()
        self.root.lift()
        if user:
            self.root.focus_force()
        self.stop()
        self.attempted = False
        self.restore_until = time.monotonic() + 0.25

    def destroyed(self, event):
        if event.widget is self.root:
            if self.timer is not None:
                self.root.after_cancel(self.timer)
                self.timer = None
            self.stop()
            self.window_state.close()

    def poll(self):
        self.timer = None
        minimized = not self.hidden and self.window_state.minimized()
        if not self.hidden and not minimized:
            self.stop()
            self.attempted = False
        elif minimized and not self.attempted and time.monotonic() >= self.restore_until:
            self.start()
        helper = self.helper
        if helper is not None:
            ready, restore, failed = None, False, False
            try:
                chunk = os.read(helper.stdout.fileno(), 4096)
                if not chunk:
                    failed = True
                self.buffer += chunk
                while b'\n' in self.buffer:
                    line, self.buffer = self.buffer.split(b'\n', 1)
                    if line == b'ready':
                        ready = True
                    elif line == b'unavailable':
                        ready = False
                    elif line == b'restore':
                        restore = True
                    elif line in (b'backend:xapp', b'backend:gtk'):
                        self.backend = line.split(b':')[1]
            except BlockingIOError:
                pass
            except OSError:
                failed = True
            failed = failed or helper.poll() is not None
            if restore:
                self.restore(user=True)
            elif self.hidden and (failed or ready is False):
                self.restore()
            elif not self.hidden:
                if not failed and ready is True and self.window_state.minimized():
                    self.hidden = True
                    self.root.withdraw()  # not merely iconify: remove the running taskbar entry
                elif failed or time.monotonic() >= self.deadline:
                    retry_gtk = not self.force_gtk and self.backend != b'gtk'
                    self.stop()
                    if retry_gtk and self.window_state.minimized():
                        self.start(force_gtk=True)
        self.timer = self.root.after(75, self.poll)


class App:
    def __init__(self,root,initial=(),destination=''):
        self.root=root;self.sources=list(initial);self.proc=None;self.events=queue.Queue();self.is_paused=False;self.cancelling=False;self.dry=False;self.started=0;self.last_progress=None;self.full_log=[];self.err_log=[];self.result=''
        self.cfg=DEFAULTS|read_json(CONFIG_DIR/'settings.json',{});self.jobs=read_json(CONFIG,{});self.history=read_json(CONFIG_DIR/'history.json',[])
        self.theme_mode=detect_theme(preference=self.cfg['theme']);self.palette=apply_theme(root,self.theme_mode);root.fastcopy_palette=self.palette;self.bg=self.palette['bg']
        self.root.title('FastCopy');self.root.iconname('FastCopy');self.root.configure(bg=self.bg);self.root.option_add('*Font',('Liberation Sans',-12));self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.images={n:tk.PhotoImage(file=str(ASSETS/(n+'.png'))) for n in ('fastcopy','menu','pause','play')};self.root.iconphoto(True,self.images['fastcopy'])
        active={'SRC_FILE_BUTTON','SRC_EDIT','HIST_BTN','DST_FILE_BUTTON','DST_COMBO','MODE_COMBO','SPEED_SLIDER','VERIFY_CHECK','ESTIMATE_CHECK','ACL_CHECK','FILTER_CHECK','INCLUDE_COMBO','EXCLUDE_COMBO','LIST_BUTTON','IDOK','FILTERHELP_BUTTON','PAUSELIST_BTN','PAUSEOK_BTN'}
        self.ui=Layout(root,'MAIN_DIALOG',enabled=active,images=self.images);self.ui.frame.pack(fill='both',expand=True);self.ui.force_visible=set()
        # A slim draggable divider fits in the existing gap above Error Log.
        saved_split=self.cfg.get('log_split',0)
        self.split_preference=saved_split if type(saved_split) is int else 0
        self.split_actual=0;self.split_drag_origin=None
        self.log_sash=tk.Frame(self.ui.frame,bg=self.palette['border'],cursor='sb_v_double_arrow',bd=0,highlightthickness=0,takefocus=True)
        self.log_sash.bind('<ButtonPress-1>',self.split_press);self.log_sash.bind('<B1-Motion>',self.split_move);self.log_sash.bind('<ButtonRelease-1>',self.split_release)
        self.log_sash.bind('<Up>',lambda e:self.split_key(-8));self.log_sash.bind('<Down>',lambda e:self.split_key(8))
        v=self.ui.variables;self.dest=v['DST_COMBO'];self.mode=v['MODE_COMBO'];self.verify=v['VERIFY_CHECK'];self.estimate=v['ESTIMATE_CHECK'];self.filter=v['FILTER_CHECK'];self.include=v['INCLUDE_COMBO'];self.exclude=v['EXCLUDE_COMBO'];self.xattrs=v['ACL_CHECK'];self.metadata=tk.BooleanVar(value=True)
        self.dest.set(destination);self.mode.set(DEFAULT_MODE);self.verify.set(self.cfg['verify']);self.estimate.set(self.cfg['estimate']);self.xattrs.set(self.cfg['xattrs']);v['STREAM_CHECK'].set(False);v['IGNORE_CHECK'].set(True);self.ui.set('BUFSIZE_EDIT',str(self.cfg['buffer']));self.ui.set('SPEED_SLIDER',valid_speed(self.cfg['speed']));self.ui.set('SPEED_STATIC',speed_label(self.ui.get('SPEED_SLIDER')));self.resume_speed=self.ui.get('SPEED_SLIDER') or 100;self.ui.set('SAMEDRV_STATIC','')
        self.ui.controls['MODE_COMBO'].configure(values=list(MODES));self.ui.controls['MODE_COMBO'].bind('<<ComboboxSelected>>',lambda e:self.mode_change())
        self.ui.controls['DST_COMBO'].configure(values=self.dest_history())
        for i,cmd in {'SRC_FILE_BUTTON':self.choose_sources,'HIST_BTN':self.show_history,'DST_FILE_BUTTON':self.choose_dest,'LIST_BUTTON':lambda:self.start(True),'IDOK':lambda:self.start(False),'FILTERHELP_BUTTON':self.filter_help,'PAUSELIST_BTN':self.pause,'PAUSEOK_BTN':self.pause}.items():self.ui.button(i,cmd)
        self.ui.controls['SRC_FILE_BUTTON'].bind('<Button-3>',lambda e:self.source_menu(e))
        self.ui.controls['SRC_FILE_BUTTON'].bind('<Control-Button-1>',lambda e:(self.source_menu(e),'break')[1])
        self.ui.controls['BUFSIZE_EDIT'].bind('<Button-1>',lambda e:self.settings(1))
        self.ui.controls['SPEED_SLIDER'].configure(command=self.speed_changed);self.ui.controls['FILTER_CHECK'].configure(command=self.filter_state)
        self.ui.enable('STREAM_CHECK',False)
        self.extended=tk.BooleanVar(value=self.cfg['extended']);self.top=tk.BooleanVar(value=self.cfg['top']);self.disk=tk.StringVar(value=self.cfg['disk'])
        self.create_menu();self.refresh_sources();self.filter_state();self.set_geometry(initial=True);self.mode_change()
        self.ui.frame.bind('<Configure>',self.resize);self.root.attributes('-topmost',self.top.get());self.root.after(50,self.poll)
        self.tray = TrayController(root)

    def size(self):return (px(231),py(319 if self.extended.get() else 292))
    def set_geometry(self,initial=False):
        w,h=self.size();self.root.minsize(w,h)
        if initial:
            saved=self.cfg.get('geometry','');self.root.geometry(saved if isinstance(saved,str) and saved else f'{w}x{h}')
        else:self.root.geometry(f'{max(w,self.ui.frame.winfo_width())}x{h}')
        self.layout_main()
    def resize(self,event):
        if event.widget is self.ui.frame:self.layout_main()
    def split_press(self,event):
        self.log_sash.focus_set();self.split_drag_origin=(event.y_root,self.split_actual)
    def split_move(self,event):
        if self.split_drag_origin is None:return
        y,offset=self.split_drag_origin
        self.split_preference=offset+event.y_root-y;self.layout_main()
    def split_release(self,event):
        if self.split_drag_origin is None:return
        self.split_move(event);self.split_drag_origin=None;self.persist_split()
    def persist_split(self):
        self.split_preference=self.split_actual;self.cfg['log_split']=self.split_actual;self.save_config()
    def split_key(self,delta):
        self.split_preference=self.split_actual+delta;self.layout_main();self.persist_split();return 'break'
    def layout_main(self):
        hidden={'OWDEL_CHECK','ATONCE_BUTTON','RUNAS_BUTTON','JOBTITLE_STATIC','PAUSEOK_BTN','PAUSELIST_BTN'}
        if self.mode.get()==STRINGS['IDS_DELETE']:
            hidden|={'VERIFY_CHECK','ESTIMATE_CHECK','ACL_CHECK','STREAM_CHECK'};hidden.discard('OWDEL_CHECK')
        if not self.extended.get():hidden|={'FROMDATE_COMBO','TODATE_COMBO','MINSIZE_COMBO','MAXSIZE_COMBO','FROMDATE_STATIC','TODATE_STATIC','MINSIZE_STATIC','MAXSIZE_STATIC'}
        if not self.filter.get():hidden.add('FILTERHELP_BUTTON')
        else:self.ui.force_visible.add('FILTERHELP_BUTTON')
        if self.proc:
            active='PAUSELIST_BTN' if self.dry else 'PAUSEOK_BTN';hidden.discard(active);self.ui.force_visible.add(active)
        # Original right-anchored controls, width-fitting editors, extended filter collapse.
        dw=max(0,(self.ui.frame.winfo_width()-px(231))/SX);dh=max(0,(self.ui.frame.winfo_height()-self.size()[1])/SY)
        # Reserve a readable minimum for both log panes. Store pixels so dragging
        # follows the pointer without accumulating dialog-unit rounding errors.
        rects={c['id']:c['rect'] for c in self.ui.spec['controls']}
        listing_height=py(rects['PATH_EDIT'][3]);error_height=py(rects['ERR_EDIT'][3]+dh)
        minimum=py(14)
        self.split_actual=max(minimum-listing_height,min(error_height-minimum,self.split_preference))
        split=self.split_actual/SY
        right={'IDOK','LIST_BUTTON','MODE_COMBO','SPEED_SLIDER','SPEED_STATIC','IGNORE_CHECK','VERIFY_CHECK','ESTIMATE_CHECK','BUF_STATIC','BUFSIZE_EDIT','SAMEDRV_STATIC','FILTER_CHECK','FILTERHELP_BUTTON','RUNAS_BUTTON','JOBTITLE_STATIC','PAUSELIST_BTN','PAUSEOK_BTN'}
        wide={'SRC_EDIT','DST_COMBO','STATUS_EDIT','INCLUDE_COMBO','EXCLUDE_COMBO','PATH_EDIT','ERR_EDIT'}
        # Use the full-width log editors' original four-dialog-unit inset
        # as the common outer margin. Win32's original trackbar extends to
        # the client edge; Tk needs its actual visible track kept inside.
        edge=self.ui.spec['size'][0]-next(c['rect'][0] for c in self.ui.spec['controls'] if c['id']=='PATH_EDIT')
        flush={'SRC_EDIT','DST_COMBO','MODE_COMBO','SPEED_SLIDER','SAMEDRV_STATIC','PATH_EDIT','ERR_EDIT','MAXSIZE_COMBO'}
        def adj(c,r):
            i=c['id']
            if i in ('BUF_STATIC','BUFSIZE_EDIT'):
                # Move the buffer label/value pair together, retaining its spacing.
                r[0]+=edge-219
            if i in ('LIST_BUTTON','IDOK','SAMEDRV_STATIC','PAUSELIST_BTN','PAUSEOK_BTN'):
                # Keep the original button sizes, gap and pause overlays.
                r[0]+=edge-226
            if i in ('FILTER_CHECK','FILTERHELP_BUTTON'):
                r[0]=edge-r[2]
            if i in flush:r[2]=edge-r[0]
            if i in right:r[0]+=dw
            if i in wide:r[2]+=dw
            if i in ('FROMDATE_COMBO','TODATE_COMBO','MINSIZE_COMBO','MAXSIZE_COMBO','FROMDATE_STATIC','TODATE_STATIC','MINSIZE_STATIC','MAXSIZE_STATIC'):r[0]+=dw*min(1,r[0]/174)
            if i in ('PATH_EDIT','ERR_EDIT','ERR_STATIC','ERRSTATUS_STATIC'):
                if not self.extended.get():r[1]-=27
                if i=='PATH_EDIT':r[3]+=split
                else:r[1]+=split
                if i=='ERR_EDIT':r[3]+=dh-split
            return r
        self.ui.relayout(hidden,adj)
        # Keep the divider in the existing gap, spanning exactly the log width.
        listing=next(w for c,w,_ in self.ui.items if c['id']=='PATH_EDIT')
        pos=listing.place_info();sash_y=int(pos['y'])+int(pos['height'])+1
        self.log_sash.place(x=int(pos['x']),y=sash_y,width=int(pos['width']),height=5);self.log_sash.lift()
        # Original pause button replaces the active action when paused, rather than adding a row.
        if self.proc:
            # Keep Cancel on its original button; pause icon lives in the other action slot.
            pauseid='PAUSEOK_BTN' if self.dry else 'PAUSELIST_BTN'
            self.ui.force_visible.add(pauseid)
            for c,w,_ in self.ui.items:
                if c['id']==pauseid:
                    r=adj(c,list(c['rect']));w.place(x=px(r[0]),y=py(r[1]),width=px(r[2]),height=py(r[3]));w.lift()
                elif c['id']!=pauseid and c['id'] in ('PAUSEOK_BTN','PAUSELIST_BTN'):w.place_forget()

    def create_menu(self):
        menu=tk.Menu(self.root,tearoff=False);self.menus={};self.jobmenu=None
        actions={'OPENLOG_MENUITEM':lambda:self.open_log(False),'FILELOG_MENUITEM':lambda:self.open_log(True),'OPENDIR_MENUITEM':lambda:self.open_dir(CONFIG_DIR),'FIXPOS_MENUITEM':self.save_pos,'FIXSIZE_MENUITEM':self.save_pos,'CLOSE_MENUITEM':self.close,'JOB_MENUITEM':self.job_dialog,'SETUP_MENUITEM':self.settings,'FONT_MENUITEM':self.font_dialog,'RESETFONT_MENUITEM':self.reset_font,'FINACT_MENUITEM':self.post_process,'SWAPTARGET_MENUITEM':self.swap,'HELP_MENUITEM':self.help,'FCSUPPORTURL_MENUITEM':lambda:webbrowser.open(STRINGS['IDS_FCSUPPORTURL']),'FASTCOPYURL_MENUITEM':lambda:webbrowser.open(STRINGS['IDS_FASTCOPYURL']),'ABOUT_MENUITEM':self.about}
        def fill(m,items):
            for item in items:
                if item.get('separator'):m.add_separator();continue
                label=item['label'];u=label.find('&');label=label.replace('&','')
                if 'children' in item:
                    child=tk.Menu(m,tearoff=False);m.add_cascade(label=label,menu=child,underline=u)
                    if label=='Post-Proccess':child.add_radiobutton(label='Normal',value='Normal',variable=self._normal_action)
                    fill(child,item['children'])
                    if label=='JobMng':self.jobmenu=child
                    continue
                i=item['id']
                if i=='TOPLEVEL_MENUITEM':m.add_checkbutton(label=label,underline=u,variable=self.top,command=self.top_changed)
                elif i=='EXTENDFILTER_MENUITEM':m.add_checkbutton(label=label,underline=u,variable=self.extended,command=self.extended_changed)
                elif i in ('AUTODISK_MENUITEM','SAMEDISK_MENUITEM','DIFFDISK_MENUITEM'):
                    m.add_radiobutton(label=label,underline=u,variable=self.disk,value={'AUTODISK_MENUITEM':'auto','SAMEDISK_MENUITEM':'same','DIFFDISK_MENUITEM':'diff'}[i],command=self.disk_changed)
                else:m.add_command(label=label,underline=u,command=actions.get(i),state='normal' if i in actions else 'disabled')
                self.menus[i]=(m,m.index('end'))
        self._normal_action=tk.StringVar(value='Normal');fill(menu,RESOURCE['menus']);self.root.configure(menu=menu);self.update_job_menu()
    def update_job_menu(self):
        if self.jobmenu is None:return
        if self.jobmenu.index('end') is not None and self.jobmenu.index('end')>=2:self.jobmenu.delete(2,'end')
        for name in sorted(self.jobs):self.jobmenu.add_command(label=name,command=lambda n=name:self.load_job(n))
    def extended_changed(self):self.cfg['extended']=self.extended.get();self.set_geometry();self.save_config()
    def top_changed(self):self.root.attributes('-topmost',self.top.get());self.cfg['top']=self.top.get();self.save_config()
    def disk_changed(self):
        self.cfg['disk']=self.disk.get();self.ui.set('SAMEDRV_STATIC',{'auto':'','same':'Fix Same HDD','diff':'Fix Diff HDD'}[self.disk.get()]);self.save_config()
    def save_config(self):
        try:write_json(CONFIG_DIR/'settings.json',self.cfg)
        except OSError as e:self.message(str(e))
    def save_pos(self):self.cfg['geometry']=self.root.geometry();self.save_config()
    def reset_font(self):self.root.option_add('*Font',('Liberation Sans',-12));self.ui.controls['STATUS_EDIT'].configure(font=('Liberation Mono',-12))
    def font_dialog(self):
        size=simpledialog.askinteger('Change Main Font','Status font size (pixels)',parent=self.root,initialvalue=12,minvalue=8,maxvalue=18)
        if size:self.ui.controls['STATUS_EDIT'].configure(font=('Liberation Mono',-size))
    def refresh_sources(self):self.ui.set('SRC_EDIT','\n'.join(map(display_path,self.sources)))
    def read_sources(self):self.sources=parse_sources(self.ui.get('SRC_EDIT'));return self.sources
    def source_menu(self,event):
        if self.proc:return
        m=tk.Menu(self.root,tearoff=False);m.add_command(label='Select Directory ...',command=self.choose_sources);m.add_command(label='Add Directory ...',command=lambda:self.choose_sources(append=True));m.tk_popup(event.x_root,event.y_root)
    def choose_sources(self,append=False):
        if self.proc:return
        try:old=self.read_sources()
        except ValueError as e:return self.message(str(e))
        paths=FolderPicker(self.root,'Select source directory',old[0] if old else '',self.palette,multiple=True).show()
        if paths:self.sources=old+paths if append else paths;self.refresh_sources()
    def choose_dest(self):
        if self.proc:return
        p=FolderPicker(self.root,'Select destination directory',self.dest.get(),self.palette,allow_create=True).show()
        if p:
            if readable(p)!=p:return self.message('Destination contains non-UTF-8 filename bytes; cannot enter it safely.')
            self.dest.set(p)
    def dest_history(self):return list(dict.fromkeys(h.get('destination','') for h in self.history if isinstance(h,dict) and h.get('destination')))
    def show_history(self):
        if self.proc:return
        m=tk.Menu(self.root,tearoff=False)
        for item in self.history:
            if not isinstance(item,dict) or 'sources' not in item:continue
            text='; '.join(map(readable,item['sources']));m.add_command(label=text[:100],command=lambda h=item:self.history_load(h))
        if m.index('end') is None:m.add_command(label='(Empty)',state='disabled')
        b=self.ui.controls['HIST_BTN'];m.tk_popup(b.winfo_rootx(),b.winfo_rooty()+b.winfo_height())
    def history_load(self,h):self.sources=list(h['sources']);self.refresh_sources();self.dest.set(h.get('destination',''))
    def record_history(self):
        item={'sources':self.sources[:],'destination':self.dest.get()};self.history=[item]+[h for h in self.history if h!=item];self.history=self.history[:max(0,min(100,int(self.cfg['history_count'])))]
        try:write_json(CONFIG_DIR/'history.json',self.history)
        except OSError:pass
        self.ui.controls['DST_COMBO'].configure(values=self.dest_history())
    def swap(self):
        try:s=self.read_sources()
        except ValueError as e:return self.message(str(e))
        if len(s)!=1:return self.message('Source must contain exactly one path.')
        old=self.dest.get();self.dest.set(s[0]);self.sources=[old] if old else [];self.refresh_sources()
    def filter_help(self):self.message('Include / Exclude: semicolon-separated, case-sensitive Linux glob patterns. Include selects matching files; Exclude takes priority and can skip directories. Date/size filters are not implemented. Filters cannot be used with Sync or Move.')
    def filter_state(self):
        for i in ('INCLUDE_COMBO','EXCLUDE_COMBO'):self.ui.enable(i,self.filter.get() and not self.proc)
        self.layout_main()
    def mode_change(self):
        delete=self.mode.get()==STRINGS['IDS_DELETE'];move=self.mode.get()==STRINGS['IDS_MOVE_FORCE']
        if move:self.verify.set(True)
        self.ui.enable('VERIFY_CHECK',not move and not delete and not self.proc)
        self.ui.enable('IDOK',not delete or bool(self.proc));self.ui.enable('LIST_BUTTON',not delete or bool(self.proc))
        self.ui.enable('DST_COMBO',not delete and not self.proc);self.ui.enable('DST_FILE_BUTTON',not delete and not self.proc)
        self.layout_main()
    def dialog(self,name,enabled=(),parent=None):
        parent=parent or self.root;previous=parent.grab_current();win=tk.Toplevel(parent);win.transient(parent);win.title(DIALOGS[name]['caption']);win.configure(bg=self.bg);win.resizable(False,False);win.iconphoto(False,self.images['fastcopy'])
        layout=Layout(win,name,enabled=enabled,images=self.images);layout.frame.pack(fill='both',expand=True)
        w,h=DIALOGS[name]['size'];win.geometry(f'{px(w)}x{py(h)}+{max(0,parent.winfo_rootx()+22)}+{max(0,parent.winfo_rooty()+40)}');win.grab_set();win.bind('<Escape>',lambda e:win.destroy())
        if previous is not None:
            def restore(event):
                if event.widget is win and previous.winfo_exists():
                    try:previous.grab_set()
                    except tk.TclError:pass
            win.bind('<Destroy>',restore)
        return win,layout
    def message(self,text,title=None):
        w,d=self.dialog('MESSAGE_DIALOG',('IDOK',));w.title(title or 'FastCopy');d.set('MESSAGE_EDIT',text);d.button('IDOK',w.destroy);d.controls['IDOK'].focus_set();w.bind('<Return>',lambda e:w.destroy());self.root.wait_window(w)
    def confirm(self,src,dest,mode):
        w,d=self.dialog('COPYCONFIRM_DIALOG',('IDOK','IDCANCEL'));answer=[False];d.set('SRC_EDIT','\n'.join(map(display_path,src)));d.set('DST_EDIT',dest)
        w.title({'sync':'Synchronizing','move':'Moving'}.get(mode,'Execute Copy'))
        if mode=='sync':
            w.title('Synchronizing — removes destination extras');d.set('DST_EDIT',dest+'\nWARNING: destination-only entries will be deleted.')
        if mode=='move':
            w.title('Moving — removes source after verification');d.set('DST_EDIT',dest+'\nWARNING: source entries will be deleted after verification.')
        def ok():answer[0]=True;w.destroy()
        d.button('IDOK',ok);d.button('IDCANCEL',w.destroy);d.controls['IDCANCEL'].focus_set();self.root.wait_window(w);return answer[0]
    def about(self):
        w,d=self.dialog('ABOUT_DIALOG',('IDOK','URL_BUTTON'));d.set('ABOUT_STATIC','FastCopy 3.63\n\nLinux interface clone 0.2.8\nOriginal: Shirouzu Hiroaki');d.set('LIB_STATIC','Native Linux engine / xxHash64\nGPLv3');d.button('IDOK',w.destroy);d.button('URL_BUTTON',lambda:webbrowser.open('https://fastcopy.jp/en/'))
    def post_process(self):
        w,d=self.dialog('FINACTION_DIALOG',('HELP_BUTTON',));d.set('TITLE_COMBO','Normal');d.button('HELP_BUTTON',lambda:self.message('Post-Process commands, sound and power actions are not implemented in this Linux release.'))
    def help(self):
        path=HERE.parent/'help/fastcopy_eng.htm'
        if not path.exists():path=ASSETS/'fastcopy_eng.htm'
        webbrowser.open(path.resolve().as_uri()) if path.exists() else self.message('See the included README for Linux differences.')
    def open_dir(self,path):
        try:path.mkdir(parents=True,exist_ok=True);subprocess.Popen(['xdg-open',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except OSError as e:self.message(str(e))
    def open_log(self,files):
        p=CONFIG_DIR/('LastFileLog.log' if files else 'FastCopy.log')
        if not p.exists():return self.message('No saved log.')
        try:subprocess.Popen(['xdg-open',str(p)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except OSError as e:self.message(str(e))
    def snapshot(self):
        return {'sources':self.read_sources()[:],'destination':self.dest.get(),'mode':self.mode.get(),'verify':self.verify.get(),'estimate':self.estimate.get(),'xattrs':self.xattrs.get(),'filter':self.filter.get(),'include':self.include.get(),'exclude':self.exclude.get()}
    def load_job(self,name):
        if self.proc:return
        j=self.jobs.get(name)
        if not isinstance(j,dict):return
        try:
            self.sources=list(j['sources']);self.refresh_sources();self.dest.set(j['destination']);m=j.get('mode',DEFAULT_MODE);m={'Diff (Size/date)':DEFAULT_MODE,'Sync (Size/date; removes extras)':STRINGS['IDS_SYNC_ATTR'],'Move (Verified overwrite)':STRINGS['IDS_MOVE_FORCE']}.get(m,m);self.mode.set(m if m in MODES else DEFAULT_MODE)
            self.verify.set(j.get('verify',True));self.estimate.set(j.get('estimate',False));self.xattrs.set(j.get('xattrs',False));self.ui.set('STREAM_CHECK',False);self.include.set(j.get('include',''));self.exclude.set(j.get('exclude',''));self.filter.set(j.get('filter',bool(j.get('include') or j.get('exclude'))));self.filter_state();self.mode_change()
        except (ValueError,TypeError,KeyError) as e:self.message(str(e))
    def job_dialog(self):
        if self.proc:return
        w,d=self.dialog('JOB_DIALOG',('TITLE_COMBO','IDOK','JOBDEL_BUTTON','HELP_BUTTON'));d.controls['TITLE_COMBO'].configure(values=sorted(self.jobs))
        def persist():
            try:write_json(CONFIG,self.jobs)
            except OSError as e:self.message(str(e));return False
            self.update_job_menu();d.controls['TITLE_COMBO'].configure(values=sorted(self.jobs));return True
        def register():
            name=d.get('TITLE_COMBO').strip()
            if not name:return self.message('Enter a JobName.')
            try:self.jobs[name]=self.snapshot()
            except ValueError as e:return self.message(str(e))
            if persist():w.destroy()
        def delete():
            name=d.get('TITLE_COMBO');self.jobs.pop(name,None);persist();d.set('TITLE_COMBO','')
        d.button('IDOK',register);d.button('JOBDEL_BUTTON',delete);d.button('HELP_BUTTON',self.help)
    def settings(self,index=0):
        if self.proc:return
        w,d=self.dialog('SETUP_DIALOG',('IDOK','IDAPPLY','IDCANCEL','SETUP_LIST'));pages=[]
        bindings={1:{'ESTIMATE_CHECK':'estimate','VERIFY_CHECK':'verify','ACL_CHECK':'xattrs','EXTENDFILTER_CHECK':'extended','SPEED_SLIDER':'speed'},2:{'BUFSIZE_EDIT':'buffer'},7:{'HISTORY_EDIT':'history_count','ERRLOG_CHECK':'errorlog','FILELOG_CHECK':'filelog'},10:{'EXECCONFIRM_CHECK':'confirm'}}
        theme_dirty=[False]
        for num in range(1,11):
            supported=set(bindings.get(num,{}))
            if num==1:supported.add('DARK_THEME_CHECK')
            if num==10:supported|={'SPAN1_RADIO','SPAN2_RADIO','SPAN3_RADIO'}
            p=Layout(d.frame,'SETUP_SHEET'+str(num),enabled=supported,images=self.images)
            for control,key in bindings.get(num,{}).items():p.set(control,valid_speed(self.cfg[key]) if key=='speed' else self.cfg[key])
            if num==1:
                p.set('IGNORE_CHECK',True);p.set('STREAM_CHECK',False);p.set('LCID_CHECK',True);p.set('SPEED_SLIDER',valid_speed(self.cfg['speed']));p.set('SPEED_STATIC',speed_label(p.get('SPEED_SLIDER')));p.controls['SPEED_SLIDER'].configure(command=lambda value, page=p:page.set('SPEED_STATIC',speed_label(int(float(value)))))
                saved=self.cfg['theme']
                p.set('DARK_THEME_CHECK',(saved if saved in ('dark','light') else self.theme_mode)=='dark')
                p.variables['DARK_THEME_CHECK'].trace_add('write',lambda *_:theme_dirty.__setitem__(0,True))
            if num==2:
                p.set('OVLSIZE_EDIT','1');p.set('OVLNUM_EDIT','20');p.set('MAXTRANS_EDIT','20')
            if num==5:
                p.set('REPARSE_CHECK',True);p.controls['HASH_COMBO'].configure(values=['xxHash64']);p.set('HASH_COMBO','xxHash64')
            if num==7:p.set('UTF8LOG_CHECK',True)
            if num==9:p.set('TASKBAR_CHECK',True)
            if num==10:p.set('DIRSEL_CHK',True);p.variables['_radio'].set({250:'SPAN1_RADIO',500:'SPAN2_RADIO',1000:'SPAN3_RADIO'}.get(self.cfg['span'],'SPAN1_RADIO'))
            pages.append(p)
        lst=d.controls['SETUP_LIST'];lst.insert('end',*SHEETS);w.geometry(f'{px(294)}x{py(224)}')
        def show(_=None):
            ix=lst.curselection();idx=ix[0] if ix else 0
            for p in pages:p.frame.place_forget()
            p=pages[idx];p.frame.place(x=px(80),y=py(4),width=px(p.spec['size'][0]),height=py(p.spec['size'][1]));p.frame.lift()
        lst.bind('<<ListboxSelect>>',show);lst.selection_set(index);lst.activate(index);show()
        def apply(close=False):
            updates={}
            try:
                for n,mapping in bindings.items():
                    for c,k in mapping.items():updates[k]=pages[n-1].get(c)
                updates['buffer']=int(updates['buffer']);updates['history_count']=int(updates['history_count']);updates['span']={'SPAN1_RADIO':250,'SPAN2_RADIO':500,'SPAN3_RADIO':1000}[pages[9].variables['_radio'].get()]
                if not 1<=updates['buffer']<=256 or not 0<=updates['history_count']<=100:raise ValueError('Buffer Size(MB): 1–256. History items: 0–100.')
            except (ValueError,KeyError) as e:return self.message(str(e))
            if theme_dirty[0]:
                updates['theme']='dark' if pages[0].get('DARK_THEME_CHECK') else 'light'
            self.cfg.update(updates);self.cfg['dirselect']=True;self.ui.set('SPEED_SLIDER',valid_speed(updates['speed']));self.speed_changed();self.save_config();theme_dirty[0]=False;self.verify.set(updates['verify']);self.estimate.set(updates['estimate']);self.xattrs.set(updates['xattrs']);self.ui.set('STREAM_CHECK',False);self.ui.set('BUFSIZE_EDIT',str(updates['buffer']));self.extended.set(updates['extended']);self.set_geometry();self.mode_change()
            if close:w.destroy()
        d.button('IDOK',lambda:apply(True));d.button('IDAPPLY',apply);d.button('IDCANCEL',w.destroy)
    def speed_changed(self,_=None):
        speed=self.ui.get('SPEED_SLIDER');self.ui.set('SPEED_STATIC',speed_label(speed))
        if speed:self.resume_speed=speed
        if self.proc and not self.cancelling:
            self.is_paused=speed==0;self.send_speed()
            for i in ('PAUSEOK_BTN','PAUSELIST_BTN'):self.ui.controls[i].config(image=self.images['play' if self.is_paused else 'pause'])
            self.ui.set('STATUS_EDIT',self.status_text())
    def send_speed(self):
        proc=self.proc
        if not proc or self.cancelling:return
        try:os.write(proc.stdin.fileno(),bytes([self.ui.get('SPEED_SLIDER')//10]))
        except BlockingIOError:
            self.root.after(50,lambda:self.send_speed() if self.proc is proc else None)
        except (BrokenPipeError,ValueError,OSError):pass
    def build_args(self,dry):
        exe=engine_path()
        if not exe:raise ValueError('Native engine not found. Run install.sh.')
        src=self.read_sources();dest=self.dest.get();mode=MODES.get(self.mode.get())
        if mode is None:raise ValueError('Delete all is not implemented in this Linux release.')
        if not src or not dest:raise ValueError('Specify Source and DestDir.')
        if mode=='sync' and len(src)!=1:raise ValueError('Sync requires one source directory.')
        if mode=='move' and not self.verify.get():raise ValueError('Move requires verification.')
        disk=self.disk.get();budget=int(self.cfg['buffer']);workers=1 if disk=='same' else min(4,budget) if disk=='diff' or budget<4 else 0
        # Original 256 MB field is a budget here, not a Win32 overlapped I/O buffer.
        buf=min(64,max(1,budget//(workers or 4)));args=[exe,'--machine','--mode',mode,'--buffer-mib',str(buf),'--dest',dest,'--speed-percent',str(self.ui.get('SPEED_SLIDER')),'--live-speed']
        if workers:args+=['--workers',str(workers)]
        if not self.verify.get():args+=['--no-verify']
        if self.xattrs.get():args+=['--xattrs']
        if self.filter.get():
            includes=[x.strip() for x in self.include.get().split(';') if x.strip()]
            excludes=[x.strip() for x in self.exclude.get().split(';') if x.strip()]
            if (includes or excludes) and mode in ('sync','move'):raise ValueError('Filters are not supported in Sync or Move.')
            for glob in includes:args+=['--include',glob]
            for glob in excludes:args+=['--exclude',glob]
        if dry:args+=['--dry-run']
        elif mode in ('sync','move'):args+=['--allow-delete']
        return args+['--']+src,mode
    def toggle_running(self,running):
        for i in ('SRC_FILE_BUTTON','HIST_BTN','SRC_EDIT','DST_FILE_BUTTON','DST_COMBO','MODE_COMBO','VERIFY_CHECK','ESTIMATE_CHECK','ACL_CHECK','FILTER_CHECK','INCLUDE_COMBO','EXCLUDE_COMBO'):self.ui.enable(i,not running)
        for i in ('JOB_MENUITEM','SETUP_MENUITEM','SWAPTARGET_MENUITEM','AUTODISK_MENUITEM','SAMEDISK_MENUITEM','DIFFDISK_MENUITEM','FONT_MENUITEM','RESETFONT_MENUITEM','FINACT_MENUITEM'):
            m,k=self.menus[i];m.entryconfigure(k,state='disabled' if running else 'normal')
        if self.jobmenu:
            for k in range(2,(self.jobmenu.index('end') or 0)+1):self.jobmenu.entryconfigure(k,state='disabled' if running else 'normal')
        self.ui.controls['LIST_BUTTON'].configure(text='Cancel...' if running and self.dry else 'Listing');self.ui.controls['IDOK'].configure(text='Cancel...' if running and not self.dry else 'Execute')
        self.ui.enable('LIST_BUTTON',not running or self.dry);self.ui.enable('IDOK',not running or not self.dry)
        if not running:self.mode_change();self.filter_state()
        self.layout_main()
    def start(self,dry):
        if self.proc:
            if self.dry==dry:self.cancel()
            return
        try:args,mode=self.build_args(dry)
        except (OSError,ValueError) as e:return self.message(str(e))
        if not dry and (mode in ('sync','move','overwrite') or self.cfg['confirm']):
            if not self.confirm(self.sources,self.dest.get(),mode):return
        self.record_history();self.dry=dry;self.started=time.monotonic();self.is_paused=self.ui.get('SPEED_SLIDER')==0;self.cancelling=False;self.last_progress=None;self.full_log=[];self.err_log=[];self.result=''
        self.ui.set('PATH_EDIT','');self.ui.set('ERR_EDIT','');self.ui.set('ERRSTATUS_STATIC','(ErrFiles : 0 / ErrDirs : 0)');self.ui.set('STATUS_EDIT','Listing...' if dry else 'Executing...')
        try:self.proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);os.set_blocking(self.proc.stdin.fileno(),False)
        except OSError as e:return self.message(str(e))
        for i in ('PAUSEOK_BTN','PAUSELIST_BTN'):self.ui.controls[i].configure(image=self.images['play' if self.is_paused else 'pause'])
        self.toggle_running(True);threading.Thread(target=self.read_output,args=(self.proc,),daemon=True).start()
    def read_output(self,proc):
        try:
            for line in proc.stdout:self.events.put(('line',line.decode('ascii',errors='replace').rstrip('\r\n')))
        finally:
            proc.stdout.close();rc=proc.wait();proc.stdin.close();self.events.put(('exit',rc))
    def status_text(self):
        if self.last_progress is None:return self.result or ('Listing...' if self.dry else 'Executing...')
        b,t,d,n,c,s,e,r=self.last_progress;elapsed=max(.01,time.monotonic()-self.started);mib=(t if self.dry else b)/1048576
        lines=[f'Total{"Size " if self.dry else "Write"}= {mib:,.1f} MiB',f'TotalFiles = {d if self.dry else c:,} / {n:,}']
        lines.append(f'SkipFiles  = {s:,}')
        if r:lines.append(f'DelEntries = {r:,}')
        lines.append(f'TotalTime  = {int(elapsed)//60}:{int(elapsed)%60:02d}')
        if not self.dry:lines.extend([f'TransRate  = {mib/elapsed:,.1f} MiB/s',f'FileRate   = {c/elapsed:,.1f} files/s'])
        if self.estimate.get() and not self.result and d and n>d:lines.append(f'Estimate   = {elapsed*(n-d)/d:,.0f} sec')
        if self.is_paused:lines.append('Suspend')
        if self.cancelling:lines.append('Cancelling...')
        if self.result:lines.append(self.result)
        return '\n'.join(lines)
    def poll(self):
        path_lines=[];error_lines=[];preview_finished=False
        for _ in range(300):
            try:kind,data=self.events.get_nowait()
            except queue.Empty:break
            if kind=='line':
                parts=data.split('\t')
                if parts[0]=='P' and len(parts)==9:
                    try:
                        self.last_progress=tuple(map(int,parts[1:]));self.ui.set('STATUS_EDIT',self.status_text());e=self.last_progress[6];self.ui.set('ERRSTATUS_STATIC',f'(ErrFiles/ErrDirs : {e} total)' if e else '(ErrFiles : 0 / ErrDirs : 0)')
                    except ValueError:pass
                elif len(parts)==2 and parts[0] in ('I','E'):
                    try:text=os.fsdecode(bytes.fromhex(parts[1]))
                    except ValueError:text=data
                    self.full_log.append(text)
                    if parts[0]=='E':self.err_log.append(text);error_lines.append(text)
                    # A preview is an action list, not an inventory of skipped files.
                    # Retain the raw engine output in the optional file log.
                    elif not (self.dry and text.startswith('Would skip: ')):path_lines.append(text)
                else:self.full_log.append(data);path_lines.append(data)
            else:
                rc=data;self.proc=None;self.is_paused=False;self.cancelling=False;self.result=('---- Listing Done ----' if self.dry else '---- Finished ----') if rc==0 else '---- Cancelled ----' if rc in (130,-signal.SIGINT,-signal.SIGTERM) else f'---- Error ({rc}) ----'
                if self.dry:
                    if self.last_progress is not None:path_lines.append(f'Skipped files: {self.last_progress[5]:,}')
                    preview_finished=True
                path_lines.append(self.result)
                self.ui.set('STATUS_EDIT',self.status_text());self.ui.set('SPEED_SLIDER',self.ui.get('SPEED_SLIDER') or self.resume_speed);self.ui.set('SPEED_STATIC',speed_label(self.ui.get('SPEED_SLIDER')));self.toggle_running(False)
                try:
                    CONFIG_DIR.mkdir(parents=True,exist_ok=True)
                    if self.cfg['errorlog'] and self.err_log:
                        with (CONFIG_DIR/'FastCopy.log').open('a',encoding='utf-8',errors='backslashreplace') as f:f.write(time.strftime('%Y-%m-%d %H:%M:%S')+'\n'+'\n'.join(self.err_log)+'\n')
                    if self.cfg['filelog']:(CONFIG_DIR/'LastFileLog.log').write_text('\n'.join(self.full_log)+'\n',encoding='utf-8',errors='backslashreplace')
                except OSError as e:error_lines.append(str(e))
        listing=self.ui.controls['PATH_EDIT'];following=listing.yview()[1]>=0.999
        self.ui.append_many('PATH_EDIT',path_lines);self.ui.append_many('ERR_EDIT',error_lines)
        if preview_finished and following:listing.yview_moveto(0)
        self.root.after(max(50,min(1000,int(self.cfg.get('span',250)))),self.poll)
    def send(self,sig):
        if self.proc:
            try:self.proc.send_signal(sig)
            except ProcessLookupError:pass
    def pause(self):
        if self.proc and not self.cancelling:
            self.ui.set('SPEED_SLIDER',self.resume_speed if self.is_paused else 0);self.speed_changed()
    def cancel(self):
        if self.proc:self.cancelling=True;self.send(signal.SIGINT);self.ui.set('STATUS_EDIT',self.status_text())
    def close(self):
        if self.proc:self.cancel();return
        self.root.destroy()


def main():
    p=argparse.ArgumentParser(description='FastCopy 3.63 resource-layout clone for Linux');p.add_argument('--dest',default='');p.add_argument('sources',nargs='*');p.add_argument('--smoke-test',action='store_true',help=argparse.SUPPRESS);args=p.parse_args()
    root=tk.Tk(className='FastCopy');app=App(root,args.sources,args.dest)
    if args.smoke_test:root.after(300,root.destroy)
    root.mainloop()

if __name__=='__main__':main()
