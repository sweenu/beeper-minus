# Beeper Minus

A small, self-hosted sidecar for [Beeper](https://www.beeper.com) that brings two Beeper Plus features to your
own server:

- **Voice note transcription**: local [whisper.cpp](https://github.com/ggml-org/whisper.cpp) or any
  OpenAI-compatible speech-to-text API. Automatic or on demand, shown under the audio in the web app and
  optionally posted to your *Note to self*.
- **Send later**: schedule a message from the chat list or from inside a conversation; edit or unschedule it.
- **MCP for AI assistants** (`web.mcp = true`), at `/mcp` on the web app's address: Beeper's own MCP
  tools (search chats and messages, send, react...), which Beeper Server only offers on localhost, plus
  ours: search voice note transcripts and transcribe one; list, schedule, edit or unschedule messages;
  open a message's PDF, image or text file; see who reacted to a message, and wait for a reply. Beeper's `list_messages`
  and `search_messages` keep their parameters but are answered by Beeper Minus: one compact line per
  message, names and texts cleaned up, voice notes with their transcripts. If Beeper ever adds a tool
  with one of our names, ours is kept.

It talks to the Beeper Client API served by **Beeper Server** (headless) or Beeper Desktop. It is not a
Beeper client replacement: the web app only shows your chats and these two features.

## How it works

One Python process (Starlette, served by uvicorn):

- listens to Beeper's WebSocket for new voice notes (automatic transcription),
- runs a 15-second scheduler for scheduled messages (state in SQLite),
- optionally serves the web app (Svelte + TypeScript, phone friendly, with a setup screen that signs Beeper
  Server in) and a small API. The browser never
  sees the Beeper token: the backend proxies a whitelist of Beeper endpoints (`/v1/accounts`, `/v1/chats`,
  `/v1/messages`, `/v1/assets/serve`, `/v1/app/setup`, `/v1/bridges`, where logins can be listed but not removed) and adds it.

All configuration lives in a TOML file: see [`config.example.toml`](config.example.toml). Turn the web app off
for a headless "transcribe into Note to self" setup.

> [!WARNING]
> The web app can read and send your messages, and has no password. It listens on localhost by default;
> read [SECURITY.md](SECURITY.md) before exposing it.
> It only answers requests for `localhost`, `web.host` and the names in `web.allowed_hosts`: list the
> name you open it with (your proxy's domain, your Tailscale name). Changes are only accepted from the
> app's own page, so other websites you visit can't send messages through it.
>
> With `tailscale serve` in front, `web.allowed_users` turns on a real login: only the Tailscale users
> listed get in.

## NixOS

```nix
{
  inputs.beeper-minus.url = "github:sweenu/beeper-minus";

  outputs = { nixpkgs, beeper-minus, ... }: {
    nixosConfigurations.server = nixpkgs.lib.nixosSystem {
      modules = [
        beeper-minus.nixosModules.default
        ({ pkgs, ... }: {
          nixpkgs.config.allowUnfree = true; # Beeper Server
          services.beeper-server.enable = true;
          services.beeper-minus = {
            enable = true;
            whisperPackage = pkgs.whisper-cpp-vulkan; # GPU; add "render" to SupplementaryGroups
            settings = {
              # Optional: without it, sign in from the web app's setup screen.
              # beeper.token_file = "/run/secrets/beeper-token"; # readable by the `beeper` user
              transcription = {
                engine = "whisper-cpp";
                auto = true;
                notes_to_self = true;
                whisper-cpp.model = pkgs.fetchurl {
                  url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin";
                  hash = "sha256-H8cPd0046xaZk6w5Huo1fvR8iHV+9y7llDh5t+jivGk=";
                };
              };
            };
          };
        })
      ];
    };
  };
}
```

- `services.beeper-server`: the headless Beeper client (`pkgs.beeper-server`, x86_64 only), on
  `127.0.0.1:23373`, state in `/var/lib/beeper`. Log in once through `POST /v1/app/setup/*`.
- `services.beeper-minus`: `settings` is the TOML config; `beeper.url` defaults to the local Beeper Server.

Both run as the `beeper` user.

### First-time Beeper Server setup

Leave `beeper.token_file` unset and open the web app: while Beeper Server isn't signed in, it shows a setup
screen that signs in with your email (or creates an account), then unlocks your encrypted messages with
your recovery key or by comparing emojis with another of your devices. The token it gets is kept in
`state_dir/beeper-token` (readable by the `beeper` user only), and Beeper Minus starts working right away.

This only works while Beeper Server is signed out. If it was signed in before (with a token you made by
hand, say), it refuses Beeper Minus until you either give that token in `beeper.token_file`, or reset
Beeper Server by deleting its data folder (`/var/lib/beeper/server` with the NixOS module) while it's
stopped. The web app explains this when it happens.

No OAuth is involved: Beeper Server can't approve OAuth requests (approval is a button in Beeper Desktop's
UI), but it accepts the account token its own sign-in returns as a read+write API token.

Headless, without the web app, sign in by hand and save the token as `beeper.token_file`:

```sh
api=http://127.0.0.1:23373
curl -X POST $api/v1/app/setup/start                      # -> setupRequestID
curl -X POST $api/v1/app/setup/email -H 'Content-Type: application/json' \
  -d '{"setupRequestID": "…", "email": "you@example.com"}'
curl -X POST $api/v1/app/setup/response -H 'Content-Type: application/json' \
  -d '{"setupRequestID": "…", "response": "<code from the email>"}'   # -> matrix.accessToken, shown once
curl -X POST $api/v1/app/setup/verification/recovery-key -H "Authorization: Bearer $token" \
  -H 'Content-Type: application/json' -d '{"recoveryKey": "…"}'
```

With Beeper Desktop instead, create a token from its Desktop API settings.

### Networks

The Accounts page (top right of the inbox) lists your networks and whether they work. Networks that run
in Beeper Cloud are connected from Beeper on your phone or computer, and the server picks them up. Networks
that run on this server (on-device ones, like WhatsApp) live in Beeper Server's data folder: link them from
the Accounts page, with a QR code, a pairing code or a form, as each network asks. Resetting Beeper Server
unlinks them. On Beeper's free plan a network counts once per account: if it's already set up on your phone
or computer, the page offers to add this server to that same login rather than a new one.

## Development

```sh
nix develop                     # python + aiohttp, node, ffmpeg
python -m tests.test_app        # end-to-end test against a fake Beeper
python -m tests.fake_beeper &   # fake Beeper on :23373 with token "test-token"
python -m beeper_minus config.toml
cd web && npm install && npm run dev   # proxies /api to :8099
npm test && npm run check              # web: formatting allowlist, types
```
