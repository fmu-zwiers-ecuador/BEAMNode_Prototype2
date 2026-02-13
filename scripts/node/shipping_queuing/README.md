# BEAM Automated Node Monitoring & Data Collection System

## Overview
This repository contains automation logic for the BEAM project's distributed sensor network.
A central **Supervisor Raspberry Pi** manages multiple **Node devices**, communicating over a BATMAN-based mesh.
The Supervisor:

- Monitors node health every 10 minutes  
- Logs alive/dead transitions  
- Requests node data once per day  
- Retries failed nodes intelligently  
- Stores incoming ZIP data in organized per-node directories  

All scheduling is automated via cron jobs configured by `setup_beam_cron.sh`.

---

## 📁 Directory Structure
```
BEAMNode_Prototype2/
└── scripts/
    └── node/
        └── shipping_queuing/
            ├── ping_nodes_10min.py
            ├── retryqueue.py
            ├── node_states.json
            ├── setup_beam_cron.sh
            └── logs/ (auto-created)
```

---

## ⚙️ Node Devices
Each node:
- Collects environmental data
- Stores it under `/home/pi/data/`
- Produces **one ZIP per day** stored in `/home/pi/shipping/`
- Filename format:  
  ```
  nodeX_YYYY-MM-DD.zip
  ```

Old ZIPs are deleted daily so Supervisor receives **one clean file per node per day**.

---

## 🛰️ Supervisor Device
The Supervisor:

- Pings nodes every **10 minutes**  
- Writes node states into `node_states.json`  
- Executes `retryqueue.py` daily at **19:00 (7 PM)**  
- Retries failed transfers up to **5 times**  
- Saves incoming ZIPs into:
  ```
  /home/pi/data/node1/
  /home/pi/data/node2/
  ...
  ```

---

## 🧠 Node State Tracking
`node_states.json` format example:

```json
"node3": {
  "ip": "192.168.1.3",
  "node_state": "dead",
  "transfer_fail": true
}
```

Updated by:
- `ping_nodes_10min.py` → alive/dead state  
- `retryqueue.py` → transfer success/failure state  

---

## 🔧 Python Scripts

### **1. ping_nodes_10min.py**
Runs every 10 minutes.

- Pings all nodes  
- Logs to `logs/ping.log`  
- Updates `node_states.json`  
- Detects transitions:
  - alive → dead  
  - dead → alive  

---

### **2. retryqueue.py**
Runs daily at 7 PM.

- Loads updated node states  
- Attempts to pull the daily ZIP file  
- Retries **up to 5 times** per failed node  
- Logs everything to `logs/queue.log`  
- Creates per-node folder structure  

---

### **3. setup_beam_cron.sh**
Fully automatic setup script:

- Creates `/home/pi/logs/`  
- Fixes BEAM directory permissions  
- Installs cron jobs under user `pi`:  
  ```
  */10 * * * * ping_nodes_10min.py
  0 19 * * * retryqueue.py
  ```
- Removes duplicates safely  
- Prints final crontab  

---

## 🛠️ Installation Instructions

### 1. Ensure scripts are in:
```
/home/pi/BEAMNode_Prototype2/scripts/node/shipping_queuing/
```

### 2. Run the setup script:
```bash
chmod +x /home/pi/setup_beam_cron.sh
./setup_beam_cron.sh
```

### 3. Verify cron installed correctly:
```bash
crontab -l
```

Expected output:
```
*/10 * * * * /usr/bin/python3 /home/pi/BEAMNode_Prototype2/scripts/node/shipping_queuing/ping_nodes_10min.py
0 19 * * * /usr/bin/python3 /home/pi/BEAMNode_Prototype2/scripts/node/shipping_queuing/retryqueue.py
```

---

## 📄 Log Files
Located in:

```
/home/pi/logs/ping.log
/home/pi/logs/queue.log
```

Each log includes timestamps and detailed system events.

---

## 🔄 Daily Workflow Summary

1. Nodes create one daily ZIP  
2. Supervisor checks node health every 10 minutes  
3. Supervisor requests data at 19:00  
4. Retries failed nodes  
5. Saves ZIPs into per-node directories  

---

## ⚠️ Important Notes

- **Do not run setup script with sudo.** Cron jobs must install under user `pi`.
- If paths change later, update them inside `setup_beam_cron.sh`.
- Ensure `node_states.json` remains writable by user `pi`.

---

## 👨‍💻 Maintainer
**Noel Challa**  
Francis Marion University — BEAM Research Initiative  

---

## 📬 Support
Open a GitHub issue for enhancements or debugging assistance.
