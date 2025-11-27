## 控制端 / 机器人端常用命令（LeKiwi）

> 以下命令在 `D:\LiRiPeng\Project\lerobot` 根目录执行，默认已进入 `conda activate lerobot` 环境。

### 启动控制端（本地模式）

```powershell
python lerobot/scripts/control_robot.py `
  --robot.type=lekiwi `
  --robot.ip=172.16.16.86 `
  --robot.port=5515 `
  --robot.video_port=5516 `
  --control.type=teleoperate `
  --control.transmission_mode=local `
  --control.fps=10 `
  --control.display_data=true
```

- `robot.ip/port/video_port` 指向局域网内的机器人端设备
- `control.transmission_mode=local`：控制端通过本地网络直连机器人
- `control.display_data=true` 时需要本地具备 GUI 环境

### 启动控制端（云模式）

```powershell
python lerobot/scripts/control_robot.py `
  --robot.type=lekiwi `
  --robot.ip=117.50.34.249 `
  --robot.port=5555 `
  --robot.video_port=5556 `
  --control.type=teleoperate `
  --control.transmission_mode=cloud `
  --control.fps=10 `
  --control.display_data=true
```

- `robot.ip/port/video_port` 指向云端服务器（ZeroMQ 转发服务）
- 控制端通过云端转发器与机器人通信

### 启动机器人（本地模式）

```powershell
python lerobot/scripts/control_robot.py `
  --robot.type=lekiwi `
  --control.type=remote_robot
```

- 默认使用 `LeKiwiRobotConfig` 中的 `transmission_mode="local"`、`port=5555`、`video_port=5556`
- 若与控制端在同一局域网，保持默认即可

### 启动机器人（云模式）

```powershell
python lerobot/scripts/control_robot.py `
  --robot.type=lekiwi `
  --robot.ip=117.50.34.249 `
  --robot.port=5515 `
  --robot.video_port=5516 `
  --robot.transmission_mode=cloud `
  --control.type=remote_robot `
  --control.transmission_mode=cloud
```

- `robot.transmission_mode=cloud` 会让 `lekiwi_remote` 以 `SUB/PUSH` 方式连到远端 ZeroMQ
- `control.transmission_mode=cloud` 也应保持一致，确保控制端发送的指令能被云端转发

> 小贴士：
> - 控制端/机器人端的 `transmission_mode` 必须一致（local/云）
> - 如果云端端口与默认值不一致，可通过命令行覆盖 `--robot.port` 等参数

### 常用参数说明（可选项 / 默认值）

| 参数 | 是否必须 | 默认值（`LeKiwiRobotConfig`） | 说明 |
|------|----------|------------------------------|------|
| `--robot.type` | 是 | `lekiwi` | 机器人型号，脚本根据类型加载配置 |
| `--control.type` | 是 | 无 | 控制模式，如 `teleoperate`、`remote_robot` |
| `--robot.ip` | 否 | `117.50.34.249`（云）或局域网 IP | 控制端/机器人端连接的服务器或对端 IP，可通过命令覆盖 |
| `--robot.port` | 否 | `5555`（控制端 → 云端）/ `5555`（机器人默认） | ZeroMQ 指令端口；本地模式通常 `5515` |
| `--robot.video_port` | 否 | `5556`（控制端）/ `5516`（机器人端默认） | ZeroMQ 视频端口 |
| `--robot.transmission_mode` | 否 | `local` | `local` 表示局域网直连、`cloud` 表示连接云端转发器 |
| `--control.transmission_mode` | 否 | 与 `robot.transmission_mode` 相同 | 建议保持一致，否则指令/视频不同步 |
| `--control.fps` | 否 | `30` | 控制循环频率，示例中限制为 10 FPS 便于调试 |
| `--control.display_data` | 否 | `false` | 是否打开本地可视化窗口；在服务器环境可设为 `false` |
| `--robot.status_report_enabled` | 否 | `true` | 心跳上报开关；设为 `false` 可禁用 |
| `--robot.status_report_endpoint` | 否 | `http://117.50.34.249:8003/api/robots/heartbeat` | 心跳 HTTP 接口地址 |
| `--robot.status_report_robot_id` | 否 | `lekiwi-main` | 在后台展示的机器人唯一 ID，可按需修改 |

> 心跳发送间隔（`status_report.interval_seconds`，默认 5 秒）请直接在配置文件中调整，无需命令行覆盖。

> 其他未覆盖的参数都可以通过 `python lerobot/scripts/control_robot.py --help` 查看，或在 `lerobot/common/robot_devices/robots/configs.py` 中查找对应默认值。

### 心跳上报启动与验证

1. **确认配置**：在 `lerobot/common/robot_devices/robots/configs.py` 中确保 `LeKiwiRobotConfig.status_report.enabled = True`，`endpoint` 指向后端 `POST /api/robots/heartbeat`。  
   - 如需仅通过命令行开启/覆盖，可在任何启动命令后追加（注意使用下划线 `_`）：  
     ```powershell
     --robot.status_report_enabled=true `
     --robot.status_report_endpoint=http://117.50.34.249:8003/api/robots/heartbeat `
     --robot.status_report_robot_id=lekiwi-main `
     --robot.status_report_robot_name="LeKiwi 移动操作臂"
     ```
   - 若不想上报，可显式 `--robot.status_report_enabled=false`
2. **启动机器人脚本**（本地/云均可，例如云模式）：

```powershell
python lerobot/scripts/control_robot.py `
  --robot.type=lekiwi `
  --robot.ip=117.50.34.249 `
  --robot.port=5515 `
  --robot.video_port=5516 `
  --robot.transmission_mode=cloud `
  --control.type=remote_robot `
  --control.transmission_mode=cloud
```

- 脚本成功启动后，终端会打印 `[Heartbeat] 状态上报线程已启动，端点: ...`

3. **手动测试心跳接口（可选）**：

```powershell
curl -X POST http://117.50.34.249:8003/api/robots/heartbeat `
     -H "Content-Type: application/json" `
     -d '{"robotId":"lekiwi-main","robotName":"LeKiwi 移动操作臂"}'
```

4. **后台验证**：打开后台管理页面或调用 `GET /api/robots`，应能看到 `robotId` 对应的在线状态、最后心跳时间等实时更新。

