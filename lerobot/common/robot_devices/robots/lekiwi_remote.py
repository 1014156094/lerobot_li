# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import json
import threading
import time
from pathlib import Path

import cv2
import zmq

from lerobot.common.robot_devices.robots.mobile_manipulator import LeKiwi
from lerobot.common.robot_devices.robots.status_reporter import HeartbeatReporter


def create_sub_socket(context, ip, port):
    """
    创建并配置 SUB socket 用于订阅命令消息（cloud 模式）。
    
    Args:
        context: ZMQ context
        ip: 服务器 IP 地址
        port: 服务器端口
        
    Returns:
        配置好的 SUB socket，已连接但需要等待延迟
    """
    cmd_socket = context.socket(zmq.SUB)
    cmd_socket.setsockopt(zmq.CONFLATE, 1)
    # 设置重连参数，提高连接稳定性
    cmd_socket.setsockopt(zmq.RECONNECT_IVL, 100)  # 初始重连间隔 100ms
    cmd_socket.setsockopt(zmq.RECONNECT_IVL_MAX, 1000)  # 最大重连间隔 1s
    cmd_socket.setsockopt(zmq.LINGER, 0)  # 关闭时立即丢弃消息
    cmd_socket.setsockopt_string(zmq.SUBSCRIBE, "")  # 需要订阅所有消息才能正常接收数据 (空主题)
    cmd_socket.connect(f"tcp://{ip}:{port}")
    # SUB socket 需要短暂延迟才能正常接收消息
    # 原因：ZeroMQ PUB/SUB 模式的 "slow joiner" 问题
    # - TCP 连接建立是异步的，需要时间完成握手
    # - SUB socket 需要与 PUB socket 完成订阅协议握手
    # - 在网络环境中，这个握手过程可能需要 100-200ms
    # 参考：https://zguide.zeromq.org/docs/chapter2/#The-Slow-Subscriber-Problem
    # 详细说明：docs/ZMQ_PUB_SUB_DELAY_EXPLANATION.md
    time.sleep(0.2)
    return cmd_socket


def create_push_socket(context, ip, port):
    """
    创建并配置 PUSH socket 用于发送视频消息（cloud 模式）。
    
    Args:
        context: ZMQ context
        ip: 服务器 IP 地址
        port: 服务器端口
        
    Returns:
        配置好的 PUSH socket，已连接
    """
    video_socket = context.socket(zmq.PUSH)
    video_socket.setsockopt(zmq.CONFLATE, 1)
    # 设置重连参数，提高连接稳定性
    video_socket.setsockopt(zmq.RECONNECT_IVL, 100)
    video_socket.setsockopt(zmq.RECONNECT_IVL_MAX, 1000)
    video_socket.setsockopt(zmq.LINGER, 0)
    video_socket.connect(f"tcp://{ip}:{port}")
    return video_socket


def setup_zmq_sockets(config):
    context = zmq.Context()
    
    # 根据 transmission_mode 参数决定连接方式
    if config.transmission_mode == "local":
        # 本地局域网连接
        cmd_socket = context.socket(zmq.PULL)
        cmd_socket.bind(f"tcp://*:{config.port}")
        cmd_socket.setsockopt(zmq.CONFLATE, 1)

        video_socket = context.socket(zmq.PUSH)
        video_socket.setsockopt(zmq.CONFLATE, 1)
        video_socket.bind(f"tcp://*:{config.video_port}")
        
        print(f"[INFO] Using local network connection. Listening on ports {config.port} and {config.video_port}")
    else:
        # 云服务器连接
        cmd_socket = create_sub_socket(context, config.ip, config.port)
        video_socket = create_push_socket(context, config.ip, config.video_port)
        
        print(f"[INFO] Using cloud server connection. Connecting to {config.ip}:{config.port} and {config.ip}:{config.video_port}")

    return context, cmd_socket, video_socket


def run_camera_capture(cameras, images_lock, latest_images_dict, stop_event):
    while not stop_event.is_set():
        local_dict = {}
        for name, cam in cameras.items():
            frame = cam.async_read()
            ret, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if ret:
                local_dict[name] = base64.b64encode(buffer).decode("utf-8")
            else:
                local_dict[name] = ""
        with images_lock:
            latest_images_dict.update(local_dict)
        time.sleep(0.01)


