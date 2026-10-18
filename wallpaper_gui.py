# -*- coding: utf-8 -*-
"""
壁纸轮换器（绿色单文件版）

用法：
- 不带参数运行：打开设置界面（本地网页，自动用系统默认浏览器打开）
- --run       : 静默轮换一次（供计划任务调用）
- --install   : 安装/更新计划任务
- --uninstall : 删除计划任务

配置保存在 EXE 同目录 config.json（不可写时退回 %APPDATA%\\WallpaperRotate）。
设置界面通过 http://127.0.0.1:18106/ 提供，只监听本机，关闭页面或空闲
15 分钟后自动退出。
"""
import ctypes
import json
import os
import random
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "WallpaperRotate"
# 任务名与 NasCab 无关，就是本机的一个壁纸轮换任务
TASK_NAME = "WallpaperRotate"
SPI_SETDESKWALLPAPER = 20
SPIF_UPDATEINIFILE = 1
SPIF_SENDCHANGE = 2

SETTINGS_PORT = 18106
IDLE_TIMEOUT = 900  # 设置界面空闲 15 分钟自动退出

DEFAULT = {
    "folders": [],            # 为空则默认用"图片"库
    "interval_hours": 6,
    "half_life_days": 7,      # 新图权重半衰期（天）
    "min_weight": 0.05,       # 老图保底权重
    "min_size_mb": 1.0,
    "min_width": 1920,
    "min_height": 1080,
}


# ---------------------------------------------------------------- 配置

