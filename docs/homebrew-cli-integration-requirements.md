# Homebrew + KeyPulse CLI Integration Requirements

This document is a requirements handoff for the agent working on the KeyPulse CLI.

The local CLI is still being optimized and is not considered final. This document should define the product contract and expected installation shape, not prescribe the exact implementation.

## Current context

KeyPulse currently has several overlapping installation paths:

- `make install` builds the `.app`, copies it into `/Applications`, installs launchd plists, and reloads agents.
- `install.sh` creates `~/.keypulse/venv`, installs the local repo in editable mode, writes a `~/.local/bin/keypulse` wrapper, writes default config, runs sink discovery, and writes launchd plists.
- The `keypulse` CLI already has operational commands such as `setup`, `start`, `serve`, `stop`, `status`, `doctor`, `healthcheck`, `obsidian sync`, `model setup`, and `sinks detect`.
- Homebrew has not yet been integrated as the public install path.

This creates confusion for public users: `make install`, `install.sh`, `.app` bundling, launchd plists, and CLI commands each own part of the lifecycle.

## Desired product principle

Homebrew should install the KeyPulse CLI.

The KeyPulse CLI should initialize, configure, supervise, and uninstall the KeyPulse runtime.

In short:

```text
Homebrew owns "put the keypulse executable on this Mac".
KeyPulse CLI owns "turn KeyPulse into a configured, running personal system".
```

This keeps Homebrew as the distribution layer and KeyPulse as the lifecycle owner.

## Target user flows

### One-line public install

The public README should eventually be able to offer:

```bash
curl -fsSL https://raw.githubusercontent.com/Longfellow1/keypulse/main/install.sh | bash
```

Expected behavior:

1. Verify macOS.
2. Verify Homebrew exists, or provide a clear install instruction.
3. Run `brew tap Longfellow1/keypulse`.
4. Run `brew install keypulse`.
5. Run `keypulse install init`.
6. End with the exact next user action, usually permissions and `keypulse doctor`.

### Advanced Homebrew install

Advanced users should be able to run:

```bash
brew tap Longfellow1/keypulse
brew install keypulse
keypulse install init
```

### Daily operation

After installation, users should mainly interact through:

```bash
keypulse status
keypulse doctor
keypulse start
keypulse stop
keypulse healthcheck
keypulse obsidian sync --incremental
keypulse obsidian sync --yesterday
keypulse model setup
keypulse sinks detect --apply
```

## CLI requirements

Add an install command group, or equivalent lifecycle surface, under the CLI.

Recommended command shape:

```bash
keypulse install init
keypulse install launchd
keypulse install uninstall
keypulse install doctor
```

Exact names can change if the CLI design prefers another shape, but the lifecycle responsibilities should remain explicit.

### `keypulse install init`

Should perform first-run setup after `brew install keypulse`.

Expected responsibilities:

- Create `~/.keypulse/` if missing.
- Create `~/.keypulse/logs/` if missing.
- Write default `~/.keypulse/config.toml` if missing.
- Never overwrite an existing config unless the user passes a force flag.
- Run or guide `keypulse setup`.
- Run or guide `keypulse model setup`.
- Run `keypulse sinks detect --apply`.
- Install launchd jobs through `keypulse install launchd`.
- Start KeyPulse or tell the user the exact command to start it.
- Run `keypulse doctor` at the end, or print the exact command.

Expected output style:

- Use human-readable steps.
- Tell the user what happened and what still needs permission.
- Do not print raw stack traces for expected setup failures.
- End with a short checklist.

### `keypulse install launchd`

Should own launchd plist creation and reload.

Expected jobs:

- `com.keypulse.daemon` -> `keypulse serve`
- `com.keypulse.healthcheck` -> `keypulse healthcheck`
- `com.keypulse.obsidian-sync` -> `keypulse obsidian sync`
- `com.keypulse.obsidian-sync-hourly` -> `keypulse obsidian sync --incremental`

Requirements:

- Use the actual `keypulse` binary path discovered at runtime, not a hardcoded repo path.
- Use absolute log paths under `~/.keypulse/logs/`.
- Write plists under `~/Library/LaunchAgents/`.
- Be idempotent.
- Preserve or warn before overwriting user-edited plists.
- Support both `launchctl load/unload` fallback and modern `bootstrap/bootout`.
- Make `keypulse start`, `keypulse stop`, and `keypulse status` consistent with installed launchd jobs.

