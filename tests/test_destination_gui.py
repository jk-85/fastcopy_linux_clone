#!/usr/bin/env python3
"""Actual Tk picker, sash drags and destination-path transfer semantics."""
import importlib.machinery,importlib.util,os,sys,tempfile,time,subprocess
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='fastcopy-dest-gui-') as tmp:
    base=Path(tmp);os.environ['XDG_CONFIG_HOME']=str(base/'config')
    loader=importlib.machinery.SourceFileLoader('fastcopy_gui',sys.argv[1]);spec=importlib.util.spec_from_loader(loader.name,loader);mod=importlib.util.module_from_spec(spec);loader.exec_module(mod)
    out=Path(sys.argv[2]) if len(sys.argv)>2 else None
    if out:out.mkdir(parents=True,exist_ok=True)
    src=base/'Source folder';src.mkdir();(src/'file.txt').write_text('verified');(src/'nested').mkdir();(src/'nested'/'other.txt').write_text('other')
    parent=base/'Backup';parent.mkdir();(parent/'existing').mkdir();(parent/'ordinary file').write_text('keep')
    root=mod.tk.Tk();app=mod.App(root,[str(src)],str(parent));root.update()
    def children(w):
        for c in w.winfo_children():yield c;yield from children(c)
    def button(w,text):return next(c for c in children(w) if isinstance(c,mod.tk.Button) and c.cget('text')==text)
    def capture(w,name):
        if out:
            w.update();subprocess.run(['scrot','-o','-a',f'{w.winfo_rootx()},{w.winfo_rooty()},{w.winfo_width()},{w.winfo_height()}',str(out/name)],check=True)
    def modal(action,callback):
        errors=[]
        def run():
            w=next(w for w in root.winfo_children() if isinstance(w,mod.tk.Toplevel))
            try:callback(w.folder_picker)
            except BaseException as e:errors.append(e);w.destroy()
        root.after(100,run);action();root.update()
        if errors:raise errors[0]
    def new_folder(p,value=None,cancel=False,capture_prompt=False):
        errors=[]
        def interact():
            dialog=next(w for w in p.win.winfo_children() if isinstance(w,mod.tk.Toplevel))
            try:
                entry=next(c for c in children(dialog) if isinstance(c,mod.tk.Entry));assert entry.cget('bg')==app.palette['field'];assert entry.cget('fg')==app.palette['fg']
                if value is not None:entry.insert(0,value)
                if capture_prompt:capture(dialog,'new-folder-prompt.png')
                button(dialog,'Cancel' if cancel else 'OK').invoke()
            except BaseException as e:errors.append(e);dialog.destroy()
        root.after(100,interact);p.new_folder_button.invoke();root.update()
        if errors:raise errors[0]
        assert p.win.grab_current()==p.win
    def choose_destination(p):
        p.navigate(str(parent));root.update();assert p.allow_create and not p.multiple
        new_folder(p,'cancelled',cancel=True);assert not (parent/'cancelled').exists()
        new_folder(p,'../outside');assert p.error.get() and not (base/'outside').exists()
        new_folder(p,'existing');assert p.error.get() and (parent/'existing').is_dir()
        new_folder(p,'ordinary file');assert p.error.get() and (parent/'ordinary file').read_text()=='keep'
        p.listbox.selection_set(p.names.index('existing'));p.select();new_folder(p,'New folder 日本語',capture_prompt=True)
        d=parent/'New folder 日本語';assert d.is_dir() and not (parent/'existing'/d.name).exists()
        assert p.path.get()==str(d) and [p.names[i] for i in p.listbox.curselection()]==[d.name]
        p.win.geometry(f'{p.win.minsize()[0]}x{p.win.winfo_height()}');root.update()
        header=[c for c in p.new_folder_button.master.winfo_children() if isinstance(c,mod.tk.Button)]
        assert [c.cget('text') for c in header]==['Go','Home','New Folder','Up']
        assert all(c.winfo_ismapped() for c in header) and len({c.winfo_rooty() for c in header})==1
        assert p.new_folder_button.winfo_rooty()<p.listbox.winfo_rooty()
        assert p.new_folder_button.winfo_rootx()+p.new_folder_button.winfo_width()<=header[-1].winfo_rootx()
        assert p.entry.winfo_width()>=120
        p.win.geometry('540x380');root.update();capture(p.win,'destination-new-folder.png');p.open_button.invoke()
    modal(app.choose_dest,choose_destination);assert app.dest.get()==str(parent/'New folder 日本語')
    def source_unchanged(p):
        assert not p.allow_create and not any(isinstance(c,mod.tk.Button) and c.cget('text')=='New Folder' for c in children(p.win));p.win.destroy()
    modal(app.choose_sources,source_unchanged);print('PASS themed New Folder prompt, invalid/duplicate/file names, Cancel, selection, parent location, modal grab restoration and Source unchanged')
    outer={c['id']:w for c,w,_ in app.ui.items};listing=outer['PATH_EDIT'];errors=outer['ERR_EDIT'];label=outer['ERR_STATIC'];sash=app.log_sash
    def dims():return (listing.winfo_height(),errors.winfo_height(),errors.winfo_y(),label.winfo_y())
    def geometry():
        root.update();assert sash.winfo_y()==listing.winfo_y()+listing.winfo_height()+1
        assert sash.winfo_y()+sash.winfo_height()<=label.winfo_y()
        assert label.winfo_y()+label.winfo_height()<=errors.winfo_y()
        assert min(listing.winfo_height(),errors.winfo_height())>=mod.py(14)
        assert listing.winfo_x()==errors.winfo_x()==sash.winfo_x()==mod.px(4)
        assert sash.winfo_width()==listing.winfo_width()==errors.winfo_width()==app.ui.frame.winfo_width()-2*mod.px(4)
    def drag(dy):
        root.update();y=sash.winfo_rooty()+2;x=sash.winfo_rootx()+sash.winfo_width()//2
        sash.event_generate('<ButtonPress-1>',x=sash.winfo_width()//2,y=2,rootx=x,rooty=y);root.update()
        sash.event_generate('<B1-Motion>',x=sash.winfo_width()//2,y=2+dy,rootx=x,rooty=y+dy);root.update()
        sash.event_generate('<ButtonRelease-1>',x=sash.winfo_width()//2,y=2,rootx=x,rooty=y+dy);root.update();geometry()
    geometry();initial=dims();capture(root,'default-main.png');drag(40);after=dims();assert after==(initial[0]+40,initial[1]-40,initial[2]+40,initial[3]+40),(initial,after)
    capture(root,'expanded-listing.png');drag(-60);assert dims()[0]==initial[0]-20 and dims()[1]==initial[1]+20;drag(-1000);assert listing.winfo_height()==mod.py(14);drag(1000);assert errors.winfo_height()==mod.py(14)
    root.geometry(f'{root.winfo_width()+100}x{root.winfo_height()+160}');root.update();geometry();drag(120);assert errors.winfo_height()>=mod.py(14)
    root.geometry(f'{app.size()[0]}x{app.size()[1]}');root.update();geometry()
    app.extended.set(True);app.set_geometry();root.update();geometry();drag(-30);geometry();capture(root,'extended-filter.png')
    app.extended.set(False);app.set_geometry();root.update();geometry();assert mod.read_json(mod.CONFIG_DIR/'settings.json',{})['log_split']==app.split_preference
    print('PASS actual mouse drag, equal and opposite pane resizing, minimum sizes, window resize, extended filter and persisted divider')
    def wait():
        until=time.monotonic()+15
        while app.proc and time.monotonic()<until:root.update();time.sleep(.02)
        root.update();assert app.proc is None
    app.start(False);wait();selected=parent/'New folder 日本語';assert 'Finished' in app.result and (selected/'file.txt').read_text()=='verified' and not (selected/src.name).exists()
    missing=parent/'automatic'/'new target';app.dest.set(str(missing)+'/');app.start(True);wait();assert 'Listing Done' in app.result and not missing.parent.exists()
    app.start(False);wait();assert 'Finished' in app.result and (missing/src.name/'file.txt').read_text()=='verified'
    explicit=parent/'explicit contents';app.sources=[str(src)+'/'];app.refresh_sources();app.dest.set(str(explicit)+'/');app.start(False);wait();assert 'Finished' in app.result and (explicit/'file.txt').read_text()=='verified'
    absent=parent/'not created';app.dest.set(str(absent));app.start(False);wait();assert not absent.exists() and any("add a trailing '/'" in x for x in app.err_log),app.err_log
    print('PASS actual transfers for existing no-slash, new slash, explicit source contents, preview without writes, missing no-slash error')
    actual=app.split_actual;root.destroy();root=mod.tk.Tk();app=mod.App(root);root.update();assert app.split_actual==actual,(app.split_actual,actual);root.destroy();print('PASS divider survives restart')
