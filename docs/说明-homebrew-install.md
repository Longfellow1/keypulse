# Homebrew Install

Penpal uses Homebrew as the public distribution path.

Homebrew installs the `penpal` executable. The Penpal CLI owns runtime initialization, launchd jobs, diagnostics, and uninstall.

## Public install

```bash
curl -fsSL https://raw.githubusercontent.com/Longfellow1/penpal/main/install.sh | bash
```

The script checks macOS and Homebrew, taps the Penpal formula, installs or upgrades `penpal`, then runs:

```bash
penpal install init
```

## Manual install

```bash
brew tap Longfellow1/penpal
brew install penpal
penpal install init
```

## Lifecycle commands

```bash
penpal install init       # Create ~/.penpal, default config, DB, launchd jobs
penpal install launchd    # Recreate launchd jobs using the installed penpal executable
penpal install doctor     # Check executable, config, database, and launchd status
penpal install uninstall  # Remove launchd jobs; preserve user data by default
```

Destructive cleanup is explicit:

```bash
penpal install uninstall --remove-runtime
penpal install uninstall --remove-data --force
```

`--remove-data --force` removes Penpal config, database, and runtime state under `~/.penpal`.

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
