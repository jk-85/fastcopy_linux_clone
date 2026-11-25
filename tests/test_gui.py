#!/usr/bin/env python3
"""GUI and dialog smoke tests. Run under X11/Xvfb with a real engine."""
import importlib.machinery,importlib.util,os,sys,tempfile,time,subprocess
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='fastcopy-gui-test-') as tmp:
    os.environ['XDG_CONFIG_HOME']=str(Path(tmp)/'config')
    loader=importlib.machinery.SourceFileLoader('fastcopy_gui',sys.argv[1]);spec=importlib.util.spec_from_loader(loader.name,loader);mod=importlib.util.module_from_spec(spec);loader.exec_module(mod)
    src=Path(tmp)/'source';dst=Path(tmp)/'destination';src.mkdir();(src/'example file.txt').write_text('FastCopy Mint GUI test\n');(src/'日本語.txt').write_text('hello');(src/'newline\nfile.txt').write_text('new')
    root=mod.tk.Tk();app=mod.App(root,[str(src)+'/'],str(dst)+'/');root.update()
    assert app.ui.get('MODE_COMBO')=='Diff (Size/Date)';assert app.ui.controls['IDOK'].cget('text')=='Execute';assert app.ui.controls['LIST_BUTTON'].cget('text')=='Listing'
    assert app.ui.get('IGNORE_CHECK');assert str(app.ui.controls['INCLUDE_COMBO'].cget('state'))=='disabled'
    print('PASS original main-window labels and disabled controls')
    def center_y(w):return w.winfo_rooty()+w.winfo_height()/2
    def check_slider(slider,neighbors):
        assert slider.winfo_height()==slider.winfo_reqheight()
        assert slider.winfo_x()+slider.winfo_width()<=slider.master.winfo_width(), 'Slider extends outside its parent'
        expected=sum(center_y(w) for w in neighbors)/len(neighbors)
        assert abs(center_y(slider)-expected)<=1,(center_y(slider),expected)
    def check_main_margins():
        ui=app.ui
        outer={c['id']:w for c,w,_ in ui.items}
        left=outer['PATH_EDIT'].winfo_x()
        assert left==mod.px(4)
        edge=ui.frame.winfo_width()-left
        assert outer['SRC_FILE_BUTTON'].winfo_x()==left
        names=['SRC_EDIT','DST_COMBO','MODE_COMBO','BUFSIZE_EDIT','SPEED_SLIDER',
               'SAMEDRV_STATIC','IDOK','FILTER_CHECK','PATH_EDIT','ERR_EDIT']
        if app.extended.get():names.append('MAXSIZE_COMBO')
        if app.filter.get():names.append('FILTERHELP_BUTTON')
        for name in names:
            w=outer[name]
            assert w.winfo_ismapped(),name
            assert w.winfo_x()+w.winfo_width()==edge,(name,w.winfo_x()+w.winfo_width(),edge)
        assert outer['LIST_BUTTON'].winfo_x()==outer['SAMEDRV_STATIC'].winfo_x()
        assert outer['IDOK'].winfo_x()-(outer['LIST_BUTTON'].winfo_x()+outer['LIST_BUTTON'].winfo_width())==mod.px(2)
        assert outer['SPEED_STATIC'].winfo_x()+outer['SPEED_STATIC'].winfo_width()==outer['SPEED_SLIDER'].winfo_x()
        assert outer['BUFSIZE_EDIT'].winfo_x()-(outer['BUF_STATIC'].winfo_x()+outer['BUF_STATIC'].winfo_width())==mod.px(4)
        for name,w in outer.items():
            if w.winfo_ismapped():assert w.winfo_x()+w.winfo_width()<=edge,(name,edge)
    check_main_margins()
    check_slider(app.ui.controls['SPEED_SLIDER'],[app.ui.controls['SPEED_STATIC']])
    root.geometry(f'{root.winfo_width()+100}x{root.winfo_height()+40}');root.update()
    check_slider(app.ui.controls['SPEED_SLIDER'],[app.ui.controls['SPEED_STATIC']])
    check_main_margins()
    root.geometry(f'{app.size()[0]}x{app.size()[1]}');root.update()
    check_main_margins()
    check_slider(app.ui.controls['SPEED_SLIDER'],[app.ui.controls['SPEED_STATIC']])
    assert app.ui.controls['SPEED_STATIC'].winfo_x()==mod.px(147)
    app.extended.set(True);app.set_geometry();root.update()
    check_slider(app.ui.controls['SPEED_SLIDER'],[app.ui.controls['SPEED_STATIC']])
    check_main_margins()
    app.filter.set(True);app.filter_state();root.update();check_main_margins()
    root.geometry(f'{root.winfo_width()+100}x{root.winfo_height()+40}');root.update();check_main_margins()
    root.geometry(f'{app.size()[0]}x{app.size()[1]}');root.update();check_main_margins()
    app.filter.set(False);app.filter_state();app.extended.set(False);app.set_geometry();root.update();check_main_margins()
    print('PASS equal 8 px main-window margins and common right edge at default/enlarged/restored sizes, extended filter and filter help')
    print('PASS main speed track aligned at original and enlarged window sizes')
    def capture(path,window=root):
        window.update();subprocess.run(['scrot','-o','-a',f'{window.winfo_rootx()},{window.winfo_rooty()},{window.winfo_width()},{window.winfo_height()}',str(path)],check=True)
    out=Path(sys.argv[2]) if len(sys.argv)>2 else None
    if out:out.mkdir(parents=True,exist_ok=True);capture(out/'main.png')
    # Render all templates and check all controls were instantiated (including repeated IDC_STATICs).
    for name,data in mod.DIALOGS.items():
        w=mod.tk.Toplevel(root);layout=mod.Layout(w,name,images=app.images);layout.frame.pack();w.update();assert len(layout.items)==len(data['controls'])-(2 if name=='JOB_DIALOG' else 0)+(1 if name=='SETUP_SHEET1' else 0)-(1 if name=='SETUP_SHEET9' else 0)-(1 if name.startswith('SETUP_SHEET') else 0),name;w.destroy()
    print('PASS rendered all 20 dialog templates / 217 controls (scheduler shortcut, note, settings Play icon and 10 help buttons omitted; Linux dark theme checkbox added)')
    app.settings();root.update();win=[w for w in root.winfo_children() if isinstance(w,mod.tk.Toplevel)][0]
    if out:capture(out/'settings.png',win)
    def descendants(w):
        for c in w.winfo_children():yield c;yield from descendants(c)
    lst=next(w for w in descendants(win) if isinstance(w,mod.tk.Listbox));assert lst.size()==10
    visible=[w for w in descendants(win) if w.winfo_ismapped()]
    slider=next(w for w in visible if isinstance(w,mod.tk.Scale))
    labels=[w for w in visible if isinstance(w,mod.tk.Label) and w.cget('text') in ('Speed Control','Full Speed')]
    assert len(labels)==2,[w.cget('text') for w in visible if isinstance(w,mod.tk.Label)]
    check_slider(slider,labels)
    assert all(abs(center_y(slider)-center_y(w))<=2.5 for w in labels)
    print('PASS settings speed track centered between its two neighbors')
    for i in range(10):
        lst.selection_clear(0,'end');lst.selection_set(i);lst.event_generate('<<ListboxSelect>>');root.update()
        if out:capture(out/f'settings-{i+1:02}.png',win)
    win.destroy();root.update();print('PASS all ten settings pages navigate')
    app.job_dialog();root.update();win=[w for w in root.winfo_children() if isinstance(w,mod.tk.Toplevel)][0]
    assert not any('windows task scheduler' in w.cget('text').lower() for w in descendants(win) if isinstance(w,(mod.tk.Button,mod.tk.Label)))
    print('PASS Windows Task Scheduler shortcut and note absent from Job dialog')
    combo=next(w for w in descendants(win) if isinstance(w,mod.ttk.Combobox));combo.set('Test profile')
    if out:capture(out/'job.png',win)
    button=next(w for w in descendants(win) if isinstance(w,mod.tk.Button) and w.cget('text')=='Register');button.invoke();root.update();assert 'Test profile' in app.jobs
    app.sources=[];app.refresh_sources();app.load_job('Test profile');assert app.read_sources()==[str(src)+'/'];print('PASS original Job dialog registers and reloads job')
    app.about();root.update();win=[w for w in root.winfo_children() if isinstance(w,mod.tk.Toplevel)][0]
    if out:capture(out/'about.png',win)
    win.destroy()
    app.post_process();root.update();win=[w for w in root.winfo_children() if isinstance(w,mod.tk.Toplevel)][0]
    if out:capture(out/'post-process.png',win)
    win.destroy()
    def wait():
        until=time.monotonic()+15
        while app.proc and time.monotonic()<until:root.update();time.sleep(.02)
        root.update();assert app.proc is None,app.result
    app.start(True);wait();assert not dst.exists();assert 'Listing Done' in app.result;print('PASS Listing is non-writing')
    app.start(False);wait();assert (dst/'example file.txt').read_text()=='FastCopy Mint GUI test\n';assert (dst/'日本語.txt').read_text()=='hello';assert (dst/'newline\nfile.txt').read_text()=='new';assert 'Finished' in app.result;print('PASS Execute copies all files through native engine')
    if out:capture(out/'completed.png')
    # Destructive copy confirmation defaults to Cancel, and can be exercised without actually running it.
    def cancel_dialog():
        w=[w for w in root.winfo_children() if isinstance(w,mod.tk.Toplevel)][0]
        if out:capture(out/'confirm.png',w)
        next(b for b in descendants(w) if isinstance(b,mod.tk.Button) and b.cget('text')=='Cancel').invoke()
    root.after(150,cancel_dialog);assert not app.confirm([str(src)],str(dst),'sync');print('PASS original copy confirmation cancels safely')
    app.mode.set('Delete all');app.mode_change();assert app.ui.controls['IDOK'].cget('state')=='disabled';print('PASS unsupported Delete all cannot execute')
    root.destroy()
