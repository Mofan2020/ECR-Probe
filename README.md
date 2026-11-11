# ECR Probe

对希沃易启学（Seewo YiQiXue）课堂协议的静态分析产物，和一个只连本机的探测台。

**只连本机。** 回环地址或本机网卡自己的地址，其他目标在代码层面被拒，一个包都不发。详见 [DISCLAIMER.md](DISCLAIMER.md)。

## 它做什么

三件事：

1. **找服务端**。先问内核要监听表，按进程名（`electronic-classroom`）认出易启学占的端口；认不出来再按候选端口扫。
2. **列命令**。146 条命令名来自对 `electronic-classroom.exe` 的静态分析，按命名空间分组，可以搜、可以点了直接填进报文编辑器。
3. **发报文 + 自动化**。按推断出的信封构造 JSON，走 WebSocket 发出去；规则引擎可以在某条命令发出后自动跟一条动作，比如"屏幕广播一开始就把它切成窗口"。

## 为什么端口要"找"

本地 WebSocket 端口不是固定的。反编译 `sub_691E30` 能看到它调 `getsockname` 再 `ntohs` 返回端口，也就是 `bind(0)` 让内核分配，实际端口由另一条通道告诉对端。

所以没有常量可写。本工具的做法是问内核要监听表再按进程认——不管它开在 33331 还是 51234 都能拿到。拿不到时退回扫端口，并对每个开着的端口试 WS 握手、看响应头里有没有 `WebSocket++` 指纹。

按进程找在 **Windows 上不需要管理员**（走 `GetExtendedTcpTable`）。macOS / Linux 上读其他进程的套接字需要 root，没权限时会退回扫端口，功能不受影响只是慢一点。

## 两道摩擦

不是安全机制，是让人停一秒。

**确认闸门。** 首次使用要原样输入一句确认语（`ecr_probe/gate.py` 里的 `CHALLENGE`）。界面里输，或者终端 `ecr-probe ack`。敲错一个字不放行。过了之后写进 `~/.ecr-probe/gate.json`。

**危险命令锁。** 这些默认全部拒绝发送：

```
关机 / 重启 / 唤醒 / 清屏 / 关屏 / 黑屏
网络管控（启用·停用·更新策略）
进程管控（启用·停用·更新策略）
远程键鼠注入 / 远程开应用·开网址
桌面录屏 / 关广播 / 踢学生
云端 logoutDevice / upgrade
```

要放开必须在终端跑：

```bash
ecr-probe allow-dangerous      # 输 yes 确认
ecr-probe allow-dangerous --lock   # 测完锁回去
```

界面上那个「危险」开关只能切换、不能绕过——它跟终端读的是同一个策略文件。

**这两层都挡不住会读文档的人。** 真正的边界是下面的本机地址守卫，那个是硬的。

## 本机边界

`ecr_probe/guard.py` 只放行两类目标：回环地址，以及本机网卡上真实存在的地址。其他任何地址——包括同一局域网内的另一台机器——在建立连接之前就被拒绝。

含义：在一台机器上跑这个程序，够不到除它自己以外的任何设备。

已知的一个未验证边界：本机客户端收到 `local.teacher->student.*` 之后，是否会用自己的连接把它转发给别的学生机，没有确认过。本工具自己的出站只到本机，但管不了本机客户端转发行为。

## 单文件与资源校验

发布物是单文件，没有安装程序。

界面资源打包在可执行文件里，启动时释放到持久目录：

| 平台 | 路径 |
|---|---|
| Windows | `%LOCALAPPDATA%\ECR-Probe\resources` |
| macOS | `~/Library/Application Support/ECR-Probe/resources` |
| Linux | `~/.local/share/ecr-probe/resources` |

`ECR_PROBE_HOME` 可以覆盖根目录。

每次启动逐文件比对 sha256，**校验通过才加载**：

- 一致 → 直接复用，不重写
- 不一致（文件被改过、缺了、多了残留、或者换了新版本）→ 整个目录换掉

整目录替换而不是逐文件修补，因为"文件都在、内容已经不对"是最难查的状态：界面报错但文件齐全，排查是无底洞。

替换走「写到临时目录 → 自检 → 整体换名」，中途被杀不会留下半个目录，也不会让下一次启动读到半套资源。

出问题可以单独查：

```bash
ecr-probe resources           # 打印路径、文件数、校验结果，不一致就顺手修
ecr-probe resources --force   # 强制重新释放
```

## 装

```bash
python -m venv .venv
.venv/bin/pip install -e .
```

Windows 上把 `.venv/bin/` 换成 `.venv\Scripts\`。

## 用

开窗口：

```bash
ecr-probe gui
```

命令行：

```bash
# 首次使用先过闸门
ecr-probe ack

