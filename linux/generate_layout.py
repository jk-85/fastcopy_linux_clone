#!/usr/bin/env python3
"""Read the unmodified English Win32 resources. Generate portable dialog/menu data."""
import json,re,sys
from pathlib import Path
root=Path(__file__).resolve().parent.parent
source=(root/'src/fastcopy.rc').read_text(encoding='utf-16')
source=source.split('LANGUAGE LANG_ENGLISH, SUBLANG_ENGLISH_US',1)[1]
# RC strings escape quotes by doubling, paths may contain escaped backslashes.
TOKEN=re.compile(r'"(?:""|[^"\n])*"|[^,]+')
def fields(s):
    out=[];i=0;start=0;quoted=False
    while i<len(s):
        c=s[i]
        if c=='"':
            if quoted and i+1<len(s) and s[i+1]=='"':i+=2;continue
            quoted=not quoted
        elif c==',' and not quoted:out.append(s[start:i].strip());start=i+1
        i+=1
    out.append(s[start:].strip());return out

def unquote(s):
    if s.startswith('"') and s.endswith('"'):s=s[1:-1]
    return s.replace('""','"').replace(r'\r','\r').replace(r'\n','\n').replace('\\\\','\\')

kinds='DEFPUSHBUTTON|PUSHBUTTON|EDITTEXT|COMBOBOX|CONTROL|LTEXT|CTEXT|RTEXT|GROUPBOX|LISTBOX|ICON'
pat=re.compile(r'^\s*('+kinds+r')\s+(.+)$',re.M)
blocks={}
for m in re.finditer(r'^(\w+) DIALOGEX (\d+), (\d+), (\d+), (\d+)\n(.*?)^BEGIN\n(.*?)^END\s*$',source,re.M|re.S):
    name,x,y,w,h,header,body=m.groups()
    controls=[]
    # Each control starts on a new line; styles sometimes continue on the next line.
    matches=list(pat.finditer(body))
    for i,p in enumerate(matches):
        kind=p.group(1);a=fields(body[p.start(2):matches[i+1].start() if i+1<len(matches) else len(body)].replace('\n',' ').strip())
        text='';cls='';style=''
        if kind=='CONTROL':text,ident,cls,style=a[:4];coords=a[4:8];style+=' '+','.join(a[8:])
        elif kind in ('EDITTEXT','COMBOBOX','LISTBOX'):ident=a[0];coords=a[1:5];style=','.join(a[5:])
        else:text,ident=a[:2];coords=a[2:6];style=','.join(a[6:])
        if len(coords)!=4:raise ValueError((name,p.group(),a))
        controls.append(dict(kind=kind,id=ident,text=unquote(text),cls=unquote(cls),style=style,rect=list(map(int,coords))))
    caption=re.search(r'^CAPTION (.*)$',header,re.M);font=re.search(r'^FONT (\d+)',header,re.M)
    blocks[name]={'size':[int(w),int(h)],'caption':unquote(caption.group(1)) if caption else '', 'font':int(font.group(1)), 'controls':controls}
menus=[];stack=[menus];pending=None
body=source.split('MAIN_MENU MENU',1)[1].split('// String Table',1)[0]
for line in body.splitlines():
    line=line.strip()
    if line.startswith('POPUP '):
        pending={'label':unquote(line[6:]),'children':[]};stack[-1].append(pending)
    elif line=='BEGIN':
        if pending is not None:stack.append(pending['children']);pending=None
    elif line=='END':
        if len(stack)>1:stack.pop()
        else:break
    elif line=='MENUITEM SEPARATOR':stack[-1].append({'separator':True})
    elif line.startswith('MENUITEM '):
        label,ident=fields(line[9:])[:2];stack[-1].append({'label':unquote(label),'id':ident})
strings={}
for name,s in re.findall(r'^\s*(IDS_\w+)\s+("(?:""|[^"\n])*")\s*$',source,re.M):strings[name]=unquote(s)
assert len(blocks)==20,len(blocks)
assert len(menus)==4,len(menus)
output=Path(sys.argv[1]) if len(sys.argv)>1 else root/'linux/rc_layout.json'
output.write_text(json.dumps({'dialogs':blocks,'menus':menus,'strings':strings},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(f'{len(blocks)} dialogs, {sum(len(b["controls"]) for b in blocks.values())} controls, {len(menus)} menus')
