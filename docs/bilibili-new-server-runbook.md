# Bilibili 播放：新服务器启用手册（Runbook）

> **用途**：把 Song/Lute 部署到一台**新服务器**后，让「B 站视频导入 + 阅读页 DASH 播放」恢复正常。
> 本文自包含 —— 只需要本文 + 仓库，不需要任何历史对话。
> 部署链路本身见 `song-lute-deploy`（本地测试 → commit → push → 服务器 `deploy.sh` → 验证），本文只管 B 站那一段。

---

## TL;DR —— 可以直接复制给 AI 助手的一段话

> Song 已经部署到新服务器 `<root@服务器IP>`。请按仓库里的 `docs/bilibili-new-server-runbook.md`
> 恢复 B 站播放：先用文档第 0 步判断服务器出口 IP 是否被 B 站风控；如果被风控，就在服务器上用
> **systemd drop-in**（不要改 unit 本体）加 `Environment=LUTE_BILIBILI_PROXY=http://127.0.0.1:18888`，
> 在我这台 Mac 上跑 `utils/bili_egress_tunnel.sh root@<服务器IP>` 建立反向隧道，然后按文档第 4 步的
> 验证清单逐条验收。注意：`127.0.0.1:18888` 是**服务器回环**地址，与我的机器 IP 无关；仓库里不应出现
> 任何服务器地址或凭据。

---

## 0. 先判断：这台服务器需不需要做

B 站能不能用，**只取决于一件事：服务器自己的出口 IP 是否被 B 站接受**。与代码、浏览器、账号都无关。

在**服务器上**跑：

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -A 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36' \
  -e 'https://www.bilibili.com' \
  'https://api.bilibili.com/x/web-interface/view?bvid=BV1aa411J7dB'
```

| 结果 | 含义 | 接下来 |
|---|---|---|
| `200` | 出口 IP 被接受 | **什么都不用做**，本文到此结束 |
| `412` | 出口 IP 被 B 站风控 | 继续第 3 步 |

- 国内机房 / 家宽 IP：多数直接 `200`。
- **海外机房（Linode、Vultr、AWS/DO 境外区等）：几乎必然 `412`** —— B 站对数据中心与海外地址一律拒绝，
  返回体是 `{"code": -412, "message": "request was banned"}`，CDN 侧还会返回 `959` 或直接超时。
  换 UA、带 Cookie、带 Referer、换 IP 段**都没有用**，封的是 IP 本身。

**不做这套配置时，Song 的行为与改造前完全一致**：请求直连，播放失败时阅读页自动降级到 B 站官方内嵌播放器
（能看画面，但字幕不跟随、也不能用 Song 的字词点击交互）。

---

## 1. 原理（30 秒）

```
  你的浏览器
      │  HTTPS
      ▼
  Song 服务器 (nginx + waitress:5001)          ← 出口 IP 被 B 站风控
      │  出站请求带上 LUTE_BILIBILI_PROXY
      │  http://127.0.0.1:18888
      ▼
  127.0.0.1:18888  ← ssh -R 建立的隧道，只在服务器回环上监听
      │  （TCP，由你的机器主动外连建立，所以你的机器不需要公网 IP）
      ▼
  你的 Mac: 127.0.0.1:8888  ← utils/bili_egress_proxy.py，CONNECT 代理
      │
      ▼
  api.bilibili.com / CDN  ← 看到的是你 Mac 的 IP（B 站接受）
