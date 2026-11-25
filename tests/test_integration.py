#!/usr/bin/env python3
"""Exercise desktop selection handoff and user-level installation in temporary paths."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

binpath=Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='fastcopy-integration-test-') as tmp:
    base=Path(tmp);(base/'bin').mkdir();fake=base/'bin'/'fastcopy'
    fake.write_text('#!/usr/bin/python3\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\n');fake.chmod(0o755)
    env={k:v for k,v in os.environ.items() if not k.startswith(('NEMO_SCRIPT_','CAJA_SCRIPT_'))}
    env.update(PATH=str(base/'bin')+':'+str(binpath)+':'+env['PATH'],XDG_CONFIG_HOME=str(base/'config'),XDG_DATA_HOME=str(base/'data'))
    files=[base/'a with spaces.txt',base/'newline\nname.txt'];[p.touch() for p in files]
    for prefix in ('NEMO','CAJA'):
        e=env|{prefix+'_SCRIPT_SELECTED_URIS':'\n'.join(p.as_uri() for p in files)+'\n'}
        p=subprocess.run([str(binpath/'fastcopy-mint-selection')],env=e,capture_output=True,text=True,check=True);assert json.loads(p.stdout)==['--']+[str(f) for f in files],p.stdout
        print('PASS',prefix,'URI selection (spaces and newline)')
    p=subprocess.run([str(binpath/'fastcopy-mint-selection'),*map(str,files)],env=env,capture_output=True,text=True,check=True);assert json.loads(p.stdout)==['--']+[str(f) for f in files];print('PASS positional selection')
    if os.geteuid()!=0:
        integrate=str(binpath/'fastcopy-mint-integrate')
        subprocess.run([integrate],env=env,check=True)
        nemo=base/'data/nemo/scripts/Open in FastCopy Mint';caja=base/'config/caja/scripts/Open in FastCopy Mint';assert nemo.exists() and caja.exists();assert os.access(nemo,os.X_OK) and os.access(caja,os.X_OK)
        p=subprocess.run([str(nemo),str(files[0])],env=env,capture_output=True,text=True,check=True);assert json.loads(p.stdout)==['--',str(files[0])]
        caja.write_text('custom launcher');subprocess.run([integrate,'--remove'],env=env,check=True);assert not nemo.exists() and caja.read_text()=='custom launcher';print('PASS launcher install, execute, remove and custom script preservation')
