# 把云端服务部署到 Coolify（照着点就行）

> 目标：得到一个 `https://` 开头的网址，手机能连，证书由 Coolify 自动申请。
> 部署完的验证只有一步：浏览器打开 `https://你的网址/health`，能看到一段 JSON 就算成功。

## 0. 前提

- 服务器已经装好 Coolify，并且你能打开它的网页面板。
- 代码已经在 GitHub 上（私有仓库也行）。如果还没有仓库，先做这一步：
  1. 打开 github.com，新建一个 **Private** 仓库，名字随意，例如 `qqmusic-companion`。
  2. 把本项目的 `cloud` 文件夹传上去（GitHub 网页版可以直接拖拽上传）。
  3. 回到 Coolify，按下面第 2 步连这个仓库。

> 代码里**没有任何密码**。口令都在 Coolify 的"环境变量"里填，不会进仓库。

## 1. 新建资源

Coolify 面板 → 你的项目 → **+ New Resource** → **Private Repository (with GitHub App)**（公开仓库就选 Public Repository）。

- Repository：选刚才那个仓库
- Branch：`main`
- Build Pack：**Dockerfile**
- Base Directory：`/cloud`
- Dockerfile Location：`/cloud/Dockerfile`
- Ports Exposes：`8080`

## 2. 填环境变量

在资源的 **Environment Variables** 里，一条条加（真实值见本地的 `cloud\.env.local`，那个文件不进仓库）：

| 名称 | 值 |
|---|---|
| `MCP_TOKEN` | 一串随机字符（见 `.env.local`） |
| `COLLECTOR_TOKEN` | 另一串随机字符（见 `.env.local`） |
| `APP_TIMEZONE` | `Asia/Shanghai` |
| `RETENTION_DAYS` | `7` |
| `STALE_SECONDS` | `60` |
| `MEDIA_STALE_SECONDS` | `90` |
| `DB_PATH` | `/data/qqmusic.db` |

口令怎么生成：

```
python -c "import secrets;print(secrets.token_urlsafe(24))"
```

生成两串，分别填进去；`COLLECTOR_TOKEN` 要和本地 `config.ini` 里的 `collector_token` 完全一致。

## 3. 加持久化存储（必须！）

资源 → **Persistent Storage** → **Add**：

- Name：`qqmusic-data`
- Mount Path：`/data`

不加这个，容器一重建，七天的听歌记录就没了。

## 4. 配域名和证书

资源 → **Domains** → 填 Coolify 给的地址，例如：

```
https://nqt3disrkppclflxphbf2qva.118.89.161.37.sslip.io
```

保存后 Coolify 会自动申请证书。手机上能打开就是证书好了。

> `sslip.io` 是免费的"把 IP 变成域名"的服务，Coolify 常用它；它只是解析，流量还是走你的服务器。

## 5. 健康检查

**Healthcheck** 里填：

- Path：`/health`
- Port：`8080`

## 6. 部署

点 **Deploy**，看日志里出现 `Application startup complete`。然后浏览器打开：

```
https://你的网址/health
```

正常会看到：

```json
{"ok":true,"version":"1.0.0","time":"...","uptime_seconds":3,"database_error":null}
```

## 7. 把网址填回电脑

打开 `local\config.ini`，把 `base_url =` 后面填成 `https://你的网址`（**结尾不要带 `/mcp`**），保存。

然后双击 `启动听歌采集.bat`。几分钟内在 Aru 里问"我现在在听什么"，能答上来就通了。

---

## 以后升级代码

改完代码 push 到 GitHub，Coolify 里点 **Redeploy**。数据在 `/data` 卷里，不会丢。
