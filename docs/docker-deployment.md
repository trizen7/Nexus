# Nexus Gateway Docker 部署

Nexus Gateway 推荐使用 Docker Compose 部署。容器只包含移动网关；Hermes Agent API Server 由部署者单独运行。

从 0.0.7 开始，Gateway 是 **HTTP 源站**：可信局域网可以直接访问，公网必须通过 Nginx、Caddy 或其他反向代理提供 HTTPS。Gateway 本身不生成、读取或热更新 TLS 证书。

## 目录结构

建议在 NAS 上使用独立目录，例如：

```text
/vol4/1000/Docker/nexus-mobile/
├─ compose.yaml
├─ gateway/
│  ├─ Dockerfile
│  ├─ requirements.txt
│  ├─ start_gateway.py
│  └─ nexus_gateway/
└─ data/
   ├─ bootstrap.token  # 仅首次初始化前存在
   ├─ config.json
   ├─ account.json
   └─ media/
```

`data/` 是唯一需要长期保留和备份的运行数据目录。由旧版本升级时，历史 `data/tls/` 可以保留，但 0.0.7 Gateway 不再读取它。

## 1. 首次初始化

首次部署不需要创建 `.env`。启动容器后，在宿主机或可信局域网访问：

```text
http://NAS地址:8787
```

初始化页面需要填写：

- Nexus 管理员账号；
- Nexus 管理员密码；
- Hermes API Server 地址；
- Hermes API Server Key；
- 一次性初始化令牌：在宿主机读取 `data/bootstrap.token`，或在容器内读取 `/data/bootstrap.token`。

Nexus 会自动生成 Session Secret。Hermes 配置保存在 `data/config.json`，管理员账号保存在 `data/account.json`。初始化令牌不会通过 `/health`、`/api/setup/status` 或其他公开 API 回显，初始化成功后 `bootstrap.token` 会自动删除；浏览器不会保存或回显 Hermes Key。

查看首次初始化令牌（不要粘贴到日志、Issue、截图或聊天中）：

```bash
cat data/bootstrap.token
# 或：docker compose exec nexus-gateway sh -c 'cat /data/bootstrap.token'
```

如果 Hermes API Server 与网关不在同一容器，请不要填写 `127.0.0.1`；容器内回环地址只指向容器自身。填写 NAS 主机地址、局域网主机地址或可解析的服务名。

`.env.example` 仅用于无网页环境、旧部署兼容或自动化预配置。已有环境变量部署首次使用新版启动后会自动生成 `data/config.json`。

## 2. 数据目录权限

容器使用固定 UID/GID `10001:10001`，避免每次重建后权限漂移：

```bash
mkdir -p data/media
chown -R 10001:10001 data
```

如果 NAS 不允许 `chown`，可在文件管理器中给予该目录读写权限。不要为了省事使用特权容器。

## 3. 构建和启动

```bash
docker compose config
docker compose build --pull
docker compose up -d
```

查看状态：

```bash
docker compose ps
docker compose logs -f --tail=100 nexus-gateway
```

健康检查：

```bash
curl http://NAS地址:8787/health
```

正常返回中应同时包含：

- `status: ok`；
- `gateway: nexus-mobile-gateway`；
- Hermes 上游状态。

若返回 `degraded`，表示容器本身已启动，但 Hermes API Server 不可达或认证失败。

## 4. 监听范围

仓库默认 Compose 映射 `8787:8787`，适合可信局域网直连。如果反向代理和 Docker 在同一台主机，并且不需要局域网直连，建议把端口映射收紧为：

```yaml
ports:
  - "127.0.0.1:8787:8787"
```

如果反向代理位于局域网另一台设备，Gateway 源站必须能被该代理访问，但仍应通过主机防火墙只允许代理地址或可信子网，绝不能把 8787 直接映射到公网。

## 5. 更新、停止与恢复

更新前先备份：

```bash
tar -czf nexus-data-backup-$(date +%Y%m%d-%H%M%S).tar.gz data
```

更新：

```bash
git pull
docker compose build --pull
docker compose up -d
```

