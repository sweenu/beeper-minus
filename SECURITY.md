# Security and privacy

Beeper Minus holds the keys to every chat on every network you linked to Beeper. Whoever can use its web
app, read its token or read its files can read your messages and send new ones as you. Read this before
exposing it anywhere.

## What is at stake

- **The web app is full access.** It lists all your chats, reads their messages and media, sends
  messages, schedules them, and archives chats. It acts as you, on WhatsApp, Signal, iMessage and every
  other network your Beeper account bridges.
- **The Beeper token is full access too**, and it works without Beeper Minus. A token from the setup
  screen is your Beeper account's access token for the Beeper Server device. Anyone who has it can use
  your account directly, not only through this app.
- **The state directory holds your data in plain text**: the token (`beeper-token`, if the setup
  screen made it), plus the SQLite database with your transcripts, scheduled messages and chat names. This
  includes chats that are end-to-end encrypted on their own network.

## Who can reach the web app

**There is no built-in password.** By default the app only listens on `127.0.0.1`. Pick one of these
ways to reach it:

1. **`tailscale serve` with `web.allowed_users`** (recommended). Serve adds HTTPS and tells the app
   which Tailscale user is connecting (`Tailscale-User-Login`), and only the users you list get in.
   - Keep `web.host` on `127.0.0.1`. Serve removes any `Tailscale-User-Login` a client sends, but a
     request that reaches the port directly can carry its own. Anyone with a shell on the server can do
     that.
   - Requests from tagged Tailscale devices carry no user, so they are refused.
   - Tailscale documents this header for `tailscale serve`, not for Tailscale Services (`svc:`), where
     we rely on Tailscale's current behavior.