def exe_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    d = exe_dir()
    p = os.path.join(d, "config.json")
    try:
        open(p, "a", encoding="utf-8").close()
        return p
    except Exception:
        d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "config.json")


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def user_pictures():
    """本机当前用户的“图片”文件夹。

    优先用 Known Folder API（FOLDERID_Pictures），兼容 OneDrive 重定向
    （那种情况下图片库在 OneDrive 目录里而不是 C:\\Users\\<名>\\Pictures）；
    失败则退回 %USERPROFILE%\\Pictures。
    """
    try:
        import uuid as _uuid
        g = _uuid.UUID("33E28130-4E1E-4676-835A-98395C3BC3BB")  # FOLDERID_Pictures
        guid = _GUID(g.time_low, g.time_mid, g.time_hi_version,
                     (ctypes.c_ubyte * 8).from_buffer_copy(g.bytes[8:]))
        fn = ctypes.windll.shell32.SHGetKnownFolderPath
        fn.restype = ctypes.c_long
        fn.argtypes = [ctypes.POINTER(_GUID), ctypes.c_uint,
                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        ptr = ctypes.c_void_p()
        ctypes.windll.ole32.CoInitialize(None)
        if fn(ctypes.byref(guid), 0, None, ctypes.byref(ptr)) == 0 and ptr.value:
            p = ctypes.wstring_at(ptr.value)
            ctypes.windll.ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            ctypes.windll.ole32.CoTaskMemFree(ptr.value)
            if os.path.isdir(p):
                return p
    except Exception:
        pass
    for base in (os.environ.get("USERPROFILE"), os.path.expanduser("~")):
        if base:
            p = os.path.join(base, "Pictures")
            if os.path.isdir(p):
                return p
    return ""


def _rebase_folder(path):
    """把从别的电脑拷贝来的用户目录路径适配到本机；不适配返回 None。

    只处理 <盘>:\\Users\\<某用户名>\\... 形态：把用户名换成本机当前用户，
    保留后面部分（如 Pictures\\Screenshots）。盘符不同也没关系。
    """
    if not path:
        return None
    if os.path.isdir(path):
        return path
    parts = os.path.normpath(path).split(os.sep)
    if len(parts) >= 4 and parts[1].lower() == "users":
        profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        cand = os.path.join(profile, *parts[3:])
        if os.path.isdir(cand):
            return cand
    return None


def load_config():
    p = config_path()
    cfg = dict(DEFAULT)
    if os.path.exists(p):
        try:
            cfg.update(json.load(open(p, encoding="utf-8")))
        except Exception:
            pass
    folders, changed = [], False
    for f in cfg.get("folders") or []:
        r = _rebase_folder(f)
        if r:
            if os.path.normpath(r) != os.path.normpath(f):
                changed = True
                log("文件夹已适配本机：%s -> %s" % (f, r))
            folders.append(r)
        else:
            changed = True
            log("文件夹不存在，已忽略：%s" % f)
    if not folders:
        pic = user_pictures()
        if pic:
            folders = [pic]
            changed = True
            log("使用本机图片文件夹：%s" % pic)
    cfg["folders"] = folders
    if changed:
        try:
            save_config(cfg)
        except Exception:
            pass
    return cfg


def save_config(cfg):
    json.dump(cfg, open(config_path(), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def log_path():
    return os.path.join(os.path.dirname(config_path()), "rotate.log")


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    return line


def tail_log(n=8):
    try:
        lines = open(log_path(), encoding="utf-8").read().splitlines()
        return lines[-n:]
    except Exception:
        return []


# ---------------------------------------------------------------- 图片尺寸（解析文件头，不加载整图）

def image_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return w, h
            if head[:2] == b"\xff\xd8":
                data = head + f.read(65536)
                i = 2
                n = len(data)
                while i < n - 9:
                    if data[i] != 0xFF:
                        i += 1
                        continue
                    m = data[i + 1]
                    if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                             0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        h, w = struct.unpack(">HH", data[i + 5:i + 9])
                        return w, h
                    if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                        i += 2
                        continue
                    seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                    i += 2 + seg
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- 核心

def collect(cfg):
    """返回 [(path, weight, age_days)]"""
    exts = (".jpg", ".jpeg", ".png")
    min_bytes = float(cfg["min_size_mb"]) * 1024 * 1024
    now = time.time()
    hl = max(0.1, float(cfg["half_life_days"]))
    floor = float(cfg["min_weight"])
    out = []
    for folder in cfg.get("folders", []):
        if not folder or not os.path.isdir(folder):
            continue
        for root, _, files in os.walk(folder):
            for name in files:
                if not name.lower().endswith(exts):
                    continue
                p = os.path.join(root, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size < min_bytes:
                    continue
                sz = image_size(p)
                if not sz:
                    continue
                if sz[0] < cfg["min_width"] or sz[1] < cfg["min_height"]:
                    continue
                added = min(st.st_ctime, st.st_mtime)
                age = max(0.0, (now - added) / 86400.0)
                w = max(floor, 0.5 ** (age / hl))
                out.append((p, w, age))
    return out


def set_wallpaper(path):
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, path, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop",
                           0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, "WallpaperStyle", 0, winreg.REG_SZ, "10")
        winreg.SetValueEx(k, "TileWallpaper", 0, winreg.REG_SZ, "0")
        winreg.CloseKey(k)
    except Exception:
        pass
    return bool(ok)


def rotate(cfg=None):
    cfg = cfg or load_config()
    items = collect(cfg)
    if not items:
        log("没有符合条件的图片")
        return None
    total = sum(w for _, w, _ in items)
    r = random.random() * total
    acc = 0.0
    pick = items[-1]
    for it in items:
        acc += it[1]
        if acc >= r:
            pick = it
            break
    path, w, age = pick
    ok = set_wallpaper(path)
    log("%s 候选=%d 选中(%.1f天,w=%.3f) %s" %
        ("OK" if ok else "FAIL", len(items), age, w, path))
    return path if ok else None


# ---------------------------------------------------------------- 计划任务

def _ps(script):
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                           "-Command", script],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _schtasks(args):
    """微软官方的任务计划命令行接口（schtasks.exe）。
    若所在环境禁用了它则抛异常，由调用方回退到 PowerShell。"""
    r = subprocess.run(["schtasks"] + args, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError((r.stdout or "") + (r.stderr or ""))
    return r.stdout or ""


def install_task(cfg):
    exe = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
    hours = max(1, int(cfg["interval_hours"]))
    try:
        _schtasks(["/Create", "/TN", TASK_NAME,
                   "/TR", '"%s" --run' % exe,
                   "/SC", "HOURLY", "/MO", str(hours), "/F"])
        log("install via schtasks: ok, every %dh" % hours)
        return True
    except Exception as e:
        log("schtasks failed (%s), fallback to PowerShell" % str(e)[:80])

    script = (
        "$a=New-ScheduledTaskAction -Execute '{exe}' -Argument '--run';"
        "$t=New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(2)) "
        "-RepetitionInterval (New-TimeSpan -Hours {h}) -RepetitionDuration (New-TimeSpan -Days 3650);"
        "Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger $t -Force | Out-Null;"
        "Write-Output 'REGISTERED'"
    ).format(exe=exe, h=hours, name=TASK_NAME)
    r = _ps(script)
    log("install: " + (r.stdout or "").strip()[:120] + (r.stderr or "").strip()[:200])
    return "REGISTERED" in (r.stdout or "")


def uninstall_task():
    try:
        _schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
        log("uninstall via schtasks: ok")
        return True
    except Exception as e:
        log("schtasks failed (%s), fallback to PowerShell" % str(e)[:80])
    r = _ps("Unregister-ScheduledTask -TaskName '%s' -Confirm:$false; Write-Output 'REMOVED'" % TASK_NAME)
    log("uninstall: " + (r.stdout or "").strip()[:120])
    return "REMOVED" in (r.stdout or "")


def task_installed():
    try:
        _schtasks(["/Query", "/TN", TASK_NAME])
        return True
    except Exception:
        pass
    r = _ps("if (Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue) {'YES'}"
            % TASK_NAME)
    return "YES" in (r.stdout or "")


# ---------------------------------------------------------------- 设置界面（本地网页 + HTTP API）

PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>壁纸轮换器</title><style>
body{font-family:"Microsoft YaHei",system-ui;margin:0;padding:18px;background:#15181c;color:#dfe3e8}
h2{margin:0 0 4px;font-size:16px;color:#8ab4f8}
#task{font-size:12px;color:#9aa4b0;margin-bottom:12px}
label{display:block;font-size:12px;color:#9aa4b0;margin:10px 0 4px}
input{width:100%;box-sizing:border-box;padding:7px 9px;background:#1e2228;color:#dfe3e8;
border:1px solid #2f353c;border-radius:5px;font-size:13px}
.row{display:flex;gap:8px}
.row>div{flex:1}
.btns{margin-top:16px;display:flex;flex-wrap:wrap;gap:8px}
button{padding:8px 14px;background:#2b3a4d;color:#8ab4f8;border:1px solid #3d5170;
border-radius:6px;cursor:pointer;font-size:13px}
button:hover{background:#33465e}
button.primary{background:#2d5a8a;color:#fff;border-color:#3d7ab5}
#dirs{display:flex;gap:6px}#dirs input{flex:1}
.stepper{display:flex;gap:6px;align-items:center}
.stepper input{flex:1;min-width:0}
.arrow{width:40px;padding:7px 0;background:#2b3a4d;color:#8ab4f8;border:1px solid #3d5170;
border-radius:5px;cursor:pointer;font-size:12px;line-height:1.2}
.arrow:hover{background:#33465e}
.badge{margin-left:8px;font-size:11px;color:#8ab4f8}
#w::-webkit-outer-spin-button,#w::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
#msg{margin-top:14px;padding:9px;border-radius:5px;background:#1c2b1f;color:#8fd19e;
font-size:12px;line-height:1.6;white-space:pre-wrap;min-height:20px}
.hint{font-size:11px;color:#6b7480;margin-top:3px}
pre{background:#1e2228;border:1px solid #2f353c;border-radius:5px;padding:8px;
font-size:11px;color:#9aa4b0;overflow:auto;max-height:110px;white-space:pre-wrap}
</style></head><body>
<h2>壁纸轮换器</h2>
<div id="task">读取状态中…</div>
<label>图片文件夹（多个用分号隔开）</label>
<div id="dirs"><input id="folders"><button onclick="browse()">浏览…</button></div>
<div class="hint">留空则自动使用本机的“图片”文件夹（当前用户目录下的 Pictures）；从其他电脑拷贝过来的配置会自动适配本机用户名；也可以直接编辑程序目录下的 config.json</div>
<div class="row"><div>
<label>最小宽度（px）<span id="tierBadge" class="badge"></span></label>
<div class="stepper">
<button type="button" class="arrow" onclick="tier(-1)" title="降到下一档">▼</button>
<input id="w" type="number" oninput="markTier()">
<button type="button" class="arrow" onclick="tier(1)" title="升到下一档">▲</button>
</div>
<div class="hint">▲▼ 按 1080P → 2K → 4K → 16K 整档切换（高度、文件大小同步）；也可以键盘直接输入任意数值，
手动改得比档位值大或小，再按箭头会跳到离当前值最近的那一档</div>
</div>
<div><label>最小高度（px）</label><input id="h" type="number"></div></div>
<div class="row"><div><label>最小大小（MB）</label><input id="mb" type="number" step="0.5" oninput="markTier()"></div>
<div><label>老图保底权重（0–1）</label><input id="floor" type="number" step="0.01"></div></div>
<div class="row"><div><label>轮换间隔（小时）</label><input id="hours" type="number"></div>
<div><label>新图权重半衰期（天）</label><input id="hl" type="number" step="0.5"></div></div>
<div class="hint">半衰期越小 → 越偏向刚加入的图片</div>
<div class="btns">
<button class="primary" onclick="act('save')">保存</button>
<button class="primary" onclick="act('rotate')">立即换一张</button>
<button onclick="act('install')">安装计划任务</button>
<button onclick="act('uninstall')">卸载计划任务</button>
<button onclick="shutdown()">关闭</button>
</div>
<div id="msg"></div>
<label>最近轮换记录</label><pre id="log"></pre>
<script>
async function get(u){const r=await fetch(u);return r.json()}
async function post(u,d){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d||{})});return r.json()}
function cfg(){return{folders:document.getElementById('folders').value,
 hours:document.getElementById('hours').value,hl:document.getElementById('hl').value,
 floor:document.getElementById('floor').value,mb:document.getElementById('mb').value,
 w:document.getElementById('w').value,h:document.getElementById('h').value}}
function set(c){document.getElementById('folders').value=c.folders||'';
 document.getElementById('hours').value=c.interval_hours;
 document.getElementById('hl').value=c.half_life_days;
 document.getElementById('floor').value=c.min_weight;
 document.getElementById('mb').value=c.min_size_mb;
 document.getElementById('w').value=c.min_width;
 document.getElementById('h').value=c.min_height;
 markTier()}
var TIERS=[{w:1920,h:1080,mb:1,name:'1080P'},
           {w:2560,h:1440,mb:2,name:'2K（1440P）'},
           {w:3840,h:2160,mb:5,name:'4K'},
           {w:15360,h:8640,mb:50,name:'16K'}];
function applyTier(t){document.getElementById('w').value=t.w;
 document.getElementById('h').value=t.h;
 document.getElementById('mb').value=t.mb;markTier()}
function tier(dir){var cur=+document.getElementById('w').value||0;var t=null;
 if(dir>0){
  for(var i=0;i<TIERS.length;i++){if(TIERS[i].w>cur){t=TIERS[i];break}}
  if(!t){say('已经是最高档：16K（15360×8640 · ≥50MB）');return}
 }else{
  for(var i=TIERS.length-1;i>=0;i--){if(TIERS[i].w<cur){t=TIERS[i];break}}
  if(!t){say('已经是最低档：1080P（1920×1080 · ≥1MB）');return}
 }
 applyTier(t);
 say('已切换到 '+t.name+' 档：'+t.w+'×'+t.h+'，文件 ≥'+t.mb+'MB（数值可继续手动修改）')}
function markTier(){var w=+document.getElementById('w').value||0,
 h=+document.getElementById('h').value||0,mb=+document.getElementById('mb').value||0;
 var cur=null;
 for(var i=0;i<TIERS.length;i++){var t=TIERS[i];
  if(t.w===w&&t.h===h&&t.mb===mb){cur=t;break}}
 document.getElementById('tierBadge').textContent=
  cur?('当前：'+cur.name+' 档'):'当前：自定义'}
function say(t){document.getElementById('msg').textContent=t}
async function browse(){say('正在打开文件夹选择框…');
 const r=await post('/api/browse');
 if(r.ok&&r.path){const f=document.getElementById('folders');
  f.value=f.value?f.value+';'+r.path:r.path;say('已添加：'+r.path)}
 else say(r.error||'未能打开文件夹选择框，请手动粘贴路径')}
async function act(a){say('处理中…');
 try{const r=await post('/api/'+a,cfg());say(r.message||'完成')}catch(e){say('请求失败：'+e)}
 refresh()}
async function refresh(){try{
 const s=await get('/api/status');
 document.getElementById('task').textContent=s.installed
  ?'计划任务已安装：每 '+s.hours+' 小时轮换一次（任务名 WallpaperRotate，可在“任务计划程序”中查看）'
  :'计划任务未安装';
 const l=await get('/api/log');
 document.getElementById('log').textContent=(l.log||[]).join('\\n')
}catch(e){}}
async function shutdown(){try{await post('/api/shutdown')}catch(e){}
 document.body.innerHTML='<h2>已关闭</h2><p style="color:#9aa4b0;font-size:13px">可以关掉这个标签页了。计划任务会在后台继续按时轮换。</p>'}
get('/api/config').then(c=>set(c));
refresh();
</script></body></html>"""

_shutdown = threading.Event()
_last_activity = [time.time()]


def parse_form(form):
    try:
        return {
            "folders": [x.strip() for x in
                        str(form.get("folders", "")).replace("；", ";").split(";")
                        if x.strip()],
            "interval_hours": max(1, int(float(form.get("hours", 6) or 6))),
            "half_life_days": max(0.1, float(form.get("hl", 7) or 7)),
            "min_weight": min(1.0, max(0.0, float(form.get("floor", 0.05) or 0.05))),
            "min_size_mb": max(0.0, float(form.get("mb", 1) or 1)),
            "min_width": max(0, int(float(form.get("w", 1920) or 1920))),
            "min_height": max(0, int(float(form.get("h", 1080) or 1080))),
        }
    except Exception as e:
        raise ValueError("参数有误：%s" % e)


def browse_folder():
    """调出系统文件夹选择框（PowerShell + WinForms），失败则返回空串。"""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$f=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$f.Description='选择图片文件夹';$f.ShowNewFolderButton=$false;"
        "if($f.ShowDialog() -eq 'OK'){$f.SelectedPath}"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden",
             "-Command", script],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
        return lines[-1] if lines else ""
    except Exception as e:
        log("browse fail: %s" % e)
        return ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _touch(self):
        _last_activity[0] = time.time()

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        self._touch()
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/api/config":
            cfg = load_config()
            self._json({"folders": ";".join(cfg["folders"]),
                        "interval_hours": cfg["interval_hours"],
                        "half_life_days": cfg["half_life_days"],
                        "min_weight": cfg["min_weight"],
                        "min_size_mb": cfg["min_size_mb"],
                        "min_width": cfg["min_width"],
                        "min_height": cfg["min_height"],
                        "config_path": config_path()})
        elif path == "/api/status":
            cfg = load_config()
            self._json({"installed": task_installed(),
                        "hours": cfg["interval_hours"]})
        elif path == "/api/log":
            self._json({"log": tail_log(8)})
        else:
            self._json({"error": "not found"}, 404)

    def _form(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_POST(self):
        self._touch()
        path = self.path.split("?", 1)[0]
        form = self._form()

        if path == "/api/browse":
            p = browse_folder()
            if p and os.path.isdir(p):
                self._json({"ok": True, "path": p})
            else:
                self._json({"ok": False,
                            "error": "没有选择文件夹（或选择框未能打开），请手动粘贴路径"})
            return

        try:
            cfg = parse_form(form)
        except ValueError as e:
            self._json({"ok": False, "message": str(e)})
            return

        if path == "/api/save":
            backup = cfg["folders"]
            save_config(cfg)
            if not backup:
                cfg["folders"] = load_config()["folders"]
            n = len(collect(cfg))
            self._json({"ok": True,
                        "message": "已保存。\n当前条件下有 %d 张图片参与轮换。\n配置：%s"
                                   % (n, config_path())})

        elif path == "/api/rotate":
            save_config(cfg)
            p = rotate(cfg)
            self._json({"ok": bool(p),
                        "message": ("已换为：\n%s" % p) if p
                                   else "没有符合当前条件的图片。"})

        elif path == "/api/install":
            save_config(cfg)
            ok = install_task(cfg)
            self._json({"ok": ok,
                        "message": ("已安装计划任务，每 %d 小时轮换一次。\n"
                                    "可在「任务计划程序」里看到任务：%s"
                                    % (cfg["interval_hours"], TASK_NAME)) if ok
                                   else "安装失败，详见日志：%s" % log_path()})

        elif path == "/api/uninstall":
            ok = uninstall_task()
            self._json({"ok": ok,
                        "message": "计划任务已删除。" if ok
                                   else "删除失败，可能本来就不存在。"})

        elif path == "/api/shutdown":
            self._json({"ok": True, "message": "正在关闭…"})
            _shutdown.set()
        else:
            self._json({"error": "not found"}, 404)


def run_settings():
    """启动本地设置页面，打开浏览器，等待关闭或空闲超时。"""
    httpd = None
    port = SETTINGS_PORT
    for p in (SETTINGS_PORT, 0):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = httpd.server_address[1]
            break
        except OSError:
            httpd = None
    if httpd is None:
        ctypes.windll.user32.MessageBoxW(
            0, "无法启动设置界面（端口被占用）。\n可直接编辑程序目录下的 config.json。",
            "壁纸轮换器", 0x30)
        return

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = "http://127.0.0.1:%d/" % port
    log("settings ui: %s" % url)

    opened = False
    try:
        opened = webbrowser.open(url)
    except Exception:
        pass
    if not opened:
        try:
            os.startfile(url)
            opened = True
        except Exception:
            pass
    if not opened:
        ctypes.windll.user32.MessageBoxW(
            0, "浏览器没有自动打开，请手动访问：\n%s" % url, "壁纸轮换器", 0x40)

    while not _shutdown.is_set():
        if time.time() - _last_activity[0] > IDLE_TIMEOUT:
            log("settings ui idle timeout")
            break
        _shutdown.wait(1.0)
    httpd.shutdown()
    log("settings ui closed")


# ---------------------------------------------------------------- 入口

def main():
    args = sys.argv[1:]
    if not args:
        run_settings()
        return 0
    if args[0] == "--run":
        rotate()
    elif args[0] == "--install":
        print("installed" if install_task(load_config()) else "failed")
    elif args[0] == "--uninstall":
        print("removed" if uninstall_task() else "failed")
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
