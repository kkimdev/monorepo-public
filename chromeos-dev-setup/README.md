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

**Symptom:** Internal mic works at ALSA level (`arecord`) but not in Chrome.

**Root cause:** Chrome uses CRAS (ChromeOS Audio Server) which may route the "default" capture device to a PCM node or channel that doesn't carry the internal mic signal. The dedicated DMIC PCM exists but is not selected by default.

**Quick test** — one-liner to detect and apply the fix:
```bash
CARD=$(arecord -l | grep DMIC | head -1 | sed 's/.*card \([0-9]*\).*/\1/')
DMIC=$(cras_test_client --dump_s | grep "sof-hda-dsp: :$CARD,6" | awk '{print $1}')
amixer -c $CARD cset name='Dmic0 Capture Switch' on,on
cras_test_client --select_input $DMIC:0
echo "Applied: card=$CARD DMIC_CRAS_ID=$DMIC"
```

Then test in Chrome. If it works, create a permanent Upstart job:

```bash
sudo tee /etc/init/internal-mic.conf > /dev/null <<'CONF'
description "Fix Internal Mic Routing at Boot"
author "User"
start on started cras
task
script
    sleep 3
    CARD=$(arecord -l | grep DMIC | head -1 | sed 's/.*card \([0-9]*\).*/\1/')
    DMIC=$(cras_test_client --dump_s | grep "sof-hda-dsp: :$CARD,6" | awk '{print $1}')
    logger "internal-mic: CARD=$CARD DMIC=$DMIC"
    /usr/bin/amixer -c $CARD cset name='Dmic0 Capture Switch' on,on && logger "internal-mic: amixer OK" || logger "internal-mic: amixer FAILED"
    /usr/bin/cras_test_client --select_input $DMIC:0 && logger "internal-mic: select_input OK" || logger "internal-mic: select_input FAILED"
end script
CONF

sudo chmod 644 /etc/init/internal-mic.conf
sudo /sbin/initctl reload-configuration
sudo /sbin/initctl start internal-mic         # first time; use restart after reboot
echo "Permanent mic fix created"
```

Verify the fix is active (no reboot needed):
```bash
CARD=$(arecord -l | grep DMIC | head -1 | sed 's/.*card \([0-9]*\).*/\1/')
amixer -c $CARD cget name='Dmic0 Capture Switch'                 # should show "values=on,on"
cras_test_client --dump_s | grep -A40 "Input Nodes" | grep "\*"   # should show * on the DMIC node
```

To debug if it fails after reboot:
```bash
sudo grep "internal-mic" /var/log/messages | tail -5
```

> **Notes:**
> - Assumes DMIC is ALSA PCM device 6 (standard for Intel SOF HDA). If different, change `6` after `:,$CARD,` to the correct device number from `arecord -l`.
> - Assumes kcontrol name is `Dmic0 Capture Switch` (standard Intel naming). If `amixer` errors, find the correct name with `amixer -c $CARD contents | grep -i dmic | grep Switch`.
### HDMI Audio Output

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

## References
- https://old.reddit.com/r/Crostini/wiki/index
- https://www.reddit.com/r/ChromeOSFlex/comments/1jp4paa/guide_bringing_android_functionality_to_chromeos/
- https://www.reddit.com/r/ChromeOSFlex/comments/swxlz8/tutorial_enable_developer_mode_on_cros/
- https://www.reddit.com/r/ChromeOSFlex/comments/1449a13/guide_reenable_chromeos_flex_developer_mode/