```

三个要点：

1. **代理只绑回环**（`127.0.0.1:8888`），隧道也用 `ssh -R` 且服务器没开 `GatewayPorts`，
   所以整条链路在公网上**不可达**，不能被人当公开代理滥用；代理本身也只实现了 CONNECT（HTTPS），
   不是通用明文中继。
2. **你的机器 IP 变了不用改任何配置**。隧道由你的机器主动去连服务器；网络切换 / 睡眠唤醒 / IP 变化
   只会让这条 TCP 断开，重连后自然从新地址出去。脚本自带重连循环（`ServerAliveInterval=15`、
   `ExitOnForwardFailure=yes`、掉线 5 秒后重试）。
3. **服务器侧唯一写死的地址是 `127.0.0.1:18888`**，就是它自己的回环。仓库里不存在服务器地址、
   隧道端口以外的信息，也没有任何凭据。

---

## 2. 三条必需条件

| # | 条件 | 怎么确认 |
|---|---|---|
| 1 | **出口机**（通常是你的 Mac）网络能直连 B 站 | 在出口机跑第 3.0 步的自检脚本，必须打印 `0` |
| 2 | 出口机能 SSH 到服务器（免密更好） | `ssh root@<服务器IP> true` |
| 3 | 服务器上的 Song 代码含 B 站中继改造 | `git -C /opt/lute log --oneline -1`，应是 `all-features-combine` 上含 `bilibili` 的提交；文件 `lute/read/bilibili_stream.py` 与 `lute/utils/outbound_proxy.py` 存在 |

出口机上需要 `python3`（任意 3.6+，代理只用标准库）和 `ssh`。**不需要公网 IP、不需要额外服务器、不需要域名。**

---

## 3. 执行（三步）

### 3.0 先在出口机上自检（必须绿）

```bash
python3 - <<'PY'
import json, urllib.request
req = urllib.request.Request(
    "https://api.bilibili.com/x/web-interface/view?bvid=BV1aa411J7dB",
    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"})
print(json.load(urllib.request.urlopen(req, timeout=20))["code"])   # 0 = 可用
PY
```

打印 `0` 才算合格。若这里就是 `-412`：说明**出口机当前网络也不被 B 站接受**
（常见于开着全局代理 / VPN / 公司网络），换网络再试，否则后面的配置再对也没用。

### 3.1 服务器：写代理地址（一次性，drop-in）

**用 drop-in，不要编辑 `/etc/systemd/system/lute.service` 本体** —— unit 被重写/覆盖时不丢，
`systemctl status` 会显式列出生效的 drop-in，删掉这个文件就完全回退。

```bash
ssh root@<服务器IP> 'bash -s' <<'REMOTE'
set -e
mkdir -p /etc/systemd/system/lute.service.d
cat > /etc/systemd/system/lute.service.d/bilibili-proxy.conf <<'EOF'
# Bilibili 封了本机 IP（数据中心/海外地址一律返回 HTTP 412）。
# 出站请求改走出口代理，出口端是另一台机器上的 utils/bili_egress_proxy.py，
# 经 SSH 反向隧道到达，所以下面的地址永远是本机回环。删掉本文件即回到直连。
[Service]
Environment=LUTE_BILIBILI_PROXY=http://127.0.0.1:18888
EOF
systemctl daemon-reload
systemctl restart lute
sleep 4
systemctl status lute --no-pager | head -8          # 应出现 Drop-In: .../bilibili-proxy.conf
PID=$(systemctl show -p MainPID --value lute)
tr '\0' '\n' < /proc/$PID/environ | grep LUTE_BILIBILI_PROXY   # 必须回显
REMOTE
```

> **别用 `systemctl show -p Environment` 下结论** —— 某些 systemd 版本只回显 unit 自身的值，不含 drop-in。
> 权威检查是 `/proc/<MainPID>/environ`。

### 3.2 出口机：起代理 + 隧道（这个终端要留着）

```bash
cd <Song 仓库目录>
utils/bili_egress_tunnel.sh root@<服务器IP>
```

脚本做两件事：起 `127.0.0.1:8888` 的 CONNECT 代理（已在跑就复用），然后循环维持
`ssh -N -R 18888:127.0.0.1:8888 root@<服务器IP>`。**Ctrl-C 即停**，服务器随即自动回落到官方内嵌播放器。

等价的手工两条命令（想分开管理时）：

```bash
python3 utils/bili_egress_proxy.py 8888                    # 终端 A：代理
ssh -N -R 18888:127.0.0.1:8888 root@<服务器IP>              # 终端 B：隧道
```

端口可改：`utils/bili_egress_tunnel.sh root@<IP> [代理端口=8888] [隧道端口=18888]`，
改了隧道端口就要同步改服务器 drop-in 里的地址。

想开机自动拉起、或不想占一个终端窗口 → 见附录 A。

---

## 4. 验证清单（由上到下，逐条过）

### 4.1 隧道在服务器上可见

```bash
ssh root@<服务器IP> "ss -ltn | grep 18888"
```
期望**两行**：`127.0.0.1:18888` 与 `[::1]:18888`。空 → 回第 3.2 步。

### 4.2 服务器能经隧道取到 B 站数据（端到端、不需要登录）

```bash
ssh root@<服务器IP> 'cd /opt/lute && LUTE_BILIBILI_PROXY=http://127.0.0.1:18888 \
  ./venv/bin/python3 -c "
from lute.read import bilibili_stream as b
info = b.stream_info(\"BV1aa411J7dB\", 1)
rows = info.get(\"videos\") or [info[\"video\"]]
print(\"ok:\", [(v[\"height\"], v[\"bandwidth\"]) for v in rows])
print(\"audio:\", [a[\"bandwidth\"] for a in info[\"audios\"]])
"'
```

期望形如 `ok: [(360, 4xxxx), (480, 6xxxx)]` —— **按码率升序，第一个就是默认档**。
`412` / `BilibiliStreamError` → 出口机自检（3.0）没绿，或隧道不在。

> 不要用 `curl` 去打阅读页/取流路由来验收：应用自己还有一层登录，裸 curl 一律 `302 → /login`。
> 上面这条直接调生产代码，是最省事的真实验证。

### 4.3 浏览器里真的出画面

用带登录态的真实浏览器打开一本 B 站书（例如 `https://<站点>/read/<id>`）：

- [ ] 画面正常播放，**不是** B 站官方 iframe 外壳
- [ ] **字幕 / 逐词跟随正常**（这是 DASH 中继独有的能力，降级态做不到）
- [ ] 齿轮里的 **Quality 显示最低档**（如 360p）；手动切到 480p 后画面继续、**不中断重来**
- [ ] 刷新页面后画质记忆生效（按高度记忆，只记手动选择）

### 4.4 降级路径也没坏（可选，1 分钟）

在出口机 Ctrl-C 掉隧道 → 刷新页面 → 应自动切到官方内嵌播放器，
且官方播放器上**点得动播放/暂停/音量**（历史上曾出现覆盖层吞点击，表现为"画面在播却点不动"）。
验完把隧道重新跑起来。

---

## 5. 排障表

| 症状 | 最可能的原因 | 处理 |
|---|---|---|
| 页面自动降级到官方 iframe（能看、字幕不跟随） | 隧道断了 / 出口机睡了 / 代理没在跑 | 看出口机终端日志；重跑 3.2。出口机侧确认 `lsof -nP -iTCP:8888 -sTCP:LISTEN` 有监听（**别用 `pgrep -f`，会匹配到自己**） |
| 取流接口 502、日志 `-412 request was banned` | 出口机当前 IP 也被风控（开了 VPN / 换了网络） | 出口机重跑 3.0；换网络 |
| `ss -ltn \| grep 18888` 为空，脚本却"没报错" | 服务器上 18888 被别的进程占了 → `ExitOnForwardFailure=yes` 直接失败 | `ssh root@<IP> "ss -ltnp \| grep 18888"` 找出占用者，或换隧道端口（同时改 drop-in） |
| `systemctl show -p Environment` 里看不到变量 | systemd 只回显 unit 自身值 | 改看 `/proc/<MainPID>/environ`（3.1 已给） |
| 黑屏但**有声音**、控制台无报错 | 选中了 HEVC 档（浏览器解不了） | 代码已优先 AVC（`avc1/avc3`）；整本只有 HEVC 时才会出现，属已知限制 |
| 隧道在线、清单也 200，但画面仍旧 | 前端资源是 CDN 上的旧版 | 一手 CSS/JS 走内容寻址（`vstatic()`），改内容自动换 URL、无需清 CDN；先硬刷新浏览器 |
| 导入书籍时标题抓不到 | 导入链路的标题请求也走代理 | 同 3.0，出口机 IP 是否被接受 |
| 服务器上跑 `pytest` 报 DBNAME 错误 | `tests/conftest.py` 强制测试库以 `test_` 开头 | 生产**不该**跑 pytest；验证用 4.2 那种独立脚本，放 `/tmp` 跑完删 |

---

## 6. 回退 / 关闭

| 目的 | 操作 |
|---|---|
| 临时关掉（保留配置） | 出口机 Ctrl-C 掉隧道 → 服务器立刻回落官方 iframe；重跑脚本即恢复 |
| 永久回到直连 | `ssh root@<IP> "rm /etc/systemd/system/lute.service.d/bilibili-proxy.conf && systemctl daemon-reload && systemctl restart lute"`；同时停掉出口机的代理/隧道 |
| 只换出口机 | 新机器上跑 3.0 自检 → 3.2 起脚本，**服务器侧不用动** |
| 只换服务器 | 服务器侧做 3.1，出口机 3.2 换 IP 即可；其余不变 |

---

## 7. 附录

### A. 出口机开机自启（可选，未实测）

把隧道交给 launchd，省得占一个终端：

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.song.bili-egress.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.song.bili-egress</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>REPO/utils/bili_egress_tunnel.sh</string>
    <string>root@SERVER_IP</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/bili-egress.log</string>
  <key>StandardErrorPath</key><string>/tmp/bili-egress.log</string>
</dict></plist>
EOF
# 把 REPO 换成 Song 仓库绝对路径、SERVER_IP 换成服务器地址，然后：
launchctl load -w ~/Library/LaunchAgents/com.song.bili-egress.plist
```

> ⚠️ 本机**未实测** launchd 版本：路径必须先替换成真实绝对路径（plist 不展开 `~`）。
> 只想省心的话，用 `tmux new -s bili` 起脚本、`Ctrl-b d` 脱离，同样不占终端。

### B. 这件事涉及的文件

| 文件 | 作用 |
|---|---|
| `lute/utils/outbound_proxy.py` | 读 `LUTE_BILIBILI_PROXY`；未设 → 返回 `None`（直连，行为同改造前） |
| `lute/read/bilibili_stream.py` | 调 B 站 `view`/`playurl`、生成多档 DASH 清单、代理分段字节；错误统一为 `BilibiliStreamError` |
| `lute/read/routes.py` | 清单与取流路由：按 `?page=` / `?q=` 选档，失败返回 502 JSON（不是 500 堆栈） |
| `utils/bili_egress_proxy.py` | 出口机上的 CONNECT 代理（仅回环，仅 CONNECT） |
| `utils/bili_egress_tunnel.sh` | 出口机上的一键启动：代理 + 自愈反向隧道 |
| `lute/static/js/bilibili-player.js` | dash.js 播放器：关 ABR、默认最低档、齿轮菜单切档、失败降级到官方 iframe |
| `lute/templates/read/bilibili_player.html` | 播放器模板（含 Quality 行，降级时隐藏） |
| `docs/high-risk-smoke-checklist.md` 第 11 项 | 回归时要过的高危点位 |
| 服务器 `/etc/systemd/system/lute.service.d/bilibili-proxy.conf` | **唯一**的服务器侧配置（不在 git 里，手工维护、`deploy.sh` 不碰） |

### C. 为什么不把地址写进代码

- 仓库里 `git grep` 不到任何服务器地址、域名或凭据；脚本与文档一律用 `root@<server>` 占位。
- 隧道是出口机**主动外连**建立的，所以出口机 IP 变化天然被支持，无需任何同步。
- 所以：**别人 clone 这个仓库部署到国内服务器，什么都不用配就能用 B 站**；部署到海外服务器的人，
  照本文自己搭一条出口链路即可，不会指向你的机器。
