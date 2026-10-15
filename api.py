#!/usr/bin/env python3
"""Liquid Glass Dashboard 数据服务 - 提供 JSON API 给前端页面调用"""

import json
import os
import time
import subprocess
import sqlite3
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

# ========== 加载配置 ==========
def load_config():
    """从 config.json 读取配置,失败则用默认值"""
    config = {
        'emby_host': 'http://<NAS-IP>:16880',
        'emby_api_key': '<your-emby-api-key>',
        'qb_host': 'http://<NAS-IP>:16058',
        'qb_user': '<username>',
        'qb_pass': '<password>',
        'mp_db_path': '/volume1/docker/mp/config/user.db',
    }
    try:
        with open('config.json') as f:
            cfg = json.load(f)
            if 'services' in cfg:
                s = cfg['services']
                if 'emby' in s:
                    config['emby_host'] = s['emby'].get('host', config['emby_host'])
                    config['emby_api_key'] = s['emby'].get('api_key', config['emby_api_key'])
                if 'qbittorrent' in s:
                    config['qb_host'] = s['qbittorrent'].get('host', config['qb_host'])
                    config['qb_user'] = s['qbittorrent'].get('username', config['qb_user'])
                    config['qb_pass'] = s['qbittorrent'].get('password', config['qb_pass'])
                if 'moviepilot' in s:
                    config['mp_db_path'] = s['moviepilot'].get('db_path', config['mp_db_path'])
    except Exception as e:
        print(f"读取 config.json 失败: {e}, 使用默认值")
    return config

CONFIG = load_config()
EMBY_HOST = CONFIG['emby_host']
EMBY_API_KEY = CONFIG['emby_api_key']
QB_HOST = CONFIG['qb_host']
QB_USER = CONFIG['qb_user']
QB_PASS = CONFIG['qb_pass']

print(f"配置加载完成: Emby={EMBY_HOST}, QB={QB_HOST}")

# ========== 工具函数 ==========
def run_cmd(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        return result.stdout.strip()
    except:
        return ''

def get_cpu_usage():
    try:
        with open('/proc/stat') as f:
            line1 = f.readline().split()
        time.sleep(0.1)
        with open('/proc/stat') as f:
            line2 = f.readline().split()
        idle1 = int(line1[4])
        total1 = sum(int(x) for x in line1[1:])
        idle2 = int(line2[4])
        total2 = sum(int(x) for x in line2[1:])
        diff_idle = idle2 - idle1
        diff_total = total2 - total1
        if diff_total == 0:
            return 0
        return round((1 - diff_idle / diff_total) * 100, 1)
    except:
        return 0

def get_memory_usage():
    try:
        with open('/proc/meminfo') as f:
            meminfo = {}
            for line in f:
                parts = line.split(':')
                if len(parts) == 2:
                    meminfo[parts[0]] = int(parts[1].strip().split()[0])
        total = meminfo.get('MemTotal', 0)
        available = meminfo.get('MemAvailable', 0)
        if total == 0:
            return 0
        return round((1 - available / total) * 100, 1)
    except:
        return 0

def get_disk_usage():
    result = run_cmd("df -h | grep -E '/volume[0-9]|/md/'")
    pools = []
    for line in result.split('\n'):
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 6:
            mount = parts[5]
            pools.append({
                'mount': mount,
                'size': parts[1],
                'used': parts[2],
                'avail': parts[3],
                'percent': int(parts[4].replace('%', '')),
            })
    return pools

def get_network_speed():
    try:
        with open('/proc/net/dev') as f:
            lines = f.readlines()
        rx1 = tx1 = 0
        for line in lines[2:]:
            parts = line.split(':')
            if len(parts) == 2:
                name = parts[0].strip()
                if name.startswith('eth') or name.startswith('ovs_eth0'):
                    stats = parts[1].split()
                    rx1 += int(stats[0])
                    tx1 += int(stats[8])
        time.sleep(1)
        with open('/proc/net/dev') as f:
            lines = f.readlines()
        rx2 = tx2 = 0
        for line in lines[2:]:
            parts = line.split(':')
            if len(parts) == 2:
                name = parts[0].strip()
                if name.startswith('eth') or name.startswith('ovs_eth0'):
                    stats = parts[1].split()
                    rx2 += int(stats[0])
                    tx2 += int(stats[8])
        down_kb = (rx2 - rx1) / 1024
        up_kb = (tx2 - tx1) / 1024
        return {'down': round(down_kb, 1), 'up': round(up_kb, 1)}
    except:
        return {'down': 0, 'up': 0}

# ========== qBittorrent API ==========
qb_cookies = None

def qb_login():
    global qb_cookies
    try:
        url = f"{QB_HOST}/api/v2/auth/login"
        data = urllib.parse.urlencode({'username': QB_USER, 'password': QB_PASS}).encode()
        req = urllib.request.Request(url, data=data, method='POST')
        resp = urllib.request.urlopen(req, timeout=5)
        result = resp.read().decode()
        set_cookies = resp.headers.get_all('Set-Cookie')
        if set_cookies:
            qb_cookies = '; '.join([c.split(';')[0] for c in set_cookies])
            return True
    except Exception as e:
        print(f"QB login error: {e}")
    return False

def qb_get_torrents():
    global qb_cookies
    if not qb_cookies:
        if not qb_login():
            return []
    try:
        url = f"{QB_HOST}/api/v2/torrents/info"
        req = urllib.request.Request(url)
        req.add_header('Cookie', qb_cookies)
        resp = urllib.request.urlopen(req, timeout=5)
        torrents = json.loads(resp.read().decode())
        result = []
        for t in torrents[:20]:
            result.append({
                'name': t.get('name', ''),
                'hash': t.get('hash', ''),
                'state': t.get('state', ''),
                'progress': round(t.get('progress', 0) * 100, 1),
                'dlspeed': t.get('dlspeed', 0),
                'upspeed': t.get('upspeed', 0),
                'size': t.get('size', 0),
            })
        return result
    except Exception as e:
        print(f"QB torrents error: {e}")
        return []

# ========== Emby API ==========
def emby_get(path):
    try:
        url = f"{EMBY_HOST}{path}&api_key={EMBY_API_KEY}"
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=5)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"Emby API error: {e}")
        return {}

