#!/bin/bash
source /home/cnw/miniconda3/etc/profile.d/conda.sh
conda activate car
cd /home/cnw/Desktop/code/lerobot_li
python lerobot/scripts/control_robot.py --robot.type=lekiwi --robot.ip=117.50.34.249 --robot.port=5515 --robot.video_port=5516 --robot.transmission_mode=cloud --control.type=remote_robot --control.transmission_mode=cloud --robot.status_report.endpoint=http://117.50.34.249:8003/api/robots/heartbeat