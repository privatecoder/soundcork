# Spotify Integration

Spotify support has two separate paths on SoundTouch speakers.

## Spotify Connect

Spotify Connect is local discovery plus Spotify's own streaming infrastructure.
It does not require Soundcork. Use the Spotify app, choose the SoundTouch
speaker as a playback target, and Spotify streams to the speaker.

## Soundcork-Managed Spotify Tokens

Soundcork can store Spotify OAuth credentials and answer Bose-style OAuth token
requests from speakers. This is intended for Spotify presets and SoundTouch
flows that expect Bose's Marge/OAuth behavior.

Spotify integration is optional. Leave the Spotify environment variables empty
if you do not use it.

## Configuration

Create an app in the Spotify Developer Dashboard:

- Redirect URI: `{BASE_URL}/mgmt/spotify/callback`
- API: Web API

Set:

```bash
SPOTIFY_CLIENT_ID=your-client-id
SPOTIFY_CLIENT_SECRET=your-client-secret
```

`SPOTIFY_REDIRECT_URI` is optional. The browser flow uses
`{BASE_URL}/mgmt/spotify/callback`.

## Linking an Account

Start the browser flow by opening this URL:

```text
http://<soundcork-host>:8001/mgmt/spotify/init
```

or, without a browser helper:

```bash
curl -X POST http://<soundcork-host>:8001/mgmt/spotify/init
```

The callback stores account tokens in:

```text
{DATA_DIR}/spotify/accounts.json
```

Verify linked accounts:

```bash
curl http://<soundcork-host>:8001/mgmt/spotify/accounts
```

## Source Configuration

The speaker still needs a Spotify source entry in the account `Sources.xml`.
If Soundcork copied the speaker's existing `Sources.xml`, this may already be
present. A source entry has this shape:

```xml
<source id="34" secret="{refresh_token}" secretType="token_version_3">
  <sourceKey type="SPOTIFY" account="{spotify_user_id}" />
</source>
```

The current UI does not fully manage Spotify source credentials. If Spotify
presets fail, inspect `{DATA_DIR}/spotify/accounts.json` and the account
`Sources.xml`.

## Speaker OAuth Requests

Speakers request tokens through Marge-style OAuth endpoints derived from the
configured Marge server. Soundcork handles token refresh using the stored
Spotify account credentials and returns the token JSON expected by firmware.

## Troubleshooting

- Confirm `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` are set.
- Confirm Spotify's registered redirect URI exactly matches the effective
  Soundcork callback URL.
- Confirm `BASE_URL` is reachable by browsers during OAuth and by speakers
  during playback.
- Check logs for `/mgmt/spotify/*` during account linking and `/marge/oauth/*`
  during speaker token refresh.
- If presets fail immediately after speaker boot, cast once via Spotify Connect
  to confirm the speaker itself can still reach Spotify.
