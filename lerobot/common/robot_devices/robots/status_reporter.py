from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib import error, request

from lerobot.common.robot_devices.robots.configs import RobotStatusReportConfig


def _guess_host_ip() -> Optional[str]:
    """Try to detect the primary IPv4 address of the robot."""
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


@dataclass
class HeartbeatPayload:
    robot_id: str
    robot_name: str
    boot_id: str
    uptime_seconds: int
    report_at: str
    ip: Optional[str]

    def as_dict(self) -> dict:
        return {
            "robotId": self.robot_id,
            "robotName": self.robot_name,
            "bootId": self.boot_id,
            "uptimeSeconds": self.uptime_seconds,
            "reportAt": self.report_at,
            "ip": self.ip,
        }


class HeartbeatReporter:
    """
    定时向后台上报机器人状态，便于后台展示实时开机/离线状态。
    """

    def __init__(self, config: RobotStatusReportConfig):
        self._config = config
        self._boot_id = f"{config.robot_id}-{uuid.uuid4().hex[:8]}"
        self._start_ts = time.time()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ip_cache = _guess_host_ip()

    def start(self):
        if not self._config.enabled:
            print(f"[Heartbeat] 状态上报未启用 (enabled={self._config.enabled})")
            return
        if not self._config.endpoint:
            print(f"[Heartbeat] 状态上报端点未配置 (endpoint={self._config.endpoint})")
            return
        if self._thread and self._thread.is_alive():
            print("[Heartbeat] 状态上报线程已在运行")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="robot-heartbeat", daemon=True)
        self._thread.start()
        print(f"[Heartbeat] 状态上报线程已启动，端点: {self._config.endpoint}, 机器人ID: {self._config.robot_id}")

    def stop(self):
        if not self._thread:
            return
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        print("[Heartbeat] 状态上报线程已停止")

    def _loop(self):
        """心跳上报主循环，包含异常处理确保线程不会崩溃"""
        interval = max(1, self._config.interval_seconds or 5)
        consecutive_failures = 0
        max_failures_before_warning = 10
        
        while not self._stop_event.is_set():
            try:
                self._send_once()
                consecutive_failures = 0  # 成功则重置失败计数
            except Exception as exc:
                # 捕获所有异常，确保线程不会因为任何错误而停止
                consecutive_failures += 1
                if consecutive_failures >= max_failures_before_warning:
                    print(f"[Heartbeat] 连续{consecutive_failures}次上报失败，最后错误: {exc}")
                    consecutive_failures = 0  # 重置计数，避免日志刷屏
                else:
                    print(f"[Heartbeat] 上报失败: {exc}")
            
            # 等待下次上报，即使发生异常也继续循环
            self._stop_event.wait(interval)

    def _send_once(self):
        """发送一次心跳，可能抛出异常"""
        payload = self._build_payload()
        data = json.dumps(payload.as_dict()).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._config.token:
            headers["Authorization"] = f"Bearer {self._config.token}"

        req = request.Request(self._config.endpoint, data=data, headers=headers, method="POST")
        timeout = max(1, self._config.timeout_seconds or 3)
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                resp.read()
        except error.URLError as exc:
            # 将网络异常重新抛出，由 _loop 统一处理
            raise

    def _build_payload(self) -> HeartbeatPayload:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        uptime = int(time.time() - self._start_ts)
        ip = self._ip_cache or _guess_host_ip()
        if self._ip_cache is None and ip:
            self._ip_cache = ip
        return HeartbeatPayload(
            robot_id=self._config.robot_id,
            robot_name=self._config.robot_name or self._config.robot_id,
            boot_id=self._boot_id,
            uptime_seconds=max(0, uptime),
            report_at=now,
            ip=ip,
        )


