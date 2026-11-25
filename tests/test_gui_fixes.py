#!/usr/bin/env python3
"""Regression coverage for 0.2.5 UI corrections. Run in Xvfb or on X11."""
import importlib.machinery,importlib.util,os,sys,tempfile,time,subprocess
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='fastcopy-ui-fixes-') as tmp:
    os.environ['XDG_CONFIG_HOME']=str(Path(tmp)/'config')
    loader=importlib.machinery.SourceFileLoader('fastcopy_gui',sys.argv[1]);spec=importlib.util.spec_from_loader(loader.name,loader);mod=importlib.util.module_from_spec(spec);loader.exec_module(mod)
    out=Path(sys.argv[2]) if len(sys.argv)>2 else None
    if out:out.mkdir(parents=True,exist_ok=True)
    base=Path(tmp);src=base/'source folder';dst=base/'dest folder';sub=src/'sub folder';sub.mkdir(parents=True);dst.mkdir();(src/'a.txt').write_text('a');(src/'skip.txt').write_text('skip');(src/'b.jpg').write_text('b');(sub/'c.txt').write_text('c');(base/'file.txt').write_text('file')
    root=mod.tk.Tk();app=mod.App(root);root.update()
    def children(w):
        for c in w.winfo_children():yield c;yield from children(c)
    def capture(w,name):
        if out:
            w.update();subprocess.run(['scrot','-o','-a',f'{w.winfo_rootx()},{w.winfo_rooty()},{w.winfo_width()},{w.winfo_height()}',str(out/name)],check=True)
    def modal():return next(w for w in root.winfo_children() if isinstance(w,mod.tk.Toplevel))
    def press(w,text):next(c for c in children(w) if isinstance(c,mod.tk.Button) and c.cget('text')==text).invoke();root.update()
    def interact(action,callback):
        errors=[]
        def run():
            w=modal()
            try:callback(w.folder_picker)
            except BaseException as e:errors.append(e);w.destroy()
        root.after(80,run);action();root.update()
        if errors:raise errors[0]
    def choose(p,folder,screen):
        p.navigate(str(base));p.win.update()
        assert p.win.cget('bg')==app.palette['bg']
        assert p.listbox.cget('bg')==app.palette['field'] and p.entry.cget('bg')==app.palette['field']
        assert p.listbox.cget('fg')==app.palette['fg']
        assert not any(isinstance(c,mod.ttk.Combobox) for c in children(p.win))
        assert 'file.txt' not in p.names and folder.name in p.names
        p.path.set(str(base/'file.txt'));p.open_button.invoke();assert p.win.winfo_exists() and p.error.get()
        p.navigate(str(base));p.listbox.selection_set(p.names.index(folder.name));p.select();assert p.path.get()==str(folder)
        capture(p.win,screen);p.open_button.invoke()
    interact(app.ui.controls['SRC_FILE_BUTTON'].invoke,lambda p:choose(p,src,'source-folder.png'))
    assert app.read_sources()==[str(src)]
    interact(app.ui.controls['DST_FILE_BUTTON'].invoke,lambda p:choose(p,dst,'dest-folder.png'))
    assert app.dest.get()==str(dst)
    def nav_cancel(p):
        p.navigate(str(src));assert 'sub folder' in p.names and 'a.txt' not in p.names
        p.listbox.selection_set(p.names.index('sub folder'));p.enter_selected();assert p.current==sub
        p.navigate(str(p.current.parent));assert p.current==src
        p.win.destroy()
    interact(app.ui.controls['SRC_FILE_BUTTON'].invoke,nav_cancel);assert app.read_sources()==[str(src)]
    print('PASS folder-only Source and DestDir pickers, dark/light palette, no filetypes dropdown, selection -> text fields, file rejection, navigation, Cancel')
    app.ui.controls['FILTER_CHECK'].invoke();root.update()
    for control in ('INCLUDE_COMBO','EXCLUDE_COMBO'):assert str(app.ui.controls[control].cget('state'))=='normal'
    app.include.set('*.txt');app.exclude.set('skip*');capture(root,'filter.png');assert app.snapshot()['include']=='*.txt'
    app.jobs['filter']=app.snapshot();app.include.set('');app.load_job('filter');assert app.include.get()=='*.txt'
    def speed_steps(scale,label):
        assert float(scale.cget('from'))==0 and float(scale.cget('to'))==100 and float(scale.cget('resolution'))==10
        for rate in range(0,101,10):
            scale.set(rate);root.update();assert label.cget('text')==mod.speed_label(rate),(rate,label.cget('text'))
    speed_steps(app.ui.controls['SPEED_SLIDER'],app.ui.controls['SPEED_STATIC']);app.ui.controls['SPEED_SLIDER'].set(50);root.update();capture(root,'speed-main.png')
    app.settings();root.update();w=modal();visible=[c for c in children(w) if c.winfo_ismapped()];scale=next(c for c in visible if isinstance(c,mod.tk.Scale));assert scale.cget('state')=='normal'
    label=next(c for c in visible if isinstance(c,mod.tk.Label) and c.cget('text')=='Full Speed');speed_steps(scale,label)
    scale.set(60);root.update();capture(w,'speed-settings.png');press(w,'Apply');assert app.cfg['speed']==60 and app.ui.get('SPEED_SLIDER')==60
    scale.set(80);root.update();press(w,'Cancel');assert app.cfg['speed']==60 and app.ui.get('SPEED_SLIDER')==60
    assert mod.read_json(mod.CONFIG_DIR/'settings.json',{})['speed']==60
    print('PASS all 11 speed positions on both sliders, default persistence, Apply and Cancel')
    app.settings(6);root.update();w=modal();visible=[c for c in children(w) if c.winfo_ismapped()]
    history=next(c for c in visible if isinstance(c,mod.tk.Label) and 'Number of history items' in c.cget('text'))
    assert '\r' not in history.cget('text') and history.winfo_height()>=history.winfo_reqheight(),(history.winfo_height(),history.winfo_reqheight());capture(w,'log-settings.png')
    lst=next(c for c in children(w) if isinstance(c,mod.tk.Listbox))
    for i in range(10):
        lst.selection_clear(0,'end');lst.selection_set(i);lst.event_generate('<<ListboxSelect>>');root.update()
        for c in children(w):
            if c.winfo_ismapped() and isinstance(c,(mod.tk.Label,mod.tk.Checkbutton,mod.tk.Radiobutton)):
                assert c.winfo_reqheight()<=c.winfo_height(),(i,c.cget('text'),c.winfo_reqheight(),c.winfo_height())
    lst.selection_clear(0,'end');lst.selection_set(8);lst.event_generate('<<ListboxSelect>>');root.update()
    assert not any(c.cget('image') or c.cget('text')=='?' for c in children(w) if isinstance(c,mod.tk.Button));capture(w,'at-minimized.png');w.destroy();root.update()
    app.about();root.update();w=modal();icon=next(c for c in children(w) if isinstance(c,mod.tk.Label) and c.cget('image'))
    assert icon.image_ref.width()<=icon.winfo_width() and icon.image_ref.height()<=icon.winfo_height();capture(w,'about.png');w.destroy();root.update()
    print('PASS uncut About icon, all visible settings labels fit including Log Settings, removed image button')
    # Actual GUI starts suspended, then resumes at 30%, copies only included files.
    app.sources=[str(src)+'/'];app.refresh_sources();app.ui.controls['SPEED_SLIDER'].set(0);root.update();app.start(False)
    until=time.monotonic()+.25
    while time.monotonic()<until:root.update();time.sleep(.01)
    assert app.proc and app.is_paused and not (dst/'a.txt').exists()
    assert str(app.ui.controls['INCLUDE_COMBO'].cget('state'))=='disabled'
    app.ui.controls['SPEED_SLIDER'].set(30);root.update();assert not app.is_paused and app.ui.get('SPEED_STATIC')=='30%'
    until=time.monotonic()+15
    while app.proc and time.monotonic()<until:root.update();time.sleep(.02)
    assert app.proc is None and 'Finished' in app.result,app.result
    assert (dst/'a.txt').read_text()=='a' and (dst/'sub folder'/'c.txt').read_text()=='c';assert not (dst/'skip.txt').exists() and not (dst/'b.jpg').exists()
    assert str(app.ui.controls['INCLUDE_COMBO'].cget('state'))=='normal'
    app.ui.controls['FILTER_CHECK'].invoke();root.update();assert str(app.ui.controls['INCLUDE_COMBO'].cget('state'))=='disabled'
    print('PASS GUI suspend/resume at 30%, real include/exclude copy, filter fields enabled only while idle and checked')
    root.destroy()
