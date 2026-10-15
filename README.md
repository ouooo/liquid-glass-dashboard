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

## 目录结构

```
nexus-dashboard/
├── index.html      # 前端单页应用
├── api.py         # Python API 后端
├── config.json    # 配置文件
├── start.sh       # 一键启动脚本
└── README.md      # 说明文档
```

## 技术栈

- **前端**: 原生 HTML / CSS / JavaScript (无框架依赖)
- **后端**: Python 3 标准库 (无第三方依赖)
- **数据源**: Emby API / qBittorrent API / MoviePilot DB / 系统 /proc

## License

MIT