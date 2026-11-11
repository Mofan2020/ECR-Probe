"""易启学（Seewo YiQiXue）课堂协议的报文构造。

命令名全部来自对 electronic-classroom.exe 静态分析的字符串表，
一个字符都没改（原程序里有两处拼写错误也照抄，见 SPELLING_NOTE）。

参数名是从配置键表和字段表推断的，标在每条命令的 params 里。
报文信封形状（method/params/traceId → code/message/data）同样是推断，
没有抓到过真实报文对齐——所以参数填错不会报错，只会被对端忽略。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

# 原程序里的拼写错误，逆向对接时必须按原文匹配，不能"修正"
SPELLING_NOTE = (
    "原二进制里 student.classromm.bindResultNotify 是 classromm（双 m），"
    "teacher.managent.getStudentAPPList 是 managent（少个 e）。按原文发，别改。"
)


@dataclass(frozen=True)
class Command:
    """一条命令。"""

    name: str
    namespace: str
    note: str = ""
    params_hint: dict[str, str] = field(default_factory=dict)

    @property
    def direction(self) -> str:
        """从 local.<源>-><目标> 里取出「源->目标」。"""
        if not self.name.startswith("local."):
            return ""
        head = self.name[len("local.") :]
        return head.split(".", 1)[0]


def _cmd(name: str, note: str = "", **params: str) -> Command:
    namespace = name.split(".")[0] if not name.startswith("local.") else "local"
    return Command(name=name, namespace=namespace, note=note, params_hint=dict(params))


# ---------------------------------------------------------------------------
# 局域网消息：local.<源>-><目标>.<动作>
#
# 这是学生端真正会响应的那一组。格式在 sub_49BE40 里集中注册，
# 44 条全部从字符串表读出。
# ---------------------------------------------------------------------------

LOCAL_COMMANDS: tuple[Command, ...] = (
    # 教师 -> 学生
    _cmd("local.teacher->student.addDevice", "登记设备",
         teacherUid="string", studentUids="string[]"),
    _cmd("local.teacher->student.notifyStart", "开始上课",
         classUid="string", teachingUid="string"),
    _cmd("local.teacher->student.notifyFinishTeaching", "下课"),
    _cmd("local.teacher->student.turnOnScreen", "恢复学生屏幕"),
    _cmd("local.teacher->student.turnOffScreen", "关闭学生屏幕"),
    _cmd("local.teacher->student.setProperty", "设置属性",
         className="string", classUid="string"),
    _cmd("local.teacher->student.turnOnMute", "取消静音"),
    _cmd("local.teacher->student.turnOffMute", "静音学生机"),
    _cmd("local.teacher->student.getStudentAppList", "取学生应用列表"),
    _cmd("local.teacher->student.notifyBindStudentResult", "绑定结果通知",
         bindStatus="int"),
    _cmd("local.teacher->student.remotecontrol.openApp", "远程打开应用",
         procName="string", icoPath="string"),
    _cmd("local.teacher->student.remotecontrol.openUrl", "远程打开网址",
         request="string"),
    _cmd("local.teacher->student.remotecontrol.keyboardMouse", "远程键鼠控制",
         eventType="int", aParam="int"),
    _cmd("local.teacher->student.notifyStartCollectClasswork", "开始收作业"),
    _cmd("local.teacher->student.notifyStopCollectClasswork", "停止收作业"),
    _cmd("local.teacher->student.notifyRefreshFileAndDir", "刷新共享文件列表"),
    _cmd("local.teacher->student.offlineBlackscreenMode", "离线黑屏",
         show="bool"),
    _cmd("local.teacher->student.studentBroadcastStartNotify", "学生广播开始"),
    _cmd("local.teacher->student.studentBroadcastStopNotify", "学生广播结束"),
    _cmd("local.teacher->student.classActivityStart", "课堂活动开始"),
    _cmd("local.teacher->student.classActivityEnd", "课堂活动结束"),
    _cmd("local.teacher->student.shutdown", "关闭学生机"),
    _cmd("local.teacher->student.reboot", "重启学生机"),
    _cmd("local.teacher->student.notifyClearDesktopApp", "清理桌面应用"),
    _cmd("local.teacher->student.UpdateProcessControlPolicy", "更新进程管控策略",
         processList="string[]"),
    _cmd("local.teacher->student.StartProcessControl", "启用进程管控"),
    _cmd("local.teacher->student.StopProcessControl", "停用进程管控"),
    _cmd("local.teacher->student.UpdateNetworkControlPolicy", "更新网络管控策略",
         networkInfos="object[]"),
    _cmd("local.teacher->student.StartNetworkControl", "启用网络管控"),
    _cmd("local.teacher->student.StopNetworkControl", "停用网络管控"),
    _cmd("local.teacher->student.broadcast.control", "广播控制"),
    _cmd("local.teacher->student.startBroadcast", "开始屏幕广播"),
    _cmd("local.teacher->student.stopBroadcast", "停止屏幕广播"),
    _cmd("local.teacher->student.startReceiveBroadcast", "开始接收广播"),
    _cmd("local.teacher->student.stopReceiveBroadcast", "停止接收广播"),
    _cmd("local.teacher->student.CollectStudentApplications", "收集学生应用"),
    _cmd("local.teacher->student.forwardRequest", "转发请求",
         method="string", params="object"),
    # 学生 -> 教师
    _cmd("local.student->teacher.notifyNewTeacherAdd", "新教师上线"),
    _cmd("local.student->teacher.notifyFinishBindStudent", "绑定完成"),
    _cmd("local.student->teacher.notifyGetCollectClasswork", "作业已收集"),
    _cmd("local.student->teacher.remotecontrol.keyboardMouse", "键鼠回传"),
    _cmd("local.student->teacher.broadcastReceiveStats", "广播接收统计",
         frame="int", total="int", decoded="int", dropped="int"),
    _cmd("local.student->teacher.forwardRequest", "转发请求"),
    # 学生 -> 学生（横向）
    _cmd("local.student->student.NotifyProcessCreate", "进程创建通知",
         procName="string", sign="string"),
)

# ---------------------------------------------------------------------------
# 学生端命名空间（云端 IoT 走这一组）
# ---------------------------------------------------------------------------

STUDENT_COMMANDS: tuple[Command, ...] = (
    _cmd("student.classroom.listStudentByName", "按名字查学生"),
    _cmd("student.classroom.bindDeviceToStudent", "设备绑学生"),
    _cmd("student.classroom.finishBindStudent", "完成绑定"),
    _cmd("student.classroom.turnOnMute"),
    _cmd("student.classroom.turnOffMute"),
    _cmd("student.classroom.notifyStart"),
    _cmd("student.classroom.notifyStop"),
    _cmd("student.classroom.blackScreen.on"),
    _cmd("student.classroom.blackScreen.off"),
    _cmd("student.classroom.notifyOfflineBlackScreenMode", "离线黑屏"),
    _cmd("student.classroom.startStudentBroadcast", "开始学生广播"),
    _cmd("student.classroom.stopStudentBroadcast", "停止学生广播"),
    _cmd("student.classromm.bindResultNotify", "绑定结果（原文双 m）"),
    _cmd("student.localNetwork.getState"),
    _cmd("student.localNetwork.state"),
    _cmd("student.remoteControl.launchCustomApp"),
    _cmd("student.remoteControl.getWinApp"),
    _cmd("student.remoteControl.notifyOpenApp"),
    _cmd("student.remoteControl.keyboardMouse"),
    _cmd("student.classWork.notifyTeacherGetClassWorkStatus"),
    _cmd("student.classWork.notifyStartCollectClassWork"),
    _cmd("student.classWork.notifyEndCollectClassWork"),
    _cmd("student.classWork.startCollectClassWork"),
    _cmd("student.classWork.endCollectClassWork"),
    _cmd("student.classWork.notifyGetClassWorkStatus"),
    _cmd("student.sharedFiles.noticeRefreshShareFileAndDir"),
    _cmd("student.sharedFiles.refreshShareFileAndDir"),
    _cmd("student.classActivity.notifyStart"),
    _cmd("student.classActivity.notifyEnd"),
    _cmd("student.desktop.monitor"),
    _cmd("student.desktop.preview"),
    _cmd("student.desktop.previewClose"),
    _cmd("student.desktop.previewStartRecord"),
    _cmd("student.desktop.previewStopRecord"),
    _cmd("student.desktop.previewRecordNotify"),
    _cmd("student.desktopBroadcast.start"),
    _cmd("student.desktopBroadcast.close"),
    _cmd("student.desktopBroadcast.addStudent"),
    _cmd("student.broadcast.pullControl"),
    _cmd("student.broadcast.viewControl"),
)

TEACHER_COMMANDS: tuple[Command, ...] = (
    _cmd("teacher.managent.getStudentAPPList", "取学生应用（原文少 e）"),
    _cmd("teacher.devctrl.getStudentProcessList"),
    _cmd("teacher.devctrl.notifyProcessInfo"),
    _cmd("teacher.devctrl.sendProcessPolicyToStudent"),
    _cmd("teacher.devctrl.startProcessControl"),
    _cmd("teacher.devctrl.stopProcessControl"),
    _cmd("teacher.devctrl.sendNetworkPolicyToStudent"),
    _cmd("teacher.devctrl.startNetworkControl"),
    _cmd("teacher.devctrl.stopNetworkControl"),
    _cmd("teacher.devctrl.getExeFileAttribute"),
    _cmd("teacher.device.addStudent"),
    _cmd("teacher.device.addTempDevice"),
    _cmd("teacher.device.addStudentNotify"),
    _cmd("teacher.desktopBroadcast.start"),
    _cmd("teacher.desktopBroadcast.close"),
    _cmd("teacher.desktopBroadcast.addStudent"),
    _cmd("teacher.desktopBroadcast.removeStudent"),
    _cmd("teacher.desktopBroadcast.startRecord"),
    _cmd("teacher.desktopBroadcast.stopRecord"),
    _cmd("teacher.desktopBroadcast.pullControl"),
    _cmd("teacher.desktopBroadcast.recordNotify"),
    _cmd("teacher.classroom.bindResult"),
    _cmd("teacher.remoteControl.openAPP"),
    _cmd("teacher.remoteControl.openUrl"),
    _cmd("teacher.remoteControl.keyboardMouse"),
    _cmd("teacher.classActivity.start"),
    _cmd("teacher.classActivity.end"),
    _cmd("teacher.envcheck.start"),
    _cmd("teacher.envcheck.stop"),
)

CLASSROOM_MGMT: tuple[Command, ...] = (
    _cmd("classroom.management.notifyStudentStatus"),
    _cmd("classroom.management.finishTeaching"),
    _cmd("classroom.management.notifyStudentBindDeviceLocal"),
    _cmd("classroom.management.turnOnScreen"),
    _cmd("classroom.management.turnOffScreen"),
    _cmd("classroom.management.turnOnMute"),
    _cmd("classroom.management.turnOffMute"),
    _cmd("classroom.management.shutdown"),
    _cmd("classroom.management.reboot"),
    _cmd("classroom.management.clearScreen"),
)

THING_SERVICE: tuple[Command, ...] = (
    _cmd("thing.service.configPush"),
    _cmd("thing.service.upgrade"),
    _cmd("thing.service.regist", "设备注册"),
    _cmd("thing.service.applyTeaching", "申请授课"),
    _cmd("thing.service.cancelApplyTeaching"),
    _cmd("thing.service.closeTeaching"),
    _cmd("thing.service.logoutDevice"),
    _cmd("thing.service.noticeDeviceInTeaching"),
    _cmd("thing.service.noticeDeviceJoinedNewTeaching"),
    _cmd("thing.service.noticeGetTeachingDevice"),
    _cmd("thing.service.setUserToken"),
    _cmd("thing.service.completeClassActivityNotice"),
    _cmd("thing.service.reqCollectLog"),
    _cmd("thing.service.noticeGetDeviceBindInfo"),
    _cmd("thing.service.wakeup"),
    _cmd("thing.service.reboot"),
    _cmd("thing.service.shutdown"),
    _cmd("thing.service.finishBindDevice"),
    _cmd("thing.service.listStudentByName"),
    _cmd("thing.service.bindDeviceToStudent"),
    _cmd("thing.service.reqAttribute"),
    _cmd("thing.service.respAttribute"),
    _cmd("thing.service.configNetwork"),
)

ALL_COMMANDS: tuple[Command, ...] = (
    LOCAL_COMMANDS
    + STUDENT_COMMANDS
    + TEACHER_COMMANDS
    + CLASSROOM_MGMT
    + THING_SERVICE
)

GROUPS: dict[str, tuple[Command, ...]] = {
    "局域网 local.*（学生端直接响应）": LOCAL_COMMANDS,
    "学生端 student.*": STUDENT_COMMANDS,
    "教师端 teacher.*": TEACHER_COMMANDS,
    "课堂管理 classroom.management.*": CLASSROOM_MGMT,
    "云端 thing.service.*": THING_SERVICE,
}


# ---------------------------------------------------------------------------
# 报文构造
# ---------------------------------------------------------------------------


@dataclass
class Request:
    """一条待发报文。保留原始 JSON 字符串，界面上要显示实际字节。"""

    method: str
    params: dict
    trace_id: str
    payload: str

    def pretty(self) -> str:
        try:
            return json.dumps(json.loads(self.payload), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            return self.payload


def new_trace_id() -> str:
    """traceId 的形状没有确认过，用 UUID4 去掉横线，够唯一就行。"""
    return uuid.uuid4().hex


def build_request(
    method: str,
    params: dict | None = None,
    *,
    trace_id: str | None = None,
    compact: bool = True,
) -> Request:
    """按推断出来的信封构造报文。

    请求：{"method": ..., "params": {...}, "traceId": ...}
    响应：{"code": ..., "message": ..., "data": {...}}

    params 里自动补一个时间戳，有些实现会拿它做时效校验。
    """
    body = dict(params or {})
    body.setdefault("timestamp", int(time.time() * 1000))
    tid = trace_id or new_trace_id()

    envelope = {"method": method, "params": body, "traceId": tid}
    payload = json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":") if compact else None,
    )
    return Request(method=method, params=body, trace_id=tid, payload=payload)


def parse_response(text: str) -> dict:
    """解析响应，尽力而为。对端回什么都不奇怪，不抛异常。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text, "parsed": False}
    if isinstance(data, dict):
        data.setdefault("parsed", True)
        return data
    return {"raw": text, "parsed": False}
