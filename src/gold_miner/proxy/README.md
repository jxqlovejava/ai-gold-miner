# Proxy 代理模块

集成 [mihomo](https://github.com/MetaCubeX/mihomo) 代理核心，让 gold-miner 独立访问外网，不依赖系统 ClashX。

## 快速开始

### 1. 下载 mihomo 二进制

**macOS ARM64 (M1/M2/M3/M4):**
```bash
cd src/gold_miner/proxy
curl -LO https://github.com/MetaCubeX/mihomo/releases/download/v1.19.25/mihomo-darwin-arm64-v1.19.25.gz
gunzip mihomo-darwin-arm64-v1.19.25.gz
mv mihomo-darwin-arm64-v1.19.25 mihomo
chmod +x mihomo
```

**macOS Intel:**
```bash
cd src/gold_miner/proxy
curl -LO https://github.com/MetaCubeX/mihomo/releases/download/v1.19.25/mihomo-darwin-amd64-v1.19.25.gz
gunzip mihomo-darwin-amd64-v1.19.25.gz
mv mihomo-darwin-amd64-v1.19.25 mihomo
chmod +x mihomo
```

> 版本号可到 https://github.com/MetaCubeX/mihomo/releases 查看最新 release

### 2. 配置订阅链接

```bash
# 在项目根目录 .env 中添加
MIHOMO_SUB_URL=https://spacex.airport-ls.top/api/v1/client/subscribe?token=YOUR_TOKEN
```

或在启动时通过环境变量传入：
```bash
MIHOMO_SUB_URL="你的订阅链接" gold-miner scan
```

### 3. 验证

```bash
source .venv/bin/activate
gold-miner scan
```

代理模块会自动发现 `src/gold_miner/proxy/mihomo` 二进制，启动独立代理进程（端口 17890），所有外网请求自动走代理。

## 工作原理

```
gold-miner scan
    ↓
ProxyManager 发现 mihomo 二进制
    ↓
生成配置 (mixed-port: 17890) + 订阅链接
    ↓
启动 mihomo 子进程
    ↓
httpx 请求通过 http://127.0.0.1:17890
    ↓
Yahoo Finance / FRED / NewsAPI 正常访问
```

## 特点

- **零系统干扰**：不修改系统代理、不碰 ClashX 配置
- **自动发现**：优先找 `proxy/mihomo`，其次 PATH 中的 `mihomo/clash-meta/clash`
- **生命周期管理**：进程随 gold-miner 启动，退出时自动清理
- **透明降级**：找不到二进制时自动回退到直连
