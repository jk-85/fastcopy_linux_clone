#!/usr/bin/python3
"""Open local Nemo/Caja selections without a shell or automatic transfer."""
import os
import sys
from urllib.parse import urlsplit, unquote_to_bytes

def selected():
    for key in ('NEMO_SCRIPT_SELECTED_URIS','CAJA_SCRIPT_SELECTED_URIS'):
        if os.environ.get(key):
            paths=[]
            for uri in os.environ[key].splitlines():
                parsed=urlsplit(uri)
                if parsed.scheme!='file' or parsed.netloc not in ('','localhost'):
                    raise ValueError('Only local paths are supported. Mount remote storage as a local directory first.')
                paths.append(os.fsdecode(unquote_to_bytes(parsed.path)))
            return paths
    if len(sys.argv)>1:
        return [os.path.abspath(p) for p in sys.argv[1:]]
    for key in ('NEMO_SCRIPT_SELECTED_FILE_PATHS','CAJA_SCRIPT_SELECTED_FILE_PATHS'):
        if os.environ.get(key):
            return [p for p in os.environ[key].split('\n') if p]
    return []

if __name__=='__main__':
    try:
        paths=selected()
        if any(not os.path.lexists(p) for p in paths):raise ValueError('Could not resolve the file manager selection.')
        os.execvp('fastcopy',['fastcopy','--']+paths)
    except (ValueError,OSError) as ex:
        import tkinter as tk
        from tkinter import messagebox
        root=tk.Tk();root.withdraw();messagebox.showerror('FastCopy Mint',str(ex));root.destroy();sys.exit(1)
