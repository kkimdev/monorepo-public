## Installing Chrome OS

Start with ChromeOS Flex for better hardware compatibility and a smoother experience; consider Brunch Framework later if you need Android apps.

- **[ChromeOS Flex](https://support.google.com/chromeosflex/answer/11541904):** Google's official ChromeOS variant for PCs and Macs.
- **[Brunch Framework](https://github.com/sebanc/brunch):** Community framework for running ChromeOS on PCs with Android app support.

### Brunch Framework settings

![Brunch Framework only](brunch-framework-only.svg)

[Configuration menu](https://github.com/sebanc/brunch/blob/main/Readme/troubleshooting-and-faqs.md#brunch-configuration-menu)

**General**

- Kernel: `6.12`
- Kernel Options: `enable_crosh_sudo`, `pwa`
- Boot verbose mode: `yes`

**Lenovo ThinkBook**

- Kernel Options: `acpi_power_button` to make power button power menu work.
- Kernel Commandline Parameters: `enforce_hyperthreading=1 initcall_blacklist=ucsi_acpi_platform_driver_init modprobe.blacklist=ucsi_acpi` to fix sleep mode.

## Terminal App Settings

Open [chrome-untrusted://terminal/html/terminal_settings.html](chrome-untrusted://terminal/html/terminal_settings.html) in Chrome's address bar.

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

Open [chrome://os-settings/crostini/details](chrome://os-settings/crostini/details) in Chrome's address bar and set up **Linux development environment** if needed.

In an **x86_64 ChromeOS Linux terminal**, fill in your Git identity and paste:

```bash
(
  set -e
  export GIT_USER_NAME="Your Name"
  export GIT_USER_EMAIL="your-email@example.com"
  sudo apt-get update
  sudo apt-get install -y git curl ca-certificates
  mkdir -p "$HOME/workspaces"
  cd "$HOME/workspaces"
  if [ ! -e monorepo-public ]; then
    git clone --filter=blob:none --sparse --depth=1 --branch main \
      https://github.com/kkimdev/monorepo-public.git
  fi
  if [ "$(git -C monorepo-public config --bool core.sparseCheckout)" = true ]; then
    git -C monorepo-public sparse-checkout add chromeos-dev-setup
  fi
  git -C monorepo-public ls-files --error-unmatch chromeos-dev-setup/setup.bash >/dev/null
  bash monorepo-public/chromeos-dev-setup/setup.bash
)
```

Reopen the terminal after installation. Keep the checkout at this path.

```bash
cros-setup   # Reapply local settings; leave running GUI services intact
cros-update  # Pull main's settings + patches + lock, then update Debian/Nix and apply
```

Updates require a clean `main` branch. To contribute, create a branch in
`~/workspaces/monorepo-public`, edit, commit, and push a PR (fork if needed).

Maintainers, from the repository root:

```bash
bash chromeos-dev-setup/setup.bash --build-only  # Check the locked build
bash chromeos-dev-setup/setup.bash --update      # Refresh dependencies and apply
```

### Optional local services

No configuration is needed for installation. The supervisor stays inactive
without `~/.config/local-services/process-compose.yaml`.
For personal background services, create that file and run:

```bash
systemctl --user start local-services.service
```

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

![Brunch Framework only](brunch-framework-only.svg)

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

1. Open [chrome://flags/#crostini-gpu-support](chrome://flags/#crostini-gpu-support), select **Enabled**, then reboot.
2. Add user to `render` group, this is already included in [`./setup.bash`](./setup.bash)
3. Run `, glxinfo -B | grep Accelerated` and confirm `Accelerated: yes`

## Troubleshooting

### Internal Microphone Not Working in Chrome

![Brunch Framework only](brunch-framework-only.svg)

**Known to affect:** ThinkBook 16 G6 IRL

**Symptom:** Selecting the DMIC manually restores the internal mic, but it
stops working again after disconnecting a Bluetooth microphone.

**Fix:** Use the installed CRAS UCM profile, which sets `FullySpecifiedUCM "1"`
and maps `Internal Mic` to the actual DMIC (`hw:sofhdadsp,6`, mixer `Dmic0`).
The default profile instead lets CRAS place the internal mic on analog PCM 0.

Open **ChromeOS crosh** (`Ctrl+Alt+T`), type `shell`, and run the following.
It skips an already applied fix, preserves the first backup, and restarts
CRAS only after a change. Playback and recording briefly stop on that restart.

```bash
sudo bash <<'SH'
set -euo pipefail
MIC_UCM_CONF=$(readlink -f /usr/share/alsa/ucm2/conf.d/sof-hda-dsp/sof-hda-dsp.conf)
if grep -Fq 'File "/conf.d/sof-hda-dsp/HiFi.conf"' "$MIC_UCM_CONF"; then
    echo "Already applied"
    exit 0
fi
test -f /usr/share/alsa/ucm2/conf.d/sof-hda-dsp/HiFi.conf
grep -Fq 'File "/Intel/sof-hda-dsp/HiFi.conf"' "$MIC_UCM_CONF"
MIC_UCM_BACKUP="$MIC_UCM_CONF.before-internal-mic"
[ -e "$MIC_UCM_BACKUP" ] || cp -p "$MIC_UCM_CONF" "$MIC_UCM_BACKUP"
trap 'cp -p "$MIC_UCM_BACKUP" "$MIC_UCM_CONF"' ERR

sed -i 's@File "/Intel/sof-hda-dsp/HiFi.conf"@File "/conf.d/sof-hda-dsp/HiFi.conf"@' "$MIC_UCM_CONF"
alsaucm -c sof-hda-dsp get '=FullySpecifiedUCM//HiFi' | grep -E '=1$'
alsaucm -c sof-hda-dsp get '=CapturePCM/Internal Mic/HiFi' | grep -E 'hw:sofhdadsp,6$'
alsaucm -c sof-hda-dsp get '=CaptureMixerElem/Internal Mic/HiFi' | grep -E '=Dmic0$'

trap - ERR
/sbin/initctl restart cras
printf 'Backup: %s\n' "$MIC_UCM_BACKUP"
SH
```

The script checks `FullySpecifiedUCM=1`, PCM `hw:sofhdadsp,6`, and mixer `Dmic0`.
A failed edit or check restores the backup without restarting CRAS.

**Verify:** Select `Internal Mic` in ChromeOS sound settings and test recording.
Then connect and use a Bluetooth mic, disconnect it, and test again without
manually changing the input. Also check the audio outputs you use.

```bash
cras_test_client --dump_s
```

The active `*` input should be `INTERNAL_MIC` on PCM `0,6` when testing the
internal mic. Device IDs may change after restarting CRAS; a USB microphone
may also be selected if connected.

**Rollback:** Restore the first backup. If the fix was applied with earlier
instructions, use the timestamped backup printed by that script instead.

```bash
MIC_UCM_CONF=$(readlink -f /usr/share/alsa/ucm2/conf.d/sof-hda-dsp/sof-hda-dsp.conf)
sudo cp -p "$MIC_UCM_CONF.before-internal-mic" "$MIC_UCM_CONF" && \
sudo /sbin/initctl restart cras
```

**Validation:** Internal mic capture and automatic return after Bluetooth
disconnect were verified on ThinkBook 16 G6 IRL on 2026-10-09. Reboot
persistence has not yet been tested. ChromeOS/Brunch updates may replace the
UCM files; recheck the active profile if the issue returns after an update.

### HDMI Audio Output Not Working

![Brunch Framework only](brunch-framework-only.svg)

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

**Known to affect:** ThinkBook 16 G9 AHP (ChromeOS Flex)

**Symptom:** Delayed initial movement and unreliable taps or tap-dragging.

**Fix:** Enable automatic reapplication (preview).

1. Open [chrome://os-settings/crostini/details](chrome://os-settings/crostini/details) in Chrome's address bar and set up **Linux development environment** if needed.
2. Open [chrome://flags/#gesture-properties-dbus-service](chrome://flags/#gesture-properties-dbus-service), select **Enabled**, then restart Chrome.
3. In Files, right-click **My files → Share with Linux**, confirm **OK**, then paste in the Linux terminal:

   ```bash
   (
     set -e
     cd "/mnt/chromeos/shared/MyFiles" 2>/dev/null || cd "/mnt/chromeos/MyFiles"
     command -v git >/dev/null || { sudo apt-get update && sudo apt-get install -y git; }
     git_metadata=$(mktemp -d)
     trap 'rm -rf "$git_metadata"' EXIT
     git clone --bare --depth=1 --filter=blob:none --single-branch --branch main \
       https://github.com/kkimdev/monorepo-public.git "$git_metadata/repo"
     rm -rf cros-touchpad
     mkdir cros-touchpad
     git --git-dir="$git_metadata/repo" --work-tree="$PWD/cros-touchpad" \
       read-tree --reset -u HEAD:chromeos-dev-setup/touchpad
   )
   ```

4. Open [chrome://extensions](chrome://extensions), enable **Developer mode**, then **Load unpacked → My files/cros-touchpad**.

To update, rerun the same command, then click the extension's **Reload** button.
For the old preview, load the new path once. Extension files stay in ChromeOS.

**Verify:** The setup page reports both values applied. Reboot and check movement,
taps, tap-dragging, and gestures.

**Notes:** Uses Secure Shell's public development ID for crosh access; no OS
developer mode or `shell` is needed. Keep `My files/cros-touchpad`. Each Chrome
startup finds the current `Touchpad` ID and reapplies both values. This replaces
Secure Shell **dev**, including its SSH features; preserve its settings first.

<details>
<summary>Manual crosh fallback</summary>

Open crosh (`Ctrl+Alt+T`), run `gesture_prop devices`, and replace `<id>`:

```text
gesture_prop set <id> "Change Timeout" array:double:0.0
gesture_prop set <id> "Motion Tap Prevent Timeout" array:double:0.0
```

Manual changes reset when Chrome restarts; device IDs can change.

</details>

## References
- https://old.reddit.com/r/Crostini/wiki/index
- https://www.reddit.com/r/ChromeOSFlex/comments/1jp4paa/guide_bringing_android_functionality_to_chromeos/
- https://www.reddit.com/r/ChromeOSFlex/comments/swxlz8/tutorial_enable_developer_mode_on_cros/
- https://www.reddit.com/r/ChromeOSFlex/comments/1449a13/guide_reenable_chromeos_flex_developer_mode/
