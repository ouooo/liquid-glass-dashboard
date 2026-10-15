#!/bin/bash
# Liquid Glass NAS Dashboard 一键启动脚本
# 用法: bash start.sh

set -e

echo "======================================"
echo "  Liquid Glass Dashboard 启动中..."
echo "======================================"

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查 Python3
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3,请先安装 Python 3.7+"
    exit 1
fi

# 读取配置
WEB_PORT=$(grep -o '"web":[0-9]*' config.json | grep -o '[0-9]*')
API_PORT=$(grep -o '"api":[0-9]*' config.json | grep -o '[0-9]*')
WEB_PORT=${WEB_PORT:-28080}
API_PORT=${API_PORT:-28090}

# 杀掉旧进程
echo "🧹 清理旧进程..."
pkill -f "python3.*api.py" 2>/dev/null || true
pkill -f "python3.*http.server.*$WEB_PORT" 2>/dev/null || true
sleep 1

# 启动 API 服务
echo "🚀 启动 API 服务 (端口 $API_PORT)..."
nohup python3 api.py > /tmp/nexus-api.log 2>&1 &
API_PID=$!
sleep 2

# 检查 API 是否启动成功
if curl -s http://localhost:$API_PORT/api/status > /dev/null 2>&1; then
    echo "✅ API 服务启动成功 (PID: $API_PID)"
else
    echo "⚠️  API 服务启动可能失败,查看日志: /tmp/nexus-api.log"
fi

# 启动静态文件服务
echo "🌐 启动 Web 服务 (端口 $WEB_PORT)..."
nohup python3 -m http.server $WEB_PORT > /tmp/nexus-web.log 2>&1 &
WEB_PID=$!
sleep 1

# 获取本机 IP
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

echo ""
echo "======================================"
echo "  ✅ Liquid Glass Dashboard 启动完成!"
echo "======================================"
echo ""
echo "  访问地址:"
echo "    本机:   http://localhost:$WEB_PORT"
echo "    内网:   http://$HOST_IP:$WEB_PORT"
echo ""
echo "  API 地址: http://$HOST_IP:$API_PORT"
echo ""
echo "  进程管理:"
echo "    查看日志: tail -f /tmp/nexus-api.log"
echo "    停止服务: pkill -f 'python3.*api.py'"
echo ""
