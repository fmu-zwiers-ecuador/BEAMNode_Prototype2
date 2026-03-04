# **(Current documentation is a Work-In-Progress)**
# **Intital Node/Supervisor Setup Guide**

# Basic Step-by-Step Guide For Node Setup

* Connect to a wifi network
* Clone the github repository by typing: 
    
        git clone https://github.com/fmu-zwiers-ecuador/BEAMNode_Prototype2
* Then go into the directory by typing:

        cd BEAMNode_Prototype2/installation_bash/
    * Type command:

                sudo chmod +x node_setup.sh
    * Then type:
            
                sudo bash node_setup.sh
* Then the user just follows the following prompts and everything should be installed smoothly, without the user having to do anything extra.

# Basic Step-by-Step Guide For Supervisor Setup

* Connect to a wifi network
* Clone the github repository by typing: 
    
        git clone https://github.com/fmu-zwiers-ecuador/BEAMNode_Prototype2
* Then go into the directory by typing:

        cd BEAMNode_Prototype2/installation_bash/
    * Type command:
                
                sudo chmod +x supervisor_setup.sh
    * Then type:
                
                sudo bash supervisor_setup.sh
* If you did not reboot, type **cd --** to travel back to the root directory. 
* Then type **cd ..** twice and travel to directory:

        cd etc/wpa_supplicant/
* Then create a new file by typing:
        sudo nano wpa_supplicant-wlan1.conf

* In this file, type:

        crtl_interface=DIR=/var/run/wpa_supplicant.conf
        update_config=1
        country=US

        network={
                ssid="FMU"
                key_mgmt=WPA-EAP
                eap=PEAP
                identity="noel.challa"
                password="password"
                phase2="auth-MSCHAPV2"
                priority=1
        }

* Then type **cd --** to travel back to the root directory.

* Create a new file by typing:
        
        sudo nano wlan1.sh

* In this file type:
        #!/bin/bash

        sudo rfkill unblock all
        sudo ip link set wlan1 up
        sudo wpa_asupplicant -B -i wlan1 -c /etc/wpa_supplicant/wpa_supplicant-wlan1.conf
        sudo dhcpdc -n wlan1

