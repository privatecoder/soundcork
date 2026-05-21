# Speaker Setup, Recovery, and Manual Procedures

This page collects speaker-side notes that are useful when Soundcork's admin UI
cannot complete setup by itself. It is based on local SoundTouch behavior and on
the German FHEM de-clouding notes, adapted to Soundcork's safer override-file
workflow.

## Safety Notes

- Shell access is unauthenticated root access. Keep it on a trusted LAN only.
- Remove persistent shell access after setup unless you intentionally need it.
- Do not edit `/opt/Bose/etc/SoundTouchSdkPrivateCfg.xml` for normal Soundcork
  use. Soundcork writes `/mnt/nv/OverrideSdkPrivateCfg.xml` instead, so the Bose
  firmware default file remains untouched.
- If a SoundTouch 10 is part of a stereo pair, unpair it before low-level file
  changes. Changing persistence files while paired can leave the unit unusable
  until firmware is reinstalled.

## Data to Capture Before Changes

Before redirecting a speaker, record its local API data:

```text
http://<speaker-ip>:8090/info
http://<speaker-ip>:8090/recents
http://<speaker-ip>:8090/preset
```

From `/info`, note:

- `deviceID`
- `margeAccountUUID`
- current device name and model

If you save XML from a browser, keep a valid XML declaration when creating local
files such as `DeviceInfo.xml`, `Recents.xml`, and `Presets.xml`. Soundcork's
admin UI normally imports the required data automatically, but these copies are
useful for repair or migration.

After shell access is enabled, the same persistence data is available under:

```sh
cd /mnt/nv/BoseApp-Persistence/1/
```

Copy at least `Sources.xml` from that directory if you are rebuilding a
Soundcork account manually. It can contain source credentials and local provider
state that are not exposed by every public speaker endpoint.

## USB Stick Requirements

Create a USB stick with:

- FAT32/vfat filesystem
- the filesystem bootable flag set where your partitioning tool exposes it
- one empty root-level file named exactly `remote_services`
- no extra root-level metadata files or editor-created `.txt` extension

Some firmware versions ignore sticks that contain macOS metadata such as
`.fseventsd`, `.Spotlight-V100`, or `._*` files. Keep the root clean.

SoundTouch 10 and SoundTouch 300 need a USB-OTG adapter: USB-A socket to
Micro-B plug with the OTG ID pin grounded. SoundTouch 20 can use its USB port
directly.

## Model-Specific USB Boot

### SoundTouch 10

1. If paired for stereo, remove the pair first.
2. Connect the prepared USB stick through a USB-OTG adapter.
3. Unplug power.
4. Plug power back in and wait for the normal boot sequence.
5. Watch the USB stick activity LED if it has one.

### SoundTouch 20

1. Insert the prepared USB stick directly into the speaker USB port.
2. Unplug power.
3. Plug power back in and wait for the normal boot sequence.

### SoundTouch 300

1. Connect the prepared USB stick through a USB-OTG adapter.
2. Unplug power.
3. Point the infrared remote at the speaker.
4. Hold the SoundTouch button on the remote.
5. While holding the button, plug power back in.
6. Release the button after the LEDs start blinking yellow.

## Logging In

After the USB boot, log in from the same LAN:

```sh
ssh root@<speaker-ip>
```

The password is empty. Modern OpenSSH clients may need legacy RSA algorithms:

```sh
ssh -o HostKeyAlgorithms=+ssh-rsa \
  -o PubkeyAcceptedAlgorithms=+ssh-rsa \
  root@<speaker-ip>
```

If SSH does not work, try telnet:

```sh
telnet <speaker-ip>
```

At the `rhino login` prompt, enter `root`; no password is required.

To keep shell access available after future reboots without the USB stick:

```sh
touch /mnt/nv/remote_services
```

Remove it again after successful setup if you do not want permanent root access:

```sh
rm /mnt/nv/remote_services
reboot
```

## Redirecting to Soundcork

Use `/admin` and **Switch to Soundcork** whenever possible. The admin action:

- copies speaker data into Soundcork's `DATA_DIR`
- writes `/mnt/nv/OverrideSdkPrivateCfg.xml`
- points Marge, stats, update, and BMX URLs at `BASE_URL`
- reboots the speaker

Manual direct editing of `/opt/Bose/etc/SoundTouchSdkPrivateCfg.xml` is not
recommended for Soundcork. If that file is malformed, recovery can require a
firmware reinstall. The override file is the reversible path.