### `keypulse install uninstall`

Should remove KeyPulse runtime state that belongs to the installer, with safe defaults.

Expected behavior:

- Stop launchd jobs.
- Remove managed launchd plists.
- Leave user data by default.
- Require an explicit flag to remove `~/.keypulse/keypulse.db`, config, logs, or Obsidian output.
- Print what remains after uninstall.

Example shape:

```bash
keypulse install uninstall
keypulse install uninstall --remove-runtime
keypulse install uninstall --remove-data
```

Exact flag names can change, but destructive data removal must be explicit.

### `keypulse install doctor`

Should verify install-level health:

- `keypulse` executable path
- config path
- database path
- active sink
- launchd plist presence
- launchd loaded status
- model backend readiness
- macOS Accessibility / Screen Recording guidance if needed

This can wrap or extend existing `keypulse doctor`.

## Homebrew formula requirements

The Homebrew formula should install the Python CLI in a stable way.

Expected formula responsibilities:

- Install `keypulse` executable.
- Package Python dependencies using Homebrew-appropriate Python virtualenv conventions.
- Depend on a supported Python version.
- Include macOS-specific dependencies required for runtime, as feasible.
- Avoid writing `~/.keypulse` during `brew install`.
- Avoid installing or loading launchd plists during `brew install`.
- Print a caveat telling users to run `keypulse install init`.

Formula should not become the lifecycle owner. It should not duplicate the CLI's launchd management.

Suggested caveat text:

```text
To initialize KeyPulse:
  keypulse install init

Then grant macOS permissions when prompted:
  System Settings -> Privacy & Security -> Accessibility
  System Settings -> Privacy & Security -> Screen Recording

Check status:
  keypulse doctor
```

## `install.sh` requirements

`install.sh` should become a thin public bootstrapper.

Expected default behavior:

1. Check macOS.
2. Check Homebrew.
3. Tap the KeyPulse formula.
4. Install or upgrade KeyPulse.
5. Run `keypulse install init`.

The current venv/editable install path should be retained only as a developer fallback, for example:

```bash
bash install.sh --dev
```

or:

```bash
bash install.sh --no-brew
```

The public path should not require users to understand Python venvs, editable installs, wrapper scripts, or repo paths.

## Backward compatibility requirements

Existing users may already have:

- `~/.local/bin/keypulse`
- `~/.keypulse/venv`
- `~/.keypulse/config.toml`
- launchd plists pointing to `~/.local/bin/keypulse`
- launchd plists pointing to `/Applications/KeyPulse.app/...`
- launchd plists pointing to a local repo checkout

The new installer should detect these states and guide migration.

Minimum expected behavior:

- Do not silently delete old venvs or wrappers.
- If an old launchd plist points to a non-Homebrew path, warn and offer to rewrite managed plists.
- If `~/.keypulse/config.toml` exists, preserve it.
- If a daemon is already running, stop/reload only through explicit install lifecycle commands.

## Non-goals for this iteration

- Do not require an Obsidian community plugin.
- Do not require a signed `.app` or DMG.
- Do not replace all CLI naming in one pass.
- Do not make Homebrew own all four launchd jobs.
- Do not publish broad community announcements until install is verified on a clean Mac.

## Acceptance criteria

The integration is acceptable when a fresh Mac user can run:

```bash
brew tap Longfellow1/keypulse
brew install keypulse
keypulse install init
keypulse doctor
```

and reach a state where:

- `keypulse --help` works.
- `keypulse status` works.
- `keypulse doctor` gives actionable setup status.
- `keypulse start` starts the daemon through the same lifecycle model as launchd.
- `keypulse stop` stops the daemon cleanly.
- `keypulse obsidian sync --yesterday` can run after sink setup.
- launchd plists reference the installed `keypulse` executable, not a developer checkout.
- rerunning `keypulse install init` is safe.

## Documentation updates expected

Update these docs when implementation lands:

- `README.md`
- `README.en.md`
- `docs/setup-onboarding.md`
- `docs/launch-checklist.md`
- a new or updated `docs/homebrew-install.md`

The README should eventually present Homebrew as the primary public path and `make install` as a developer/local build path.
