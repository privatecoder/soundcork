# Radio Browser Sources

[radio-browser.info](https://www.radio-browser.info/) is a community-maintained
internet radio station directory. Soundcork can expose Radio Browser stations as
SoundTouch content items when the speaker/account has a `RADIO_BROWSER` source.

## Add the Source

Add a source entry to the account `Sources.xml` if it is not already present:

```xml
<source>
  <sourceKey type="RADIO_BROWSER" account="" />
</source>
```

Depending on speaker state, you may need to reboot the speaker or rerun
**Switch to Soundcork** so the source list is refreshed.

## Preset Content Item

Use a Radio Browser station UUID as the `location`:

```xml
<ContentItem
    source="RADIO_BROWSER"
    type="stationurl"
    isPresetable="true"
    location="/stations/byuuid/9610c454-0601-11e8-ae97-52543be04c81">
  <itemName>Station Name</itemName>
  <containerArt></containerArt>
</ContentItem>
```

Find UUIDs on the Radio Browser website or API. The UUID appears in station URLs
such as:

```text
https://www.radio-browser.info/history/d28420a4-eccf-47a2-ace1-088c7e7cb7e0
```

## Test Directly Against a Speaker

Replace `<uuid>` and `<speaker-ip>`:

```bash
curl -d '<ContentItem source="RADIO_BROWSER" type="stationurl" location="/stations/byuuid/<uuid>"/>' \
  http://<speaker-ip>:8090/select
```

## Notes

- Radio Browser provides metadata and stream URLs; actual playback still depends
  on the target stream being reachable by the speaker.
- If the source does not appear on the speaker, check the account `Sources.xml`
  served by Soundcork and the speaker's `/sources` endpoint.
- If a station fails, try another station from the same directory before
  assuming the source setup is wrong.
