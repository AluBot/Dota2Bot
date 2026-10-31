# Dota2Bot

A Steam bot that follows streamers through their Steam friend list and tells what they are doing in Dota 2.
It reads their Dota 2 Rich Presence, inspects live matches via the Dota 2 Game Coordinator and the Steam Web API,
keeps a short match history in PostgreSQL and exposes everything through a small local HTTP API
for a Twitch bot.

## How it works

* A streamer registers on the website, which calls `/accept_invite/`; then they send a friend invite to the bot,
  and the bot accepts it only for the next 10 minutes.
* Rich Presence updates of the friends are classified into activities: playing, spectating, in the menu, unsupported.
* Playing and spectating matches are polled until all players and heroes are known. Playing matches are saved into
  the database together with all their players, and the outcome is looked up after the friend leaves the match.
* An hourly sync with the friends' match history fills what the bot has missed and adds the `abandon` flag.

## Layout

| Path | What is there |
|---|---|
| `src/main.py` | Entry point: runs the bot and the local API together |
| `src/core/` | The bot class |
| `src/web/` | FastAPI app and routes (`127.0.0.1:8000`) |
| `src/ext/friends/` | Rich Presence, live matches, match history (`presence`, `models`, `matches`, `queries`, `cog`) |
| `src/ext/datafeed.py` | Daily refresh of the Dota 2 items in the database |
| `src/shared/` | Git submodule with the code shared with other bots |
| `sql/` | Database schema |

## Setup

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
git clone --recurse-submodules <repo url>
cp .env.example .env   # fill in the values
# run sql/01_initial.sql against your PostgreSQL database (it expects the `irenesbot.tokens` table)
make run
```

## Development

```sh
make ruff     # linter and formatter check
make ty       # type checker
make check    # both
make update   # upgrade the dependencies
```

## Deployment

Pushes to `main` run `.github/workflows/deploy_service.yml`, which pulls the code on the server and restarts
the `dota2bot` systemd service (see `dota2bot.service`).
