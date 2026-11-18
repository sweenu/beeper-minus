# Beeper Minus

A small, self-hosted sidecar for [Beeper](https://www.beeper.com) that brings three Beeper Plus features to
your own server:

- **Voice note transcription**: local [whisper.cpp](https://github.com/ggml-org/whisper.cpp) or any
  OpenAI-compatible speech-to-text API. Automatic or on demand, shown under the audio in the web app and
  optionally posted to your *Note to self*.
- **Snooze**: archives a chat and unarchives it at the time you pick.
- **Send later**: schedule a message from the chat list or from inside a conversation; edit or unschedule it.

It talks to the Beeper Client API served by **Beeper Server** (headless) or Beeper Desktop. It is not a
Beeper client replacement: the web app only shows your chats and these three features.

## How it works

One Python process (aiohttp):

- listens to Beeper's WebSocket for new voice notes (automatic transcription),
- runs a 15-second scheduler for snoozes and scheduled messages (state in SQLite),
- optionally serves the web app (Svelte + TypeScript, phone friendly, with a setup screen that signs Beeper
  Server in) and a small API. The browser never
  sees the Beeper token: the backend proxies a whitelist of Beeper endpoints (`/v1/accounts`, `/v1/chats`,
  `/v1/messages`, `/v1/assets/serve`, `/v1/app/setup`) and adds it.

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
                notes_to_self_chat_id = "!abc:beeper.com";
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

## Development

```sh
nix develop                     # python + aiohttp, node, ffmpeg
python -m tests.test_app        # end-to-end test against a fake Beeper
python -m tests.fake_beeper &   # fake Beeper on :23373 with token "test-token"
python -m beeper_minus config.toml
cd web && npm install && npm run dev   # proxies /api to :8099
```
