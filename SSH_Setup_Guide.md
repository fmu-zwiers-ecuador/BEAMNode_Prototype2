# **SSH Setup Guide**

* On the supervisor node, type:
    
        ssh-keygen -t ed25519
* Press enter for all prompts
* Then, copy the key to all nodes, for example:
        
        ssh-copy-id pi@node1.local
* If asked for a password, simply just type "password"
* To verify, attempt to ssh into the nodes afterwards. For example:
        
        ssh pi@node1.local