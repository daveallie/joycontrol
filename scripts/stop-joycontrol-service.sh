#!/bin/bash

if [ ! -f home/pi/digiimo-pi/joycontrol-device-id ]; then
  systemctl stop joycontrol
fi
