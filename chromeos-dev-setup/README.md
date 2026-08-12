## Recommended Brunch Framework Options
Reference: https://github.com/sebanc/brunch/blob/main/Readme/troubleshooting-and-faqs.md#brunch-configuration-menu

General
- Kernel: `6.12`
- Kernel Options: `enable_crosh_sudo`, `pwa`
- Boot verbose mode: `yes`

Lenovo Thinkbook Specific
- Kernel Options: `acpi_power_button` to make power button power menu work.
- Kernel Commandline Parameters: `enforce_hyperthreading=1 initcall_blacklist=ucsi_acpi_platform_driver_init modprobe.blacklist=ucsi_acpi` to fix sleep mode.

## Terminal App Settings

Navigate to `chrome-untrusted://terminal/html/terminal_settings.html`

![Terminal App Keyboard & mouse tab](Terminal%20App%20Keyboard%20&%20mouse%20tab.png)
![Terminal App behavior tab](Terminal%20App%20behavior%20tab.png)

## Terminal App Nerd Font

References
- https://vcfvct.wordpress.com/2021/11/14/chromeos-dev-setup/
- https://www.reddit.com/r/Crostini/comments/s1dgvk/best_way_to_get_nerd_fonts_on_crostini/

`[Ctrl]+[Shift]+J` in Terminal App then paste the following.

```javascript
await term_.prefs_.set('font-family', '"JetBrainsMono Nerd Font", "JetBrains Mono"');
await term_.prefs_.set('user-css-text', '@font-face {font-family: "JetBrainsMono Nerd Font"; src: url("https://raw.githubusercontent.com/ryanoasis/nerd-fonts/master/patched-fonts/JetBrainsMono/Ligatures/Regular/JetBrainsMonoNerdFont-Regular.ttf"); font-weight: normal; font-style: normal;}')
```

## Setup Chrome OS Crostini

[`./setup.bash`](./setup.bash)

Use `cros-setup` to reapply the configuration without restarting user services
or replacing the current shell. Home Manager prints any service restart
suggestions instead of interrupting ongoing terminal or GUI work.

Use the existing `cros-reset` command later to deliberately restart the
compositor stack and apply deferred GUI-service changes. Running `cros-reset`
closes Linux GUI applications.

## SSH Key Generation & Github Registration
```bash

# https://docs.github.com/en/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519

# https://docs.github.com/en/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account
# Paste the output to https://github.com/settings/keys
cat ~/.ssh/id_ed25519.pub
```

## Keyboard Backlight Timeout Config

1. `[Ctrl]+[Alt]+T` to open crosh terminal
2. Type `shell` then `[Enter]`
3.
```bash
cat /usr/share/power_manager/keyboard_backlight_keep_on_ms
# 24 hours in milliseconds
echo "86400000" | sudo tee /usr/share/power_manager/keyboard_backlight_keep_on_ms

sudo restart powerd
```

## Enable GPU Acceleration

Reference: https://www.reddit.com/r/chromeos/comments/1u0niv3/crostini_linux_on_chromeos_is_stuck_using/

1. Enable chrome://flags#crostini-gpu-support and reboot.
2. Add user to `render` group, this is already included in [`./setup.bash`](./setup.bash)
3. Run `, glxinfo -B | grep Accelerated` and confirm `Accelerated: yes`

## Troubleshooting

### Internal Microphone Not Working in Chrome

**Known to affect:** ThinkBook 16 G6 IRL

**Symptom:** Internal mic works at ALSA level (`arecord`) but not in Chrome.

**Root cause:** Chrome uses CRAS (ChromeOS Audio Server) which may route the "default" capture device to a PCM node or channel that doesn't carry the internal mic signal. The dedicated DMIC PCM exists but is not selected by default.

**Steps to fix:**
```bash
PAIR=$(arecord -l | sed -n 's/^card \([0-9][0-9]*\):.*device \([0-9][0-9]*\): DMIC.*/\1 \2/p' | head -1)
CARD=${PAIR%% *}
DEVICE=${PAIR#* }
DMIC=$(cras_test_client --dump_s | awk -v target=":$CARD,$DEVICE" '
  /^Input Devices:/ { in_inputs=1; next }
  /^Input Nodes:/ { in_inputs=0 }
  in_inputs && index($0, target) { print $1; exit }
')
amixer -c "$CARD" cset name='Dmic0 Capture Switch' on,on
cras_test_client --select_input "$DMIC:0"
echo "Applied: ALSA=$CARD,$DEVICE CRAS_NODE=$DMIC:0"
```

Then test in Chrome. If it works, create a permanent Upstart job:

```bash
sudo tee /etc/init/internal-mic.conf > /dev/null <<'CONF'
description "Fix Internal Mic Routing at Boot"
author "User"
start on started system-services
stop on stopping system-services
task
script
    for i in $(seq 1 30); do
        PAIR=$(arecord -l | sed -n 's/^card \([0-9][0-9]*\):.*device \([0-9][0-9]*\): DMIC.*/\1 \2/p' | head -1)
        CARD=${PAIR%% *}
        DEVICE=${PAIR#* }
        DMIC=$(cras_test_client --dump_s | awk -v target=":$CARD,$DEVICE" '
            /^Input Devices:/ { in_inputs=1; next }
            /^Input Nodes:/ { in_inputs=0 }
            in_inputs && index($0, target) { print $1; exit }
        ')
        logger "internal-mic: attempt=$i CARD=$CARD DEVICE=$DEVICE DMIC=$DMIC"
        [ -n "$CARD" ] && [ -n "$DEVICE" ] && [ -n "$DMIC" ] && break
        sleep 2
    done
    if [ -z "$CARD" ] || [ -z "$DEVICE" ] || [ -z "$DMIC" ]; then
        logger "internal-mic: discovery FAILED CARD=$CARD DEVICE=$DEVICE DMIC=$DMIC"
        exit 1
    fi
    /usr/bin/amixer -c "$CARD" cset name='Dmic0 Capture Switch' on,on && logger "internal-mic: amixer OK" || logger "internal-mic: amixer FAILED"
    /usr/bin/cras_test_client --select_input "$DMIC:0" && logger "internal-mic: select_input OK node=$DMIC:0" || logger "internal-mic: select_input FAILED node=$DMIC:0"

    sleep 1
    ACTIVE=$(cras_test_client --dump_s | awk '
        /^Input Nodes:/ { in_nodes=1; next }
        /^Attached clients:/ { in_nodes=0 }
        in_nodes && $2 ~ /^[0-9]+:[0-9]+$/ && $0 ~ /X\*/ { print $2; exit }
    ')
    if [ "$ACTIVE" = "$DMIC:0" ]; then
        logger "internal-mic: verification OK active=$ACTIVE"
    else
        logger "internal-mic: verification FAILED expected=$DMIC:0 active=$ACTIVE"
        exit 1
    fi
end script
CONF

sudo chmod 644 /etc/init/internal-mic.conf
sudo /sbin/initctl reload-configuration
echo "Permanent mic fix created"
```

Test the Upstart job immediately without rebooting:
```bash
sudo /sbin/initctl stop internal-mic 2>/dev/null || true
sudo /sbin/initctl start internal-mic
sudo /sbin/initctl status internal-mic
sudo grep "internal-mic" /var/log/messages | tail -30
```

`internal-mic stop/waiting` is expected after a successful run because the job is
declared as a one-time `task`. The logs should contain `verification OK`.

Verify that the job selected the correct CRAS input:
```bash
PAIR=$(arecord -l | sed -n 's/^card \([0-9][0-9]*\):.*device \([0-9][0-9]*\): DMIC.*/\1 \2/p' | head -1)
CARD=${PAIR%% *}
DEVICE=${PAIR#* }
STATE=$(cras_test_client --dump_s)
DMIC=$(printf '%s\n' "$STATE" | awk -v target=":$CARD,$DEVICE" '
  /^Input Devices:/ { in_inputs=1; next }
  /^Input Nodes:/ { in_inputs=0 }
  in_inputs && index($0, target) { print $1; exit }
')
EXPECTED="$DMIC:0"
ACTIVE=$(printf '%s\n' "$STATE" | awk '
  /^Input Nodes:/ { in_nodes=1; next }
  /^Attached clients:/ { in_nodes=0 }
  in_nodes && $2 ~ /^[0-9]+:[0-9]+$/ && $0 ~ /X\*/ { print $2; exit }
')

amixer -c "$CARD" cget name='Dmic0 Capture Switch' | grep 'values='
echo "Expected CRAS input: $EXPECTED (ALSA hw:$CARD,$DEVICE)"
echo "Active CRAS input:   $ACTIVE"

if [ -n "$DMIC" ] && [ "$ACTIVE" = "$EXPECTED" ]; then
  echo "PASS: CRAS is using the dedicated DMIC"
else
  echo "FAIL: CRAS is not using the dedicated DMIC"
  echo "Repair with: cras_test_client --select_input $EXPECTED"
fi
```

To debug if it fails after reboot:
```bash
sudo grep "internal-mic" /var/log/messages | tail -10
```

> **Why `started failsafe`?** Custom `/etc/init/*.conf` files on Brunch are loaded during `boot-services`, which fires *after* `started cras` — so `started cras or startup` is missed. `failsafe` is the last boot event and guarantees ALSA/CRAS are ready. The retry loop handles the remaining race where the DMIC device isn't enumerated yet at `failsafe` time.
>
> **Notes:**
> - Assumes kcontrol name is `Dmic0 Capture Switch` (standard Intel naming). If `amixer` errors, find the correct name with `amixer -c $CARD contents | grep -i dmic | grep Switch`.

