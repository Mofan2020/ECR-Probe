#!/usr/bin/env node
/*
 * app.js 的冒烟测试：在 node 里用假 DOM 跑一遍 boot()。
 *
 * 目的不是测渲染效果，是证明 boot() 不抛异常、且启动后确实调了桥方法。
 * 「窗口打开了」不能证明 JS 跑通了 —— 脚本在第一个 querySelector 上
 * 就炸掉的话，窗口照样开着，只是里面一片空白。
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "ecr_probe", "gui", "app.js");
const source = fs.readFileSync(APP, "utf8");

let failures = 0;
function check(ok, label) {
  if (ok) {
    console.log(`  ok   ${label}`);
  } else {
    console.log(`  FAIL ${label}`);
    failures += 1;
  }
}

// ---------------------------------------------------------------- 假 DOM

function makeElement(tag = "div") {
  const el = {
    tagName: tag.toUpperCase(),
    children: [],
    dataset: {},
    style: {},
    classList: {
      _set: new Set(),
      add(...names) { names.forEach((n) => this._set.add(n)); },
      remove(...names) { names.forEach((n) => this._set.delete(n)); },
      toggle(name, force) {
        if (force === undefined) {
          this._set.has(name) ? this._set.delete(name) : this._set.add(name);
        } else if (force) {
          this._set.add(name);
        } else {
          this._set.delete(name);
        }
      },
      contains(name) { return this._set.has(name); },
    },
    _listeners: {},
    _text: "",
    value: "",
    scrollTop: 0,
    scrollHeight: 100,
    clientHeight: 100,
    title: "",
    appendChild(child) { this.children.push(child); return child; },
    append(...nodes) { this.children.push(...nodes); },
    remove() {},
    addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); },
    dispatch(type, event) { (this._listeners[type] || []).forEach((fn) => fn(event || {})); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    select() {},
    focus() {},
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    set innerHTML(v) { this._text = String(v); this.children = []; },
    get innerHTML() { return this._text; },
    set className(v) { this._class = String(v); },
    get className() { return this._class || ""; },
  };
  return el;
}

const registry = new Map();
function elementFor(selector) {
  const key = String(selector);
  if (!registry.has(key)) registry.set(key, makeElement());
  return registry.get(key);
}

const document = {
  querySelector: elementFor,
  querySelectorAll(selector) {
    if (selector === ".cmd.active") return [];
    if (selector === "[data-copy]") {
      return ["payload", "reply", "log"].map((name) => {
        const btn = elementFor(`[data-copy="${name}"]`);
        btn.dataset.copy = name;
        return btn;
      });
    }
    return [];
  },
  createElement: makeElement,
  body: makeElement("body"),
  execCommand() { return true; },
  addEventListener() {},
};

// ---------------------------------------------------------------- 假桥

const CHALLENGE = "我确认只对自己拥有的设备使用，并已读完全部免责条款";
let gatePassed = false;
const called = [];
function makeApi() {
  const handler = {
    get(_target, prop) {
      return (...args) => {
        called.push(String(prop));
        if (prop === "get_commands") {
          return Promise.resolve({
            ok: true,
            total: 2,
            spelling: "notes",
            groups: [
              {
                title: "组一",
                items: [
                  { name: "local.teacher->student.shutdown", direction: "teacher->student", note: "关机", params: {}, risky: true },
                  { name: "local.teacher->student.startBroadcast", direction: "teacher->student", note: "广播", params: { displayMode: "window" }, risky: false },
                ],
              },
            ],
          });
        }
        if (prop === "get_log") return Promise.resolve({ ok: true, lines: ["[00:00:00] hello"] });
        if (prop === "get_rules") return Promise.resolve({ ok: true, rules: [], path: "/tmp/x.json" });
        if (prop === "local_info") return Promise.resolve({ ok: true, addresses: ["127.0.0.1"] });
        if (prop === "status") return Promise.resolve({ ok: true, connected: false });
        if (prop === "gate_status") {
          return Promise.resolve({
            ok: true,
            passed: gatePassed,
            challenge: "我确认只对自己拥有的设备使用，并已读完全部免责条款",
            dangerous_allowed: false,
          });
        }
        if (prop === "pass_gate") {
          const ok = String(args[0] || "").trim() === CHALLENGE;
          if (ok) gatePassed = true;
          return Promise.resolve(ok ? { ok: true } : { ok: false, error: "不一致" });
        }
        if (prop === "set_dangerous") {
          return Promise.resolve({ ok: true, dangerous_allowed: !!args[0] });
        }
        if (prop === "copy_text") return Promise.resolve({ ok: true });
        if (prop === "send") {
          return Promise.resolve({ ok: true, payload: "{}", reply: "{}", fired: [{ trigger: "a", action: "b", repeat: 1, ok: true }] });
        }
        if (prop === "discover") return Promise.resolve({ ok: true, ports: [{ port: 33331, process: "electronic-classroom.exe", pid: 42 }], listeners_total: 3 });
        if (prop === "locate") {
          return Promise.resolve({ ok: true, source: "process", found: true, open_ports: [33331], hits: [{ port: 33331, path: "/", accepted: true, matched: true, server: "WebSocket++/0.8.2" }] });
        }
        if (prop === "connect") return Promise.resolve({ ok: true, target: "ws://127.0.0.1:33331/" });
        return Promise.resolve({ ok: true });
      };
    },
  };
  return new Proxy({}, handler);
}

const windowStub = {
  addEventListener(type, fn) { (windowStub._listeners[type] ||= []).push(fn); },
  _listeners: {},
  dispatch(type) { (windowStub._listeners[type] || []).forEach((fn) => fn({})); },
  pywebview: { api: makeApi() },
  alert() {},
};

const sandbox = {
  document,
  window: windowStub,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  setTimeout,
  setInterval: () => 0,
  clearInterval: () => {},
  console,
};

// ---------------------------------------------------------------- 跑

(async () => {
  const context = vm.createContext(sandbox);
  vm.runInContext(source, context, { filename: "app.js" });
  check(true, "脚本加载无语法错误");

  const bootHandler = (windowStub._listeners["pywebviewready"] || [])[0];
  check(typeof bootHandler === "function", "注册了 pywebviewready 监听");

  try {
    await bootHandler();
  } catch (err) {
    console.log(`  FAIL boot() 抛异常: ${err && err.stack ? err.stack : err}`);
    failures += 1;
  }
  check(true, "boot() 执行完毕无异常");

  const tick = (ms) => new Promise((r) => setTimeout(r, ms));
  await tick(80);

  // ---- 闸门：没过时工作台不许启动 ----
  const gateEl = elementFor("#gate");
  check(!gateEl.classList.contains("hide"), "闸门未过时遮罩可见");
  check(!called.includes("get_commands"), "闸门未过时不加载命令目录");
  check(elementFor("#gatePhrase").textContent === CHALLENGE, "遮罩里显示了确认语");

  // 输错不放行
  elementFor("#gateInput").value = "瞎写的";
  elementFor("#gateGo").dispatch("click", {});
  await tick(80);
  check(!gateEl.classList.contains("hide"), "确认语不对时遮罩不消失");
  check(!called.includes("get_commands"), "确认语不对时不加载命令");
  check(elementFor("#gateMsg").textContent === "不一致", "输错有提示");

  // 输对才放行
  elementFor("#gateInput").value = CHALLENGE;
  elementFor("#gateGo").dispatch("click", {});
  await tick(140);
  check(gateEl.classList.contains("hide"), "确认语正确后遮罩隐藏");
  check(called.includes("get_commands"), "过闸门后才加载命令目录");
  check(called.includes("get_log"), "启动时取了日志");
  check(called.includes("get_rules"), "启动时取了规则");
  check(called.includes("local_info"), "启动时取了本机地址");

  // 命令目录渲染进 DOM
  const groups = elementFor("#groups");
  check(groups.children.length > 0, "命令列表渲染出了内容");

  // 点第一条命令应填进 method
  const firstCmd = groups.children.find((c) => c.className === "cmd");
  check(!!firstCmd, "渲染出了可选命令行");
  if (firstCmd) {
    firstCmd.dispatch("click", {});
    check(elementFor("#method").value.includes("local."), "点命令后 method 被填入");
  }

  // 「按进程找」把端口写回输入框
  elementFor("#ports").value = "";
  elementFor("#discover").dispatch("click", {});
  await new Promise((r) => setTimeout(r, 60));
  check(elementFor("#ports").value === "33331", "发现端口后写回了端口输入框");

  // 复制按钮能触发桥
  const copyBtn = elementFor('[data-copy="log"]');
  copyBtn.dispatch("click", {});
  await new Promise((r) => setTimeout(r, 40));
  check(called.includes("copy_text"), "复制按钮调到了桥的 copy_text");

  // 危险命令要有锁标记
  const riskyRows = groups.children.filter(
    (c) => c.className === "cmd" && String(c.innerHTML).includes("lock")
  );
  check(riskyRows.length > 0, "危险命令渲染出了锁标记");

  // 危险开关点得动
  elementFor("#dangerToggle").dispatch("click", {});
  await tick(60);
  check(called.includes("set_dangerous"), "危险开关调到了桥的 set_dangerous");

  console.log("");
  if (failures) {
    console.log(`${failures} 项失败`);
    process.exit(1);
  }
  console.log("app.js 冒烟测试全部通过");
})();
