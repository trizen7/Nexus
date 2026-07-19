# Nexus Gateway Docker 部署

Nexus Gateway 推荐使用 Docker Compose 部署。容器只包含移动网关；Hermes Agent API Server 由部署者单独运行。

## 目录结构

建议在 NAS 上使用独立目录，例如飞牛 NAS：

```text
/vol4/1000/Docker/nexus-mobile/
├─ compose.yaml
├─ gateway/
│  ├─ Dockerfile
│  ├─ requirements.txt
│  ├─ start_gateway.py
│  └─ nexus_gateway/
└─ data/
   ├─ config.json
   ├─ account.json
   └─ media/
```

`data/` 是唯一需要长期保留和备份的运行数据目录。

## 1. 首次初始化

首次部署不需要创建 `.env`。启动容器后，在浏览器访问：

```text
http://NAS地址:8787
```

初始化页面需要填写：

- Nexus 管理员账号；
- Nexus 管理员密码；
- Hermes API Server 地址；
- Hermes API Server Key。

Nexus 会自动生成 Session Secret。Hermes 配置保存在 `data/config.json`，管理员账号保存在 `data/account.json`，浏览器不会保存或回显 API Key。

如果 Hermes API Server 与网关不在同一容器，请不要填写 `127.0.0.1`；容器内的回环地址只指向容器自身。填写 NAS 主机地址、局域网主机地址或可解析的服务名。

`.env.example` 仅用于无网页环境、旧部署兼容或自动化预配置。已有 `.env` 部署首次使用新版启动后会自动生成 `data/config.json`。

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

## 4. 更新

```bash
git pull
docker compose build --pull
docker compose up -d
```

更新前备份：

```bash
tar -czf nexus-data-backup-$(date +%Y%m%d-%H%M%S).tar.gz data
```

## 5. 停止和恢复

停止但保留数据：

```bash
docker compose down
```

重新启动：

```bash
docker compose up -d
```

不要使用 `docker compose down -v` 删除数据。当前 Compose 使用绑定目录而非匿名卷，但仍应避免形成误操作习惯。

## 6. 反向代理

局域网测试可直接使用 `http://NAS地址:8787`。公网必须使用 HTTPS 反向代理。

SSE 流式回答要求代理不要缓存响应，并允许较长连接时间。反向代理由部署者维护，Nexus 仓库不绑定 Caddy、Nginx 或特定域名。

## 7. 日志与资源

Compose 已配置 Docker `json-file` 日志轮转：

- 单文件最大 10 MB；
- 最多保留 3 个文件。

容器不需要数据库或 Redis。账号状态、运行状态和媒体文件保存在 `/data`。

## 8. 从旧星禾网关迁移

以后只维护 Nexus，不再继续更新独立的星禾移动端分支。

建议迁移方式：

1. 停止旧网关；
2. 备份旧 `data/`；
3. 将需要保留的 `account.json`、`runs.json`、`session_media.json` 和媒体目录复制到 Nexus 的 `data/`；
4. 确认目录权限为 `10001:10001`；
5. 使用 `NEXUS_*` 环境变量启动 Nexus；
6. 用 `/health`、登录、会话列表和文件下载逐项验证；
7. 确认稳定后再删除旧容器或旧 Windows 启动项。

Android 的 Nexus 包名为 `app.nexus.mobile`，它会与旧星禾版并存，不会覆盖旧 App。完成迁移后可手动卸载旧星禾版。旧 App 中的本地草稿和缓存不会自动迁移到 Nexus App。

## 9. 安全边界

- 不提交 `gateway/.env` 和 `data/`；
- Hermes 主 Token 只保存在网关；
- App 只持有 Nexus 登录后签发的设备令牌；
- 公网必须使用 HTTPS；
- 定期轮换网关密码、Session Secret 和 Hermes API Token；
- 不把 8787 管理入口直接暴露到公网且不设访问控制。