def calibrate_follower_arm(motors_bus, calib_dir_str):
    """
    Calibrates the follower arm. Attempts to load an existing calibration file;
    if not found, runs manual calibration and saves the result.
    """
    calib_dir = Path(calib_dir_str)
    calib_dir.mkdir(parents=True, exist_ok=True)
    calib_file = calib_dir / "main_follower.json"
    try:
        from lerobot.common.robot_devices.robots.feetech_calibration import run_arm_manual_calibration
    except ImportError:
        print("[WARNING] Calibration function not available. Skipping calibration.")
        return

    if calib_file.exists():
        with open(calib_file) as f:
            calibration = json.load(f)
        print(f"[INFO] Loaded calibration from {calib_file}")
    else:
        print("[INFO] Calibration file not found. Running manual calibration...")
        calibration = run_arm_manual_calibration(motors_bus, "lekiwi", "follower_arm", "follower")
        print(f"[INFO] Calibration complete. Saving to {calib_file}")
        with open(calib_file, "w") as f:
            json.dump(calibration, f)
    try:
        motors_bus.set_calibration(calibration)
        print("[INFO] Applied calibration for follower arm.")
    except Exception as e:
        print(f"[WARNING] Could not apply calibration: {e}")


def run_lekiwi(robot_config):
    """
    Runs the LeKiwi robot:
      - Sets up cameras and connects them.
      - Initializes the follower arm motors.
      - Calibrates the follower arm if necessary.
      - Creates ZeroMQ sockets for receiving commands and streaming observations.
      - Processes incoming commands (arm and wheel commands) and sends back sensor and camera data.
    """
    # Import helper functions and classes
    from lerobot.common.robot_devices.cameras.utils import make_cameras_from_configs
    from lerobot.common.robot_devices.motors.feetech import FeetechMotorsBus, TorqueMode

    # Initialize cameras from the robot configuration.
    cameras = make_cameras_from_configs(robot_config.cameras)
    for cam in cameras.values():
        cam.connect()

    # Initialize the motors bus using the follower arm configuration.
    motor_config = robot_config.follower_arms.get("main")
    if motor_config is None:
        print("[ERROR] Follower arm 'main' configuration not found.")
        return
    motors_bus = FeetechMotorsBus(motor_config)
    motors_bus.connect()

    # Calibrate the follower arm.
    calibrate_follower_arm(motors_bus, robot_config.calibration_dir)

    # Create the LeKiwi robot instance.
    robot = LeKiwi(motors_bus)

    # Define the expected arm motor IDs.
    arm_motor_ids = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]

    # Disable torque for each arm motor.
    for motor in arm_motor_ids:
        motors_bus.write("Torque_Enable", TorqueMode.DISABLED.value, motor)

    # Set up ZeroMQ sockets.
    context, cmd_socket, video_socket = setup_zmq_sockets(robot_config)

    # Start the camera capture thread.
    latest_images_dict = {}
    images_lock = threading.Lock()
    stop_event = threading.Event()
    
    # 启动心跳上报
    if hasattr(robot_config, 'status_report') and robot_config.status_report:
        heartbeat_reporter = HeartbeatReporter(robot_config.status_report)
        heartbeat_reporter.start()
    else:
        print("[WARNING] status_report 配置不存在或未启用，跳过心跳上报")
        heartbeat_reporter = None
    cam_thread = threading.Thread(
        target=run_camera_capture, args=(cameras, images_lock, latest_images_dict, stop_event), daemon=True
    )
    cam_thread.start()

    last_cmd_time = time.time()
    last_connection_check = time.time()
    consecutive_errors = 0
    max_consecutive_errors = 5
    is_cloud_mode = robot_config.transmission_mode != "local"
    
    print("LeKiwi robot server started. Waiting for commands...")

    try:
        while True:
            loop_start_time = time.time()

            # Process incoming commands (non-blocking).
            while True:
                try:
                    msg = cmd_socket.recv_string(zmq.NOBLOCK)
                    consecutive_errors = 0  # 重置错误计数
                    last_cmd_time = time.time()
                except zmq.Again:
                    # 没收到数据则跳过
                    break
                except (zmq.ZMQError, zmq.ContextTerminated) as e:
                    # ZMQ 连接错误，需要重连
                    consecutive_errors += 1
                    error_msg = f"[ERROR] ZMQ connection error: {e}"
                    print(error_msg)
                    
                    # 如果是 cloud 模式且连续错误，尝试重连
                    if is_cloud_mode and consecutive_errors >= max_consecutive_errors:
                        print(f"[WARNING] 连续 {consecutive_errors} 次错误，尝试重新连接命令socket...")
                        try:
                            # 关闭旧socket
                            cmd_socket.close()
                            # 创建新socket并重连（使用辅助函数）
                            cmd_socket = create_sub_socket(context, robot_config.ip, robot_config.port)
                            consecutive_errors = 0
                            print(f"[SUCCESS] 命令socket重连成功")
                        except Exception as reconnect_error:
                            print(f"[ERROR] 重连失败: {reconnect_error}")
                            time.sleep(1)  # 避免频繁重连
                    break
                except Exception as e:
                    # 其他未预期的错误
                    print(f"[ERROR] Unexpected error receiving command: {e}")
                    break
                    
                # 解析和执行命令
                try:
                    data = json.loads(msg)
                    # Process arm position commands.
                    if "arm_positions" in data:
                        arm_positions = data["arm_positions"]
                        print(f"[机械臂指令] 手臂位置指令: {arm_positions}")
                        if not isinstance(arm_positions, list):
                            print(f"[ERROR] Invalid arm_positions: {arm_positions}")
                        elif len(arm_positions) < len(arm_motor_ids):
                            print(
                                f"[WARNING] Received {len(arm_positions)} arm positions, expected {len(arm_motor_ids)}"
                            )
                        else:
                            for motor, pos in zip(arm_motor_ids, arm_positions, strict=False):
                                motors_bus.write("Goal_Position", pos, motor)
                    # Process wheel (base) commands.
                    if "raw_velocity" in data:
                        raw_command = data["raw_velocity"]
                        print(f"[轮子指令] 轮子速度指令: {raw_command}")
                        # Expect keys: "left_wheel", "back_wheel", "right_wheel".
                        command_speeds = [
                            int(raw_command.get("left_wheel", 0)),
                            int(raw_command.get("back_wheel", 0)),
                            int(raw_command.get("right_wheel", 0)),
                        ]
                        robot.set_velocity(command_speeds)
                        last_cmd_time = time.time()
                except Exception as e:
                    print(f"[ERROR] Parsing message failed: {e}")

            # 连接健康检查（仅在 cloud 模式下）
            # 如果超过 10 秒未收到命令，可能是连接问题
            if is_cloud_mode:
                now = time.time()
                if now - last_connection_check >= 10.0:
                    time_since_last_cmd = now - last_cmd_time
                    if time_since_last_cmd > 10.0:
                        print(f"[WARNING] 已超过 {time_since_last_cmd:.1f} 秒未收到命令，检查连接状态...")
                        # 尝试通过非阻塞接收测试连接
                        try:
                            cmd_socket.recv_string(zmq.NOBLOCK)
                        except zmq.Again:
                            # 正常情况，只是没有新消息
                            pass
                        except Exception as e:
                            print(f"[WARNING] 连接测试失败: {e}")
                    last_connection_check = now

            # Watchdog: stop the robot if no command is received for over 0.5 seconds.
            now = time.time()
            if now - last_cmd_time > 0.5:
                robot.stop()
                last_cmd_time = now

            # Read current wheel speeds from the robot.
            current_velocity = robot.read_velocity()

            # Read the follower arm state from the motors bus.
            follower_arm_state = []
            for motor in arm_motor_ids:
                try:
                    pos = motors_bus.read("Present_Position", motor)
                    # Convert the position to a float (or use as is if already numeric).
                    follower_arm_state.append(float(pos) if not isinstance(pos, (int, float)) else pos)
                except Exception as e:
                    print(f"[ERROR] Reading motor {motor} failed: {e}")

            # Get the latest camera images.
            with images_lock:
                images_dict_copy = dict(latest_images_dict)

            # Build the observation dictionary.
            observation = {
                "images": images_dict_copy,
                "present_speed": current_velocity,
                "follower_arm_state": follower_arm_state,
            }

            # Send the observation over the video socket.
            try:
                observation_json = json.dumps(observation)
                video_socket.send_string(observation_json)
                # 计算图像数量用于打印
                num_images = len(images_dict_copy) if images_dict_copy else 0
                print(f"[SUCCESS] 发送视频画面成功 - 图像数量: {num_images}, 速度: {current_velocity}, 手臂状态: {len(follower_arm_state)}个关节")
            except Exception as e:
                num_images = len(images_dict_copy) if images_dict_copy else 0
                print(f"[ERROR] 发送视频画面失败 - 图像数量: {num_images}, 速度: {current_velocity}, 手臂状态: {len(follower_arm_state)}个关节, error: {e}")

            # Ensure a short sleep to avoid overloading the CPU.
            elapsed = time.time() - loop_start_time
            time.sleep(
                max(0.033 - elapsed, 0)
            )  # If robot jitters increase the sleep and monitor cpu load with `top` in cmd
    except KeyboardInterrupt:
        print("Shutting down LeKiwi server.")
    finally:
        stop_event.set()
        cam_thread.join()
        robot.stop()
        motors_bus.disconnect()
        if heartbeat_reporter:
            heartbeat_reporter.stop()
        cmd_socket.close()
        video_socket.close()
        context.term()
