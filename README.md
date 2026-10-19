# Liquid Glass NAS Dashboard

Liquid Glass 风格 NAS 监控仪表盘,深色半透明玻璃拟态 UI,接入真实 NAS 数据。

## 功能特性

- 🎨 **Liquid Glass 风格** - 深色背景 + backdrop-filter 半透明玻璃卡片,细腻边框高光
- 📊 **实时系统监控** - CPU / 内存 / 网络速率 / 存储池容量
- 🎬 **媒体库统计** - Emby 电影 / 剧集 / 音乐真实数量
- ⬇️ **下载器监控** - qBittorrent 种子列表 / 上传下载速度
- 🖥️ **服务状态** - Emby / MoviePilot / qBittorrent / Transmission 健康检查
- 🔍 **多源搜索** - Bing 搜索 / Emby 媒体库搜索 / 站点资源搜索
- 📱 **响应式布局** - 适配桌面 / 平板 / 手机

## 快速开始

### 前置要求

- Python 3.7+
- 能访问 NAS 内网地址

### 一键启动

```bash
cd nexus-dashboard
bash start.sh
```

启动后访问: `http://<NAS-IP>:28080`

### 手动启动

```bash
# 启动 API 服务 (端口 28090)
python3 api.py &

# 启动静态文件服务 (端口 28080)
python3 -m http.server 28080 &
```

## 配置说明

编辑 `config.json` 修改服务地址和端口:

```json
{
  "ports": {
    "web": 28080,
    "api": 28090
  },
  "services": {
    "emby": {
      "host": "http://10.31.0.88:16880",
      "api_key": "your-api-key"
    }
  }
}
```

### 自动配置

默认从 MoviePilot 数据库自动读取 Emby / qBittorrent 配置,无需手动填写。

## 目录结构

```
nexus-dashboard/
├── index.html      # 前端单页应用
├── api.py         # Python API 后端
├── config.json    # 配置文件
├── start.sh       # 一键启动脚本
└── README.md      # 说明文档
```

## 页面说明

| 页面 | 功能 |
|---|---|
| 总览 | 系统状态 / 媒体库概览 / 最近入库 |
| 服务 | 各服务运行状态健康检查 |
| 媒体库 | 电影 / 剧集 / 音乐统计 |
| 数据活动 | qBittorrent 种子列表 |
| 存储 | 存储池容量 / 挂载点 |
| 脚本 | 脚本运行记录 |
| 活动日志 | 事件时间线 |

## 开机自启

将以下内容添加到 `/etc/rc.local` 或 systemd service:

```bash
cd /volume1/docker/nexus-dashboard
nohup python3 api.py > /tmp/nexus-api.log 2>&1 &
nohup python3 -m http.server 28080 > /tmp/nexus-web.log 2>&1 &
```

## 技术栈

- **前端**: 原生 HTML / CSS / JavaScript (无框架依赖)
- **后端**: Python 3 标准库 (无第三方依赖)
- **数据源**: Emby API / qBittorrent API / MoviePilot DB / 系统 /proc

## License

MIT
