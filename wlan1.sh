#!/bin/bash

sudo rfkill unblock all
sudo ip link set wlan1 up
sudo wpa_supplicant -B -i wlan1 -c /etc/wpa_supplicant/wpa_supplicant-wlan1.conf
sudo dhcpcd -n wlan1
