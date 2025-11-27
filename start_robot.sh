 #!/bin/bash
conda activate car
cd /home/cnw/Desktop/code/lerobot_li
python lerobot/scripts/control_robot.py --robot.type=lekiwi --control.type=remote_robot --robot.status_report.enabled=true --robot.status_report.endpoint=http://172.16.16.90:8003/api/robots/heartbeat