---
name: factory-sites
description: Set up and troubleshoot the machines factories run on — the local hub, other machines over SSH, sandboxed sites, model API keys, and installing `factory` remotely. Use when someone wants factories to run on another machine or in a sandbox, when a site shows as unreachable, or when runs fail for configuration reasons.
---

# Factory sites

A **site** is a machine with a `factory daemon` and a state directory
(`~/.factory`). The site on your own machine is also the **hub**: the CLI and the
dashboard talk only to the hub, and the hub keeps SSH tunnels to every other
site. Sites are autonomous — their runs and overseer keep going while your laptop
sleeps.

## The local hub

```sh
factory up        # start (idempotent); prints every site's health
factory down      # stop the hub; runs keep going and are re-adopted on the next `up`
factory sites     # health, capacity, and running counts for every site
```

Configuration lives in `~/.factory/site.json` (all optional):

```json
{
  "name": "laptop",
  "max_runs": 4,
  "isolation": "none",
  "default_model": "deepseek/deepseek-v4-flash",
  "default_harness": "builtin",
  "opencode": "/Users/me/.opencode/bin/opencode",
  "opencode_model": "opencode/big-pickle",
  "codex_model": "gpt-6.1-sol",
  "codex_writable": ["~/.pad"],
  "approvals": { "url": "https://computer.jimmyhmiller.com/approvals", "token_file": "~/.config/gatekeeper/token" },
  "reviewers": ["judge"],
  "overseer": { "policy": "nudge", "interval_seconds": 20 },
  "providers": [
    { "name": "llama", "base_url": "http://127.0.0.1:8188/v1/chat/completions", "key_env": "" }
  ]
}
```

`reviewers` are the reviewers that watch runs whose run and factory name none
(default: none, so no judgment). Each reviewer names its own model and harness;
there is no overseer model (`overseer.model` is refused with a pointer here).

There is no built-in model: `default_model` (builtin harness), `opencode_model`
(opencode harness) and `codex_model` (codex harness) are the site's defaults, and
without them every run must name a model.

`approvals` is where stations with `approve: yes` send their proposals: the
`approvals` Gatekeeper function's base URL and a file holding a token it accepts
(a device token from `gatekeeper-login` works; it can submit and read but never
decide). Without it, a run that needs approval is refused when it is made.

`default_harness` is `builtin`, `opencode`, or `codex`. The `opencode` harness needs OpenCode
on the site (found on PATH or in `~/.opencode/bin`; `opencode` overrides the path);
`opencode_model` is its default model. Under `isolation: "sandbox"` the whole
OpenCode process runs in the sandbox.

The `codex` harness needs the Codex CLI on the site, logged in (found on PATH;
`codex` overrides the path); `codex_model` is its default model. Codex runs every
agent command in its own sandbox: an agent with `files` and `shell` can write
only its workspace, plus the directories in `codex_writable` (for example `~/.pad`,
so agents can write pads); a reviewer is read-only. Codex keeps its threads and
credentials in the operator's CODEX_HOME and always reads its `AGENTS.md`, so
the operator's global Codex instructions apply to factory agents; every other
setting is passed on the command line, never read from the operator's
`config.toml`.

`isolation: "sandbox"` wraps every agent shell command: `sandbox-exec` on macOS,
`bwrap` on Linux. Writes are confined to the run's workspace (plus temp dirs); file
tools are always confined to the workspace. Overseer `policy` is `observe` (record
findings), `nudge` (also message the agent), or `pause` (alerts pause the run).

## Model keys

Keys are read on the site that runs the agent: from its environment, or from
`~/.factory/secrets.env` (`DEEPSEEK_API_KEY=…` lines, mode 0600). A daemon started
over SSH has no interactive environment, so remote sites need `secrets.env`:

```sh
factory site secrets SITE DEEPSEEK_API_KEY     # copies the value from this shell, over ssh stdin
```

## Adding another machine

Requirements on the remote: SSH access without a password prompt, `git`, a C
compiler driver (`cc`) and libcurl to link with. No Coil toolchain is needed there:
`site install` compiles `factory` for the remote platform on this machine, ships
the object over ssh, and links it on the remote. For a sandboxed Linux site, `bwrap`.

```sh
factory site install metaphysics --ssh computer.example.com   # compiles here, links there
factory site add metaphysics --ssh computer.example.com [--sandbox]
factory site secrets metaphysics DEEPSEEK_API_KEY
factory sites
```

`site add` starts the remote daemon, fetches its token, and registers it with the
hub. From then on `factory start F --on metaphysics --project .` pushes the
project's HEAD to a mirror on that machine, runs there, and `factory pull RUN`
brings the branch back.

## Troubleshooting

| symptom | check |
| --- | --- |
| `hub is not running` | `factory up`; its log is `~/.factory/daemon.log` |
| site `unreachable` | `ssh HOST true` works non-interactively? remote daemon up (`ssh HOST ~/.factory/bin/factory status`)? |
| runs fail at once with a key error | `secrets.env` on that site; `factory show RUN` names the variable |
| `lost` runs after a reboot | `factory resume RUN` on each; they continue from their last checkpoint |
| remote link fails | install `cc` and libcurl development files there (Debian/Ubuntu: `build-essential libcurl4-openssl-dev`) and rerun `site install` |
