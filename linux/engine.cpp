// FastCopy Mint Linux implementation. GPL-3.0-only. Uses upstream xxHash 0.6.5.
#include "xxhash.h"
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <fnmatch.h>
#include <iostream>
#include <map>
#include <mutex>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/xattr.h>
#include <thread>
#include <unistd.h>
#include <vector>
namespace fs = std::filesystem;
using namespace std::chrono_literals;
using U = unsigned long long;
struct Failure : std::runtime_error {
  using runtime_error::runtime_error;
};
struct Cancel : Failure {
  Cancel() : Failure("cancelled") {}
};
static std::atomic<bool> cancelled{false}, paused{false};
static std::atomic<unsigned> speed_percent{100};
static bool live_speed = false;
static std::mutex control_mutex;
using Clock = std::chrono::steady_clock;
// Optional private control pipe from the GUI. Each byte is a level 0..10.
// Nonblocking, ordered updates avoid signal races on startup and rapid dragging.
void control_tick() {
  if (!live_speed) return;
  thread_local Clock::time_point last{};
  auto now = Clock::now();
  if (now - last < 10ms) return;
  last = now;
  std::unique_lock<std::mutex> lock(control_mutex, std::try_to_lock);
  if (!lock.owns_lock()) return;
  unsigned char levels[256];
  auto n = read(STDIN_FILENO, levels, sizeof(levels));
  if (n == 0) cancelled.store(true); // GUI closed its end of the pipe.
  else if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
    cancelled.store(true);
  for (ssize_t i = 0; i < n; ++i)
    if (levels[i] <= 10) speed_percent.store(levels[i] * 10);
}
static_assert(std::atomic<bool>::is_always_lock_free);
static_assert(std::atomic<unsigned>::is_always_lock_free);
void sig(int s) {
  if (s == SIGUSR1)
    paused.store(true);
  else if (s == SIGUSR2) {
    paused.store(false);
    if (speed_percent.load() == 0) speed_percent.store(100);
  } else
    cancelled.store(true);
}
void checkpoint() {
  control_tick();
  while ((paused.load() || speed_percent.load() == 0) && !cancelled.load()) {
    std::this_thread::sleep_for(20ms);
    control_tick();
  }
  if (cancelled.load())
    throw Cancel();
}
// Percentages are a duty cycle relative to time spent doing actual I/O/hash
// work, not a fixed bandwidth guarantee. Re-evaluate on speed changes.
void pace(Clock::time_point start) {
  auto work = Clock::now() - start;
  for (;;) {
    checkpoint();
    auto rate = speed_percent.load();
    if (rate == 0) continue;
    if (rate == 100) return;
    auto left = work * 100 / rate - (Clock::now() - start);
    if (left <= Clock::duration::zero()) return;
    std::this_thread::sleep_for(std::min(left, std::chrono::duration_cast<Clock::duration>(20ms)));
  }
}
size_t chunk_size(size_t buffer_size) {
  // Shorter chunks bound responsiveness while the interactive limiter is on.
  return speed_percent.load() == 100 ? buffer_size : std::min<size_t>(buffer_size, 1024 * 1024);
}
[[noreturn]] void fail(const std::string &s) {
  throw Failure(s + ": " + std::strerror(errno));
}
std::string hex(const std::string &s) {
  static const char h[] = "0123456789abcdef";
  std::string o;
  for (unsigned char c : s) {
    o += h[c >> 4];
    o += h[c & 15];
  }
  return o;
}
std::mutex out_mutex;
struct Opt {
  std::string mode = "diff";
  fs::path dest;
  bool dest_has_slash = false;
  std::vector<std::string> sources, include, exclude;
  unsigned workers = 0, buffer = 4;
  bool verify = true, dry = false, allow_delete = false, machine = false,
       metadata = true, xattrs = false;
};
Opt opt;
mode_t process_umask = 022;
std::atomic<U> bytes{0}, done{0}, copied{0}, skipped{0}, errors{0}, deleted{0};
U total_bytes = 0, total_files = 0;
std::chrono::steady_clock::time_point last_progress{};
void message(const char *kind, const std::string &text) {
  std::lock_guard<std::mutex> lock(out_mutex);
  if (opt.machine)
    std::cout << kind << '\t' << hex(text) << '\n';
  else
    std::cout << kind << ": " << text << '\n';
  std::cout.flush();
}
void progress(bool force = false) {
  std::lock_guard<std::mutex> lock(out_mutex);
  auto now = std::chrono::steady_clock::now();
  if (!force && now - last_progress < 200ms)
    return;
  last_progress = now;
  if (opt.machine)
    std::cout << "P\t" << bytes.load() << '\t' << total_bytes << '\t'
              << done.load() << '\t' << total_files << '\t' << copied.load()
              << '\t' << skipped.load() << '\t' << errors.load() << '\t'
              << deleted.load() << '\n';
  else
    std::cout << "Progress: " << bytes.load() << "/" << total_bytes
              << " bytes; " << done.load() << "/" << total_files
              << " entries; copied=" << copied.load()
              << " skipped=" << skipped.load() << " errors=" << errors.load()
              << " removed=" << deleted.load() << '\n';
  std::cout.flush();
}
void report(const std::string &s) {
  ++errors;
  message("E", s);
}
struct Stat {
  struct stat s{};
  bool exists = false;
};
Stat statpath(const fs::path &p) {
  Stat r;
  if (lstat(p.c_str(), &r.s) == 0) {
    r.exists = true;
    return r;
  }
  if (errno == ENOENT)
    return r;
  fail("lstat " + p.string());
}
Stat fdstat(int fd) {
  Stat r;
  if (fstat(fd, &r.s))
    fail("fstat");
  r.exists = true;
  return r;
}
bool timeeq(timespec a, timespec b) {
  return a.tv_sec == b.tv_sec && a.tv_nsec == b.tv_nsec;
}
bool newer(timespec a, timespec b) {
  return a.tv_sec > b.tv_sec || (a.tv_sec == b.tv_sec && a.tv_nsec > b.tv_nsec);
}
bool same_inode(const Stat &a, const Stat &b) {
  return a.exists && b.exists && a.s.st_dev == b.s.st_dev &&
         a.s.st_ino == b.s.st_ino;
}
bool unchanged(const Stat &a, const Stat &b) {
  return a.exists == b.exists &&
         (!a.exists ||
          (same_inode(a, b) && a.s.st_mode == b.s.st_mode &&
           a.s.st_size == b.s.st_size && timeeq(a.s.st_mtim, b.s.st_mtim) &&
           timeeq(a.s.st_ctim, b.s.st_ctim)));
}
bool dir(const Stat &s) { return s.exists && S_ISDIR(s.s.st_mode); }
bool regular(const Stat &s) { return s.exists && S_ISREG(s.s.st_mode); }
bool symlink(const Stat &s) { return s.exists && S_ISLNK(s.s.st_mode); }
fs::path trim(fs::path p) {
  while (p != p.root_path() && p.filename().empty())
    p = p.parent_path();
  return p;
}
fs::path resolve_leaf(const fs::path &p) {
  auto a = trim(fs::absolute(p));
  if (a == a.root_path())
    return a;
  if (a.filename() == "." || a.filename() == "..")
    return fs::weakly_canonical(a);
  return fs::weakly_canonical(a.parent_path()) / a.filename();
}
bool within(const fs::path &a, const fs::path &b) {
  auto i = a.begin(), j = b.begin();
  for (; j != b.end(); ++i, ++j)
    if (i == a.end() || *i != *j)
      return false;
  return true;
}
void safe_parents(const fs::path &p) {
  fs::path cur;
  for (auto &part : p) {
    cur /= part;
    auto s = statpath(cur);
    if (s.exists && !dir(s))
      throw Failure("not a real directory: " + cur.string());
  }
}
void mkdirs(const fs::path &p) {
  safe_parents(p);
  fs::create_directories(p);
  safe_parents(p);
}
struct FD {
  int n;
  explicit FD(int f) : n(f) {
    if (n < 0)
      fail("open");
  }
  ~FD() {
    if (n >= 0)
      close(n);
  }
  FD(const FD &) = delete;
  FD &operator=(const FD &) = delete;
};
struct Temp {
  fs::path path;
  bool active = true;
  int fd = -1;
  explicit Temp(const fs::path &parent) {
    auto str = (parent / ".fastcopy-mint-XXXXXX").string();
    std::vector<char> b(str.begin(), str.end());
    b.push_back(0);
    fd = mkstemp(b.data());
    if (fd < 0)
      fail("create temporary file");
    path = b.data();
  }
  ~Temp() {
    if (fd >= 0)
      close(fd);
    if (active)
      unlink(path.c_str());
  }
};
struct Hash {
  XXH64_state_t *s = XXH64_createState();
  Hash() {
    if (!s)
      throw Failure("xxHash allocation failed");
    XXH64_reset(s, 0);
  }
  ~Hash() { XXH64_freeState(s); }
  void add(const void *p, size_t n) {
    if (XXH64_update(s, p, n) != XXH_OK)
      throw Failure("xxHash update failed");
  }
  U get() { return XXH64_digest(s); }
};
ssize_t read_retry(int fd, void *p, size_t n) {
  for (;;) {
    checkpoint();
    auto r = read(fd, p, n);
    if (r < 0 && errno == EINTR)
      continue;
    if (r < 0)
      fail("read");
    return r;
  }
}
void write_all(int fd, const char *p, size_t n) {
  while (n) {
    checkpoint();
    auto r = write(fd, p, n);
    if (r < 0 && errno == EINTR)
      continue;
    if (r <= 0)
      fail("write");
    p += r;
    n -= r;
  }
}
U digest_fd(int fd, std::vector<char> &buf) {
  if (lseek(fd, 0, SEEK_SET) < 0)
    fail("seek for verify");
  Hash h;
  for (;;) {
    checkpoint();
    auto start = Clock::now();
    auto n = read_retry(fd, buf.data(), chunk_size(buf.size()));
    if (!n)
      break;
    h.add(buf.data(), n);
    pace(start);
  }
  return h.get();
}
void copy_xattrs(int from, int to) {
  if (!opt.xattrs)
    return;
  auto n = flistxattr(from, nullptr, 0);
  if (n < 0)
    fail("list xattrs");
  if (n == 0)
    return;
  std::vector<char> names(n);
  n = flistxattr(from, names.data(), names.size());
  if (n < 0)
    fail("list xattrs");
  for (ssize_t i = 0; i < n;) {
    std::string name = names.data() + i;
    i += name.size() + 1;
    if (name.rfind("user.", 0) != 0 && name != "system.posix_acl_access" &&
        name != "system.posix_acl_default")
      continue;
    auto len = fgetxattr(from, name.c_str(), nullptr, 0);
    if (len < 0)
      fail("get xattr " + name);
    std::vector<char> value(len);
    len = fgetxattr(from, name.c_str(), value.data(), value.size());
    if (len < 0)
      fail("get xattr " + name);
    if (fsetxattr(to, name.c_str(), value.data(), len, 0))
      fail("set xattr " + name);
  }
}
void set_meta(int fd, const Stat &s) {
  if (!opt.metadata) {
    if (fchmod(fd, (S_ISDIR(s.s.st_mode) ? 0777 : 0666) & ~process_umask))
      fail("chmod default");
    return;
  } // Deliberately do not copy ownership or setuid/setgid bits.
  if (fchmod(fd, s.s.st_mode & 01777))
    fail("chmod");
  timespec ts[] = {s.s.st_atim, s.s.st_mtim};
  if (futimens(fd, ts))
    fail("set timestamps");
}
void sync_dir(const fs::path &p) {
  FD d(open(p.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
  if (fsync(d.n) && errno != EINVAL && errno != ENOTSUP && errno != EROFS)
    fail("sync directory");
}
void commit(Temp &t, const fs::path &target, const Stat &old) {
  checkpoint();
  safe_parents(target.parent_path());
  if (!unchanged(old, statpath(target)))
    throw Failure("destination changed during copy: " + target.string());
  if (old.exists) {
    if (rename(t.path.c_str(), target.c_str()))
      fail("replace " + target.string());
  } else {
#ifdef SYS_renameat2
    if (syscall(SYS_renameat2, AT_FDCWD, t.path.c_str(), AT_FDCWD,
                target.c_str(), 1) == 0) {
      t.active = false;
      return;
    }
    if (errno != ENOSYS && errno != EINVAL && errno != EOPNOTSUPP)
      fail("publish " + target.string());
#endif
    if (link(t.path.c_str(), target.c_str()))
      fail("publish " + target.string());
    if (unlink(t.path.c_str()))
      fail("remove temporary name");
  }
  t.active = false;
}
struct Item {
  fs::path src, rel;
  Stat s;
  bool root_contents = false;
  bool needs_copy = false;
  bool success = false;
  bool made_dir = false;
  Stat published;
  Stat planned_dst;
};
std::vector<Item> items;
std::map<fs::path, size_t> index_by_rel;
bool matches(const fs::path &rel, const std::vector<std::string> &patterns) {
  for (const auto &pat : patterns) {
    if (fnmatch(pat.c_str(), rel.c_str(), 0) == 0 ||
        fnmatch(pat.c_str(), rel.filename().c_str(), 0) == 0)
      return true;
  }
  return false;
}
void scan(const fs::path &src, const fs::path &rel,
          bool root_contents = false) {
  checkpoint();
  if (!root_contents && matches(rel, opt.exclude))
    return;
  auto s = statpath(src);
  if (!s.exists)
    throw Failure("source vanished: " + src.string());
  if (!dir(s) && !opt.include.empty() && !matches(rel, opt.include))
    return;
  if (!dir(s) && !regular(s) && !symlink(s))
    throw Failure("unsupported special file: " + src.string());
  size_t first = items.size();
  if (!index_by_rel.emplace(rel, first).second)
    throw Failure("multiple sources map to: " + rel.string());
  items.push_back({src, rel, s, root_contents, false, false, false, {}, {}});
  if (dir(s)) {
    for (const auto &e : fs::directory_iterator(src))
      scan(e.path(), rel / e.path().filename());
    // With include filters, keep only ancestor directories of matching files.
    if (!opt.include.empty() && items.size() == first + 1) {
      index_by_rel.erase(rel);
      items.pop_back();
    }
  }
}
fs::path target(const Item &i) {
  return i.rel.empty() ? opt.dest : opt.dest / i.rel;
}
void plan() {
  if (opt.sources.empty())
    throw Failure("at least one source required");
  if (opt.dest.empty())
    throw Failure("destination directory required");
  // Retain the user's trailing slash before normalizing the path.
  opt.dest_has_slash = opt.dest.native().back() == '/';
  opt.dest = resolve_leaf(opt.dest);
  if (opt.dest == opt.dest.root_path())
    throw Failure("filesystem root is not an allowed destination");
  safe_parents(opt.dest);
  if (!opt.dest_has_slash && !dir(statpath(opt.dest)))
    throw Failure("destination directory does not exist: " + opt.dest.string() +
                  "; add a trailing '/' to create it, or choose an existing directory");
  if (opt.mode == "sync") {
    const char *home = getenv("HOME");
    const std::vector<fs::path> protected_dirs = {
        "/home",  "/root", "/etc", "/usr",  "/var", "/bin", "/sbin", "/lib",
        "/lib64", "/boot", "/dev", "/proc", "/sys", "/run", "/opt",  "/tmp"};
    if (std::find(protected_dirs.begin(), protected_dirs.end(), opt.dest) !=
            protected_dirs.end() ||
        (home && opt.dest == resolve_leaf(home)))
      throw Failure("refusing to synchronize into a system or home directory "
                    "root; use a dedicated destination folder");
  }
  if (opt.mode == "sync" && (opt.sources.size() != 1 || !opt.exclude.empty() || !opt.include.empty()))
    throw Failure("sync requires one directory source, without filters");
  std::vector<fs::path> roots;
  for (const auto &raw : opt.sources) {
    if (raw.empty())
      throw Failure("empty source");
    fs::path src = resolve_leaf(raw);
    auto s = statpath(src);
    if (!s.exists)
      throw Failure("missing source: " + src.string());
    if (src == src.root_path())
      throw Failure("filesystem root is not an allowed source");
    if ((dir(s) && (within(opt.dest, src) || within(src, opt.dest))) ||
        within(opt.dest, src) || src == opt.dest)
      throw Failure("source and destination overlap: " + src.string());
    for (const auto &r : roots)
      if (within(src, r) || within(r, src))
        throw Failure("overlapping sources");
    roots.push_back(src);
    bool contents = raw.back() == '/';
    if (contents && !dir(s))
      throw Failure("trailing slash requires a directory: " + src.string());
    if (opt.mode == "sync" && !dir(s))
      throw Failure("sync source must be a directory");
    // Original FastCopy DestDir convention: without a final slash, a
    // single folder's contents go directly into the selected destination.
    // Multiple folders retain their names; an explicit source slash still
    // selects contents. Sync always mirrors contents into the exact target.
    if (opt.mode == "sync" ||
        (dir(s) && !opt.dest_has_slash && opt.sources.size() == 1))
      contents = true;
    scan(src, contents ? fs::path{} : src.filename(), contents);
  }
  for (auto &i : items) {
    checkpoint();
    auto dst = target(i);
    safe_parents(dst.parent_path());
    auto d = statpath(dst);
    i.planned_dst = d;
    if (dir(i.s)) {
      if (d.exists && !dir(d))
        throw Failure("directory conflicts with destination: " + dst.string());
      continue;
    }
    ++total_files;
    if (same_inode(i.s, d))
      throw Failure("source and destination refer to same inode: " +
                    dst.string());
    if (d.exists &&
        ((regular(i.s) && !regular(d)) || (symlink(i.s) && !symlink(d))))
      throw Failure("destination type conflict: " + dst.string());
    if (!d.exists)
      i.needs_copy = true;
    else if (opt.mode == "no-overwrite")
      i.needs_copy = false;
    else if (opt.mode == "overwrite" || opt.mode == "move")
      i.needs_copy = true;
    else if (opt.mode == "newer")
      i.needs_copy = newer(i.s.s.st_mtim, d.s.st_mtim);
    else if (symlink(i.s))
      i.needs_copy = fs::read_symlink(i.src) != fs::read_symlink(dst);
    else
      i.needs_copy =
          i.s.s.st_size != d.s.st_size || !timeeq(i.s.s.st_mtim, d.s.st_mtim);
    if (i.needs_copy && regular(i.s))
      total_bytes += i.s.s.st_size;
  }
}
void copy_one(Item &i, std::vector<char> &buf) {
  checkpoint();
  auto dst = target(i);
  safe_parents(dst.parent_path());
  auto old = statpath(dst);
  if (!unchanged(i.planned_dst, old))
    throw Failure("destination changed since scan: " + dst.string());
  if (!unchanged(i.s, statpath(i.src)))
    throw Failure("source changed since scan: " + i.src.string());
  if (same_inode(i.s, old))
    throw Failure("same source/destination inode");
  if (old.exists &&
      ((regular(i.s) && !regular(old)) || (symlink(i.s) && !symlink(old))))
    throw Failure("destination type changed");
  if (opt.mode == "no-overwrite" && old.exists) {
    ++skipped;
    return;
  }
  Temp t(dst.parent_path());
  if (regular(i.s)) {
    FD in(open(i.src.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
    if (!unchanged(i.s, fdstat(in.n)))
      throw Failure("source replaced before opening");
    Hash h;
    U size = 0;
    for (;;) {
      checkpoint();
      auto start = Clock::now();
      auto n = read_retry(in.n, buf.data(), chunk_size(buf.size()));
      if (!n)
        break;
      if (opt.verify)
        h.add(buf.data(),
              n); // Represent all-zero chunks as sparse holes where supported.
      if (std::all_of(buf.data(), buf.data() + n,
                      [](char c) { return c == 0; })) {
        if (lseek(t.fd, n, SEEK_CUR) < 0)
          fail("seek sparse output");
      } else
        write_all(t.fd, buf.data(), n);
      size += n;
      bytes += n;
      progress();
      pace(start);
    }
    if (size != static_cast<U>(i.s.s.st_size) || !unchanged(i.s, fdstat(in.n)))
      throw Failure("source changed while reading");
    if (ftruncate(t.fd, size))
      fail("set output size");
    set_meta(t.fd, i.s);
    copy_xattrs(in.n, t.fd);
    if (fsync(t.fd))
      fail("flush output");
    if (opt.verify) {
      if (digest_fd(t.fd, buf) != h.get())
        throw Failure("xxHash64 verification failed");
      if (opt.metadata) {
        timespec ts[] = {i.s.s.st_atim, i.s.s.st_mtim};
        if (futimens(t.fd, ts))
          fail("restore timestamps after verify");
        if (fsync(t.fd))
          fail("flush timestamps");
      }
    }
    if (!unchanged(i.s, fdstat(in.n)))
      throw Failure("source changed during verification");
  } else {
    auto value = fs::read_symlink(i.src);
    if (close(t.fd))
      fail("close temp");
    t.fd = -1;
    if (unlink(t.path.c_str()))
      fail("unlink temp placeholder");
    t.active = false;
    if (::symlink(value.c_str(), t.path.c_str()))
      fail("create temporary symlink");
    t.active = true;
    if (opt.metadata) {
      timespec ts[] = {i.s.s.st_atim, i.s.s.st_mtim};
      if (utimensat(AT_FDCWD, t.path.c_str(), ts, AT_SYMLINK_NOFOLLOW))
        fail("set link timestamps");
    }
    if (opt.verify && fs::read_symlink(t.path) != value)
      throw Failure("symlink verification failed");
  }
  if (!unchanged(i.s, statpath(i.src)))
    throw Failure("source changed before publish");
  commit(t, dst, old);
  i.published = statpath(dst);
  if (opt.mode == "move" || opt.mode == "sync")
    sync_dir(dst.parent_path());
  i.success = true;
  ++copied;
  message("I", "Copied: " + i.src.string() + " -> " + dst.string());
}
void prepare_dirs() {
  if (opt.dest_has_slash)
    mkdirs(opt.dest);
  else {
    safe_parents(opt.dest);
    if (!dir(statpath(opt.dest)))
      throw Failure("selected destination no longer exists: " + opt.dest.string());
  }
  for (auto &i : items)
    if (dir(i.s)) {
      checkpoint();
      auto dst = target(i);
      safe_parents(dst.parent_path());
      auto s = statpath(dst);
      if (!s.exists) {
        if (mkdir(dst.c_str(), 0700))
          fail("mkdir " + dst.string());
        i.made_dir = true;
      } else if (!dir(s))
        throw Failure("directory changed: " + dst.string());
    }
}
void finish_dirs() {
  for (auto it = items.rbegin(); it != items.rend(); ++it)
    if (dir(it->s) && !it->root_contents) {
      checkpoint();
      auto dst = target(*it);
      try {
        if ((opt.metadata || opt.xattrs) &&
            (it->made_dir || (!errors.load() && opt.mode != "no-overwrite" &&
                              opt.mode != "sync"))) {
          FD fd(open(dst.c_str(),
                     O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
          FD src(open(it->src.c_str(),
                      O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
          if (!same_inode(it->s, fdstat(src.n)))
            throw Failure("source directory replaced");
          if (it->planned_dst.exists &&
              !same_inode(it->planned_dst, fdstat(fd.n)))
            throw Failure("destination directory replaced");
          if (opt.metadata || it->made_dir)
            set_meta(fd.n, it->s);
          copy_xattrs(src.n, fd.n);
        } else if (it->made_dir) {
          if (chmod(dst.c_str(), 0777 & ~process_umask))
            fail("chmod new directory");
        }
      } catch (const std::exception &e) {
        report(dst.string() + ": " + e.what());
      }
    }
}
struct Extra {
  fs::path p;
  Stat s;
};
std::vector<Extra> extras;
void collect_extras(const fs::path &p, const fs::path &rel) {
  checkpoint();
  auto s = statpath(p);
  if (!s.exists)
    return;
  bool wanted = index_by_rel.count(rel) > 0;
  if (dir(s)) { // Do not cross mounted filesystems during pruning.
    auto base = statpath(opt.dest);
    if (s.s.st_dev != base.s.st_dev)
      throw Failure("refusing to prune across mount: " + p.string());
    for (auto &e : fs::directory_iterator(p))
      collect_extras(e.path(), rel / e.path().filename());
  }
  if (!wanted && !rel.empty())
    extras.push_back({p, s});
}
void check_source_tree() {
  for (auto &i : items) {
    checkpoint();
    if (!unchanged(i.s, statpath(i.src)))
      throw Failure("source changed after scan: " + i.src.string());
  }
}
void prune() {
  check_source_tree();
  for (auto &i : items) {
    auto d = statpath(target(i));
    if (dir(i.s)) {
      if (!dir(d) || (i.planned_dst.exists && !same_inode(i.planned_dst, d)))
        throw Failure("destination directory changed before pruning: " +
                      target(i).string());
    } else if (!unchanged(i.needs_copy ? i.published : i.planned_dst, d))
      throw Failure("destination changed before pruning: " +
                    target(i).string());
  }
  for (auto &e : extras) {
    checkpoint();
    safe_parents(e.p.parent_path());
    auto now = statpath(e.p);
    if (!now.exists)
      continue;
    if (!same_inode(e.s, now) || (!dir(e.s) && !unchanged(e.s, now)))
      throw Failure("extra destination changed: " + e.p.string());
    if (dir(now) ? rmdir(e.p.c_str()) : unlink(e.p.c_str()))
      fail("remove " + e.p.string());
    ++deleted;
    message("I", "Removed destination extra: " + e.p.string());
  }
}
void move_cleanup() {
  check_source_tree();
  for (auto &i : items)
    if (dir(i.s)) {
      sync_dir(target(i));
      sync_dir(target(i).parent_path());
    }
  for (auto p = opt.dest; p != p.root_path(); p = p.parent_path())
    sync_dir(p);
  for (auto &i : items)
    if (!dir(i.s) &&
        (!i.success || !unchanged(i.published, statpath(target(i)))))
      throw Failure("destination missing or changed; move source retained: " +
                    target(i).string());
  for (auto it = items.rbegin(); it != items.rend(); ++it) {
    checkpoint();
    safe_parents(it->src.parent_path());
    auto now = statpath(it->src);
    if (!same_inode(it->s, now) || (!dir(it->s) && !unchanged(it->s, now)))
      throw Failure("source changed before removal: " + it->src.string());
    if (dir(now)) {
      if (it->root_contents)
        continue;
      if (rmdir(it->src.c_str())) {
        if (errno == ENOTEMPTY || errno == EEXIST) {
          message("I",
                  "Retained nonempty source directory: " + it->src.string());
          continue;
        }
        fail("remove source directory");
      }
    } else if (unlink(it->src.c_str()))
      fail("remove source file");
    if (!dir(now) && now.s.st_nlink > 1) {
      for (auto &other : items)
        if (other.src != it->src && same_inode(other.s, now)) {
          auto updated = statpath(other.src);
          if (updated.exists && same_inode(updated, now) &&
              updated.s.st_nlink == now.s.st_nlink - 1 &&
              updated.s.st_mode == now.s.st_mode &&
              updated.s.st_size == now.s.st_size &&
              timeeq(updated.s.st_mtim, now.s.st_mtim))
            other.s = updated;
        }
    }
    ++deleted;
    message("I", "Removed source: " + it->src.string());
  }
}
void usage() {
  std::cout
      << "FastCopy Mint 0.2.8 (Linux reimplementation of FastCopy "
         "3.63)\nUsage: fastcopy-mint-cli [options] --dest DIRECTORY -- "
         "SOURCE...\n  --mode diff|no-overwrite|newer|overwrite|sync|move\n  "
         "--verify / --no-verify    xxHash64 read-back (default on; mandatory "
         "for move)\n  --workers N (1-32; default auto) --buffer-mib N (1-64 "
         "per worker)\n  --dry-run                 no filesystem "
         "modifications\n  --allow-delete            required for real sync or "
         "move\n  --include GLOB / --exclude GLOB   repeatable; not allowed for sync "
         "or move\n  --speed-percent N        0 (suspend), 10..90, 100 (full); duty-cycle limiter\n  "
         "--live-speed             GUI control pipe (bytes 0..10 on stdin)\n  --no-metadata             don't preserve mode/mtime\n  "
         "--xattrs                  copy user xattrs and POSIX ACLs on regular "
         "files/directories\n  --machine                 tab-delimited "
         "progress, hex-encoded messages\nDestDir ending in '/' may be created; "
         "copies source folders by name.\nWithout '/', DestDir must exist; a single "
         "source folder copies its contents.\nMultiple source folders retain their names. "
         "A source ending in '/' explicitly copies its contents.\nSync always mirrors "
         "contents into the exact DestDir.\nSignals: "
         "SIGUSR1 pause, SIGUSR2 resume, SIGINT/SIGTERM cancel.\n";
}
unsigned number(const std::string &s, unsigned max) {
  size_t pos = 0;
  auto v = std::stoul(s, &pos);
  if (pos != s.size() || v < 1 || v > max)
    throw Failure("invalid numeric option: " + s);
  return v;
}
int main(int argc, char **argv) {
  process_umask = umask(0);
  umask(process_umask);
  std::signal(SIGINT, sig);
  std::signal(SIGTERM, sig);
  std::signal(SIGUSR1, sig);
  std::signal(SIGUSR2, sig);
  try {
    for (int a = 1; a < argc; ++a) {
      std::string arg = argv[a];
      auto val = [&]() {
        if (++a >= argc)
          throw Failure("missing value for " + arg);
        return std::string(argv[a]);
      };
      if (arg == "--") {
        while (++a < argc)
          opt.sources.push_back(argv[a]);
        break;
      } else if (arg == "--help" || arg == "-h" || arg == "--version") {
        usage();
        return 0;
      } else if (arg == "--dest")
        opt.dest = val();
      else if (arg == "--mode")
        opt.mode = val();
      else if (arg == "--workers")
        opt.workers = number(val(), 32);
      else if (arg == "--buffer-mib")
        opt.buffer = number(val(), 64);
      else if (arg == "--include")
        opt.include.push_back(val());
      else if (arg == "--exclude")
        opt.exclude.push_back(val());
      else if (arg == "--speed-percent") {
        auto value = val();
        unsigned speed = value == "0" ? 0 : number(value, 100);
        if (speed % 10) throw Failure("speed must be 0, 10, ..., 100");
        speed_percent.store(speed);
      } else if (arg == "--live-speed")
        live_speed = true;
      else if (arg == "--verify")
        opt.verify = true;
      else if (arg == "--no-verify")
        opt.verify = false;
      else if (arg == "--dry-run")
        opt.dry = true;
      else if (arg == "--allow-delete")
        opt.allow_delete = true;
      else if (arg == "--machine")
        opt.machine = true;
      else if (arg == "--no-metadata")
        opt.metadata = false;
      else if (arg == "--xattrs")
        opt.xattrs = true;
      else if (arg.size() && arg[0] == '-')
        throw Failure("unknown option: " + arg);
      else
        opt.sources.push_back(arg);
    }
    const std::vector<std::string> modes = {
        "diff", "no-overwrite", "newer", "overwrite", "sync", "move"};
    if (std::find(modes.begin(), modes.end(), opt.mode) == modes.end())
      throw Failure("unknown mode");
    if ((opt.mode == "sync" || opt.mode == "move") && !opt.dry &&
        !opt.allow_delete)
      throw Failure("sync/move needs --allow-delete or --dry-run");
    if (opt.mode == "move" && (!opt.exclude.empty() || !opt.include.empty() || !opt.verify))
      throw Failure("move requires verification and no filters");
    if (live_speed) {
      auto flags = fcntl(STDIN_FILENO, F_GETFL);
      if (flags < 0 || fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK) < 0)
        fail("set up live speed control");
    }
    // An initially suspended GUI transfer must still be resumable before scanning.
    if (speed_percent.load() == 0) progress(true);
    plan();
    if (opt.mode == "sync" && statpath(opt.dest).exists)
      collect_extras(opt.dest, {});
    progress(true);
    if (opt.dry) {
      if (!items.empty() && opt.dest_has_slash && !statpath(opt.dest).exists &&
          std::none_of(items.begin(), items.end(), [](const Item &i) { return i.rel.empty(); }))
        message("I", "Would create: " + opt.dest.string());
      for (auto &i : items) {
        checkpoint();
        if (dir(i.s)) {
          if (!statpath(target(i)).exists)
            message("I", "Would create: " + target(i).string());
        } else if (i.needs_copy) {
          message("I", "Would copy: " + i.src.string() + " -> " +
                           target(i).string());
          if (opt.mode == "move")
            message("I", "Would remove source after verification: " +
                             i.src.string());
        } else {
          ++skipped;
          message("I", "Would skip: " + target(i).string());
        }
        if (!dir(i.s))
          ++done;
      }
      for (auto &e : extras)
        message("I", "Would remove destination extra: " + e.p.string());
      progress(true);
      return 0;
    }
    if (!opt.include.empty() && items.empty()) {
      message("I", "No files matched the include filters");
      progress(true);
      return 0;
    }
    prepare_dirs();
    if (!opt.workers) {
      auto d = statpath(opt.dest);
      opt.workers = 1;
      for (auto &i : items)
        if (i.s.s.st_dev != d.s.st_dev) {
          opt.workers = 4;
          break;
        }
    }
    message("I", "Transfer workers: " + std::to_string(opt.workers));
    std::atomic<size_t> next{0};
    auto worker = [&]() {
      try {
        std::vector<char> buf(static_cast<size_t>(opt.buffer) * 1024 * 1024);
        for (;;) {
          checkpoint();
          size_t n = next++;
          if (n >= items.size())
            break;
          auto &i = items[n];
          if (dir(i.s))
            continue;
          try {
            if (i.needs_copy)
              copy_one(i, buf);
            else {
              if (!unchanged(i.planned_dst, statpath(target(i))) ||
                  !unchanged(i.s, statpath(i.src)))
                throw Failure("source or destination changed since scan");
              ++skipped;
              message("I", "Skipped: " + i.src.string());
            }
          } catch (const Cancel &) {
            throw;
          } catch (const std::exception &e) {
            report(i.src.string() + ": " + e.what());
          }
          ++done;
          progress();
        }
      } catch (const Cancel &) {
      } catch (const std::exception &e) {
        report(e.what());
      }
    };
    std::vector<std::thread> threads;
    try {
      for (unsigned j = 0; j < opt.workers; ++j)
        threads.emplace_back(worker);
    } catch (...) {
      cancelled = true;
      for (auto &t : threads)
        t.join();
      throw;
    }
    for (auto &t : threads)
      t.join();
    checkpoint();
    finish_dirs();
    checkpoint();
    if (!errors.load()) {
      if (opt.mode == "sync")
        prune();
      if (opt.mode == "move")
        move_cleanup();
    } else if (opt.mode == "sync" || opt.mode == "move")
      message("I", "Deletion phase skipped because of errors");
    progress(true);
    return errors.load() ? 1 : 0;
  } catch (const Cancel &) {
    message("I", "Cancelled; published files retained, no rollback");
    progress(true);
    return 130;
  } catch (const std::exception &e) {
    report(e.what());
    progress(true);
    return 1;
  }
}