def emby_get_stats():
    movies = emby_get("/Items?IncludeItemTypes=Movie&Recursive=true&Limit=1")
    shows = emby_get("/Items?IncludeItemTypes=Series&Recursive=true&Limit=1")
    return {
        'movies': movies.get('TotalRecordCount', 0),
        'shows': shows.get('TotalRecordCount', 0),
    }

# ========== HTTP Handler ==========
class DashboardHandler(BaseHTTPRequestHandler):
    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/status':
            net = get_network_speed()
            self.send_json({
                'cpu': get_cpu_usage(),
                'memory': get_memory_usage(),
                'net_down': net['down'],
                'net_up': net['up'],
            })

        elif self.path == '/api/storage':
            self.send_json({'pools': get_disk_usage()})

        elif self.path == '/api/torrents':
            self.send_json({'torrents': qb_get_torrents()})

        elif self.path.startswith('/api/media'):
            stats = emby_get_stats()
            self.send_json(stats)

        elif self.path == '/api/apps':
            apps = []
            services = [
                {'name': 'Emby', 'desc': '媒体串流', 'icon': '🎬', 'url': EMBY_HOST},
                {'name': 'qBittorrent', 'desc': '下载器', 'icon': '⬇️', 'url': QB_HOST},
            ]
            for s in services:
                try:
                    req = urllib.request.Request(s['url'], method='GET')
                    urllib.request.urlopen(req, timeout=2)
                    apps.append({**s, 'status': 'online'})
                except:
                    apps.append({**s, 'status': 'offline'})
            self.send_json({'apps': apps})

        else:
            self.send_json({'error': 'Not found'})

    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    port = 28090
    print(f"Liquid Glass Dashboard API running on http://0.0.0.0:{port}")
    import socketserver
    class ReusableHTTPServer(socketserver.TCPServer):
        allow_reuse_address = True
    server = ReusableHTTPServer(('0.0.0.0', port), DashboardHandler)
    server.serve_forever()
