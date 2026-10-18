#!/usr/bin/env python3
"""NEXUS Dashboard 数据服务 v4 - 认证 + 安全加固"""

import json
import os
import re
import time
import hashlib
import secrets
import subprocess
import sqlite3
import threading
import urllib.request
import urllib.parse
import ssl
from http.server import ThreadingHTTPServer as HTTPServer, BaseHTTPRequestHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DASH_DATA_DIR', BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, 'config.json')
COOKIES_PATH = os.path.join(DATA_DIR, 'cookies.json')
SESSIONS_PATH = os.path.join(DATA_DIR, 'sessions.json')

def load_cookies():
    try:
        with open(COOKIES_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_cookies(data):
    with open(COOKIES_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)

FAV_PATH = os.path.join(DATA_DIR, 'favorites.json')

def load_favs():
    try:
        with open(FAV_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []

def save_favs(favs):
    with open(FAV_PATH, 'w', encoding='utf-8') as f:
        json.dump(favs, f, ensure_ascii=False, indent=2)

CATBOX_PATH = os.path.join(DATA_DIR, 'catbox_map.json')

def _load_catbox_map():
    try:
        with open(CATBOX_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _save_catbox_map(m):
    try:
        with open(CATBOX_PATH, 'w', encoding='utf-8') as f:
            json.dump(m, f, ensure_ascii=False)
    except Exception:
        pass

def _catbox_upload(data, filename):
    """匿名上传图片到 catbox.moe，返回 https 直链；失败抛异常。"""
    boundary = '----nx' + hashlib.md5(str(len(data)).encode()).hexdigest()[:12]
    head = (
        '--%s\r\nContent-Disposition: form-data; name="reqtype"\r\n\r\nfileupload\r\n'
        '--%s\r\nContent-Disposition: form-data; name="fileToUpload"; filename="%s"\r\n'
        'Content-Type: application/octet-stream\r\n\r\n' % (boundary, boundary, filename)
    ).encode('latin-1')
    tail = ('\r\n--%s--\r\n' % boundary).encode('latin-1')
    body = head + data + tail
    req = urllib.request.Request(
        'https://catbox.moe/user/api.php', data=body,
        headers={'Content-Type': 'multipart/form-data; boundary=' + boundary,
                 'User-Agent': 'Mozilla/5.0 (dashboard)'},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = r.read().decode('utf-8', errors='ignore').strip()
    if resp.startswith('http'):
        return resp
    raise RuntimeError('catbox upload failed: ' + resp[:120])

def update_mp_sites_from_cookies(cookie_data):
    """收到浏览器 CookieCloud 推送后，自动更新 MP site 表"""
    updated = 0
    try:
        conn = sqlite3.connect(MP_DB)
        for domain, cookies in cookie_data.items():
            # cookies 是该域名下的 cookie 字符串列表
            cookie_str = '; '.join([c if isinstance(c, str) else c.get('name','')+'='+c.get('value','') for c in cookies])
            if not cookie_str or len(cookie_str) < 10:
                continue
            cur = conn.execute(
                "UPDATE site SET cookie=?, lst_mod_date=datetime('now','localtime') WHERE is_active=1 AND domain LIKE ?",
                (cookie_str, '%' + domain.replace('www.', '') + '%')
            )
            updated += cur.rowcount
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[CookieCloud] update MP sites error: {e}")
    return updated

def load_config():
    cfg = {
        'emby_host': '', 'emby_api_key': '',
        'qb_host': '', 'qb_user': '', 'qb_pass': '',
        'mp_db': '/volume1/docker/mp/config/user.db',
        'port': 28080,
        'svc_ports': {},
        'svc_aliases': {},
        'volume_aliases': {},
        'monitor_services': [],
        'favicon': '',
        'auth_enabled': True,
        'auth_username': 'admin',
        'auth_password_hash': '',
        'rbac': {
            'enabled': True,
            'roles': {},
            'users': [],
        },
    }
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            file_cfg = json.load(f)
        svc = file_cfg.get('services', {})
        cfg['emby_host'] = svc.get('emby', {}).get('host', '')
        cfg['emby_api_key'] = svc.get('emby', {}).get('api_key', '')
        cfg['qb_host'] = svc.get('qbittorrent', {}).get('host', '')
        cfg['qb_user'] = svc.get('qbittorrent', {}).get('username', '')
        cfg['qb_pass'] = svc.get('qbittorrent', {}).get('password', '')
        cfg['mp_db'] = svc.get('moviepilot', {}).get('db_path', cfg['mp_db'])
        cfg['port'] = file_cfg.get('ports', {}).get('api', 28080)
        cfg['svc_ports'] = file_cfg.get('svc_ports', {})
        cfg['svc_aliases'] = file_cfg.get('svc_aliases', {})
        cfg['volume_aliases'] = file_cfg.get('volume_aliases', {})
        cfg['monitor_services'] = file_cfg.get('monitor_services', []) or []
        auth = file_cfg.get('auth', {})
        cfg['auth_enabled'] = auth.get('enabled', True)
        cfg['auth_username'] = auth.get('username', 'admin')
        cfg['auth_password_hash'] = auth.get('password_hash', '')
        rbac = file_cfg.get('rbac', {})
        cfg['rbac'] = {
            'enabled': rbac.get('enabled', True),
            'roles': rbac.get('roles', {}) or {},
            'users': rbac.get('users', []) or [],
        }
    except Exception as e:
        print(f"[warn] config: {e}")
    # 默认密码 hash (admin123)，首次登录后应在设置中修改
    if not cfg['auth_password_hash']:
        cfg['auth_password_hash'] = hashlib.sha256('admin123'.encode()).hexdigest()
    return cfg

CONFIG = load_config()
EMBY_HOST = CONFIG['emby_host'].rstrip('/') or 'http://10.31.0.88:16880'
EMBY_API_KEY = CONFIG['emby_api_key'] or '76f3b50c02784b02b19fc56819de7e5d'
QB_HOST = CONFIG['qb_host'].rstrip('/') or 'http://10.31.0.88:16058'
QB_USER = CONFIG['qb_user'] or 'aoo'
QB_PASS = CONFIG['qb_pass'] or 'aa0772'

# ========== 认证与会话 ==========
SESSIONS = {}  # token -> {username, role, expires}
SESSION_LOCK = threading.Lock()
LOGIN_ATTEMPTS = {}  # ip -> {count, first_attempt}
SESSION_TTL = 30 * 24 * 3600  # 30 天，后端重启后从磁盘恢复
RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = 300  # 5 分钟

def _load_sessions():
    try:
        with open(SESSIONS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        now = time.time()
        loaded = {}
        for tok, rec in data.items():
            if isinstance(rec, dict) and rec.get('expires', 0) > now:
                loaded[tok] = rec
        return loaded
    except Exception:
        return {}

def _save_sessions():
    try:
        with SESSION_LOCK:
            data = {tok: rec for tok, rec in SESSIONS.items() if rec.get('expires', 0) > time.time()}
        tmp = SESSIONS_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp, SESSIONS_PATH)
    except Exception as e:
        print(f"[warn] save_sessions: {e}")

# 启动时恢复会话
SESSIONS = _load_sessions()

def create_session(username, role):
    token = secrets.token_urlsafe(32)
    with SESSION_LOCK:
        SESSIONS[token] = {'username': username, 'role': role, 'expires': time.time() + SESSION_TTL}
    _save_sessions()
    return token

def check_session(token):
    if not token:
        return None
    with SESSION_LOCK:
        s = SESSIONS.get(token)
        if not s:
            return None
        if time.time() > s['expires']:
            del SESSIONS[token]
            _save_sessions()
            return None
        # 滑动续期：每次使用延长有效期
        s['expires'] = time.time() + SESSION_TTL
        return s

def destroy_session(token):
    with SESSION_LOCK:
        SESSIONS.pop(token, None)
    _save_sessions()

def check_rate_limit(ip):
    now = time.time()
    rec = LOGIN_ATTEMPTS.get(ip)
    if rec and now - rec['first_attempt'] > RATE_LIMIT_WINDOW:
        LOGIN_ATTEMPTS.pop(ip, None)
        rec = None
    if rec and rec['count'] >= RATE_LIMIT_MAX:
        return False
    return True

def record_failed_attempt(ip):
    now = time.time()
    rec = LOGIN_ATTEMPTS.get(ip)
    if not rec or now - rec['first_attempt'] > RATE_LIMIT_WINDOW:
        LOGIN_ATTEMPTS[ip] = {'count': 1, 'first_attempt': now}
    else:
        rec['count'] += 1

def clear_attempts(ip):
    LOGIN_ATTEMPTS.pop(ip, None)

def verify_password(password):
    return hashlib.sha256(password.encode()).hexdigest() == CONFIG['auth_password_hash']

# ========== 脚本索引与运行状态 ==========
SCRIPTS_DIR = os.environ.get('DASH_SCRIPTS_DIR', '')
SCRIPT_RUNS_PATH = os.path.join(DATA_DIR, 'script_runs.json')
SCRIPT_EXTS = ('.py', '.sh', '.bash')
SCRIPT_SKIP_DIRS = {'__pycache__', '_archive', 'backup', '.git', '.ipynb_checkpoints'}

def _load_script_runs():
    try:
        with open(SCRIPT_RUNS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_script_runs(data):
    try:
        tmp = SCRIPT_RUNS_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SCRIPT_RUNS_PATH)
    except Exception as e:
        print(f"[warn] save_script_runs: {e}")

def scan_scripts():
    """扫描 SCRIPTS_DIR 下所有脚本文件，返回索引列表"""
    results = []
    if not os.path.isdir(SCRIPTS_DIR):
        return results
    for name in sorted(os.listdir(SCRIPTS_DIR)):
        if name.startswith('.') or name.endswith('.bak') or '.bak.' in name:
            continue
        full = os.path.join(SCRIPTS_DIR, name)
        if os.path.isdir(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in SCRIPT_EXTS:
            continue
        try:
            st = os.stat(full)
        except Exception:
            continue
        # 读取首行 shebang
        shebang = ''
        try:
            with open(full, 'r', encoding='utf-8', errors='ignore') as f:
                first = f.readline().strip()
                if first.startswith('#!'):
                    shebang = first[:120]
        except Exception:
            pass
        results.append({
            'name': name,
            'ext': ext,
            'size': st.st_size,
            'mtime': st.st_mtime,
            'executable': bool(st.st_mode & 0o111),
            'shebang': shebang,
        })
    return results

def run_script(name):
    """手动运行脚本，记录退出码和输出尾部"""
    # 安全：只允许 SCRIPTS_DIR 下的文件名，禁止路径穿越
    safe = os.path.basename(name)
    if safe != name or not safe:
        return {'ok': False, 'error': '非法脚本名'}
    full = os.path.join(SCRIPTS_DIR, safe)
    if not os.path.isfile(full):
        return {'ok': False, 'error': '脚本不存在'}
    ext = os.path.splitext(safe)[1].lower()
    if ext == '.py':
        cmd = ['python3', full]
    elif ext in ('.sh', '.bash'):
        cmd = ['bash', full]
    else:
        return {'ok': False, 'error': f'不支持的扩展名 {ext}'}
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=SCRIPTS_DIR)
        rc = proc.returncode
        out = (proc.stdout or '')[-4000:]
        err = (proc.stderr or '')[-4000:]
    except subprocess.TimeoutExpired:
        rc = -1
        out = ''
        err = '运行超时（300 秒）'
    except Exception as e:
        rc = -2
        out = ''
        err = str(e)
    duration = time.time() - started
    runs = _load_script_runs()
    runs[safe] = {
        'last_run': started,
        'exit_code': rc,
        'duration': round(duration, 2),
        'output_tail': out[-2000:],
        'error_tail': err[-2000:],
    }
    _save_script_runs(runs)
    return {'ok': rc == 0, 'exit_code': rc, 'duration': round(duration, 2),
            'output_tail': out[-2000:], 'error_tail': err[-2000:]}

PUBLIC_PATHS = {'/api/login', '/cookiecloud/sync', '/'}
STATIC_EXT = ('.html', '.css', '.js', '.png', '.jpg', '.ico', '.svg', '.woff', '.woff2', '.ttf')
MP_DB = CONFIG.get('mp_db') or '/volume1/docker/mp/config/user.db'
API_PORT = int(os.environ.get('DASH_API_PORT', CONFIG['port']))

DS220_HOST = '10.31.0.50'
SA_HOST = '10.31.0.88'
EMBY_USER_ID = '603301695e8c4db683563d8087fd71b7'  # 用户 m

# ========== RBAC（角色权限 + 媒体范围） ==========
# 权限树：大类 + 子权限。权限键 = 'page'（全开该页所有子权限）或 'page:sub'（单子权限）。
PERM_TREE = {
    'overview':     {'label': '总览',     'subs': ['view']},
    'applications': {'label': '服务',     'subs': ['view', 'config']},
    'media':        {'label': '媒体库',   'subs': ['view', 'detail']},
    'search':       {'label': '搜索',     'subs': ['all', 'bing', 'pansou', 'tmdb', 'douban']},
    'trending':     {'label': '流行趋势', 'subs': ['view']},
    'transfers':    {'label': '下载活动', 'subs': ['view', 'limit', 'manage']},
    'storage':      {'label': '存储',     'subs': ['view']},
    'scripts':      {'label': '脚本',     'subs': ['view', 'run']},
    'events':       {'label': '活动日志', 'subs': ['view']},
    'settings':     {'label': '设置',     'subs': ['view', 'manage', 'rbac']},
}
ALL_PERMS = list(PERM_TREE.keys())
ALL_PERM_KEYS = [p + ':' + s for p, t in PERM_TREE.items() for s in t['subs']]

def _has_perm(perms, perm):
    """判断角色权限是否包含 perm（可为 'page' 或 'page:sub'）。
    - 权限数组为空 = 全放开（RBAC 关闭 / 旧后端兼容）
    - 拥有大类键 'page' = 该页全部子权限
    - 页面级检查（无冒号）：拥有该页任一子权限即放行"""
    if not perms:
        return True
    if perm in perms:
        return True
    page = perm.split(':')[0]
    if page in perms:
        return True
    if ':' not in perm:
        return any(x.startswith(page + ':') for x in perms)
    return False

# 各 API 端点需要的最小权限（可精确到子权限）
PATH_PERM = {
    '/api/status': 'overview',
    '/api/storage': 'storage',
    '/api/torrents': 'transfers',
    '/api/limits': 'transfers:limit',
    '/api/media': 'media',
    '/api/recent': 'media',
    '/api/carousel': 'media',
    '/api/search/emby': 'media',
    '/api/search/pt': 'search',
    '/api/search/pansou': 'search',
    '/api/search/online': 'search',
    '/api/trending': 'trending',
    '/api/apps': 'applications',
    '/api/test-service': 'applications:config',
    '/api/scripts': 'scripts',
    '/api/events': 'events',
    '/api/config': 'settings',
    '/api/restart': 'settings:manage',
}

def endpoint_perm(path):
    if path.startswith('/api/detail/'):
        return 'media:detail'
    return PATH_PERM.get(path)

def get_role(role_name):
    """返回角色定义。admin 角色内置全权限 + 默认媒体范围(空=用户m)。"""
    if role_name == 'admin':
        return {'permissions': list(ALL_PERMS), 'emby_user': '', 'name': 'admin'}
    roles = (CONFIG.get('rbac') or {}).get('roles', {}) or {}
    r = roles.get(role_name) or {}
    return {
        'permissions': list(r.get('permissions', [])),
        'emby_user': r.get('emby_user', '') or '',
        'name': role_name,
    }

def find_user(username):
    """按用户名找账号：管理员(auth) 或 rbac.users 普通账号。返回 None 表示不存在。"""
    if not username:
        return None
    if username == CONFIG.get('auth_username', 'admin'):
        return {'username': username, 'role': 'admin', 'is_admin': True}
    for u in (CONFIG.get('rbac') or {}).get('users', []) or []:
        if u.get('username') == username:
            return {'username': username, 'role': u.get('role', 'member'), 'is_admin': False}
    return None

def user_password_hash(username):
    if not username:
        return ''
    if username == CONFIG.get('auth_username', 'admin'):
        return CONFIG.get('auth_password_hash', '')
    for u in (CONFIG.get('rbac') or {}).get('users', []) or []:
        if u.get('username') == username:
            return u.get('password_hash', '')
    return ''



# ========== 工具 ==========
def run_cmd(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ''

def http_get(url, timeout=5, headers=None):
    try:
        req = urllib.request.Request(url)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode()
    except Exception as e:
        return None

def _proxy_opener():
    """经 10.31.0.88:16001 代理网关访问外网（NAS 直连境外站易被墙/超时）"""
    import ssl
    proxy = {'http': 'http://10.31.0.88:16001', 'https': 'http://10.31.0.88:16001'}
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxy))

def http_get_proxy(url, timeout=10, headers=None):
    try:
        req = urllib.request.Request(url)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        resp = _proxy_opener().open(req, timeout=timeout)
        return resp.read().decode('utf-8', 'ignore')
    except Exception as e:
        return None

def _search_ddrk(keyword):
    """搜索低端影视 ddrk.one，返回结构化结果列表（代理网关偶发不通，最多重试 3 次）"""
    enc = urllib.parse.quote(keyword)
    url = 'https://ddrk.one/?s=' + enc
    html = None
    for t in (12, 12, 15):
        html = http_get_proxy(url, timeout=t, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if html:
            break
    if not html:
        return []
    results = []
    # 解析 <h2 class="post-box-title"><a href="/vod/ID/" rel="bookmark">标题 (更新至 XXX)</a></h2>
    import re
    pattern = r'<h2[^>]*post-box-title[^>]*>\s*<a\s+href="([^"]+)"[^>]*>([^<]+)</a>'
    for m in re.finditer(pattern, html):
        href = m.group(1)
        title_raw = m.group(2).strip()
        # 分离标题和更新状态
        quality = ''
        name = title_raw
        if '(' in title_raw and title_raw.endswith(')'):
            lp = title_raw.rfind('(')
            name = title_raw[:lp].strip()
            quality = title_raw[lp+1:-1].strip()
        full_url = 'https://ddrk.one' + href if href.startswith('/') else href
        results.append({
            'title': name,
            'quality': quality,
            'url': full_url,
            'source': '低端影视',
            'icon': '🎬',
        })
    return results

def _to_punycode(domain):
    """将中文域名转为 punycode"""
    import encodings.idna
    parts = domain.split('.')
    out = []
    for p in parts:
        try:
            if p.isascii():
                out.append(p)
            else:
                out.append(encodings.idna.ToASCII(p).decode('ascii'))
        except Exception:
            out.append(p)
    return '.'.join(out)

def _search_online(keyword):
    """在线观影搜索：ddrk.one 直连 + 影片名域名兜底"""
    results = []
    # 主源：低端影视
    ddrk_results = _search_ddrk(keyword)
    results.extend(ddrk_results)
    # 兜底：影片名域名（www.电影名.com，自动 punycode + /tv 路径）
    clean_name = re.sub(r'\(\d{4}\)|\d{4}$', '', keyword).strip()
    if clean_name:
        domain = 'www.' + clean_name + '.com'
        puny_domain = _to_punycode(domain)
        results.append({
            'title': clean_name,
            'quality': '在线观影',
            'url': 'https://' + puny_domain + '/tv',
            'source': '域名直连',
            'icon': '🌐',
        })
    return results

def _iqiyi_rank(limit=10):
    """爱奇艺风云榜·电视剧（页面 title 交替：剧名 / 年份题材主演）。"""
    import re as _re
    try:
        html = http_get_proxy('https://www.iqiyi.com/ranks1PCW/2/-6', timeout=15,
                       headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.iqiyi.com/'})
        if not html:
            return []
        # 提取封面图：榜单条目里的 img src
        imgs = _re.findall(r'<img[^>]+(?:data-src|src)="(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', html, _re.I)
        ts = _re.findall(r'title="([^"]{2,60})"', html)
        out = []
        img_idx = 0
        for i in range(len(ts) - 1):
            if len(out) >= limit:
                break
            if '/' not in ts[i] and '/' in ts[i + 1]:
                name = ts[i].strip()
                m = _re.match(r'(\d{4})\s*/\s*([^/]+)', ts[i + 1])
                # 取一张未用过的封面（跳过前几张非封面图）
                cover = ''
                while img_idx < len(imgs):
                    cand = imgs[img_idx]
                    img_idx += 1
                    if 'logo' not in cand.lower() and 'icon' not in cand.lower() and 'avatar' not in cand.lower():
                        cover = cand
                        break
                out.append({
                    'title': name, 'year': m.group(1) if m else '',
                    'genres': (m.group(2).strip() if m else '').replace('  ', ' '),
                    'extra': '剧集', 'rate': '', 'cover': cover,
                    'country': '中国大陆',
                })
        return out
    except Exception:
        return []

def _get_trending(platform='all'):
    """获取各平台热门影视"""
    result = {}
    # B站热门
    if platform in ('all', 'bilibili'):
        try:
            html = http_get_proxy('https://api.bilibili.com/x/web-interface/popular?ps=10&pn=1', timeout=10,
                           headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com'})
            if html:
                data = json.loads(html)
                items = data.get('data', {}).get('list', [])
                result['bilibili'] = [{
                    'title': it.get('title', ''),
                    'owner': (it.get('owner', {}).get('name', '') or '').replace('UP ', '').replace('up ', ''),
                    'play': it.get('stat', {}).get('view', 0),
                    'cover': it.get('pic', ''),
                    'url': 'https://www.bilibili.com/video/' + it.get('bvid', ''),
                } for it in items[:10]]
        except Exception:
            result['bilibili'] = []
    # 豆瓣热门电影（公开榜）
    if platform in ('all', 'douban'):
        result['douban'] = _douban_subjects('movie')
        result['douban_tv'] = _douban_subjects('tv')
    # 爱奇艺风云榜·电视剧
    if platform in ('all', 'iqiyi'):
        result['iqiyi'] = _iqiyi_rank()
    return result

def _tmdb_poster(title, is_tv):
    """按标题查 TMDB 拿高清海报（w500）；返回空串则前端回退源封面。"""
    try:
        t = (title or '').strip()
        if not t:
            return ''
        data = _tmdb_get("search/%s?query=%s&page=1" % ('tv' if is_tv else 'movie', urllib.parse.quote(t)))
        for it in (data.get('results') or [])[:5]:
            if it.get('poster_path'):
                return "https://image.tmdb.org/t/p/w500" + it['poster_path']
        return ''
    except Exception:
        return ''

def _douban_subjects(dtype):
    """豆瓣热门榜。补齐与媒体库搜索卡一致的字段：year / extra(电影|剧集) / genres"""
    import re as _re
    import concurrent.futures as _cf

    def _enrich(url):
        """豆瓣条目富化：年份 + 题材 + 国家（subject_abstract 内嵌接口，免抓条目页被反爬）"""
        try:
            m = _re.search(r'/subject/(\d+)/', url or '')
            if not m:
                return {'year': '', 'genres': '', 'country': ''}
            h = http_get('https://movie.douban.com/j/subject_abstract?subject_id=' + m.group(1), timeout=6,
                         headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://movie.douban.com/'})
            if not h:
                return {'year': '', 'genres': '', 'country': ''}
            data = json.loads(h)
            s = data.get('subject', {}) or {}
            regions = s.get('regions') or []
            return {
                'year': str(s.get('release_year') or ''),
                'genres': '、'.join(g for g in (s.get('types') or []) if g),
                'country': '、'.join(r for r in regions if r),
            }
        except Exception:
            return {'year': '', 'genres': '', 'country': ''}

    try:
        html = http_get('https://movie.douban.com/j/search_subjects?type=' + dtype + '&tag=%E7%83%AD%E9%97%A8&page_limit=10&page_start=0',
                       timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if html:
            data = json.loads(html)
            out = []
            subjects = data.get('subjects', [])[:10]
            enriched = {}
            posters = {}
            try:
                with _cf.ThreadPoolExecutor(max_workers=6) as ex:
                    urls = [it.get('url', '') for it in subjects]
                    for u, e, p in zip(urls,
                                       ex.map(_enrich, urls),
                                       ex.map(lambda s: _tmdb_poster(s.get('title', ''), dtype == 'tv'), subjects)):
                        if u:
                            enriched[u] = e
                        if p and u:
                            posters[u] = p
            except Exception:
                pass
            for it in subjects:
                title = it.get('title', '')
                url = it.get('url', '')
                en = enriched.get(url, {})
                title_clean = _re.sub(r'\s*[（(]?(19|20)\d{2}[）)]?\s*$', '', title).strip()
                out.append({
                    'title': title_clean,
                    'year': en.get('year', ''),
                    'rate': it.get('rate', ''),
                    'cover': posters.get(url) or it.get('cover', ''),
                    'url': url,
                    'extra': '剧集' if dtype == 'tv' else '电影',
                    'genres': en.get('genres', ''),
                    'country': en.get('country', ''),
                })
            return out
    except Exception:
        return []
    return []

def get_cpu_usage():
    try:
        with open('/proc/stat') as f:
            l1 = f.readline().split()
        time.sleep(0.1)
        with open('/proc/stat') as f:
            l2 = f.readline().split()
        idle1, total1 = int(l1[4]), sum(int(x) for x in l1[1:])
        idle2, total2 = int(l2[4]), sum(int(x) for x in l2[1:])
        dt, di = total2 - total1, idle2 - idle1
        return round((1 - di / dt) * 100, 1) if dt else None
    except Exception:
        return None

def get_memory_usage():
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                p = line.split(':')
                if len(p) == 2:
                    info[p[0]] = int(p[1].strip().split()[0])
        total, avail = info.get('MemTotal', 0), info.get('MemAvailable', 0)
        return round((1 - avail / total) * 100, 1) if total else None
    except Exception:
        return None

def _classify_device(source):
    """按挂载源识别存储所属设备：DS220(10.31.0.50)、Mage20/极空间(10.31.0.129)、其余为 SA 本地。"""
    if '10.31.0.50' in source:
        return 'DS'
    if '10.31.0.129' in source:
        return 'Mage20'
    return 'SA'

def get_disk_usage():
    # 用 df -Pk（而非 -B1）：df -B1 会在 SMB/极空间这类远程卷上阻塞数秒，导致 /api/storage 超时
    result = run_cmd("df -Pk | grep -E '/volume[0-9]|/md/'")
    pools = []
    aliases = CONFIG.get('volume_aliases', {})
    for line in result.split('\n'):
        if not line: continue
        parts = line.split()
        if len(parts) >= 6:
            try:
                mount = parts[5]
                pools.append({
                    'mount': mount,
                    'alias': aliases.get(mount, ''),
                    'source': parts[0],
                    'device': _classify_device(parts[0]),
                    'size_bytes': int(parts[1]) * 1024,
                    'used_bytes': int(parts[2]) * 1024,
                    'avail_bytes': int(parts[3]) * 1024,
                    'percent': int(parts[4].replace('%', '')),
                })
            except ValueError:
                continue
    # 按设备分组：SA 本地 / DS / Mage20
    dev_map = {}
    for p in pools:
        dev_map.setdefault(p['device'], []).append(p)
    devices = []
    for dev in ('SA', 'DS', 'Mage20'):
        plist = dev_map.get(dev)
        if not plist:
            continue
        total = sum(p['size_bytes'] for p in plist)
        used = sum(p['used_bytes'] for p in plist)
        avail = sum(p['avail_bytes'] for p in plist)
        devices.append({
            'name': dev,
            'size_bytes': total,
            'used_bytes': used,
            'avail_bytes': avail,
            'percent': round(used / total * 100) if total else 0,
            'pools': plist,
        })
    return {'pools': pools, 'devices': devices}

# 网络采样
_net_cache = {'down': 0.0, 'up': 0.0}
_DAY_FILE = os.path.join(DATA_DIR, 'net_day_base.json')

def _read_net_counters():
    rx = tx = 0
    with open('/proc/net/dev') as f:
        for line in f.readlines()[2:]:
            p = line.split(':')
            if len(p) != 2:
                continue
            iface = p[0].strip()
            # 只统计物理主接口 eth0，排除 docker bridge / veth / lo
            if iface == 'eth0':
                s = p[1].split()
                rx += int(s[0]); tx += int(s[8])
    return rx, tx

def _get_day_traffic():
    rx, tx = _read_net_counters()
    today = time.strftime('%Y-%m-%d')
    try:
        with open(_DAY_FILE) as f:
            saved = json.load(f)
        if saved.get('date') == today:
            # 如果计数器比基线小，说明重启后计数器清零了，重置基线
            if rx < saved['rx'] or tx < saved['tx']:
                saved['rx'] = rx
                saved['tx'] = tx
                with open(_DAY_FILE, 'w') as f:
                    json.dump(saved, f)
            return max(0, rx - saved['rx']), max(0, tx - saved['tx'])
    except Exception:
        pass
    # 新的一天或首次运行，记录基线
    base = {'date': today, 'rx': rx, 'tx': tx}
    try:
        with open(_DAY_FILE, 'w') as f:
            json.dump(base, f)
    except Exception:
        pass
    return 0, 0

def _net_sampler():
    global _net_cache
    while True:
        try:
            rx1, tx1 = _read_net_counters()
            time.sleep(2)
            rx2, tx2 = _read_net_counters()
            _net_cache['down'] = round((rx2 - rx1) / 1024 / 2, 1)
            _net_cache['up'] = round((tx2 - tx1) / 1024 / 2, 1)
        except Exception:
            pass
        time.sleep(3)
threading.Thread(target=_net_sampler, daemon=True).start()

def get_cpu_temp():
    try:
        with open('/sys/class/hwmon/hwmon0/temp1_input') as f:
            return round(int(f.read().strip()) / 1000)
    except Exception:
        return None

def get_uptime():
    """系统运行时长（秒）"""
    try:
        with open('/proc/uptime') as f:
            return int(float(f.read().split()[0]))
    except Exception:
        return None

# ========== Emby ==========
def _uid(user_id):
    """媒体查询作用域：返回角色绑定的 Emby 用户，空则默认用户 m。"""
    return user_id or EMBY_USER_ID

def emby_get(path):
    try:
        sep = '&' if '?' in path else '?'
        url = f"{EMBY_HOST}{path}{sep}api_key={EMBY_API_KEY}"
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[warn] emby {path}: {e}")
        return {}

def emby_poster(item_id, image_tag, width=300):
    if not image_tag:
        return ''
    return f"{EMBY_HOST}/Items/{item_id}/Images/Primary?tag={image_tag}&maxWidth={width}&api_key={EMBY_API_KEY}"

def _series_season_episode(item_id, user_id):
    """遍历 Series 的 Season 列表，返回 (季数, 总集数)。Emby 的 Series 不返回 RecursiveChildCount，需逐季求和。"""
    try:
        seas = emby_get(f"/Users/{_uid(user_id)}/Items?ParentId={item_id}&IncludeItemTypes=Season&Recursive=false&Fields=ChildCount")
        items = seas.get('Items') or []
        eps = 0
        for s in items:
            eps += s.get('ChildCount', 0) or 0
        return len(items), eps
    except Exception:
        return 0, 0

def _series_episode_count(item_id, user_id):
    """总集数（兼容旧调用）"""
    return _series_season_episode(item_id, user_id)[1]

def emby_search(query, user_id=None):
    result = emby_get(f"/Users/{_uid(user_id)}/Items?searchTerm={urllib.parse.quote(query)}&IncludeItemTypes=Movie,Series&Recursive=true&Limit=24&Fields=Overview,Runtime,ProductionYear,CommunityRating,Genres,Studios")
    items = result.get('Items', [])
    out = []
    for it in items:
        tags = it.get('ImageTags', {})
        runtime = it.get('RunTimeTicks', 0)
        runtime_min = int(runtime / 600000000) if runtime else 0
        if it.get('Type') == 'Series':
            seasons, episodes = _series_season_episode(it.get('Id', ''), user_id)
            extra = f"{seasons}季" + (f" · {episodes}集" if episodes else "")
        else:
            extra = f"{runtime_min}分钟" if runtime_min else ""
        out.append({
            'name': it.get('Name', ''),
            'year': it.get('ProductionYear', ''),
            'rating': round(it.get('CommunityRating', 0) or 0, 1),
            'id': it.get('Id', ''),
            'type': it.get('Type', ''),
            'poster': emby_poster(it.get('Id', ''), tags.get('Primary')),
            'overview': it.get('Overview', '')[:120],
            'runtime': runtime_min,
            'genres': ', '.join(it.get('Genres',[])[:3]),
            'studios': ', '.join([s if isinstance(s, str) else s.get('Name','') for s in it.get('Studios',[])[:2]]),
            'extra': extra,
        })
    return out

def pt_search(query, exclude=''):
    """PT 站串行搜索：逐站请求，站间间隔 1.5s 限速"""
    import time
    sites = []
    try:
        conn = sqlite3.connect(MP_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT name, url, cookie, ua, proxy FROM site WHERE is_active=1 AND cookie != ''")
        for row in cur:
            sites.append({
                'name': row['name'],
                'url': row['url'].rstrip('/'),
                'cookie': row['cookie'] or '',
                'ua': row['ua'] or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'proxy': row['proxy'] or 0,
            })
        conn.close()
    except Exception:
        return []

    exclude_terms = [e.strip().lower() for e in (exclude or '').split() if e.strip()]
    results = []

    import concurrent.futures
    def _search_site(site):
        try:
            search_url = site['url'] + '/torrents.php?search=' + urllib.parse.quote(query)
            req = urllib.request.Request(search_url)
            req.add_header('Cookie', site['cookie'])
            req.add_header('User-Agent', site['ua'])
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            req.add_header('Accept-Language', 'zh-CN,zh;q=0.9,en;q=0.8')
            req.add_header('Referer', site['url'] + '/')
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            if site['proxy']:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({'https': 'http://10.31.0.88:16001', 'http': 'http://10.31.0.88:16001'}),
                    urllib.request.HTTPSHandler(context=ctx))
            else:
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
            resp = opener.open(req, timeout=3.5)
            html = resp.read().decode('utf-8', errors='ignore')
            final_url = resp.geturl()
            if resp.status == 403 or 'login' in final_url.lower():
                return []
            links = re.findall(r'href="(details\.php\?id=\d+[^"]*)"[^>]*>(.*?)</a>', html, re.DOTALL)
            partial = []
            for href, title_html in links:
                title = re.sub(r'<[^>]+>', '', title_html).strip()
                if not title or len(title) < 3:
                    continue
                # 排除关键词过滤
                tl = title.lower()
                if any(term in tl for term in exclude_terms):
                    continue
                detail_url = site['url'] + '/' + href
                idx = html.find(href)
                context = html[max(0,idx-2000):idx+2000]
                m_size = re.search(r'([\d.]+\s*(?:GB|MB|TB))', context, re.IGNORECASE)
                size = m_size.group(1) if m_size else ''
                m_seed = re.search(r'>(\d+)</a>', context[context.find(href):])
                seeders = m_seed.group(1) if m_seed else '0'
                partial.append({
                    'title': title, 'site': site['name'], 'url': detail_url,
                    'size': size, 'seeders': seeders, 'leechers': '0',
                })
            return partial
        except Exception:
            return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        for partial in ex.map(_search_site, sites[:20]):  # 并发查最多20站
            results.extend(partial)

    # 按做种数排序，去重
    seen = set()
    unique = []
    for r in sorted(results, key=lambda x: int(x.get('seeders', 0) or 0), reverse=True):
        key = r['title'][:50]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique[:100]

def _cjk(s):
    return bool(re.search(r'[\u4e00-\u9fff]', s or ''))

def _official_name(name, orig, tmdb_id, itype):
    """计算「官方名/别名」：显示名是中文→官方英文名(原语名)；显示名非中文→反向查 TMDB 取官方中文名"""
    if _cjk(name):
        return orig if orig and orig != name else ''
    # 显示名非中文：优先 TMDB 中文名
    if tmdb_id:
        try:
            if itype == 'Series':
                d = _tmdb_get('tv/%s' % tmdb_id)
            else:
                d = _tmdb_get('movie/%s' % tmdb_id)
            zh = ((d or {}).get('name') if itype == 'Series' else (d or {}).get('title')) or ''
            if zh and zh != name and zh != orig:
                return zh
        except Exception:
            pass
    return orig if orig and orig != name else ''

_LANG_CN = {'en':'英语','zh':'中文','ja':'日语','ko':'韩语','fr':'法语','de':'德语','es':'西班牙语','ru':'俄语','it':'意大利语','pt':'葡萄牙语','hi':'印地语','th':'泰语','vi':'越南语','ar':'阿拉伯语','tr':'土耳其语','nl':'荷兰语','pl':'波兰语','sv':'瑞典语','da':'丹麦语','no':'挪威语','fi':'芬兰语','cs':'捷克语','el':'希腊语','he':'希伯来语','id':'印尼语','ms':'马来语','uk':'乌克兰语','hu':'匈牙利语','ro':'罗马尼亚语','bg':'保加利亚语','sr':'塞尔维亚语','hr':'克罗地亚语','sk':'斯洛伐克语','sl':'斯洛文尼亚语','lt':'立陶宛语','lv':'拉脱维亚语','et':'爱沙尼亚语','is':'冰岛语','ga':'爱尔兰语','fa':'波斯语','bn':'孟加拉语','ta':'泰米尔语','te':'泰卢固语','ml':'马拉雅拉姆语','kn':'卡纳达语','mr':'马拉地语','pa':'旁遮普语','ne':'尼泊尔语','my':'缅甸语','km':'高棉语','mn':'蒙古语','ka':'格鲁吉亚语','hy':'亚美尼亚语','az':'阿塞拜疆语','uz':'乌兹别克语','kk':'哈萨克语','sw':'斯瓦希里语','af':'南非荷兰语','tl':'他加禄语'}
_COUNTRY_CN = {'US':'美国','CN':'中国大陆','HK':'中国香港','TW':'中国台湾','MO':'中国澳门','JP':'日本','KR':'韩国','GB':'英国','FR':'法国','DE':'德国','ES':'西班牙','IT':'意大利','RU':'俄罗斯','CA':'加拿大','AU':'澳大利亚','IN':'印度','TH':'泰国','VN':'越南','MY':'马来西亚','SG':'新加坡','ID':'印尼','PH':'菲律宾','BR':'巴西','MX':'墨西哥','AR':'阿根廷','TR':'土耳其','NL':'荷兰','SE':'瑞典','NO':'挪威','DK':'丹麦','FI':'芬兰','PL':'波兰','CZ':'捷克','IE':'爱尔兰','NZ':'新西兰','BE':'比利时','CH':'瑞士','AT':'奥地利','PT':'葡萄牙','GR':'希腊','IL':'以色列','EG':'埃及','SA':'沙特','AE':'阿联酋','ZA':'南非','UA':'乌克兰','RO':'罗马尼亚','HU':'匈牙利','BG':'保加利亚','HR':'克罗地亚','RS':'塞尔维亚','SK':'斯洛伐克','SI':'斯洛文尼亚','LT':'立陶宛','LV':'拉脱维亚','EE':'爱沙尼亚','IS':'冰岛','LU':'卢森堡','MT':'马耳他','CY':'塞浦路斯'}
_STATUS_CN = {'Rumored':'传闻中','Planned':'计划中','In Production':'制作中','Post Production':'后期制作','Released':'已上映','Canceled':'已取消','Returning Series':'回归中','Ended':'已完结','Pilot':'试播'}

def _tmdb_enrich(tmdb_id, media_type):
    """TMDB 富化：status / 原始语言(中文) / 出品国家(中文) / 制作公司"""
    if not tmdb_id:
        return {}
    try:
        data = _tmdb_get(f"{media_type}/{tmdb_id}?append_to_response=release_dates")
        if not data or 'id' not in data:
            return {}
        countries = []
        for c in data.get('production_countries', [])[:3]:
            code = c.get('iso_3166_1', '')
            countries.append(_COUNTRY_CN.get(code, c.get('name', '') or code))
        return {
            'status': _STATUS_CN.get(data.get('status', ''), data.get('status', '')),
            'originalLanguage': _LANG_CN.get(data.get('original_language', ''), data.get('original_language', '')),
            'productionCountry': '、'.join(x for x in countries if x),
            'studios': [s.get('name', '') for s in data.get('production_companies', [])[:3]],
        }
    except Exception:
        return {}

def emby_detail(item_id, user_id=None):
    """获取影片详情"""
    try:
        r = emby_get(f"/Users/{_uid(user_id)}/Items/{item_id}?Fields=Overview,Runtime,ProductionYear,CommunityRating,Genres,Studios,People,OfficialRating,DisplayOrder,Tags,Bitrate,Path,ChildCount,RecursiveChildCount")
        it = r
        if not it or 'Id' not in it:
            return tmdb_detail(item_id)
        tags = it.get('ImageTags', {})
        runtime = it.get('RunTimeTicks', 0)
        runtime_min = int(runtime / 600000000) if runtime else 0
        people = [p for p in it.get('People', []) if isinstance(p, dict)]
        directors = [p for p in people if p.get('Type') == 'Director']
        writers = [p for p in people if p.get('Type') == 'Writer']
        actors = [p for p in people if p.get('Type') == 'Actor'][:8]
        itype = it.get('Type', '')
        name = it.get('Name', '') or ''
        orig = it.get('OriginalTitle', '') or ''
        tmdb_id = (it.get('ProviderIds', {}) or {}).get('Tmdb', '') or ''
        path = it.get('Path', '') or ''
        enr = _tmdb_enrich(tmdb_id, 'tv' if itype == 'Series' else 'movie') if tmdb_id else {}
        return {
            'id': str(item_id),
            'name': name,
            'originalTitle': orig,
            'officialName': _official_name(name, orig, tmdb_id, itype),
            'fileName': os.path.basename(path) if path else '',
            'year': it.get('ProductionYear', ''),
            'rating': round(it.get('CommunityRating', 0) or 0, 1),
            'officialRating': it.get('OfficialRating', ''),
            'runtime': runtime_min,
            'overview': it.get('Overview', ''),
            'genres': it.get('Genres',[]),
            'studios': ([s if isinstance(s, str) else s.get('Name','') for s in it.get('Studios',[])[:3]] or enr.get('studios', [])),
            'directors': [p.get('Name','') for p in directors[:3]],
            'writers': [p.get('Name','') for p in writers[:3]],
            'actors': [{'name': p.get('Name',''), 'role': p.get('Role',''), 'image': emby_poster(p.get('Id',''), p.get('PrimaryImageTag',''), 200) if p.get('PrimaryImageTag') else ''} for p in actors],
            'poster': emby_poster(item_id, tags.get('Primary'), 600),
            'backdrop': emby_poster(item_id, tags.get('Backdrop'), 1280),
            'type': it.get('Type', ''),
            'status': it.get('Status', '') or enr.get('status', ''),
            'originalLanguage': enr.get('originalLanguage', ''),
            'productionCountry': enr.get('productionCountry', ''),
            'premiereDate': it.get('PremiereDate', '')[:10] if it.get('PremiereDate') else '',
            'digitalRelease': '',
            'inLibrary': True,
            'tmdbId': it.get('ProviderIds', {}).get('Tmdb', ''),
            'imdbId': it.get('ProviderIds', {}).get('Imdb', ''),
            'tvdbId': it.get('ProviderIds', {}).get('Tvdb', ''),
            'seriesCount': _series_season_episode(item_id, user_id)[0] if it.get('Type') == 'Series' else (it.get('ChildCount', 0) or 0),
            'episodeCount': _series_episode_count(item_id, user_id) if (it.get('Type') == 'Series' and not (it.get('RecursiveChildCount') or 0)) else (it.get('RecursiveChildCount', 0) or 0),
        }
    except Exception as e:
        return tmdb_detail(item_id)

def tmdb_detail(tmdb_id):
    """TMDB 详情兜底（非 Emby 库内项目）"""
    try:
        data = _tmdb_get(f"movie/{tmdb_id}?append_to_response=credits,external_ids")
        is_tv = False
        if not data or 'id' not in data:
            data = _tmdb_get(f"tv/{tmdb_id}?append_to_response=credits,external_ids")
            if not data or 'id' not in data:
                return {'error': 'not found'}
            is_tv = True
        credits = data.get('credits', {})
        directors = [c['name'] for c in credits.get('crew', []) if c.get('job') == 'Director'][:3]
        actors = [{'name': c.get('name',''), 'role': c.get('character',''), 'image': 'https://image.tmdb.org/t/p/w200' + (c.get('profile_path') or '')} for c in credits.get('cast', [])[:8]]
        ext = data.get('external_ids', {})
        countries = []
        for c in data.get('production_countries', [])[:3]:
            code = c.get('iso_3166_1', '')
            countries.append(_COUNTRY_CN.get(code, c.get('name', '') or code))
        name = data.get('name', '') if is_tv else data.get('title', '')
        return {
            'id': str(tmdb_id),
            'name': name,
            'originalTitle': (data.get('original_name', '') if is_tv else data.get('original_title', '')),
            'officialName': (data.get('original_name', '') if is_tv else data.get('original_title', '')),
            'fileName': '',
            'year': ((data.get('first_air_date', '') if is_tv else data.get('release_date', '')) or '')[:4],
            'rating': round(data.get('vote_average', 0) or 0, 1),
            'runtime': data.get('runtime', 0),
            'overview': data.get('overview', ''),
            'genres': [g['name'] for g in data.get('genres', [])],
            'studios': [s['name'] for s in data.get('production_companies', [])[:3]],
            'directors': directors,
            'writers': [],
            'actors': actors,
            'poster': 'https://image.tmdb.org/t/p/w600' + (data.get('poster_path') or ''),
            'backdrop': 'https://image.tmdb.org/t/p/original' + (data.get('backdrop_path') or ''),
            'type': 'Series' if is_tv else 'Movie',
            'status': _STATUS_CN.get(data.get('status', ''), data.get('status', '')),
            'originalLanguage': _LANG_CN.get(data.get('original_language', ''), data.get('original_language', '')),
            'productionCountry': '、'.join(x for x in countries if x),
            'premiereDate': (data.get('first_air_date', '') if is_tv else data.get('release_date', '')),
            'digitalRelease': '',
            'inLibrary': False,
            'tmdbId': str(data.get('id', '')),
            'imdbId': ext.get('imdb_id', ''),
            'tvdbId': ext.get('tvdb_id', ''),
        }
    except Exception as e:
        return {'error': str(e)}

def _emby_count(parent_id, include_types='', user_id=None):
    """查某个库下指定类型的总数"""
    url = f"/Items?ParentId={parent_id}&Recursive=true&Limit=0"
    if include_types:
        url += f"&IncludeItemTypes={include_types}"
    url += f"&UserId={_uid(user_id)}"
    try:
        r = emby_get(url)
        return r.get('TotalRecordCount', 0)
    except Exception:
        return 0

def emby_library_stats(user_id=None):
    """层级化媒体库统计：电影/电视剧/音频 三大类"""
    try:
        views = emby_get(f"/Users/{_uid(user_id)}/Views")
        raw = []
        for v in views.get('Items', []):
            name = v.get('Name', '')
            ctype = v.get('CollectionType', '')
            item_id = v.get('ItemId', v.get('Id', ''))
            if not item_id or ctype == 'playlists':
                continue
            raw.append({'name': name, 'type': ctype, 'id': item_id})

        # ---- 电影大类 ----
        movie_libs = [l for l in raw if l['type'] == 'movies']
        movie_total = sum(_emby_count(l['id'], 'Movie', user_id) for l in movie_libs)
        movie_subs = [{'name': l['name'], 'count': _emby_count(l['id'], 'Movie', user_id)} for l in movie_libs]

        # ---- 电视剧大类：部数/季数/集数 ----
        # CollectionType=tvshows 的库，以及无类型但实际含 Series 的库（如纪录片）
        tv_libs = [l for l in raw if l['type'] == 'tvshows']
        for l in raw:
            if l['type'] == 'tvshows':
                continue
            if l['type'] in ('movies', 'music', 'audiobooks', 'homevideos', 'boxsets', 'playlists'):
                continue
            # 无类型的库，探一下是否有 Series
            if _emby_count(l['id'], 'Series', user_id) > 0:
                tv_libs.append(l)
        tv_subs = []
        tv_shows_total = 0
        tv_seasons_total = 0
        tv_episodes_total = 0
        for l in tv_libs:
            shows = _emby_count(l['id'], 'Series', user_id)
            seasons = _emby_count(l['id'], 'Season', user_id)
            episodes = _emby_count(l['id'], 'Episode', user_id)
            if shows == 0 and seasons == 0 and episodes == 0:
                continue
            tv_subs.append({'name': l['name'], 'shows': shows, 'seasons': seasons, 'episodes': episodes})
            tv_shows_total += shows
            tv_seasons_total += seasons
            tv_episodes_total += episodes

        # ---- 音频大类 ----
        music_libs = [l for l in raw if l['type'] == 'music']
        audio_total_songs = sum(_emby_count(l['id'], 'Audio', user_id) for l in music_libs)
        audio_albums = sum(_emby_count(l['id'], 'MusicAlbum', user_id) for l in music_libs)
        # 艺术家：全局 Artists
        try:
            artists_r = emby_get(f"/Artists?Recursive=true&Limit=0&UserId={_uid(user_id)}")
            audio_artists = artists_r.get('TotalRecordCount', 0)
        except Exception:
            audio_artists = 0

        audio_book_libs = [l for l in raw if l['type'] == 'audiobooks']
        ab_books = sum(_emby_count(l['id'], 'AudioBook', user_id) for l in audio_book_libs)
        ab_episodes = sum(_emby_count(l['id'], 'AudioBookChapter', user_id) for l in audio_book_libs)

        return {
            'libraries': [
                {
                    'category': '电影',
                    'icon': '🎥',
                    'total': movie_total,
                    'total_label': '部',
                    'subs': [{'name': s['name'], 'count': s['count'], 'unit': '部'} for s in movie_subs],
                },
                {
                    'category': '电视剧',
                    'icon': '📺',
                    'total': tv_shows_total,
                    'total_label': '部',
                    'extra': f'{tv_seasons_total} 季 · {tv_episodes_total} 集',
                    'subs': [{'name': s['name'], 'shows': s['shows'], 'seasons': s['seasons'], 'episodes': s['episodes']} for s in tv_subs],
                },
                {
                    'category': '音频',
                    'icon': '🎵',
                    'subs': [
                        {
                            'name': '音乐',
                            'songs': audio_total_songs,
                            'albums': audio_albums,
                            'artists': audio_artists,
                        },
                        {
                            'name': '有声书/剧',
                            'books': ab_books,
                            'chapters': ab_episodes,
                        },
                    ],
                },
            ],
        }
    except Exception as e:
        print(f"[warn] library_stats: {e}")
        return {'libraries': []}

# ========== qBittorrent ==========
qb_cookies = None
def qb_login():
    global qb_cookies
    try:
        data = urllib.parse.urlencode({'username': QB_USER, 'password': QB_PASS}).encode()
        req = urllib.request.Request(f"{QB_HOST}/api/v2/auth/login", data=data, method='POST')
        resp = urllib.request.urlopen(req, timeout=5)
        sc = resp.headers.get_all('Set-Cookie')
        if sc:
            qb_cookies = '; '.join([c.split(';')[0] for c in sc])
            return True
    except Exception as e:
        print(f"[warn] qb login: {e}")
    return False

def qb_request(path, retry=True):
    global qb_cookies
    if not qb_cookies and not qb_login():
        return None
    try:
        req = urllib.request.Request(f"{QB_HOST}{path}")
        req.add_header('Cookie', qb_cookies)
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry:
            qb_cookies = None
            return qb_request(path, retry=False)
    except Exception as e:
        print(f"[warn] qb {path}: {e}")
    return None

def qb_torrents():
    data = qb_request('/api/v2/torrents/info')
    if not data: return []
    # 并发取前15个种子的文件数（qBittorrent 单种子 files 接口）
    import concurrent.futures
    def _fc(t):
        try:
            fl = qb_request('/api/v2/torrents/files?hash=' + urllib.parse.quote(t.get('hash', '')))
            return len(fl) if isinstance(fl, list) else 0
        except Exception:
            return 0
    tops = data[:15]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fcs = list(ex.map(_fc, tops))
    return [{
        'name': t.get('name', ''),
        'state': t.get('state', ''),
        'progress': round(t.get('progress', 0) * 100, 1),
        'dlspeed': t.get('dlspeed', 0),
        'upspeed': t.get('upspeed', 0),
        'num_seeds': t.get('num_seeds', 0),
        'size': t.get('size', 0),
        'fileCount': fcs[i] if i < len(fcs) else 0,
    } for i, t in enumerate(tops)]

def qb_transfer():
    return qb_request('/api/v2/transfer/info') or {}

# ========== Transmission ==========
def tr_torrents(host, port, username='aoo', password='aa0772'):
    """Transmission RPC"""
    import base64
    try:
        url = f"http://{host}:{port}/transmission/rpc"
        auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        # 获取 session id
        req = urllib.request.Request(url)
        req.add_header('Authorization', f'Basic {auth}')
        try:
            urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as e:
            sid = e.headers.get('X-Transmission-Session-Id', '')
        else:
            sid = ''
        def _call(args):
            body = json.dumps({'method': 'torrent-get', 'arguments': args}).encode()
            r = urllib.request.Request(url, data=body, method='POST')
            r.add_header('Content-Type', 'application/json')
            r.add_header('Authorization', f'Basic {auth}')
            if sid: r.add_header('X-Transmission-Session-Id', sid)
            with urllib.request.urlopen(r, timeout=5) as rr:
                return json.loads(rr.read().decode())
        # 第一段：快速取列表（不带 files，避免 19s 大响应拖慢 /api/torrents）
        base = _call({'fields': ['id', 'name', 'percentDone', 'rateDownload', 'rateUpload', 'seeders', 'sizeWhenDone', 'status']})
        torrents = (base.get('arguments') or {}).get('torrents', [])
        tops = torrents[:15]
        fmap = {}
        ids = [t.get('id') for t in tops if t.get('id') is not None][:8]
        if ids:
            try:
                fd = _call({'ids': ids, 'fields': ['id', 'files']})
                for t in (fd.get('arguments') or {}).get('torrents', []):
                    fmap[t.get('id')] = len(t.get('files') or [])
            except Exception as fe:
                print(f"[warn] tr files {host}:{port}: {fe!r}")
        return [{
            'name': t.get('name', ''),
            'progress': round(t.get('percentDone', 0) * 100, 1),
            'dlspeed': t.get('rateDownload', 0),
            'upspeed': t.get('rateUpload', 0),
            'num_seeds': t.get('seeders', 0),
            'size': t.get('sizeWhenDone', 0),
            'fileCount': fmap.get(t.get('id'), 0),
        } for t in tops]
    except Exception as e:
        print(f"[warn] tr {host}:{port}: {e}")
        return []

def qb_global_limits():
    """qBittorrent 全局限速，单位 KiB/s（与 setDownloadLimit 一致）"""
    d = qb_request('/api/v2/transfer/globalLimit') or {}
    try:
        return {'dl': int(d.get('dl_limit', 0)), 'up': int(d.get('up_limit', 0))}
    except Exception:
        return {'dl': 0, 'up': 0}

def qb_set_limits(dl_kb=None, up_kb=None):
    """设置 qBittorrent 全局限速（KiB/s，0=不限）"""
    if dl_kb is not None:
        qb_request('/api/v2/transfer/setDownloadLimit?limit=' + str(int(dl_kb)))
    if up_kb is not None:
        qb_request('/api/v2/transfer/setUploadLimit?limit=' + str(int(up_kb)))

def _tr_rpc(host, port, method, arguments, username='aoo', password='aa0772'):
    import base64
    try:
        url = f"http://{host}:{port}/transmission/rpc"
        auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        req = urllib.request.Request(url)
        req.add_header('Authorization', f'Basic {auth}')
        try:
            urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as e:
            sid = e.headers.get('X-Transmission-Session-Id', '')
        else:
            sid = ''
        body = json.dumps({'method': method, 'arguments': arguments}).encode()
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('Authorization', f'Basic {auth}')
        if sid:
            req.add_header('X-Transmission-Session-Id', sid)
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[warn] tr rpc {host}:{port} {method}: {e}")
        return {}

def tr_session_limits(host, port, username='aoo', password='aa0772'):
    """Transmission 会话限速，单位 KiB/s，附启用开关"""
    r = _tr_rpc(host, port, 'session-get',
                {'fields': ['speed-limit-down', 'speed-limit-up',
                            'speed-limit-down-enabled', 'speed-limit-up-enabled']},
                username, password)
    a = r.get('arguments', {})
    return {
        'dl': int(a.get('speed-limit-down', 0)),
        'up': int(a.get('speed-limit-up', 0)),
        'dl_en': bool(a.get('speed-limit-down-enabled', False)),
        'up_en': bool(a.get('speed-limit-up-enabled', False)),
    }

def tr_set_limits(host, port, dl_kb=None, up_kb=None, username='aoo', password='aa0772'):
    """设置 Transmission 会话限速（KiB/s，0=不限）"""
    args = {}
    if dl_kb is not None:
        args['speed-limit-down'] = int(dl_kb)
        args['speed-limit-down-enabled'] = int(dl_kb) > 0
    if up_kb is not None:
        args['speed-limit-up'] = int(up_kb)
        args['speed-limit-up-enabled'] = int(up_kb) > 0
    if args:
        _tr_rpc(host, port, 'session-set', args, username, password)

# ========== 轮播源 ==========
def _emby_carousel_items(r):
    """统一从 Emby Items 响应提取轮播字段"""
    out = []
    for it in r.get('Items', []):
        tags = it.get('ImageTags', {})
        genres = it.get('Genres', [])
        extra = ''
        if it.get('Type') == 'Series':
            se = it.get('ChildCount', 0)
            ec = it.get('RecursiveItemCount', 0)
            if se: extra = f"{se}季" + (f" · {ec}集" if ec else '')
        elif it.get('Type') == 'Episode':
            sn = it.get('ParentIndexNumber', 0)
            en = it.get('IndexNumber', 0)
            if sn: extra = f"S{sn:02d}E{en:02d}" if en else f"第{sn}季"
        out.append({
            'id': it.get('Id', ''),
            'title': it.get('Name', ''),
            'year': str(it.get('ProductionYear', '')),
            'type': '电影' if it.get('Type') == 'Movie' else '剧集',
            'image': emby_poster(it.get('Id', ''), tags.get('Backdrop') or tags.get('Primary'), 1280),
            'rating': round(it.get('CommunityRating', 0) or 0, 1),
            'genres': ' / '.join(genres[:3]) if genres else '',
            'overview': (it.get('Overview', '') or '')[:150],
            'runtime': it.get('RuntimeTicks', 0) and int(it.get('RuntimeTicks', 0) / 600000000) or 0,
            'officialRating': it.get('OfficialRating', ''),
            'extra': extra,
        })
    return out

def carousel_recent(user_id=None):
    r = emby_get(f"/Items?SortBy=DateCreated&SortOrder=Descending&IncludeItemTypes=Movie,Series&Recursive=true&Limit=5&Fields=Overview,Genres,RuntimeTicks,OfficialRating,CommunityRating&UserId={_uid(user_id)}")
    return _emby_carousel_items(r)

def carousel_random(user_id=None):
    r = emby_get(f"/Items?SortBy=Random&SortOrder=Ascending&IncludeItemTypes=Movie,Series&Recursive=true&Limit=5&Fields=Overview,Genres,RuntimeTicks,OfficialRating,CommunityRating&UserId={_uid(user_id)}")
    return _emby_carousel_items(r)

def carousel_trending(user_id=None):
    r = emby_get(f"/Items?SortBy=CommunityRating&SortOrder=Descending&IncludeItemTypes=Movie,Series&Recursive=true&Limit=5&Fields=Overview,Genres,RuntimeTicks,OfficialRating,CommunityRating&UserId={_uid(user_id)}")
    return _emby_carousel_items(r)

TMDB_API_KEY = "db55323b8d3e4154498498a75642b381"

def _tmdb_get(path):
    sep = '&' if '?' in path else '?'
    url = f"https://api.themoviedb.org/3/{path}{sep}api_key={TMDB_API_KEY}&language=zh-CN"
    req = urllib.request.Request(url, headers={'User-Agent': 'NEXUS/1.0'})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[warn] tmdb: {e}")
        return {"results": []}

def carousel_tmdb():
    """TMDB 流行电影"""
    data = _tmdb_get("movie/popular")
    # genre id map
    gdata = _tmdb_get("genre/movie/list")
    gmap = {g['id']: g['name'] for g in gdata.get('genres', [])}
    out = []
    for it in data.get('results', [])[:5]:
        poster = "https://image.tmdb.org/t/p/original" + (it.get('backdrop_path') or it.get('poster_path') or '')
        genres = [gmap.get(gid, '') for gid in it.get('genre_ids', [])[:3]]
        out.append({
            'id': str(it.get('id', '')),
            'title': it.get('title', ''),
            'year': it.get('release_date', '')[:4],
            'type': '电影',
            'image': poster,
            'rating': round(it.get('vote_average', 0) or 0, 1),
            'overview': (it.get('overview', '') or '')[:150],
            'genres': ' / '.join([g for g in genres if g]),
            'runtime': 0,
        })
    return out

def carousel_douban():
    """豆瓣正在热映（爬豆瓣 nowplaying 页面）"""
    try:
        url = "https://movie.douban.com/cinema/nowplaying/liuzhou/"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        html = urllib.request.urlopen(req, timeout=10, context=ctx).read().decode("utf-8")
        # 解析每个 list-item
        items = re.findall(r'<li[^>]*data-title="([^"]*)"[^>]*data-score="([^"]*)"[^>]*data-release="([^"]*)"[^>]*data-subject="([^"]*)"', html)
        # 匹配对应图片
        imgs = re.findall(r'data-subject="(\d+)".*?<img[^>]*src="([^"]+)"', html, re.DOTALL)
        img_map = dict(imgs)
        out = []
        for title, score, year, sid in items[:5]:
            poster = img_map.get(sid, "")
            s = float(score) if score and score != "0" else 0
            out.append({
                'id': str(sid),
                'title': title,
                'year': year,
                'type': '电影',
                'image': poster,
                'rating': s,
            })
        if out:
            return out
    except Exception as e:
        print(f"[warn] douban scrape: {e}")
    # fallback: TMDB 中文
    try:
        data = _tmdb_get("discover/movie?sort_by=popularity.desc&with_original_language=zh")
        out = []
        for it in data.get('results', [])[:5]:
            poster = "https://image.tmdb.org/t/p/original" + (it.get('backdrop_path') or it.get('poster_path') or '')
            out.append({
                'id': str(it.get('id', '')),
                'title': it.get('title', ''),
                'year': it.get('release_date', '')[:4],
                'type': '电影',
                'image': poster,
                'rating': round(it.get('vote_average', 0) or 0, 1),
            })
        return out
    except Exception:
        return []

# ========== 最近入库 ==========
_HEALTH_POOL = [
    '每天喝够 1500-1700ml 水，少量多次，比一次猛灌更利健康。',
    '成年人每天睡 7-9 小时为宜；长期睡眠不足会增加代谢与心血管风险。',
    '久坐每 45-60 分钟起身活动 3 分钟，可显著缓解腰椎与颈椎压力。',
    '早餐补足蛋白质（蛋/奶/豆），有助于稳定全天血糖与精力。',
    '每日 30 分钟中等强度运动（快走/骑车）即可获得多数运动收益。',
    '屏幕每 20 分钟远眺 6 米外物体 20 秒，可有效缓解视疲劳。',
    '控盐是关键：成人每日食盐建议不超过 5 克，重口要克制。',
    '每餐先吃蔬菜再吃主食，餐后血糖更平稳，更易控制体重。',
    '鲜榨果汁含糖不低，直接吃整果更好——多纤维、少糖负担。',
    '洗澡水温 38-40℃ 为宜，过热会破坏皮肤屏障，加剧干燥。',
    '晒太阳每日 10-20 分钟（避开正午暴晒）助维生素 D 合成。',
    '用腹式深呼吸 5 分钟可快速平复情绪、降低心率与血压。',
    '饭后不立即剧烈运动，散步 15 分钟有助消化与血糖管理。',
    '久坐人群每小时起身，比集中大运动更能抵消久坐危害。',
    '咖啡因摄入建议：每天不超过 3 杯咖啡，下午 2 点后少喝防失眠。',
    '刷牙采用巴氏刷牙法，每次不少于 2 分钟，别遗漏牙龈沟。',
    '减重以每周 0.5-1 公斤为宜，过快反弹风险更高、肌肉流失更多。',
    '电子产品睡前 1 小时调低亮度，蓝光会抑制褪黑素分泌。',
    '感冒喝热水有效：热饮可缓解鼻塞，注意别烫到。',
    '冷萃≠低糖，含糖饮料无论冷热，对血糖影响都需留意。',
    '每日吃够 300-500g 蔬菜，深色蔬菜占一半，补足膳食纤维。',
    '步行是最被低估的运动：每天 8000 步，长期心血管获益明显。',
    '情绪与肠道相关：压力大时多吃发酵食品，有助菌群稳定。',
    '干眼人群可用 20-20-20 法则＋人工泪液，勿频繁揉眼。',
    '献血前要进食、补水，避免空腹；献血后不立即剧烈运动。',
    '睡前一小时放下手机，纸质阅读或轻拉伸有助更快入睡。',
    '喝茶别喝太烫：65℃ 以上热饮被列为可能致癌物，放温再喝。',
    '久站工作者可定时踮脚，促进下肢血液回流，缓解腿肿。',
    '坚果每天一小把（约 30g），不饱和脂肪酸有益心血管。',
    '膝盖保养：下蹲发力用臀腿肌肉，避免膝内扣，循序渐进运动。',
]

def daily_tips():
    """健康小知识：随机取 3 条轮播。注：丁香医生每日推送无公开接口（App 动态/签名），
    面板内置通用健康知识池保证稳定可用；如需丁香品牌内容请另行提供可访问来源。"""
    import random
    pick = random.sample(_HEALTH_POOL, 3)
    return [{'title': '今日健康小知识', 'content': t} for t in pick]

def recent_transfers(limit=12, user_id=None):
    try:
        conn = sqlite3.connect(MP_DB, timeout=3)
        c = conn.cursor()
        c.execute("SELECT title, year, type, category, image, date FROM transferhistory WHERE status=1 ORDER BY date DESC LIMIT ?", (limit,))
        rows = c.fetchall()
        conn.close()
        out = []
        for row in rows:
            title, year, typ, cat, image, date = row
            # 过滤音乐
            if cat and '音乐' in str(cat):
                continue
            if typ and '音乐' in str(typ):
                continue
            # 获取评分和Emby ID
            rating = 0
            emby_id = ''
            try:
                s = emby_get(f"/Users/{_uid(user_id)}/Items?searchTerm={urllib.parse.quote(title or '')}&IncludeItemTypes=Movie,Series&Recursive=true&Limit=1&Fields=CommunityRating,PrimaryImageAspectRatio")
                if s.get('Items'):
                    rating = round(s['Items'][0].get('CommunityRating', 0) or 0, 1)
                    emby_id = s['Items'][0].get('Id', '')
            except Exception:
                pass
            out.append({
                'title': title or '',
                'year': year or '',
                'type': typ or '',
                'date': date or '',
                'rating': rating,
                'image': image or '',
                'id': emby_id,
            })
        return out[:10]
    except Exception as e:
        print(f"[warn] recent: {e}")
        return []

# ========== 服务状态 ==========
def check_service(host, port, use_https=False, path='/'):
    """探测服务在线。不跟随重定向：任何 <500 的 HTTP 响应（200/301/302/401/403）都视为在线。
    公网域名（ioom.top）优先内网直连（hairpin 通常不通且慢）；直连失败后走代理网关兜底。"""
    import http.client, ssl
    def _direct(h, u):
        ctx = None
        if u:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if u:
            conn = http.client.HTTPSConnection(h, port, timeout=3, context=ctx)
        else:
            conn = http.client.HTTPConnection(h, port, timeout=3)
        conn.request('GET', path or '/', headers={'User-Agent': 'NexusDash/1.0'})
        resp = conn.getresponse()
        code = resp.status
        conn.close()
        return code < 500
    # 公网域名优先内网直连：内网服务本身是 http（https 是公网 nginx TLS 层），用 http 探测内网 IP
    candidates = [(SA_HOST, False), (host, use_https)] if host == 'ioom.top' else [(host, use_https)]
    for h, u in candidates:
        try:
            if _direct(h, u):
                return True
        except Exception as e:
            print(f"[check_service] direct fail host={h} port={port} https={u}: {e!r}", flush=True)
    # 兜底：走代理网关（公网/境外域名）
    scheme = 'https' if use_https else 'http'
    url = f"{scheme}://{host}:{port}{path}"
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'NexusDash/1.0')
        with _proxy_opener().open(req, timeout=5) as r:
            return r.status < 500
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False

def get_services():
    """SA6400 + DS220+ 服务状态（端口已通过 docker ps + netstat 实测核实，支持 config.json 覆盖）"""
    services = [
        {'name': 'Emby', 'host': SA_HOST, 'port': 16880, 'use_https': False, 'device': 'SA', 'desc': '媒体串流', 'icon': '🎬', 'public': 'https://ioom.top:16881'},
        {'name': 'MP', 'host': SA_HOST, 'port': 16995, 'use_https': False, 'device': 'SA', 'desc': '自动化整理', 'icon': '🤖', 'public': 'https://ioom.top:16995'},
        {'name': 'QB', 'host': SA_HOST, 'port': 16058, 'use_https': False, 'device': 'SA', 'desc': '下载客户端', 'icon': '⬇️', 'public': 'https://ioom.top:16058'},
        {'name': 'TR', 'host': SA_HOST, 'port': 16062, 'use_https': False, 'device': 'SA', 'desc': '下载客户端', 'icon': '📡', 'public': 'https://ioom.top:16062'},
        {'name': '代理', 'host': SA_HOST, 'port': 16001, 'use_https': False, 'device': 'SA', 'desc': '代理网关', 'icon': '🌐', 'public': 'http://ioom.top:16001'},
        {'name': '网盘搜索', 'host': SA_HOST, 'port': 18666, 'use_https': False, 'device': 'SA', 'desc': '网盘搜索', 'icon': '📦', 'public': 'http://ioom.top:18666'},
        {'name': '面板', 'host': '127.0.0.1', 'port': 28080, 'use_https': False, 'device': 'SA', 'desc': '本面板', 'icon': '🖥️', 'public': ''},
        {'name': '云盘挂载', 'host': SA_HOST, 'port': 16057, 'use_https': False, 'device': 'SA', 'desc': '网盘挂载', 'icon': '💾', 'public': 'https://ioom.top:16057'},
        {'name': 'AdGuard', 'host': SA_HOST, 'port': 18080, 'use_https': False, 'device': 'SA', 'desc': 'DNS 过滤', 'icon': '🛡️', 'public': 'http://ioom.top:18080'},
        {'name': 'IYUU', 'host': SA_HOST, 'port': 11311, 'use_https': False, 'device': 'SA', 'desc': '辅种', 'icon': '🌱', 'public': 'http://ioom.top:11311'},
        {'name': '有声书', 'host': SA_HOST, 'port': 16887, 'use_https': False, 'device': 'SA', 'desc': '有声书', 'icon': '📚', 'public': 'https://ioom.top:16887'},
        {'name': 'TR-DS', 'host': DS220_HOST, 'port': 16666, 'use_https': False, 'device': 'DS', 'desc': '下载客户端', 'icon': '🖥️', 'public': ''},
    ]
    # 用户自定义监控服务（config monitor_services，可增删改查）
    for cs in (CONFIG.get('monitor_services', []) or []):
        if not cs.get('name'):
            continue
        services.append({
            'name': cs.get('name', ''),
            'host': cs.get('host', SA_HOST),
            'port': int(cs.get('port', 80) or 80),
            'use_https': (cs.get('proto', 'http') == 'https'),
            'device': cs.get('device', 'SA'),
            'desc': cs.get('desc', ''),
            'icon': cs.get('icon', '📦'),
            'public': cs.get('public', ''),
            'custom': True,
        })
    # 应用前端配置覆盖（端口 + 协议 + 别名）
    overrides = CONFIG.get('svc_ports', {})
    aliases = CONFIG.get('svc_aliases', {})
    for s in services:
        ov = overrides.get(s['name'])
        if ov:
            s['host'] = ov.get('host', s['host'])
            s['port'] = int(ov.get('port', s['port']))
            proto = ov.get('proto', '')
            if proto:
                s['use_https'] = (proto == 'https')
    result = []
    # 并行健康检查：串行 3s×N 曾达 34s 超过前端 30s 超时（服务监控卡片空白根因），改为并发，总耗时≈最慢服务
    ok_map = {}
    def _probe(s):
        if s['name'] == '面板':
            ok_map[s['name']] = True
        else:
            try:
                ok_map[s['name']] = check_service(s['host'], s['port'], s['use_https'])
            except Exception:
                ok_map[s['name']] = False
    _ths = [threading.Thread(target=_probe, args=(s,)) for s in services]
    for _t in _ths:
        _t.start()
    for _t in _ths:
        _t.join()
    for s in services:
        ok = ok_map.get(s['name'], False)
        public_url = s.get('public', '')
        if s['name'] == '面板':
            public_url = ''
        _DISPLAY_FIX = {'MP': '媒体自动化', 'TR-DS': 'TR'}
        display_name = _DISPLAY_FIX.get(s['name']) or aliases.get(s['name'], s['name'])
        proto = 'https' if s['use_https'] else 'http'
        result.append({
            'name': display_name,
            'key': s['name'],
            'device': s['device'],
            'group': s['device'],
            'desc': s['desc'],
            'port': s['port'],
            'icon': s.get('icon', '📦'),
            'url': public_url or f"{proto}://{s['host']}:{s['port']}",
            'status': 'online' if ok else 'offline',
        })
    return result

# ========== 脚本与活动日志 ==========
def get_crontab_scripts():
    """读取 /etc/cron.d/ 下的定时脚本"""
    scripts = []
    try:
        import os, glob
        for f in glob.glob('/etc/cron.d/*'):
            if f.endswith('.bak') or 'synosched' in f or 'spacereclaim' in f:
                continue
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split()
                    if len(parts) >= 6:
                        schedule = ' '.join(parts[:5])
                        cmd = ' '.join(parts[5:])
                        name = os.path.basename(f)
                        scripts.append({'name': name, 'schedule': schedule, 'command': cmd[:120]})
    except Exception:
        pass
    return scripts[:30]

def get_recent_events():
    """从 MP user.db 读最近整理入库记录（MoviePilot transferhistory）"""
    events = []
    try:
        import sqlite3
        db = CONFIG.get('mp_db') or '/volume1/docker/mp/config/user.db'
        conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=3)
        cur = conn.cursor()
        cur.execute("SELECT title, type, category, date FROM transferhistory ORDER BY date DESC LIMIT 15")
        for row in cur.fetchall():
            events.append({
                'title': row[0], 'type': row[1],
                'category': row[2], 'date': row[3]
            })
        conn.close()
    except Exception:
        pass
    return events

# ========== HTTP Handler ==========
class Handler(BaseHTTPRequestHandler):
    def _security_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-XSS-Protection', '1; mode=block')

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _get_client_ip(self):
        return self.client_address[0]

    def _extract_token(self):
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return auth[7:]
        cookie = self.headers.get('Cookie', '')
        for part in cookie.split(';'):
            part = part.strip()
            if part.startswith('nexus_token='):
                return part[len('nexus_token='):]
        return None

    def _require_auth(self):
        """Returns session dict if authenticated, None otherwise."""
        if not CONFIG.get('auth_enabled', True):
            return {'username': CONFIG.get('auth_username', 'admin')}
        token = self._extract_token()
        s = check_session(token)
        if s:
            return s
        return None

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def do_POST(self):
        path = self.path.split('?')[0]

        # 登录（公开）
        if path == '/api/login':
            ip = self._get_client_ip()
            if not check_rate_limit(ip):
                self.send_json({'error': '尝试次数过多，请 5 分钟后再试'}, code=429)
                return
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(body)
            except Exception:
                self.send_json({'error': '无效请求'}, code=400)
                return
            username = data.get('username', '')
            password = data.get('password', '')
            uinfo = find_user(username)
            phash = user_password_hash(username)
            if uinfo and phash and hashlib.sha256(password.encode()).hexdigest() == phash:
                clear_attempts(ip)
                role = uinfo['role']
                token = create_session(username, role)
                self.send_json({
                    'ok': True, 'token': token, 'username': username,
                    'role': role, 'is_admin': uinfo['is_admin'],
                    'permissions': get_role(role)['permissions'],
                })
            else:
                record_failed_attempt(ip)
                self.send_json({'error': '用户名或密码错误'}, code=401)
            return

        # 登出
        if path == '/api/logout':
            token = self._extract_token()
            if token:
                destroy_session(token)
            self.send_json({'ok': True})
            return

        if path == '/cookiecloud/sync':
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    _cc_cfg = json.load(f).get('cookiecloud', {})
                if not _cc_cfg.get('enabled', True):
                    self.send_json({'success': False, 'error': 'CookieCloud 接收已在设置中关闭'}, code=403)
                    return
            except Exception:
                pass
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8', errors='ignore') if length else ''
            post_data = urllib.parse.parse_qs(body)
            password = post_data.get('password', [''])[0]
            cookie_raw = post_data.get('cookie_data', [''])[0]
            try:
                cookie_data = json.loads(cookie_raw) if cookie_raw else {}
            except Exception:
                cookie_data = {}
            existing = load_cookies()
            existing.update(cookie_data)
            save_cookies(existing)
            updated = update_mp_sites_from_cookies(cookie_data)
            self.send_json({'success': True, 'domains': len(cookie_data), 'mp_updated': updated})
        elif path == '/api/favorites':
            s = self._require_auth()
            if not s:
                self.send_json({'error': '未授权'}, code=401)
                return
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8', errors='ignore') if length else '{}'
            try:
                data = json.loads(body) if body else {}
            except Exception:
                data = {}
            favs = load_favs()
            action = data.get('action')
            if action == 'add':
                item = data.get('item') or {}
                if item.get('id') and not any(f.get('id') == item.get('id') for f in favs):
                    favs.insert(0, item)
                    save_favs(favs)
            elif action == 'remove':
                fid = data.get('id')
                favs = [f for f in favs if f.get('id') != fid]
                save_favs(favs)
            self.send_json({'ok': True, 'favorites': favs})
        elif path == '/api/config':
            # 设置保存需要认证 + settings 权限
            s = self._require_auth()
            if not s:
                self.send_json({'error': '未授权'}, code=401)
                return
            role = s.get('role', 'admin')
            if not _has_perm(get_role(role)['permissions'], 'settings:manage') and not _has_perm(get_role(role)['permissions'], 'settings:rbac'):
                self.send_json({'error': '无权限修改配置'}, code=403)
                return
            self._handle_config_post()
        elif path == '/api/limits':
            # 修改下载器限速：需要 transfers 权限
            s = self._require_auth()
            if not s:
                self.send_json({'error': '未授权'}, code=401)
                return
            role = s.get('role', 'admin')
            if not _has_perm(get_role(role)['permissions'], 'transfers:limit'):
                self.send_json({'error': '无权限修改限速'}, code=403)
                return
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(body)
            except Exception:
                self.send_json({'error': '无效请求'}, code=400)
                return
            engine = data.get('engine', 'qb')
            dl_kb = data.get('dl_kb')
            up_kb = data.get('up_kb')
            if dl_kb is not None: dl_kb = round(float(dl_kb))
            if up_kb is not None: up_kb = round(float(up_kb))
            try:
                if engine == 'qb':
                    qb_set_limits(dl_kb, up_kb)
                elif engine == 'tr_sa':
                    tr_set_limits(SA_HOST, 16062, dl_kb, up_kb)
                elif engine == 'tr_ds':
                    tr_set_limits(DS220_HOST, 16666, dl_kb, up_kb)
                else:
                    self.send_json({'error': '未知引擎'}, code=400)
                    return
            except Exception as e:
                self.send_json({'error': '设置限速失败: ' + str(e)}, code=500)
                return
            self.send_json({'ok': True})

        elif path == '/api/scripts/run':
            s = self._require_auth()
            if not s:
                self.send_json({'error': '未授权'}, code=401)
                return
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(body)
            except Exception:
                self.send_json({'error': '无效请求'}, code=400)
                return
            name = data.get('name', '')
            result = run_script(name)
            self.send_json(result)

        else:
            self.send_json({'error': 'not found'}, code=404)

    def do_GET(self):
        path = self.path.split('?')[0]
        params = urllib.parse.parse_qs(self.path.split('?')[1]) if '?' in self.path else {}

        # 认证门禁：除登录接口和静态文件外，所有 API 需要会话
        if path.startswith('/api/') and path not in ('/api/login', '/api/img'):
            s = self._require_auth()
            if not s:
                self.send_json({'error': 'unauthorized'}, code=401)
                return
            role = s.get('role', 'admin')
            rdef = get_role(role)
            self._current_user = s.get('username', '')
            self._current_role = role
            # RBAC 关闭时放开全部权限与默认媒体范围
            rbac_on = bool((CONFIG.get('rbac') or {}).get('enabled', True))
            if rbac_on:
                self._current_perms = rdef['permissions']
                self._current_emby_user = rdef['emby_user'] or EMBY_USER_ID
            else:
                self._current_perms = list(ALL_PERMS)
                self._current_emby_user = EMBY_USER_ID
            # 权限门禁
            perm = endpoint_perm(path)
            if perm and not _has_perm(self._current_perms, perm):
                self.send_json({'error': '无权限访问此功能', 'required': perm}, code=403)
                return

        if path == '/api/status':
            day_rx, day_tx = _get_day_traffic()
            self.send_json({
                'cpu': get_cpu_usage(),
                'memory': get_memory_usage(),
                'net_down': _net_cache['down'],
                'net_up': _net_cache['up'],
                'cpu_temp': get_cpu_temp(),
                'day_down': round(day_rx / (1024**3), 1),
                'day_up': round(day_tx / (1024**3), 1),
                'uptime': get_uptime(),
            })

        elif path == '/api/scripts':
            scripts = scan_scripts()
            runs = _load_script_runs()
            for s in scripts:
                r = runs.get(s['name'], {})
                s['last_run'] = r.get('last_run')
                s['exit_code'] = r.get('exit_code')
                s['duration'] = r.get('duration')
                s['output_tail'] = r.get('output_tail', '')
                s['error_tail'] = r.get('error_tail', '')
            self.send_json({'scripts': scripts, 'dir': SCRIPTS_DIR, 'total': len(scripts)})

        elif path == '/api/storage':
            du = get_disk_usage()
            self.send_json({'pools': du['pools'], 'devices': du['devices']})

        elif path == '/api/torrents':
            torrents = qb_torrents()
            transfer = qb_transfer()
            tr_sa = tr_torrents(SA_HOST, 16062)
            tr_ds = tr_torrents(DS220_HOST, 16666)
            qb_lim = qb_global_limits()
            sa_lim = tr_session_limits(SA_HOST, 16062)
            ds_lim = tr_session_limits(DS220_HOST, 16666)
            def _sum(field, items):
                return sum((t.get(field) or 0) for t in items)
            self.send_json({
                'qb': {
                    'torrents': torrents,
                    'agg_dl': transfer.get('dl_info_speed', 0),
                    'agg_up': transfer.get('up_info_speed', 0),
                    'dl_limit': qb_lim['dl'], 'up_limit': qb_lim['up'],
                },
                'tr_sa': {
                    'torrents': tr_sa,
                    'agg_dl': _sum('dlspeed', tr_sa), 'agg_up': _sum('upspeed', tr_sa),
                    'dl_limit': sa_lim['dl'], 'up_limit': sa_lim['up'],
                },
                'tr_ds': {
                    'torrents': tr_ds,
                    'agg_dl': _sum('dlspeed', tr_ds), 'agg_up': _sum('upspeed', tr_ds),
                    'dl_limit': ds_lim['dl'], 'up_limit': ds_lim['up'],
                },
            })

        elif path == '/api/media':
            self.send_json(emby_library_stats(self._current_emby_user))

        elif path == '/api/recent':
            self.send_json({'items': recent_transfers(12, self._current_emby_user)})

        elif path == '/api/daily_tips':
            self.send_json({'tips': daily_tips()})

        elif path == '/api/carousel':
            src = params.get('src', ['random'])[0]
            eu = self._current_emby_user
            if src == 'recent':
                items = carousel_recent(eu)
            elif src == 'trending':
                items = carousel_trending(eu)
            elif src == 'tmdb':
                items = carousel_tmdb()
            elif src == 'douban':
                items = carousel_douban()
            else:
                items = carousel_random(eu)
            self.send_json({'items': items, 'source': src})

        elif path == '/api/search/emby':
            q = params.get('q', [''])[0]
            self.send_json({'results': emby_search(q, self._current_emby_user) if q else []})

        elif path == '/api/search/pt':
            q = params.get('q', [''])[0]
            ex = params.get('exclude', [''])[0]
            if not q:
                self.send_json({'results': []})
            else:
                results = pt_search(q, ex)
                self.send_json({'results': results})

        elif path == '/api/search/pansou':
            q = params.get('q', [''])[0]
            if not q:
                self.send_json({'results': []})
            else:
                try:
                    url = 'http://127.0.0.1:18666/api/search?keyword=' + urllib.parse.quote(q)
                    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
                    with urllib.request.urlopen(req, timeout=45) as r:
                        data = json.loads(r.read().decode('utf-8'))
                    # 扁平化 merged_by_type：先按关键词过滤（18666 返回模糊大列表），再每类截断，避免无关条目挤占
                    results = []
                    merged = data.get('data', {}).get('merged_by_type', {})
                    PER = 30
                    ql = q.lower()
                    for ptype, items in merged.items():
                        matched = [it for it in (items or []) if ql in (it.get('note', '') or '').lower()]
                        if not matched:
                            matched = (items or [])[:3]  # 关键词无精确命中时保留少量该类结果兜底
                        for item in matched[:PER]:
                            results.append({
                                'title': item.get('note', ''),
                                'url': item.get('url', ''),
                                'password': item.get('password', ''),
                                'source': item.get('source', ptype),
                                'date': item.get('datetime', '')[:10],
                                'type': ptype,
                            })
                        if len(results) >= 300:
                            break
                    self.send_json({'results': results[:300]})
                except Exception as e:
                    self.send_json({'results': [], 'error': str(e)})

        elif path == '/api/search/online':
            q = params.get('q', [''])[0]
            if not q:
                self.send_json({'results': []})
            else:
                results = _search_online(q)
                self.send_json({'results': results})

        elif path == '/api/trending':
            platform = params.get('p', ['all'])[0]
            self.send_json({'data': _get_trending(platform)})

        elif path.startswith('/api/detail/'):
            item_id = path.split('/api/detail/')[1]
            self.send_json(emby_detail(item_id, self._current_emby_user))

        elif path == '/api/favorites':
            self.send_json({'favorites': load_favs()})
            return
        elif path == '/api/features':
            feats = {}
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                feats = raw.get('features', {}) or {}
            except Exception:
                pass
            self.send_json({'features': feats})

        elif path == '/api/apps':
            self.send_json({'apps': get_services()})

        elif path == '/api/test-service':
            host = params.get('host', [''])[0]
            port = int(params.get('port', ['80'])[0])
            proto = params.get('proto', ['http'])[0]
            ok = check_service(host, port, proto == 'https')
            print(f"[ts] host={host} port={port} proto={proto} -> {ok}", flush=True)
            self.send_json({'ok': ok, 'host': host, 'port': port, 'proto': proto})

        elif path == '/api/scripts':
            self.send_json({'scripts': get_crontab_scripts()})

        elif path == '/api/events':
            self.send_json({'events': get_recent_events()})

        elif path == '/cookiecloud/cookies':
            # MP 插件或其他客户端拉取所有 Cookie
            self.send_json(load_cookies())

        elif path == '/cookiecloud/status':
            cookies = load_cookies()
            self.send_json({'domains': len(cookies), 'last_sync': os.path.getmtime(COOKIES_PATH) if os.path.exists(COOKIES_PATH) else 0})

        elif path == '/api/config':
            if self.command == 'GET':
                # 脱敏返回
                cfg_out = {
                    'features': {
                        'trending': True, 'online_search': True, 'pt_search': True,
                        'pansou_search': True, 'carousel': True,
                    },
                }
                try:
                    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                        raw = json.load(f)
                    svc = raw.get('services', {})
                    cfg_out.update({
                        'emby_host': svc.get('emby',{}).get('host',''),
                        'emby_api_key': svc.get('emby',{}).get('api_key',''),
                        'qb_host': svc.get('qbittorrent',{}).get('host',''),
                        'qb_user': svc.get('qbittorrent',{}).get('username',''),
                        'qb_pass': svc.get('qbittorrent',{}).get('password',''),
                        'mp_db': svc.get('moviepilot',{}).get('db_path',''),
                        'pansou_url': svc.get('pansou',{}).get('url',''),
                        'mihomo_proxy': svc.get('proxy',{}).get('mihomo',''),
                        'cookiecloud_password': raw.get('cookiecloud',{}).get('password',''),
                        'cookiecloud_enabled': raw.get('cookiecloud',{}).get('enabled', True),
                    })
                    if 'features' in raw:
                        cfg_out['features'] = raw['features']
                    cfg_out['volume_aliases'] = raw.get('volume_aliases', {})
                    cfg_out['svc_aliases'] = raw.get('svc_aliases', {})
                    cfg_out['favicon'] = raw.get('favicon', '')
                    cfg_out['monitor_services'] = raw.get('monitor_services', [])
                except Exception:
                    pass
                cfg_out['auth_enabled'] = CONFIG.get('auth_enabled', True)
                cfg_out['auth_username'] = CONFIG.get('auth_username', 'admin')
                # RBAC 配置（脱敏：不含任何密码）
                rbac = CONFIG.get('rbac', {}) or {}
                cfg_out['rbac_enabled'] = bool(rbac.get('enabled', True))
                cfg_out['rbac_roles'] = rbac.get('roles', {}) or {}
                cfg_out['rbac_users'] = [
                    {'username': u.get('username', ''), 'role': u.get('role', 'member')}
                    for u in (rbac.get('users', []) or [])
                ]
                self.send_json(cfg_out)

        elif path == '/api/me':
            token = self._extract_token()
            s = check_session(token) if token else None
            if s:
                role = s.get('role', 'admin')
                rdef = get_role(role)
                self.send_json({
                    'username': s['username'], 'authenticated': True,
                    'role': role, 'is_admin': role == 'admin',
                    'permissions': rdef['permissions'],
                    'rbac_enabled': bool((CONFIG.get('rbac') or {}).get('enabled', True)),
                })
            else:
                self.send_json({'authenticated': False}, code=401)

        elif path == '/api/restart':
            import sys
            self.send_json({'ok': True, 'msg': 'restarting'})
            self.wfile.flush()
            def _do_restart():
                time.sleep(0.5)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            threading.Thread(target=_do_restart, daemon=True).start()

        elif path == '/api/img':
            url = params.get('url', [''])[0]
            allowed_hosts = ('10.31.0.88', 'doubanio.com', 'hdslb.com', 'douban.com', 'tmdb.org', 'image.tmdb.org')
            if not url or not any(h in url for h in allowed_hosts):
                self.send_json({'error': 'bad url'}, code=400)
                return
            # catbox 图床缓存：同一图片只上传一次，命中直接 302 到稳定直链（不占本地、避免第三方图床 403）
            key = 'cat:' + hashlib.md5(url.encode()).hexdigest()
            cmap = _load_catbox_map()
            if key in cmap and cmap[key]:
                self.send_response(302)
                self.send_header('Location', cmap[key])
                self.send_header('Cache-Control', 'public, max-age=86400')
                self._security_headers()
                self.end_headers()
                return
            try:
                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
                # 豆瓣图床防盗链需要 Referer；内网 emby 直连，外部图床走代理网关
                if 'douban' in url:
                    req.add_header('Referer', 'https://movie.douban.com/')
                    opener = _proxy_opener()
                else:
                    opener = urllib.request.build_opener()
                with opener.open(req, timeout=10) as r:
                    data = r.read()
                    ct = r.headers.get('Content-Type', 'image/jpeg')
                # 尝试上传到 catbox 并缓存直链
                try:
                    fname = 'n' + key[4:14] + '.jpg'
                    cu = _catbox_upload(data, fname)
                    if cu.startswith('http'):
                        cmap[key] = cu
                        _save_catbox_map(cmap)
                        self.send_response(302)
                        self.send_header('Location', cu)
                        self.send_header('Cache-Control', 'public, max-age=86400')
                        self._security_headers()
                        self.end_headers()
                        return
                except Exception:
                    pass  # 上传失败则退回原图流
                self.send_response(200)
                self.send_header('Content-Type', ct)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'public, max-age=86400')
                self._security_headers()
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_json({'error': str(e)}, code=502)

        else:
            _MIME = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.svg':'image/svg+xml','.ico':'image/x-icon','.css':'text/css','.js':'application/javascript','.json':'application/json','.txt':'text/plain','.html':'text/html'}
            _base = os.path.normpath(BASE_DIR)
            _fpath = os.path.normpath(os.path.join(_base, path.lstrip('/')))
            _ext = os.path.splitext(_fpath)[1].lower()
            if os.path.isfile(_fpath) and _fpath.startswith(_base) and _ext in _MIME:
                with open(_fpath, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', _MIME[_ext])
                self.send_header('Content-Length', str(len(content)))
                self._security_headers()
                self.end_headers()
                self.wfile.write(content)
                return
            try:
                with open(os.path.join(BASE_DIR, 'index.html'), 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self._security_headers()
                self.end_headers()
                self.wfile.write(content)
            except Exception:
                self.send_json({'error': 'Not found'}, code=404)

    def _handle_config_post(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
            except Exception:
                # config.json 尚不存在或损坏：从空配置开始
                raw = {}
            if 'services' not in raw: raw['services'] = {}
            raw['services'].setdefault('emby', {})
            raw['services'].setdefault('qbittorrent', {})
            raw['services'].setdefault('moviepilot', {})
            raw['services'].setdefault('pansou', {})
            raw['services'].setdefault('proxy', {})
            raw.setdefault('cookiecloud', {})
            raw.setdefault('auth', {})
            if 'emby_host' in data: raw['services']['emby']['host'] = data['emby_host']
            if 'emby_api_key' in data: raw['services']['emby']['api_key'] = data['emby_api_key']
            if 'qb_host' in data: raw['services']['qbittorrent']['host'] = data['qb_host']
            if 'qb_user' in data: raw['services']['qbittorrent']['username'] = data['qb_user']
            if 'qb_pass' in data: raw['services']['qbittorrent']['password'] = data['qb_pass']
            if 'mp_db' in data: raw['services']['moviepilot']['db_path'] = data['mp_db']
            if 'pansou_url' in data: raw['services']['pansou']['url'] = data['pansou_url']
            if 'mihomo_proxy' in data: raw['services']['proxy']['mihomo'] = data['mihomo_proxy']
            if 'cookiecloud_password' in data: raw['cookiecloud']['password'] = data['cookiecloud_password']
            if 'cookiecloud_enabled' in data: raw['cookiecloud']['enabled'] = bool(data['cookiecloud_enabled'])
            if 'features' in data:
                raw.setdefault('features', {})
                for k, v in data['features'].items():
                    raw['features'][k] = bool(v)
            if 'volume_aliases' in data:
                raw['volume_aliases'] = data['volume_aliases']
            if 'svc_aliases' in data:
                raw['svc_aliases'] = data['svc_aliases']
            if 'svc_ports' in data: raw['svc_ports'] = data['svc_ports']
            if 'favicon' in data: raw['favicon'] = data['favicon']
            if 'monitor_services' in data: raw['monitor_services'] = data['monitor_services']
            # 认证设置
            if 'auth_username' in data:
                raw['auth']['username'] = data['auth_username']
            if 'auth_enabled' in data:
                raw['auth']['enabled'] = bool(data['auth_enabled'])
            if 'auth_password' in data and data['auth_password']:
                raw['auth']['password_hash'] = hashlib.sha256(data['auth_password'].encode()).hexdigest()
            # RBAC 设置
            if any(k in data for k in ('rbac_enabled', 'rbac_roles', 'rbac_users')):
                raw.setdefault('rbac', {})
                if 'rbac_enabled' in data:
                    raw['rbac']['enabled'] = bool(data['rbac_enabled'])
                if 'rbac_roles' in data:
                    raw['rbac']['roles'] = data['rbac_roles'] or {}
                if 'rbac_users' in data:
                    old_users = raw['rbac'].get('users', []) or []
                    users = []
                    for u in data['rbac_users']:
                        uname = (u.get('username') or '').strip()
                        if not uname:
                            continue
                        nu = {'username': uname, 'role': u.get('role', 'member')}
                        if u.get('password'):
                            nu['password_hash'] = hashlib.sha256(u['password'].encode()).hexdigest()
                        else:
                            old = next((o for o in old_users if o.get('username') == uname), None)
                            if old and old.get('password_hash'):
                                nu['password_hash'] = old['password_hash']
                        users.append(nu)
                    raw['rbac']['users'] = users
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            global CONFIG
            CONFIG = load_config()
            self.send_json({'ok': True})
        except Exception as e:
            self.send_json({'error': str(e)}, code=500)

    def log_message(self, *args):
        pass

if __name__ == '__main__':
    import socket
    import socketserver
    # P0 修复：原 class S 继承 socketserver.TCPServer 是【单线程】服务器，
    # 任意慢上游请求（PT 120s / 在线搜索 90s / trending 30s）都会阻塞整个服务
    # → backlog 满、全站冻结。现改为顶部导入的 ThreadingHTTPServer（每请求一线程）
    # + 并发信号量限流（超限连接立即关闭，快速失败，防止慢请求无限堆积线程耗尽）。
    _sem = threading.BoundedSemaphore(24)
    class S(HTTPServer):
        allow_reuse_address = True
        daemon_threads = True
        request_queue_size = 64
        def process_request(self, request, client_address):
            if _sem.acquire(blocking=False):
                threading.Thread(target=self._handle, args=(request, client_address), daemon=True).start()
            else:
                try:
                    request.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    request.close()
                except Exception:
                    pass
        def _handle(self, request, client_address):
            try:
                self.finish_request(request, client_address)
            except Exception:
                pass
            finally:
                try:
                    self.shutdown_request(request)
                except Exception:
                    pass
                _sem.release()
    print(f"NEXUS API on :{API_PORT}")
    S(('0.0.0.0', API_PORT), Handler).serve_forever()