If you are documenting or repairing a historical direct-edit installation, the
expected precautions are: change to `/opt/Bose/etc/`, remount the filesystem
writable with `rw`, make a backup of `SoundTouchSdkPrivateCfg.xml`, and only
then edit the file. Do not use this path for normal Soundcork onboarding.

The effective redirect values are:

```xml
<margeServerUrl>{BASE_URL}/marge</margeServerUrl>
<statsServerUrl>{BASE_URL}</statsServerUrl>
<swUpdateUrl>{BASE_URL}/updates/soundtouch</swUpdateUrl>
<bmxRegistryUrl>{BASE_URL}/bmx/registry/v1/services</bmxRegistryUrl>
```

If `BASE_URL` changes, rerun **Switch to Soundcork** so the speaker receives a
fresh override.

## Firmware Recovery / De-Bricking

If a speaker no longer joins Wi-Fi and does not offer its own setup access
point, firmware reinstall from USB may recover it. This is a last-resort
procedure.

General process:

1. Download the correct SoundTouch firmware package for the exact model.
2. Extract it and place the contained `.stu` firmware file on a bootable FAT32
   USB stick.
3. Boot the speaker into firmware recovery mode for that model.
4. Wait; reinstall can take several minutes.

SoundTouch 10 recovery sequence:

1. Connect the firmware USB stick through a USB-OTG adapter.
2. Unplug power.
3. Hold preset button `4` and volume-down on the speaker.
4. Plug power back in while holding both buttons.
5. Release the buttons after the speaker starts reading the USB stick.
6. Wait roughly five minutes for reinstall to finish.

Firmware archives may disappear from Bose-hosted URLs over time, so keep a local
copy of firmware that matches devices you maintain. Known starting points for
firmware lookup are:

- Internet Archive mirror:
  `https://archive.org/download/bose-soundtouch-software-and-firmware/`
- Bose SoundTouch USB update page:
  `https://downloads.bose.com/ced/soundtouch/soundtouch_usb/index.html`

## Manual SoundTouch 10 Stereo Pair

Two SoundTouch 10 speakers can be paired without Bose cloud services by editing
`GroupService.xml` on both devices. Prefer Soundcork's miniapp group controls
for normal zone grouping; this procedure is for manual stereo-pair recovery or
experimentation.

Preparation:

- enable shell access on both speakers
- decide which device is left/master and which is right/slave
- collect both device IDs and IP addresses
- work in `/mnt/nv/BoseApp-Persistence/1/` on each speaker

On an unpaired speaker, `GroupService.xml` normally contains an empty group.
Replace it on both speakers with the same group definition:

```xml
<group id="<group-id>">
  <name><pair-name></name>
  <masterDeviceId><left-device-id></masterDeviceId>
  <roles>
    <groupRole>
      <deviceId><left-device-id></deviceId>
      <role>LEFT</role>
      <ipAddress><left-speaker-ip></ipAddress>
    </groupRole>
    <groupRole>
      <deviceId><right-device-id></deviceId>
      <role>RIGHT</role>
      <ipAddress><right-speaker-ip></ipAddress>
    </groupRole>
  </roles>
  <senderIPAddress><left-speaker-ip></senderIPAddress>
</group>
```

On the right/slave speaker only, add this status element before `</group>`:

```xml
<status>GROUP_OK</status>
```

Reboot both speakers. Send playback commands to the left/master speaker; it
synchronizes the right/slave speaker.

## Troubleshooting

- If `/admin` shows **Reachable: No**, verify SSH or telnet works from the
  Soundcork host, not only from your laptop.
- If USB boot does not enable shell access, reformat the stick, ensure FAT32,
  set the bootable flag if available, remove hidden metadata, and retry.
- If Wi-Fi setup is unreliable during first SSH setup, try Ethernet for the
  initial boot and onboarding.
- If a speaker is already pointed at Soundcork but missing from the datastore,
  use **Add to Soundcork** in `/admin` to adopt it into an account.
- If you want to undo Soundcork, delete `/mnt/nv/OverrideSdkPrivateCfg.xml` and
  reboot, or use **Remove from Soundcork** / **Reset to Bose** in `/admin`.

## Source

Additional model-specific notes were cross-checked against the FHEM wiki page
"BOSE SoundTouch de-clouding":

```text
https://wiki.fhem.de/wiki/BOSE_SoundTouch_de-clouding
```