# 按进程找端口
ecr-probe discover

# 扫指定端口并试所有候选路径
ecr-probe locate --ports 33331,39173,3333

# 列出全部命令
ecr-probe list

# 连上去发一条
ecr-probe send --port 33331 --path / \
  --method 'local.teacher->student.turnOffMute' \
  --params '{"teacherUid":"x"}'
```

界面里：`按进程找` → 点其中一个端口探测 → 点握手成功的路径连接 → 左栏选命令 → 发送。日志、报文、回应都能一键复制。

## 自动化规则

规则是「触发方法 → 动作方法」，触发支持结尾 `*` 通配，可以配连发次数和间隔。

出厂带一条你要的场景：`local.teacher->student.startBroadcast` 发出后，自动发 `local.teacher->student.setProperty` 带 `{"displayMode": "window"}`，连发 3 次、间隔 400ms——因为对端可能只认第一帧，也可能要持续纠正。

**这条规则的参数是推断的。** `displayMode` 这个键在配置表里确认存在，但哪个取值表示窗口、哪条命令负责改它，都没有验证过。所以出厂另外给两条候选（`broadcast.control`、`student.broadcast.viewControl`），默认关，第一条不生效就开第二条试。

规则存在 `~/.ecr-probe/rules.json`。

## 命令目录

| 命名空间 | 条数 | 说明 |
|---|---|---|
| `local.*` | 44 | `local.<源>-><目标>.<动作>`，学生端直接响应这组 |
| `student.*` | 40 | 学生端业务 |
| `teacher.*` | 29 | 教师端业务 |
| `classroom.management.*` | 10 | 课堂管理 |
| `thing.service.*` | 23 | 云端 IoT，走 MQTT |

原程序里有两处拼写错误，照抄不改：`student.classromm.bindResultNotify`（双 m）、`teacher.managent.getStudentAPPList`（少 e）。对接时必须按原文匹配。

## 它不保证能用

仓库里 189 个测试全部打的是自己写的 mock 服务端。**没有任何一个测试对真实客户端发过请求。**

静态分析确认了命令名和部分字段名，但没确认：

- 本地 WS 服务端的 URI 路径（只确认了 `/super_connect` 那一条）
- 报文信封的真实形状和字段拼写（`traceId` 还是 `traceid`）
- 参数的具体结构和取值范围
- 握手阶段是否有签名校验、校验什么

所以：可能连不上，也可能连上了但对端忽略你发的每条消息。这不是免责套话，是当前的事实状态。

## 结构

```
ecr_probe/
  gate.py         确认闸门 + 危险命令策略
  resources.py    界面资源释放到持久目录 + sha256 校验
  guard.py        本机地址守卫，唯一的出站闸门
  netlist.py      读内核监听表（Windows 用 GetExtendedTcpTable）
  wsclient.py     RFC 6455 客户端，自己实现，看得见每个字节
  `commands.py`     146 条命令目录
  rules.py        自动化规则 + 落盘
  probe.py        端口发现 + 路径握手 + 指纹
  bridge.py       pywebview 的 Python 侧
  clipboard.py    写系统剪贴板
  mockserver.py   测试用 WS 靶子
  gui/            index.html / style.css / app.js
tests/            189 个
launcher.py       打包入口
scripts/
  check-app-js.js app.js 假 DOM 冒烟测试
.github/workflows/
  ci.yml          三平台 × 两版本测试 + 资源门禁
  release.yml     tag 触发：门禁 → 打包单文件 → 发 Pre-release
```

## 测试

```bash
.venv/bin/python -m pytest
```

覆盖：守卫的拒绝与放行（含 DNS 重绑定）、WS 握手与帧往返、端口表达式、路径探测、命令目录完整性、报文信封、规则匹配与落盘、桥接端到端（含规则自动触发）、前端契约（app.js 调用的每个桥方法都存在）、app.js 假 DOM 冒烟。

## 协议发现摘要

分析对象是学生端 `SeewoYiQiXueStudent_1.0.13.4013`：

- Electron 外壳 + `resources/cppService/electronic-classroom.exe`（9.25 MB 原生 C++）
- 四个 WebSocket 服务端：`LocalWsServer` / `SignCheckLocalWsServer` / `SuperConnectWSServer` / `SignCheckSuperConnectWSServer`
- 传输用 `websocketpp 0.8.2`，JSON 用 `nlohmann::json`
- 云端 MQTT 走 `ssl://iot-broker.seewo.com:8883`
- 屏幕帧走命名共享内存，组播地址是配置项（`multicastIP` / `multicastPort`）

## 许可

MIT，见 [LICENSE](LICENSE)。使用前请读 [DISCLAIMER.md](DISCLAIMER.md)。
