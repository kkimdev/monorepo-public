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

### HDMI Audio Output

Reference: https://github.com/sebanc/brunch/issues/2273

1. `[Ctrl]+[Alt]+T` to open crosh terminal
2. Type `shell` then `[Enter]`
3. Copy-paste the following for one-off enabling.
```bash
CARD_NUM=$(aplay -l | grep -i "HDMI" | head -n 1 | cut -d" " -f2 | tr -d ":")
CARD_NUM=${CARD_NUM:-0}
sudo amixer -c $CARD_NUM sset IEC958 on
echo "One-time activation complete for Card: $CARD_NUM"
```
4. If HDMI output is working, copy-paste the following to permanently enable.
```bash
sudo bash -c "
cat <<EOF > /etc/init/hdmi-audio.conf
description \"Activate HDMI Audio at Boot\"
author \"User\"
start on started system-services
stop on stopping system-services
task
script
    sleep 10
    /usr/bin/amixer -c $CARD_NUM sset IEC958 on
end script
EOF

chmod 644 /etc/init/hdmi-audio.conf
initctl reload-configuration
initctl start hdmi-audio
echo \"Permanent boot job created for Card: $CARD_NUM\"
"
```

## References
- https://old.reddit.com/r/Crostini/wiki/index
- https://www.reddit.com/r/ChromeOSFlex/comments/1jp4paa/guide_bringing_android_functionality_to_chromeos/
- https://www.reddit.com/r/ChromeOSFlex/comments/swxlz8/tutorial_enable_developer_mode_on_cros/
- https://www.reddit.com/r/ChromeOSFlex/comments/1449a13/guide_reenable_chromeos_flex_developer_mode/