### HDMI Audio Output Not Working

**Known to affect:** ThinkBook 16 G6 IRL

Reference: https://github.com/sebanc/brunch/issues/2273

1. `[Ctrl]+[Alt]+T` to open crosh terminal
2. Type `shell` then `[Enter]`
3. Copy-paste the following for one-off enabling.
```bash
CARD_NUM=$(aplay -l | grep -i "HDMI" | head -n 1 | cut -d" " -f2 | tr -d ":")
CARD_NUM=${CARD_NUM:-0}
echo "Checking status for Card: $CARD_NUM"
sudo amixer -c $CARD_NUM get IEC958
sudo amixer -c $CARD_NUM sset IEC958 on
echo "One-time activation complete for Card: $CARD_NUM"
```
4. If HDMI output is working, create a permanent Upstart job:
```bash
sudo tee /etc/init/hdmi-audio.conf > /dev/null <<'CONF'
description "Activate HDMI Audio at Boot"
author "User"
start on started system-services
stop on stopping system-services
task
script
    sleep 10
    CARD=$(aplay -l | grep -i HDMI | head -1 | sed 's/.*card \([0-9]*\).*/\1/')
    CARD=${CARD:-0}
    logger "hdmi-audio: CARD=$CARD Starting"
    /usr/bin/amixer -c $CARD sset IEC958 on && logger "hdmi-audio: amixer OK" || logger "hdmi-audio: amixer FAILED"
end script
CONF

sudo chmod 644 /etc/init/hdmi-audio.conf
sudo /sbin/initctl reload-configuration
sudo /sbin/initctl start hdmi-audio
echo "Permanent HDMI fix created"
```

To debug if it fails after reboot:
```bash
sudo grep "hdmi-audio" /var/log/messages | tail -5
```

### Touchpad initial touch delay and tap suppression

**Known to affect:** ThinkBook 16 G9 AHP

**Symptom:** The first ~0.2s of each touch is ignored — cursor only begins moving ~0.2s after touchdown, and tap-to-click fails intermittently (taps either complete before the delay expires or are suppressed when preceded by finger motion). Even very light touches work after the delay, confirming the issue is time-based, not pressure-based.

**Root cause:** The gestures library `ImmediateInterpreter` uses conservative timeouts that don't suit this hardware: `Change Timeout` (0.2s) holds a newly-landed finger in a "pending" state before committing to pointer movement, while `Motion Tap Prevent Timeout` prevents taps from registering when preceded by recent motion. Zeroing both removes these artificial delays.

**Steps to fix:**

1. Enable `chrome://flags/#gesture-properties-dbus-service` → **Enabled** → Restart Chrome.
2. Open crosh with `Ctrl+Alt+T` (`gesture_prop` is a crosh built-in; no `shell` needed).
3. Find the touchpad device ID and check current timeout values:
   ```
   crosh> gesture_prop devices
   crosh> gesture_prop get <device_id> "Change Timeout"
   crosh> gesture_prop get <device_id> "Motion Tap Prevent Timeout"
   ```
4. Zero both timeout properties:
   ```
   crosh> gesture_prop set <device_id> "Change Timeout" array:double:0.0
   crosh> gesture_prop set <device_id> "Motion Tap Prevent Timeout" array:double:0.0
   ```

Verify the fix is active (no reboot needed):
```
crosh> gesture_prop get <device_id> "Change Timeout"              # should show 0.0
crosh> gesture_prop get <device_id> "Motion Tap Prevent Timeout"  # should show 0.0
```
Cursor should respond instantly after touchdown, and tap-to-click should work reliably even when preceded by finger motion.

> **Notes:**
> - On the affected ThinkBook 16 G9 AHP, `Change Timeout` (0.2s) and `Motion Tap Prevent Timeout` both caused delays; setting both to 0.0 fixed the touchpad.
> - Value syntax uses dbus-send format: `array:double:0.0` (floats), `array:boolean:false` (bools), `array:int32:1` (ints), comma-separated for multiple values.
> - Changes take effect immediately but **do not persist across reboots**. A startup script running after Chrome starts is required for persistence.
> - If further tuning is needed, check `Scroll Probe Max Time`, `Tap Timeout`, `Tap Pause`, `Keyboard Touchpad No Press Time`.

## References
- https://old.reddit.com/r/Crostini/wiki/index
- https://www.reddit.com/r/ChromeOSFlex/comments/1jp4paa/guide_bringing_android_functionality_to_chromeos/
- https://www.reddit.com/r/ChromeOSFlex/comments/swxlz8/tutorial_enable_developer_mode_on_cros/
- https://www.reddit.com/r/ChromeOSFlex/comments/1449a13/guide_reenable_chromeos_flex_developer_mode/
