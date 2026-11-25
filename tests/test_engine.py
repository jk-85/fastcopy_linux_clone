#!/usr/bin/env python3
"""Run only against temporary files. Python standard library; no special privileges."""
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ENGINE = Path(sys.argv.pop(1)).resolve()
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='fastcopy-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.src=self.root/'source';self.dst=self.root/'target';self.src.mkdir()
    def run_engine(self,*opts,srcs=None,expected=0,dest=None):
        args=[str(ENGINE),'--machine','--dest',(str(self.dst)+'/' if dest is None else (str(dest)+'/' if isinstance(dest,Path) else dest)),*opts,'--',*(str(p) for p in (srcs if srcs is not None else [str(self.src)+'/']))]
        p=subprocess.run(args,capture_output=True,text=True,timeout=30)
        self.assertEqual(p.returncode,expected,p.stdout+'\n'+p.stderr)
        return p.stdout
    def test_copy_unicode_tree_metadata_symlink(self):
        sub=self.src/'Grüße 日本語';sub.mkdir();f=sub/'file name.txt';f.write_bytes(os.urandom(1_000_000));os.chmod(f,0o640);os.utime(f,ns=(1_650_000_000_000_000_000,1_660_000_000_000_000_123));(self.src/'empty').mkdir();(self.src/'broken link').symlink_to('nowhere')
        self.run_engine('--workers','4','--buffer-mib','1');g=self.dst/sub.name/f.name
        self.assertEqual(f.read_bytes(),g.read_bytes());self.assertEqual(g.stat().st_mtime_ns,1_660_000_000_000_000_123);self.assertEqual(stat.S_IMODE(g.stat().st_mode),0o640);self.assertEqual(os.readlink(self.dst/'broken link'),'nowhere');self.assertTrue((self.dst/'empty').is_dir())
    def test_default_folder_copy(self):
        (self.src/'a').write_text('a');self.run_engine(srcs=[self.src]);self.assertEqual((self.dst/'source'/'a').read_text(),'a')
    def test_destination_slash_create_and_preview(self):
        (self.src/'a').write_text('a');sub=self.src/'sub';sub.mkdir();(sub/'b').write_text('b')
        target=self.root/'new'/'destination';text=str(target)+'/'
        out=self.run_engine('--dry-run',srcs=[self.src],dest=text)
        self.assertFalse(target.parent.exists());self.assertIn(('Would create: '+str(target)).encode().hex(),out)
        self.run_engine(srcs=[self.src],dest=text)
        self.assertEqual((target/'source'/'a').read_text(),'a');self.assertEqual((target/'source'/'sub'/'b').read_text(),'b')
        contents=self.root/'contents';self.run_engine(dest=str(contents)+'/')
        self.assertEqual((contents/'a').read_text(),'a');self.assertFalse((contents/'source').exists())
    def test_destination_no_slash_existing_only(self):
        (self.src/'a').write_text('a');missing=self.root/'missing'
        out=self.run_engine(srcs=[self.src],dest=str(missing),expected=1)
        self.assertTrue(any("add a trailing '/'" in bytes.fromhex(line.split('\t',1)[1]).decode() for line in out.splitlines() if line.startswith('E\t')))
        self.run_engine('--dry-run',srcs=[self.src],dest=str(missing),expected=1)
        self.assertFalse(missing.exists());self.dst.mkdir();(self.dst/'unrelated').write_text('keep')
        self.run_engine(srcs=[self.src],dest=str(self.dst));self.assertEqual((self.dst/'a').read_text(),'a');self.assertFalse((self.dst/'source').exists());self.assertEqual((self.dst/'unrelated').read_text(),'keep')
        regular_file=self.root/'file';regular_file.write_text('safe');self.run_engine(srcs=[self.src],dest=str(regular_file)+'/',expected=1);self.assertEqual(regular_file.read_text(),'safe')
    def test_destination_multi_source_and_sync(self):
        (self.src/'a').write_text('a');other=self.root/'other';other.mkdir();(other/'b').write_text('b')
        for suffix in ('','/'):
            d=self.root/('multi-existing' if not suffix else 'multi-new')
            if not suffix:d.mkdir()
            self.run_engine(srcs=[self.src,other],dest=str(d)+suffix)
            self.assertEqual((d/'source'/'a').read_text(),'a');self.assertEqual((d/'other'/'b').read_text(),'b')
        self.dst.mkdir();(self.dst/'extra').write_text('old')
        self.run_engine('--mode','sync','--dry-run',srcs=[self.src],dest=str(self.dst)+'/')
        self.assertTrue((self.dst/'extra').exists());self.assertFalse((self.dst/'a').exists())
        self.run_engine('--mode','sync','--allow-delete',srcs=[self.src],dest=str(self.dst)+'/')
        self.assertEqual(sorted(x.name for x in self.dst.iterdir()),['a'])
    def test_diff_no_overwrite_newer_overwrite(self):
        f=self.src/'a';f.write_text('original');self.run_engine();g=self.dst/'a';self.assertEqual(g.read_text(),'original');out=self.run_engine();self.assertEqual(out.strip().splitlines()[-1].split('\t')[5:9],['0','1','0','0'])
        f.write_text('updated content');os.utime(f,ns=(time.time_ns(),g.stat().st_mtime_ns-10_000_000_000));self.run_engine('--mode','newer');self.assertEqual(g.read_text(),'original');self.run_engine('--mode','no-overwrite');self.assertEqual(g.read_text(),'original');self.run_engine('--mode','diff');self.assertEqual(g.read_text(),'updated content');f.write_text('overwrite');self.run_engine('--mode','overwrite');self.assertEqual(g.read_text(),'overwrite')
    def test_preview_no_writes(self):
        (self.src/'a').write_text('a');self.run_engine('--dry-run');self.assertFalse(self.dst.exists())
    def test_sync_preview_and_confirmed_prune(self):
        (self.src/'keep').write_text('keep');self.dst.mkdir();(self.dst/'extra').mkdir();(self.dst/'extra'/'x').write_text('x');outside=self.root/'outside';outside.mkdir();(outside/'protected').write_text('safe');(self.dst/'extrasymlink').symlink_to(outside,target_is_directory=True)
        self.run_engine('--mode','sync',expected=1);self.run_engine('--mode','sync','--dry-run');self.assertTrue((self.dst/'extra'/'x').exists());self.assertFalse((self.dst/'keep').exists());self.run_engine('--mode','sync','--allow-delete');self.assertEqual(sorted(x.name for x in self.dst.iterdir()),['keep']);self.assertEqual((outside/'protected').read_text(),'safe')
    def test_move_verified(self):
        (self.src/'folder').mkdir();f=self.src/'folder'/'a';f.write_bytes(os.urandom(300_000));os.link(f,self.src/'hard alias');content=f.read_bytes();self.run_engine('--mode','move',expected=1);self.assertTrue(f.exists());self.run_engine('--mode','move','--allow-delete','--no-verify',expected=1);self.run_engine('--mode','move','--allow-delete',srcs=[self.src]);self.assertFalse(self.src.exists());self.assertEqual((self.dst/'source'/'folder'/'a').read_bytes(),content);self.assertEqual((self.dst/'source'/'hard alias').read_bytes(),content)
    def test_conflict_prevents_prune(self):
        (self.src/'a').write_text('x');self.dst.mkdir();(self.dst/'a').mkdir();(self.dst/'extra').write_text('safe');self.run_engine('--mode','sync','--allow-delete',expected=1);self.assertTrue((self.dst/'extra').exists())
    def test_special_file_prevents_move(self):
        (self.src/'good').write_text('safe');os.mkfifo(self.src/'fifo');self.run_engine('--mode','move','--allow-delete',expected=1);self.assertTrue((self.src/'good').exists());self.assertFalse(self.dst.exists())
    def test_reject_overlap_and_symlink_dest(self):
        (self.src/'a').write_text('a');self.run_engine(dest=self.src/'nested',expected=1);self.assertFalse((self.src/'nested').exists());self.dst.mkdir();other=self.root/'other';other.mkdir();(self.dst/'a').symlink_to(other/'outside');self.run_engine(expected=1);self.assertFalse((other/'outside').exists())
    def test_filter_and_long_names(self):
        (self.src/'ignore.tmp').write_text('no');d=self.src
        for _ in range(5):d=d/('a'*90);d.mkdir()
        f=d/'-name\nwith line';f.write_text('ok');self.run_engine('--exclude','*.tmp');self.assertFalse((self.dst/'ignore.tmp').exists());self.assertEqual((self.dst/f.relative_to(self.src)).read_text(),'ok')
    def test_include_exclude_preview_and_no_matches(self):
        (self.src/'keep').mkdir();(self.src/'empty').mkdir();(self.src/'blocked').mkdir()
        (self.src/'keep'/'a.txt').write_text('a');(self.src/'keep'/'skip.txt').write_text('skip')
        (self.src/'keep'/'b.jpg').write_text('b');(self.src/'blocked'/'c.txt').write_text('c')
        (self.src/'README.md').write_text('readme');(self.src/'other.bin').write_text('other')
        opts=['--include','*.txt','--include','README.md','--exclude','skip*','--exclude','blocked']
        out=self.run_engine(*opts,'--dry-run');self.assertFalse(self.dst.exists())
        self.assertIn('a.txt'.encode().hex(),out);self.assertNotIn('skip.txt'.encode().hex(),out)
        self.run_engine(*opts);self.assertEqual(sorted(str(x.relative_to(self.dst)) for x in self.dst.rglob('*')),['README.md','keep','keep/a.txt'])
        never=self.root/'no-matches';self.run_engine('--include','*.xyz',dest=never);self.assertFalse(never.exists())
        self.run_engine('--mode','move','--allow-delete','--include','*.txt',expected=1)
        self.run_engine('--mode','sync','--allow-delete','--include','*.txt',expected=1)
        self.run_engine('--mode','sync','--dry-run','--include','*.txt',expected=1)
        self.assertTrue((self.src/'keep'/'a.txt').exists());self.assertTrue((self.dst/'README.md').exists())
    def test_speed_duty_cycle(self):
        f=self.src/'large'
        with f.open('wb') as o:o.truncate(96*1024*1024)
        elapsed=[]
        for rate in (100,10):
            start=time.monotonic();d=self.root/f'rate-{rate}'
            self.run_engine('--speed-percent',str(rate),'--workers','1','--buffer-mib','1',dest=d)
            elapsed.append(time.monotonic()-start);self.assertEqual((d/'large').stat().st_size,f.stat().st_size)
        print(f'  measured verified sparse copy: full={elapsed[0]:.3f}s; 10%={elapsed[1]:.3f}s',flush=True)
        self.assertGreater(elapsed[1],elapsed[0]*1.5,'10% setting must actually slow the transfer')
        self.run_engine('--speed-percent','35',expected=1)
    def test_live_speed_pipe_suspend_resume(self):
        (self.src/'file').write_bytes(b'a'*1024*1024)
        args=[str(ENGINE),'--machine','--dest',str(self.dst)+'/', '--speed-percent','0','--live-speed','--',str(self.src)+'/']
        p=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        try:
            self.assertTrue(p.stdout.readline().startswith(b'P\t'));time.sleep(.10)
            self.assertIsNone(p.poll());self.assertFalse(self.dst.exists())
            p.stdin.write(bytes([3,7,4,10]));p.stdin.flush()
            self.assertEqual(p.wait(timeout=10),0,p.stdout.read());self.assertEqual((self.dst/'file').read_bytes(),(self.src/'file').read_bytes())
        finally:
            if p.poll() is None:p.kill();p.wait()
            p.stdin.close();p.stdout.close()
    def test_live_speed_pipe_disconnect_cancels(self):
        (self.src/'file').write_text('safe')
        p=subprocess.Popen([str(ENGINE),'--machine','--dest',str(self.dst)+'/', '--speed-percent','0','--live-speed','--',str(self.src)+'/'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        try:
            self.assertTrue(p.stdout.readline().startswith(b'P\t'));p.stdin.close()
            self.assertEqual(p.wait(timeout=10),130,p.stdout.read());self.assertFalse(self.dst.exists())
        finally:
            if p.poll() is None:p.kill();p.wait()
            p.stdout.close()
    def test_sparse_and_zero_size(self):
        f=self.src/'sparse';f.touch();f.open('r+b').close()
        with f.open('r+b') as o:o.seek(16*1024*1024);o.write(b'z')
        (self.src/'empty').touch();self.run_engine('--buffer-mib','1');g=self.dst/'sparse';self.assertEqual(g.stat().st_size,f.stat().st_size);self.assertEqual(g.read_bytes(),f.read_bytes());self.assertEqual((self.dst/'empty').stat().st_size,0)
    def test_xattr(self):
        f=self.src/'a';f.write_text('a')
        try:os.setxattr(f,'user.fastcopy-test',b'abc\0xyz')
        except OSError as e:self.skipTest(str(e))
        self.run_engine('--xattrs');self.assertEqual(os.getxattr(self.dst/'a','user.fastcopy-test'),b'abc\0xyz')
    def test_multiple_sources_and_collision(self):
        (self.src/'a').write_text('one');other=self.root/'other';other.mkdir();(other/'b').write_text('two');self.run_engine(srcs=[self.src/'a',other/'b']);self.assertEqual((self.dst/'a').read_text(),'one');self.assertEqual((self.dst/'b').read_text(),'two');(other/'a').write_text('collision');self.run_engine(srcs=[self.src/'a',other/'a'],expected=1);self.assertEqual((self.dst/'a').read_text(),'one')
    def test_cancel_pause_leaves_original(self):
        f=self.src/'large';self.dst.mkdir();old=self.dst/'large';old.write_text('old')
        # Large sparse input gives time to send signals without consuming disk.
        with f.open('wb') as o:o.truncate(1024*1024*1024)
        args=[str(ENGINE),'--machine','--dest',str(self.dst),'--mode','overwrite','--buffer-mib','1','--',str(f)]
        p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        try:
            line=p.stdout.readline();self.assertTrue(line.startswith('P\t'),line)
            p.send_signal(signal.SIGUSR1);time.sleep(.08);self.assertIsNone(p.poll());p.send_signal(signal.SIGUSR2);time.sleep(.03);p.send_signal(signal.SIGINT);out,_=p.communicate(timeout=15);self.assertEqual(p.returncode,130,out);self.assertEqual(old.read_text(),'old');self.assertFalse(list(self.dst.glob('.fastcopy-mint-*')))
        finally:
            if p.poll() is None:p.kill();p.wait()
    @unittest.skipIf(os.geteuid()==0,'requires a regular user')
    def test_permission_error_prevents_move_cleanup(self):
        f=self.src/'good';f.write_text('safe');self.dst.mkdir();self.dst.chmod(0o500)
        try:self.run_engine('--mode','move','--allow-delete',expected=1);self.assertEqual(f.read_text(),'safe')
        finally:self.dst.chmod(0o700)
    def test_no_metadata_respects_umask(self):
        f=self.src/'a';f.write_text('a');os.chmod(f,0o777);self.run_engine('--no-metadata');self.assertNotEqual(stat.S_IMODE((self.dst/'a').stat().st_mode),0o777)

if __name__=='__main__':unittest.main(verbosity=2)