停止但保留数据：

```bash
docker compose down
```

重新启动：

```bash
docker compose up -d
```

不要使用 `docker compose down -v` 删除数据。当前 Compose 使用绑定目录而非匿名卷，但仍应避免形成误操作习惯。

## 6. HTTPS 反向代理

### Nginx 示例

上游是本机 HTTP `127.0.0.1:8787`，外部只开放 HTTPS 域名：

```nginx
server {
    listen 80;
    server_name nexus.example.com;
    return 308 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name nexus.example.com;

    ssl_certificate     /etc/letsencrypt/live/nexus.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/nexus.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        proxy_buffering off;
        proxy_cache off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

确认 HTTPS、证书续期和所有子域均正确后，可由 Nginx 添加 HSTS；不要在尚未验证的域名上贸然启用长期 HSTS。

### Caddy 示例

Caddy 可自动申请和续期受信任证书：

```caddyfile
nexus.example.com {
    reverse_proxy 127.0.0.1:8787 {
        header_up Host {host}
        header_up X-Forwarded-Proto {scheme}
        flush_interval -1
    }
}
```

若 Caddy 前面还有 CDN、WAF 或负载均衡器，也要确保它们不会缓存 SSE，且空闲/读取超时足够长。

### 代理安全边界

- Gateway 不盲目信任 `X-Forwarded-*`，不会据此判断真实客户端、协议或安全状态；来源 IP 限速仍以 Gateway 实际看到的连接为准。
- `Host` 和 `X-Forwarded-Proto` 应继续转发，便于标准代理链、日志和未来兼容，但不能代替源站隔离。
- TLS 私钥、证书签发、自动续期、HTTP→HTTPS 跳转、HSTS、WAF 和公网访问控制全部由反向代理负责。
- 不要把 HTTP 源站端口直接暴露到公网。
- Android 外网地址填写 `https://nexus.example.com`；App 会拒绝公网 HTTP。

## 7. 日志与资源

Compose 已配置 Docker `json-file` 日志轮转：

- 单文件最大 10 MB；
- 最多保留 3 个文件。

容器不需要数据库或 Redis。账号状态、运行状态和媒体文件保存在 `/data`。

默认存储保护：单文件上限 50 MiB、媒体总配额 10 GiB、磁盘至少保留 512 MiB。可通过 `NEXUS_MAX_UPLOAD_BYTES`、`NEXUS_MAX_TOTAL_STORAGE_BYTES`、`NEXUS_MIN_FREE_DISK_BYTES` 调整；登录限速可通过 `NEXUS_LOGIN_RATE_LIMIT` 和 `NEXUS_LOGIN_RATE_WINDOW_SECONDS` 调整。

## 8. 从旧版 Gateway 迁移

建议迁移方式：

1. 停止旧网关；
2. 备份旧 `data/`；
3. 保留需要的 `account.json`、`config.json`、`runs.json`、`session_media.json` 和媒体目录；
4. 确认目录权限为 `10001:10001`；
5. 删除旧 `NEXUS_TLS_*`、`NEXUS_HTTPS_PORT` 和 `--tls-dir` 启动参数；
6. 启动 0.0.7 HTTP 源站；历史 `data/tls/` 可保留但不再使用；
7. 在反向代理配置正式 HTTPS 域名；
8. 用 `/health`、登录、会话列表、流式回答和文件下载逐项验证；
9. 确认稳定后再删除旧容器或旧 Windows 启动项。


## 9. 安全边界

- 不提交 `gateway/.env` 和 `data/`；
- Hermes 主 Token 只保存在网关；
- 首次初始化必须使用 `/data/bootstrap.token` 中的一次性令牌，且令牌不由公开 API 返回；
- App 只持有 Nexus 登录后签发的设备令牌；
- 局域网 HTTP 只用于受信任网络；公网入口必须使用 HTTPS 反向代理；
- 定期轮换网关密码、Session Secret 和 Hermes API Token；
- 公网防火墙只开放反向代理端口，不开放 Gateway 8787。