2. **A reverse proxy that requires login** (oauth2-proxy, Authelia, your proxy's own auth...), with its
   domain in `web.allowed_hosts`. Beeper Minus can't check that the proxy did its job: make sure nothing
   reaches the app around it.
3. **Plain Tailscale, or an SSH tunnel, with no login.** Everyone who can reach the port has full access.
   On Tailscale that means every device your access rules let through, including nodes and people you
   shared the machine with. Restrict the port to your own user in your tailnet's access rules.

Pitfalls:

- **Don't set `web.host = "0.0.0.0"`** unless a firewall stands in front of it. That opens the app to
  your whole local network, and to the internet if the machine is reachable from it.
- **Use HTTPS beyond localhost.** Over plain HTTP on a LAN, anyone on the network sees your messages.
  Tailscale encrypts the traffic, but browsers still treat `http://` as insecure, so the setup screen's
  Copy button won't work there.
- **List the name you open the app with in `web.allowed_hosts`** (`localhost` and `web.host` are always
  allowed). Other names are refused, which stops another website from reaching the app by pointing its
  own domain at your server's address (DNS rebinding).

## Built-in protections

- **The browser never sees the Beeper token.** The backend adds it to the requests it forwards to
  Beeper, and it only forwards accounts, chats, messages, media, setup and network sign-in endpoints.
  Removing a network's login, and everything else, stays out of reach.
- **Other websites can't act through your browser.** A page you visit can make your browser send
  requests to the app. Any request that changes something is refused unless it comes from the app's own
  page (`Sec-Fetch-Site` / `Origin`). Clients that send neither header, like `curl`, are let through:
  they aren't browsers, so this attack doesn't apply to them.
- **Files people send you can't run as the app.** Media is served from the app's address with
  `Content-Security-Policy: sandbox` and `nosniff`, so an `.html` or `.svg` attachment can't run scripts
  there.
- **The recovery key can't be replaced through the app.** Creating a recovery key replaces the old one
  and cuts access to your encrypted history. The app only allows it while the account has none at all.
- **Message formatting is rebuilt, never pasted in.** Formatted messages arrive as HTML. The app
  recreates only basic formatting (bold, italic, code, quotes, lists, line breaks) and links to
  `http`, `https` or `mailto` addresses, without any other attribute. Scripts, images, styles and
  everything else are reduced to plain text or dropped (`web/src/lib/richtext.ts`, tested by `npm test`).
- **Logs** contain chat and message IDs, never message text or the token.

## The MCP endpoint

`web.mcp = true` serves `/mcp` from the web app, for AI assistants. The web app's access rules apply to it
(allowed hosts, `web.allowed_users`, refusal of requests from other websites), and it has no login of its own.

- **It reads your messages and sends them, right away.** `/mcp` passes Beeper's own MCP tools through
  (`send_message`, `edit_message`, `delete_message`, `react`, `start_chat`...) with the Beeper Minus token,
  so the assistant can act as you on every network. Our own tools read every chat too.
- **Any program on a device that can reach the app can use it**, not only your AI assistant: Tailscale
  identifies the device and its user, not the program. `web.mcp_token_file` adds a token that only the
  programs you give it to know. It only guards `/mcp`: the web app's own API (`/api/...`), which can do
  even more, stays open to every program on those devices, as it is today.
- **`download_attachment` hands the assistant a link to the web app** (`/api/beeper/v1/assets/serve?...`),
  which works for anyone who can open the web app, and a `curl` command to save the file. File names are
  reduced to letters, digits, `.`, `-` and `_` first, so a name someone chose can't run anything in a shell.
- **PDFs anyone sends you are parsed on your server** (`pypdf`) when the assistant opens one with
  `read_attachment`. Files over 10 MB (5 MB for images) aren't opened, and parsing runs off the main loop,
  but a malicious PDF could still hit a bug in `pypdf`.
- **Messages and transcripts are written by other people.** Someone can send you "schedule a message to Bob
  saying...". The tools tell the assistant to treat them as data, and cut names and texts so nobody can
  pass as you (`[me]`) or fake another message, but an assistant can still be fooled: keep it asking for
  your approval before any tool that sends, edits, deletes, reacts or schedules.

## Setting Beeper Server up

- **Finish the setup screen right away, or do it on a private connection.** Until Beeper Server is
  signed in, anyone who can reach the web app can sign it in to *their* Beeper account. Beeper Minus would
  then act on their account.
- **The setup screen shows a new recovery key once**, for new accounts only. Save it before clicking
  "I've saved it". Recovery keys you type are passed to Beeper Server and not stored by Beeper Minus.
- **Revoking access:** sign the Beeper Server device out of your account from another Beeper client,
  then delete `beeper-token` from the state directory. If you registered a token by hand, remove it from
  Beeper Server's `conn:tokens`.
- **Beeper Server's own API** listens on `127.0.0.1:23373` and its sign-in endpoints need no token.
  Keep it on localhost.

## Secrets and files

- **Pass secrets as files** (`beeper.token_file`, `transcription.openai.api_key_file`), for example
  with agenix or sops-nix. Never write a token directly into your Nix configuration: everything in the
  Nix store is readable by every user on the machine.
- **The secret files must be readable by the `beeper` user** (the NixOS module runs both services as
  that user), and by nobody else.
- **Back up the state directory as carefully as the token**: it contains the token and your transcripts.
  The NixOS module keeps it private (`0700`), and the setup screen writes the token `0600`.
- **Versions before the commit that added this file wrote the token into the logs** whenever a request
  to Beeper failed. If you ran one, check with `journalctl -u beeper-minus | grep -c Bearer`. If it
  finds anything, revoke the token as above. The old entries stay readable by anyone allowed to read the
  journal until they expire: `journalctl --rotate --vacuum-time=1s` removes them, along with every other
  archived log on the machine.

## Privacy of transcription

- **`whisper-cpp` runs on your server.** Audio never leaves it.
- **The `openai` engine uploads voice notes to the provider you configure**, including voice notes from
  chats that are end-to-end encrypted on their network (Signal, WhatsApp...). Their senders didn't agree
  to that: prefer a local engine, or a server you run yourself.
- **`notes_to_self` turns voice notes into text in your Note to self chat**, which syncs to all
  your Beeper devices like any other chat.
- **Transcripts stay in the database** until you delete them. Beeper Minus has no expiry.

## Send later

- **Scheduled messages are sent even if they're late.** If Beeper Minus or Beeper Server was down at
  the chosen time, the message goes out as soon as both are back. Check the Scheduled page after an
  outage.
- **Rejected messages are kept, not lost.** When Beeper refuses one for good (a deleted chat...), it
  stays on the Scheduled page with the error, and editing it retries. Sign-in, permission and
  rate-limit errors are retried automatically. A send that timed out is kept with an error too, not retried:
  Beeper may have sent it anyway, so check the chat before editing it.

## Keeping up to date

The `beeper-server` package pins a version from Beeper's update feed (see `nix/beeper-server.nix`).
Beeper's security fixes only reach you when that version is bumped.
