# Homebrew Install

KeyPulse uses Homebrew as the public distribution path.

Homebrew installs the `keypulse` executable. The KeyPulse CLI owns runtime initialization, launchd jobs, diagnostics, and uninstall.

## Public install

```bash
curl -fsSL https://raw.githubusercontent.com/Longfellow1/keypulse/main/install.sh | bash
```

The script checks macOS and Homebrew, taps the KeyPulse formula, installs or upgrades `keypulse`, then runs:

```bash
keypulse install init
```

## Manual install

```bash
brew tap Longfellow1/keypulse
brew install keypulse
keypulse install init
```

## Lifecycle commands

```bash
keypulse install init       # Create ~/.keypulse, default config, DB, sink binding, launchd jobs
keypulse install launchd    # Recreate launchd jobs using the installed keypulse executable
keypulse install doctor     # Check executable, config, database, sink, model, and launchd status
keypulse install uninstall  # Remove launchd jobs; preserve user data by default
```

Destructive cleanup is explicit:

```bash
keypulse install uninstall --remove-runtime
keypulse install uninstall --remove-data --force
```

`--remove-data --force` removes KeyPulse config, database, and runtime state under `~/.keypulse`. It does not remove Obsidian vault notes.

## Developer install

For local development from a checkout:

```bash
bash install.sh --dev
```

This keeps the older editable virtualenv path for development only. Public users should not need Python venvs, editable installs, wrapper scripts, or repo paths.

The `.app` bundle path remains a developer packaging path:

```bash
make install
```

Do not use `make install` as the public README install path.
