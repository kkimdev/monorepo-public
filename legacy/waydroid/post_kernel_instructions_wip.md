Need to confirm  if the following instructions are correct.



# Copy and paste into (termina) chronos@localhost (Ctrl+Alt+T)
lxc config set penguin security.privileged true
lxc config device add penguin loop-control unix-char path=/dev/loop-control
for i in {0..7}; do lxc config device add penguin loop$i unix-block path=/dev/loop$i; done
lxc restart penguin


# Inside container

#!/bin/bash
# 1. Fix Sudo permissions (in case of UID shift)
sudo chown root:root /etc/sudoers /etc/sudo.conf /etc/sudoers.d -R 2>/dev/null

# 2. Setup Binder nodes
sudo mkdir -p /dev/binderfs
sudo mount -t binder binder /dev/binderfs
sudo chmod 666 /dev/binderfs/*
sudo ln -sf /dev/binderfs/binder /dev/binder
sudo ln -sf /dev/binderfs/hwbinder /dev/hwbinder
sudo ln -sf /dev/binderfs/vndbinder /dev/vndbinder

# 3. Start Waydroid
sudo systemctl restart waydroid-container
echo "Starting Waydroid session... please wait."
waydroid session start
