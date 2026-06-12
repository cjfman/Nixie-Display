# Run-once signing public keys

Trusted RSA **public** keys for verifying `deployment_scripts/*.sh` before
`raspi_run` executes them. The filename stem is the name logged on a successful
verify (e.g. `charles.pem` → "signed by 'charles'").

These are public — safe to commit. The matching **private** keys live OUTSIDE the
repo (e.g. `~/.nixie_runonce/`) and are gitignored; never commit one.

On the Pi these get copied to `~/.nixie/runonce_keys/` (which is what the runner
actually checks) — do that from a trusted terminal with:

    bash scripts/setup_audio_perms.sh --install-keys

Installing a key turns ON enforcement: from then on only signed scripts run.

Sign a script with the matching private key:

    bin/sign_runonce --key ~/.nixie_runonce/charles_priv.pem --name charles deployment_scripts/foo.sh
