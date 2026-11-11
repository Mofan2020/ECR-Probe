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

仓库里 133 个测试全部打的是自己写的 mock 服务端。**没有任何一个测试对真实客户端发过请求。**

静态分析确认了命令名和部分字段名，但没确认：

- 本地 WS 服务端的 URI 路径（只确认了 `/super_connect` 那一条）
- 报文信封的真实形状和字段拼写（`traceId` 还是 `traceid`）
- 参数的具体结构和取值范围
- 握手阶段是否有签名校验、校验什么

所以：可能连不上，也可能连上了但对端忽略你发的每条消息。这不是免责套话，是当前的事实状态。

## 结构

```
ecr_probe/
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
tests/            133 个
scripts/
  check-app-js.js app.js 假 DOM 冒烟测试
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
