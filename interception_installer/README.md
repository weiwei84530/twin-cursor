# Interception driver installer (vendored)

`install-interception.exe` is the official command-line installer for the
[Interception](https://github.com/oblitum/Interception) kernel driver,
taken unmodified from the upstream release
[v1.0.1](https://github.com/oblitum/Interception/releases/tag/v1.0.1)
(`Interception.zip`, `command line installer/install-interception.exe`).

It is bundled into the TwinCursor executable so that the app can offer to
install the driver on machines that do not have it. Interception is
licensed for non-commercial use under the LGPL 3.0 (see `LGPL 3.0.txt`,
also from the upstream release), which permits redistribution of the
driver and installer binaries when the driver is used solely through the
Interception library API — which is how TwinCursor uses it.
